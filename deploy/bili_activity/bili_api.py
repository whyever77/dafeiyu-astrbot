"""Small, structured Bilibili adapter for the local companion.

API shapes were reviewed against Iseenope/bilibili-mcp-server at
20eedd09862aea2c160163007f45221c544651b2 (MIT). No POST is automatically retried.
The caller must obtain approval before invoking Executor.execute().
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import math
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

API = "https://api.bilibili.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
MIXIN = (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
         27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
         37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
         22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52)
WRITE_ACTIONS = frozenset({
    "comment", "reply", "follow", "unfollow", "favorite_add", "favorite_remove",
    "folder_create", "folder_rename", "favorite_move", "like", "coin", "danmaku",
})


class BiliError(RuntimeError):
    """Messages deliberately omit URLs, cookies and remote response bodies."""

    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code if type(code) is int else None


def integer_range(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"invalid {name}")
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"invalid {name}") from None
    if str(number) != str(value) or not low <= number <= high:
        raise ValueError(f"invalid {name}")
    return number


def interaction_args(action, args):
    """Validate identically for preview and direct Executor calls; no credentials."""
    args = dict(args)
    if action == "like":
        args["like"] = integer_range(args.get("like"), "like", 0, 1)
    elif action == "coin":
        args["multiply"] = integer_range(args.get("multiply"), "multiply", 1, 2)
        args["select_like"] = integer_range(args.get("select_like", 0), "select_like", 0, 1)
    elif action == "danmaku":
        message = args.get("message")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 100:
            raise ValueError("danmaku must contain 1..100 characters")
        args["message"] = message.strip()
        for name, default, low, high in (
            ("progress", None, 0, 86400000), ("color", 16777215, 0, 16777215),
            ("fontsize", 25, 18, 36), ("pool", 0, 0, 0), ("mode", 1, 1, 5),
        ):
            args[name] = integer_range(args.get(name, default), name, low, high)
        if args["fontsize"] not in {18, 25, 36} or args["mode"] not in {1, 4, 5}:
            raise ValueError("unsupported danmaku fontsize or mode")
    return args


def positive(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive integer") from None
    if result <= 0 or str(value) != str(result):
        raise ValueError(f"{name} must be a positive integer")
    return result


def bounded_page(args):
    return positive(args.get("page", 1), "page"), min(50, positive(args.get("page_size", 20), "page_size"))


def private_json(path, value):
    """Write complete credentials atomically; never print them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def decode_danmaku_xml(body):
    if len(body) > 8 * 1024 * 1024 or b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise BiliError("danmaku payload rejected")
    root = ET.fromstring(body)
    if root.tag != "i":
        raise BiliError("danmaku response was not XML data")
    result = []
    for item in root.findall("d"):
        props = item.attrib.get("p", "").split(",")
        if len(props) < 2:
            continue
        try:
            at = float(props[0])
        except ValueError:
            continue
        if at < 0 or not math.isfinite(at):
            continue
        result.append({"at": at, "text": item.text or "", "id": props[7] if len(props) > 7 else ""})
    return sorted(result, key=lambda x: x["at"])


class BiliAPI:
    def __init__(self, cookie_file):
        self.cookie_file = Path(cookie_file)
        self._wbi = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    def cookies(self):
        if not self.cookie_file.exists():
            return {}
        with self.cookie_file.open(encoding="utf-8") as stream:
            value = json.load(stream)
        return value.get("cookies", value)

    def credentials_present(self):
        value = self.cookies()
        return all(value.get(k) for k in ("SESSDATA", "bili_jct", "DedeUserID"))

    def headers(self, authenticated=True):
        headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
        if authenticated:
            # Only the API host receives these headers. Never media CDN URLs.
            headers["Cookie"] = "; ".join(
                f"{name}={value}" for name, value in self.cookies().items()
                if name in {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4"}
                and value and "\n" not in str(value) and "\r" not in str(value))
        return headers

    async def _throttle(self):
        async with self._lock:
            wait = 0.5 - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def request(self, path, params=None, method="GET", data=None, allow_guest=False):
        if not path.startswith("/x/") or ".." in path or "?" in path:
            raise ValueError("invalid API path")
        if method not in {"GET", "POST"}:
            raise ValueError("invalid method")
        await self._throttle()
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=25), trust_env=True,
            headers=self.headers(), raise_for_status=False,
        ) as session:
            async with session.request(method, API + path, params=params, data=data, allow_redirects=False) as response:
                if response.status != 200:
                    raise BiliError(f"Bilibili HTTP {response.status}", code=-412 if response.status == 412 else None)
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise BiliError("API response exceeded limit")
                try:
                    result = json.loads(body)
                except (ValueError, UnicodeError):
                    raise BiliError("API returned invalid JSON") from None
                guest_nav = allow_guest and path == "/x/web-interface/nav" and result.get("code") == -101
                if result.get("code") != 0 and not guest_nav:
                    code = result.get("code")
                    raise BiliError("Bilibili API rejected request", code=code)
                # has/like returns the integer 0 when not liked.
                return result["data"] if result.get("data") is not None else {}

    async def signed(self, path, params):
        if not self._wbi or self._wbi[0] < time.time():
            nav = await self.request("/x/web-interface/nav", allow_guest=True)
            images = nav.get("wbi_img", {})
            raw = "".join(Path(urlparse(images[k]).path).stem for k in ("img_url", "sub_url"))
            if len(raw) < 64:
                raise BiliError("WBI keys unavailable")
            self._wbi = (time.time() + 3600, "".join(raw[i] for i in MIXIN)[:32])
        values = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in params.items()}
        values["wts"] = str(int(time.time()))
        query = urlencode(sorted(values.items())).replace("+", "%20")
        values["w_rid"] = hashlib.md5((query + self._wbi[1]).encode()).hexdigest()
        return await self.request(path, params=values)

    @staticmethod
    def media_url(url):
        parsed = urlparse(url)
        if parsed.scheme == "":
            url = "https:" + url
            parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        domains = ("bilibili.com", "bilivideo.com", "bilivideo.cn", "bilivideo.net", "acgvideo.com", "hdslb.com", "biliapi.net")
        if parsed.scheme not in {"https", "http"} or parsed.username or parsed.port not in (None, 80, 443):
            raise BiliError("untrusted media URL")
        if not any(host == d or host.endswith("." + d) for d in domains):
            raise BiliError("untrusted media host")
        # Request TLS even when the legacy subtitle API returns an HTTP URL.
        return url.replace("http://", "https://", 1)

    async def get_bytes(self, url, limit):
        url = self.media_url(url)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                         trust_env=True, headers=self.headers(False)) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise BiliError(f"media HTTP {response.status}")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > limit:
                        raise BiliError("media exceeded limit")
                return bytes(body)

    async def download(self, url, path, limit):
        url = self.media_url(url)
        path = Path(path)
        complete = False
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120),
                                             trust_env=True, headers=self.headers(False)) as session:
                async with session.get(url, allow_redirects=False) as response:
                    if response.status != 200 or (response.content_length or 0) > limit:
                        raise BiliError("media download unavailable or too large")
                    count = 0
                    with path.open("wb") as stream:
                        async for chunk in response.content.iter_chunked(65536):
                            count += len(chunk)
                            if count > limit:
                                raise BiliError("media download exceeded limit")
                            stream.write(chunk)
                    complete = True
                    return count
        finally:
            if not complete:
                path.unlink(missing_ok=True)

    async def identity(self, video_id, cid=None, allow_parts=False, page_number=None):
        """Resolve BV/av, validate selected part, never silently choose another cid."""
        video_id = str(video_id)
        match = re.search(r"(BV[1-9A-HJ-NP-Za-km-z]{10})(?![A-Za-z0-9])", video_id)
        if match:
            params = {"bvid": match.group(1)}
        elif re.fullmatch(r"(?:av)?[0-9]+", video_id):
            params = {"aid": positive(video_id.removeprefix("av"), "aid")}
        else:
            raise ValueError("a full BV or av identifier is required; title matching is not identity")
        info = await self.request("/x/web-interface/view", params)
        if (("bvid" in params and info.get("bvid") != params["bvid"])
                or ("aid" in params and int(info.get("aid", 0)) != params["aid"])):
            raise BiliError("video API returned a different identity")
        pages = info.get("pages") or []
        if page_number is not None:
            page_number = positive(page_number, "page")
            selected = next((p for p in pages if int(p.get("page", 0)) == page_number), None)
            if not selected:
                raise ValueError("page does not belong to this video")
            selected_cid = positive(selected.get("cid"), "cid")
            if cid is not None and positive(cid, "cid") != selected_cid:
                raise ValueError("page and cid refer to different video parts")
            cid = selected_cid
        if cid is None:
            if len(pages) != 1:
                if allow_parts:
                    return {"bvid": info["bvid"], "aid": int(info["aid"]), "cid": None,
                            "title": info.get("title", ""), "owner": info.get("owner", {}),
                            "pages": [{"cid": p["cid"], "page": p["page"], "part": p.get("part", ""),
                                       "duration": p.get("duration", 0)} for p in pages],
                            "note": "choose an explicit cid before reading media"}
                raise ValueError("multi-part video requires an explicit cid")
            page = pages[0]
        else:
            cid = positive(cid, "cid")
            page = next((p for p in pages if int(p.get("cid", 0)) == cid), None)
            if page is None:
                raise ValueError("cid does not belong to this video")
        return {
            "bvid": info["bvid"], "aid": int(info["aid"]), "cid": int(page["cid"]),
            "title": info.get("title", ""), "part": page.get("part", ""),
            "page": page.get("page"), "duration": page.get("duration", info.get("duration", 0)),
            "owner": info.get("owner", {}), "pages": [
                {"cid": p["cid"], "page": p["page"], "part": p.get("part", "")} for p in pages],
        }

    async def read(self, operation, args):
        page, size = bounded_page(args)
        if operation == "status":
            if not self.credentials_present():
                return {"logged_in": False, "credential_file_present": self.cookie_file.exists()}
            nav = await self.request("/x/web-interface/nav")
            return {"logged_in": bool(nav.get("isLogin")), "uid": nav.get("mid"), "name": nav.get("uname")}
        if operation in {"info", "subtitles", "danmaku", "comments", "replies"}:
            identity = await self.identity(args.get("bvid") or args.get("video_id"), args.get("cid"),
                                           allow_parts=operation == "info",
                                           page_number=args.get("page") if operation == "info" else None)
            result = {"identity": identity, **{k: identity[k] for k in ("bvid", "aid", "cid")}}
            if operation == "info":
                return result
            if operation == "subtitles":
                player = await self.signed("/x/player/wbi/v2", {"bvid": identity["bvid"], "cid": identity["cid"]})
                tracks = (player.get("subtitle") or {}).get("subtitles") or []
                if not tracks:
                    return {**result, "subtitles": [], "tracks": [], "note": "no subtitle track available"}
                selected = next((x for x in tracks if str(x.get("lan", "")).startswith("zh")), tracks[0])
                raw = await self.get_bytes(selected["subtitle_url"], 2 * 1024 * 1024)
                body = json.loads(raw).get("body", [])
                return {**result, "language": selected.get("lan"), "subtitles": [
                    {"from": x["from"], "to": x["to"], "content": x["content"]} for x in body]}
            if operation == "danmaku":
                raw = await self.get_bytes(f"https://comment.bilibili.com/{identity['cid']}.xml", 8 * 1024 * 1024)
                entries = decode_danmaku_xml(raw)
                start, end = float(args.get("start", 0)), float(args.get("end", identity["duration"]))
                if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end < start:
                    raise ValueError("invalid danmaku range")
                selected = [x for x in entries if start <= x["at"] <= end]
                offset = max(0, int(args.get("offset", 0)))
                limit = min(200, positive(args.get("limit", 100), "limit"))
                items = selected[offset:offset + limit]
                return {**result, "range": [start, end], "items": items,
                        "next_offset": offset + limit if offset + limit < len(selected) else None,
                        "note": "current XML pool; not guaranteed to contain all historical danmaku"}
            if operation == "comments":
                data = await self.request("/x/v2/reply/main", {
                    "type": 1, "oid": identity["aid"], "mode": 3,
                    "next": max(0, int(args.get("cursor", args.get("next", 0)))), "ps": size})
                cursor = data.get("cursor", {})
                return {**result, "comments": [self.comment(x) for x in data.get("replies") or []],
                        "next_cursor": None if cursor.get("is_end") else cursor.get("next"),
                        "is_end": bool(cursor.get("is_end"))}
            data = await self.request("/x/v2/reply/reply", {
                "type": 1, "oid": identity["aid"], "root": positive(args.get("root_rpid"), "root_rpid"),
                "pn": page, "ps": size})
            count = (data.get("page") or {}).get("count", 0)
            return {**result, "root": self.comment(data["root"]) if data.get("root") else None,
                    "replies": [self.comment(x) for x in data.get("replies") or []],
                    "page": page, "next_page": page + 1 if page * size < count else None, "total": count}
        if operation == "search":
            word = str(args.get("keyword", "")).strip()
            if not word or len(word) > 100:
                raise ValueError("keyword must contain 1..100 characters")
            return await self.signed("/x/web-interface/wbi/search/type",
                                     {"search_type": "video", "keyword": word, "page": page, "page_size": size})
        if operation == "home":
            return await self.request("/x/web-interface/index/top/feed/rcmd", {"ps": min(size, 10), "fresh_type": 3, "version": 1})
        if operation == "following_feed":
            params = {"type": "video"}
            if args.get("offset"):
                params["offset"] = str(args["offset"])
            return await self.request("/x/polymer/web-dynamic/v1/feed/all", params)
        if operation == "user":
            return await self.signed("/x/space/wbi/acc/info", {"mid": positive(args.get("uid"), "uid")})
        if operation == "followings":
            return await self.request("/x/relation/followings",
                                      {"vmid": positive(args.get("uid") or self.cookies().get("DedeUserID"), "uid"),
                                       "pn": page, "ps": size, "order": "desc"})
        if operation == "folders":
            params = {"up_mid": positive(args.get("uid") or self.cookies().get("DedeUserID"), "uid"), "type": 0}
            if args.get("aid"):
                params.update(rid=positive(args["aid"], "aid"), type=2)
            return await self.request("/x/v3/fav/folder/created/list-all", params)
        if operation == "favorites":
            return await self.request("/x/v3/fav/resource/list", {
                "media_id": positive(args.get("folder_id"), "folder_id"), "pn": page, "ps": size, "order": "mtime"})
        raise ValueError("unsupported read operation")

    @staticmethod
    def comment(value):
        return {
            "rpid": str(value["rpid"]), "root_rpid": str(value.get("root", 0)),
            "parent_rpid": str(value.get("parent", 0)), "message": (value.get("content") or {}).get("message", ""),
            "uid": str(value.get("mid", "")), "name": (value.get("member") or {}).get("uname", ""),
            "reply_count": value.get("rcount", 0), "ctime": value.get("ctime"),
            "replies": [BiliAPI.comment(x) for x in value.get("replies") or []],
        }

    async def prepare_write(self, action, args):
        """Validate and freeze API payload before the non-idempotent operation."""
        if action not in WRITE_ACTIONS:
            raise ValueError("unsupported account operation")
        args = interaction_args(action, args)
        if not self.credentials_present():
            raise BiliError("QR login is required", code=-101)
        if args.get("account_uid") and str(args["account_uid"]) != str(self.cookies().get("DedeUserID")):
            raise BiliError("login account changed since the proposal")
        csrf = self.cookies()["bili_jct"]
        payload = {"csrf": csrf, "csrf_token": csrf}
        identity = None
        if action in {"comment", "reply", "favorite_add", "favorite_remove", "favorite_move", "like", "coin", "danmaku"}:
            identity = await self.identity(args.get("bvid"), args.get("cid"))
        if action == "like":
            payload.update(aid=identity["aid"], like=args["like"])
            return "/x/v2/view/like", payload, identity
        if action == "coin":
            before = await self.request("/x/web-interface/archive/coins", {"aid": identity["aid"]})
            count = integer_range(before.get("multiply"), "current coins", 0, 2)
            if count + args["multiply"] > 2:
                raise ValueError("video coin limit would be exceeded")
            identity = {**identity, "coins_before": count}
            payload.update(aid=identity["aid"], multiply=args["multiply"], select_like=args["select_like"])
            return "/x/web-interface/coin/add", payload, identity
        if action == "danmaku":
            if args["progress"] > float(identity["duration"]) * 1000:
                raise ValueError("danmaku progress exceeds selected part duration")
            payload.update(type=1, oid=identity["cid"], msg=args["message"],
                           **{k: args[k] for k in ("progress", "color", "fontsize", "pool", "mode")})
            return "/x/v2/dm/post", payload, identity
        if action in {"comment", "reply"}:
            message = str(args.get("message", "")).strip()
            if not message or len(message) > 1000:
                raise ValueError("comment must contain 1..1000 characters")
            payload.update(type=1, oid=identity["aid"], message=message, plat=1)
            if action == "reply":
                root = positive(args.get("root_rpid"), "root_rpid")
                parent = positive(args.get("parent_rpid"), "parent_rpid")
                # Check the exact reply target exists in the declared video's thread.
                detail = await self.request("/x/v2/reply/detail", {"type": 1, "oid": identity["aid"], "root": parent})
                target = detail.get("root") or {}
                if int(target.get("rpid", 0)) != parent or int(target.get("oid", identity["aid"])) != identity["aid"]:
                    raise BiliError("reply target could not be verified")
                actual_root = int(target.get("root") or parent)
                if root != actual_root:
                    raise BiliError("reply root does not match the selected comment")
                payload.update(root=root, parent=parent)
            return "/x/v2/reply/add", payload, identity
        if action in {"follow", "unfollow"}:
            payload.update(fid=positive(args.get("uid"), "uid"), act=1 if action == "follow" else 2, re_src=14)
            return "/x/relation/modify", payload, None
        if action in {"favorite_add", "favorite_remove"}:
            folder = positive(args.get("folder_id"), "folder_id")
            await self.owned_folder(folder)
            payload.update(rid=identity["aid"], type=2,
                           add_media_ids=str(folder) if action == "favorite_add" else "",
                           del_media_ids=str(folder) if action == "favorite_remove" else "")
            return "/x/v3/fav/resource/deal", payload, identity
        if action == "favorite_move":
            source = positive(args.get("source_folder_id"), "source_folder_id")
            target = positive(args.get("target_folder_id"), "target_folder_id")
            if source == target:
                raise ValueError("source and destination folders must differ")
            await self.owned_folder(source)
            await self.owned_folder(target)
            memberships = await self.memberships(identity["aid"])
            if source not in memberships:
                raise ValueError("video is not in the source folder")
            payload.update(src_media_id=source, tar_media_id=target, resources=f"{identity['aid']}:2",
                           mid=positive(self.cookies()["DedeUserID"], "uid"))
            return "/x/v3/fav/resource/move", payload, identity
        title = str(args.get("title", "")).strip()
        if not title or len(title) > 80:
            raise ValueError("folder title must contain 1..80 characters")
        payload["title"] = title
        if action == "folder_create":
            payload.update(intro="", privacy=1)  # private by default
            return "/x/v3/fav/folder/add", payload, None
        folder = positive(args.get("folder_id"), "folder_id")
        existing = await self.owned_folder(folder)
        payload.update(media_id=folder, intro=existing.get("intro", ""),
                       privacy=1 if int(existing.get("attr", 0)) & 1 else 0,
                       cover=existing.get("cover", ""))
        return "/x/v3/fav/folder/edit", payload, None

    async def owned_folder(self, folder):
        result = await self.request("/x/v3/fav/folder/info", {"media_id": folder})
        owner = (result.get("upper") or {}).get("mid")
        if str(owner) != str(self.cookies().get("DedeUserID")):
            raise BiliError("folder is not owned by this login")
        return result

    async def memberships(self, aid):
        result = await self.read("folders", {"aid": aid})
        return {int(x["id"]) for x in result.get("list") or [] if x.get("fav_state") == 1}

    async def verify(self, action, args, response, identity):
        if action == "like":
            liked = await self.request("/x/web-interface/archive/has/like", {"aid": identity["aid"]})
            return {"verified": type(liked) is int and liked == int(args["like"]),
                    "identity": identity, "visibility": "unknown"}
        if action == "coin":
            result = await self.request("/x/web-interface/archive/coins", {"aid": identity["aid"]})
            count = result.get("multiply")
            verified = type(count) is int and count == identity["coins_before"] + int(args["multiply"])
            if int(args.get("select_like", 0)):
                liked = await self.request("/x/web-interface/archive/has/like", {"aid": identity["aid"]})
                verified = verified and type(liked) is int and liked == 1
            return {"verified": verified, "identity": identity, "visibility": "unknown"}
        if action == "danmaku":
            dmid = str(response.get("dmid_str") or response.get("dmid") or "")
            offset = 0
            # The XML pool is bounded and may lag behind the POST response.
            for _ in range(10):
                result = await self.read("danmaku", {"bvid": identity["bvid"], "cid": identity["cid"],
                    "start": max(0, int(args["progress"]) / 1000 - 1),
                    "end": int(args["progress"]) / 1000 + 1, "offset": offset, "limit": 200})
                if dmid and any(str(x.get("id")) == dmid and x.get("text") == args["message"].strip()
                                and abs(float(x["at"]) * 1000 - int(args["progress"])) <= 1
                                for x in result["items"]):
                    return {"verified": True, "visibility": "public", "dmid": dmid, "identity": identity}
                offset = result.get("next_offset")
                if offset is None:
                    break
            return {"verified": False, "visibility": "unknown", "dmid": dmid, "identity": identity}
        if action in {"follow", "unfollow"}:
            result = await self.request("/x/relation", {"fid": positive(args["uid"], "uid")})
            followed = int(result.get("attribute", 0)) in {2, 6}
            return {"verified": followed == (action == "follow"), "followed": followed, "uid": args["uid"]}
        if action.startswith("favorite_"):
            memberships = await self.memberships(identity["aid"])
            if action == "favorite_move":
                verified = int(args["source_folder_id"]) not in memberships and int(args["target_folder_id"]) in memberships
            else:
                verified = (int(args["folder_id"]) in memberships) == (action == "favorite_add")
            return {"verified": verified, "identity": identity, "folder_ids": sorted(memberships)}
        if action.startswith("folder_"):
            folder_id = positive(response.get("id") if action == "folder_create" else args["folder_id"], "folder_id")
            result = await self.owned_folder(folder_id)
            return {"verified": result.get("title") == str(args["title"]).strip(),
                    "folder_id": folder_id, "title": result.get("title")}
        rpid = str(response.get("rpid") or (response.get("reply") or {}).get("rpid") or "")
        if not rpid:
            return {"verified": False, "reason": "reply identifier missing; no retry"}
        detail = await self.request("/x/v2/reply/detail", {"type": 1, "oid": identity["aid"], "root": rpid})
        reply = detail.get("root") or {}
        verified = (str(reply.get("rpid")) == rpid and
                    (reply.get("content") or {}).get("message") == str(args["message"]).strip() and
                    str(reply.get("mid")) == str(self.cookies()["DedeUserID"]) and
                    int(reply.get("oid", identity["aid"])) == identity["aid"])
        if action == "reply":
            verified = verified and (str(reply.get("root")) == str(args["root_rpid"])) and (str(reply.get("parent")) == str(args["parent_rpid"]))
        return {"verified": verified, "rpid": rpid, "identity": identity, "visibility": "unknown"}


class Executor:
    """Persistent operation ledger. Unknown POST outcomes are never retried."""
    def __init__(self, api, state_path):
        self.api = api
        self.state_path = Path(state_path)
        with closing(self._connect()) as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,fingerprint TEXT,status TEXT,result TEXT)")
            db.execute("UPDATE operations SET status='uncertain' WHERE status='executing'")
        self.lock = asyncio.Lock()

    def _connect(self):
        # Open per operation so Windows releases the file between calls and
        # tests/temporary state can be removed without killing the process.
        db = sqlite3.connect(self.state_path, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def close(self):
        """Compatibility hook; no long-lived connection is held."""

    async def execute(self, operation_id, action, args):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", operation_id):
            raise ValueError("invalid operation_id")
        serialized = json.dumps([action, args], sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        async with self.lock:
            db = self._connect()
            try:
                row = db.execute("SELECT fingerprint,status,result FROM operations WHERE id=?", (operation_id,)).fetchone()
                if row:
                    if row[0] != fingerprint:
                        raise ValueError("operation_id already belongs to different arguments")
                    return {"operation_id": operation_id, "status": row[1], "replayed": True,
                            **(json.loads(row[2]) if row[2] else {"verified": False, "reason": "unknown previous outcome; no retry"})}
                # Validation may fail safely before the durable execution reservation.
                path, data, identity = await self.api.prepare_write(action, args)
                db.execute("INSERT INTO operations VALUES(?,?,?,NULL)", (operation_id, fingerprint, "executing"))
                result = {"verified": False, "visibility": "unknown", "reason": "operation outcome unknown; no retry"}
                response = None
                try:
                    response = await self.api.request(path, method="POST", data=data)
                    result = await self.api.verify(action, args, response, identity)
                except asyncio.CancelledError:
                    db.execute("UPDATE operations SET status='uncertain',result=? WHERE id=?",
                               (json.dumps(result), operation_id))
                    raise
                except Exception as exc:
                    result = {"verified": False, "reason": type(exc).__name__, "retry_allowed": False,
                              "visibility": "unknown", "risk_code": getattr(exc, "code", None)}
                    if action in {"comment", "reply"} and isinstance(response, dict):
                        result["rpid"] = str(response.get("rpid") or (response.get("reply") or {}).get("rpid") or "")
                result.setdefault("visibility", "unknown")
                status = "verified" if result.get("verified") is True else "uncertain"
                db.execute("UPDATE operations SET status=?,result=? WHERE id=?",
                           (status, json.dumps(result, ensure_ascii=False), operation_id))
                return {"operation_id": operation_id, "status": status, "replayed": False, **result}
            finally:
                db.close()

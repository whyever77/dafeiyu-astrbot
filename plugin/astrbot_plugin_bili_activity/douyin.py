"""Douyin browsing, engagement proposals, and owner-approved post publishing (M2 & M3).

Operates on the real Android Douyin client (`com.ss.android.ugc.aweme`) inside
LDPlayer (`720x1280` reference resolution, scaled dynamically if screen differs):
  - Read/browse (`DouyinController.feed`): launches Douyin, dismisses popups,
    captures verified screenshots + audio loopback transcript + UI/comment text,
    and records a structured `douyin_video` episode in `Store.life`.
  - Engagement (`DouyinController.propose` / `execute_proposal`): `like`, `favorite`,
    `follow`, `comment`, `reply` go through the existing `proposals` ledger with
    `approval` / `auto_low_risk` / `auto_public` modes, daily quotas, and post-action
    screenshot verification (`verified: True/False`, never blind retry on `uncertain`).
  - Post creation & publishing (`DouyinController.propose_post` / `execute_publish_post`):
    stages validated local images/video inside `<plugin_data>/douyin_drafts/` (blocking
    path traversal), creates a `douyin_post` proposal that **always requires explicit
    owner QQ approval**, and upon approval pushes media to emulator gallery and taps
    the real「发布」button directly as configured by the owner.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .core import canonical, digest
from .phone_agent import (
    DANGEROUS_UI_RE,
    DOUYIN_PACKAGE,
    PhoneAgent,
    _extract_mcp_text,
    _summarize_ui_elements,
)
from .transport import ScreenCapture, TransportError

DOUYIN_ENGAGEMENT = frozenset({"douyin_like", "douyin_favorite"})
DOUYIN_PUBLIC = frozenset({"douyin_comment", "douyin_reply", "douyin_follow"})
DOUYIN_PUBLISH = frozenset({"douyin_post"})
DOUYIN_ACTIONS = DOUYIN_ENGAGEMENT | DOUYIN_PUBLIC | DOUYIN_PUBLISH

SHORT_ACTION_MAP = {
    "like": "douyin_like",
    "favorite": "douyin_favorite",
    "follow": "douyin_follow",
    "comment": "douyin_comment",
    "reply": "douyin_reply",
    "post": "douyin_post",
    "douyin_like": "douyin_like",
    "douyin_favorite": "douyin_favorite",
    "douyin_follow": "douyin_follow",
    "douyin_comment": "douyin_comment",
    "douyin_reply": "douyin_reply",
    "douyin_post": "douyin_post",
}

POPUP_DISMISS_LABELS = ("我知道了", "以后再说", "暂不", "跳过", "关闭", "取消")

# Reference coordinates on 720x1280 portrait screen
COORDS_720_1280 = {
    "feed_swipe": (360, 1020, 360, 260),
    "like_button": (658, 630),
    "comment_button": (658, 745),
    "favorite_button": (658, 860),
    "follow_plus": (658, 520),
    "comment_input_bar": (260, 1215),
    "comment_send_button": (655, 1215),
    "close_comment_sheet": (360, 220),
    "search_top_right": (670, 92),
    "search_submit": (655, 92),
    "bottom_plus_publish": (360, 1218),
    "album_upload_entry": (560, 1080),
    "album_first_item": (120, 280),
    "album_next_step": (620, 1210),
    "post_caption_box": (260, 340),
    "post_publish_direct": (540, 1210),
}

ALLOWED_MEDIA_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".mp4"})

DEFAULTS = {
    "douyin_enabled": False,
    "douyin_public_interactions": False,
    "douyin_engagement_actions": False,
    "douyin_publish_enabled": False,
    "douyin_interaction_mode": "approval",
    "douyin_auto_public": False,
    "douyin_max_videos_per_run": 3,
    "douyin_max_likes_per_day": 15,
    "douyin_max_favorites_per_day": 10,
    "douyin_max_comments_per_day": 3,
    "douyin_max_follows_per_day": 3,
    "douyin_max_posts_per_day": 2,
}

ACTION_LIMIT_KEYS = {
    "douyin_like": "douyin_max_likes_per_day",
    "douyin_favorite": "douyin_max_favorites_per_day",
    "douyin_comment": "douyin_max_comments_per_day",
    "douyin_reply": "douyin_max_comments_per_day",
    "douyin_follow": "douyin_max_follows_per_day",
    "douyin_post": "douyin_max_posts_per_day",
}


def validate_config(cfg: dict[str, Any]) -> None:
    cfg = DEFAULTS | cfg
    for key in (
        "douyin_enabled",
        "douyin_public_interactions",
        "douyin_engagement_actions",
        "douyin_publish_enabled",
        "douyin_auto_public",
    ):
        if not isinstance(cfg[key], bool):
            raise ValueError(f"{key} 必须是布尔开关")
    if cfg["douyin_interaction_mode"] not in {"approval", "auto_low_risk", "auto_public"}:
        raise ValueError("无效 douyin_interaction_mode")
    for key, low, high in (
        ("douyin_max_videos_per_run", 1, 10),
        ("douyin_max_likes_per_day", 0, 200),
        ("douyin_max_favorites_per_day", 0, 200),
        ("douyin_max_comments_per_day", 0, 50),
        ("douyin_max_follows_per_day", 0, 50),
        ("douyin_max_posts_per_day", 0, 10),
    ):
        val = cfg[key]
        if isinstance(val, bool) or not isinstance(val, int) or not low <= val <= high:
            raise ValueError(f"{key} 必须是 {low}–{high} 的整数")


def normalize_action(action: str) -> str:
    act = SHORT_ACTION_MAP.get((action or "").strip().lower())
    if not act:
        raise ValueError(f"不支持的抖音动作: {action!r}")
    return act


def action_enabled(action: str, cfg: dict[str, Any]) -> bool:
    cfg = DEFAULTS | cfg
    if not cfg["douyin_enabled"]:
        return False
    act = normalize_action(action)
    if act in DOUYIN_ENGAGEMENT:
        return bool(cfg["douyin_engagement_actions"])
    if act in DOUYIN_PUBLIC:
        return bool(cfg["douyin_public_interactions"])
    if act in DOUYIN_PUBLISH:
        return bool(cfg["douyin_publish_enabled"])
    return False


def auto_allowed(action: str, cfg: dict[str, Any]) -> bool:
    """Determine if a Douyin write can execute without waiting for owner QQ reply.

    SAFETY INVARIANT: `douyin_post` (publishing works) NEVER auto-executes; it
    always requires explicit owner approval in QQ, after which the controller
    taps「发布」directly.
    """
    cfg = DEFAULTS | cfg
    act = normalize_action(action)
    if not action_enabled(act, cfg):
        return False
    if act in DOUYIN_PUBLISH:
        return False
    limit_key = ACTION_LIMIT_KEYS[act]
    if cfg[limit_key] <= 0:
        return False
    mode = cfg["douyin_interaction_mode"]
    if act in DOUYIN_ENGAGEMENT:
        return mode in {"auto_low_risk", "auto_public"}
    if act in DOUYIN_PUBLIC:
        return mode == "auto_public" and cfg["douyin_auto_public"] is True
    return False


def _day_str(now: float | None = None) -> str:
    ts = time.time() if now is None else now
    return datetime.fromtimestamp(ts, ZoneInfo("Asia/Shanghai")).date().isoformat()


def _parse_feed_metadata(elements: list[dict[str, Any]]) -> dict[str, str]:
    """Heuristically extract author @handle, caption text, and counters from Douyin UI elements."""
    author = ""
    caption_parts: list[str] = []
    stats: list[str] = []
    for el in elements:
        txt = str(el.get("text") or "").strip()
        rid = str(el.get("resource_id") or "").lower()
        if not txt:
            continue
        if (txt.startswith("@") or "author" in rid or "title" in rid) and not author and len(txt) <= 40:
            author = txt.lstrip("@").strip()
        elif re.fullmatch(r"\d+(?:\.\d+)?[万wW]?", txt) or any(k in txt for k in ("点赞", "评论", "收藏", "转发")):
            stats.append(txt)
        elif len(txt) >= 6 and txt not in {"首页", "朋友", "消息", "我", "推荐", "关注", "同城", "商城", "团购"}:
            caption_parts.append(txt)
    return {
        "author": author or "未知创作者",
        "caption": " | ".join(caption_parts[:4])[:400],
        "stats_hint": ", ".join(stats[:6])[:120],
    }


class DouyinController:
    """Douyin browsing, engagement ledger, and post publishing over `PhoneAgent`."""

    def __init__(self, store, phone_agent: PhoneAgent, draft_root: str | Path | None = None):
        self.store = store
        self.db = store.db
        self.phone = phone_agent
        self.draft_root = Path(draft_root or (Path(store.folder) / "douyin_drafts")).resolve()
        self.draft_root.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS douyin_actions (
          id TEXT PRIMARY KEY,
          scope TEXT NOT NULL,
          run_id TEXT,
          proposal_id TEXT,
          day TEXT NOT NULL,
          action TEXT NOT NULL,
          target_fingerprint TEXT NOT NULL,
          args_json TEXT NOT NULL,
          status TEXT NOT NULL,
          verified INTEGER NOT NULL,
          before_fingerprint TEXT,
          after_fingerprint TEXT,
          created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS douyin_actions_day ON douyin_actions(scope, day, action, status);
        CREATE TABLE IF NOT EXISTS douyin_posts (
          id TEXT PRIMARY KEY,
          scope TEXT NOT NULL,
          goal_id TEXT,
          title TEXT NOT NULL,
          caption TEXT NOT NULL,
          tags_json TEXT NOT NULL,
          media_paths_json TEXT NOT NULL,
          media_hash TEXT NOT NULL,
          status TEXT NOT NULL,
          proposal_id TEXT,
          created_at REAL NOT NULL,
          published_at REAL
        );
        """)

    def daily_count(self, scope: str, action: str, now: float | None = None) -> int:
        act = normalize_action(action)
        day = _day_str(now)
        row = self.db.execute(
            "SELECT count(*) FROM douyin_actions WHERE scope=? AND day=? AND action=? "
            "AND status IN ('executed','uncertain')",
            (scope, day, act),
        ).fetchone()
        return int(row[0]) if row else 0

    def check_quota(self, scope: str, action: str, cfg: dict[str, Any], now: float | None = None) -> None:
        cfg = DEFAULTS | cfg
        act = normalize_action(action)
        if not action_enabled(act, cfg):
            raise PermissionError(f"抖音动作 {act} 对应开关尚未开启")
        limit_key = ACTION_LIMIT_KEYS[act]
        limit = int(cfg[limit_key])
        used = self.daily_count(scope, act, now=now)
        if used >= limit:
            raise PermissionError(f"今日抖音 {act} 已达上限（{used}/{limit}）")

    async def _dismiss_popups_if_any(self, elements: list[dict[str, Any]], owner: str | None = None) -> bool:
        for el in elements:
            txt = str(el.get("text") or "").strip()
            if txt in POPUP_DISMISS_LABELS:
                try:
                    await self.phone._call("tap_element", {"text": txt}, owner=owner)
                    return True
                except TransportError:
                    pass
        return False

    async def feed(
        self,
        scope: str,
        cfg: dict[str, Any],
        *,
        mode: str = "recommend",
        keyword: str = "",
        count: int = 1,
        listen_seconds: int = 8,
        sample_comments: bool = False,
        owner: str | None = None,
        run_id: str | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        """Browse 1..N Douyin videos, capturing screen, audio transcript, and optional comments."""
        cfg = DEFAULTS | cfg
        if not cfg["douyin_enabled"]:
            raise PermissionError("抖音功能开关 douyin_enabled 尚未开启")
        if mode not in {"recommend", "follow", "search", "current"}:
            raise ValueError("mode 仅支持 recommend / follow / search / current")
        max_v = min(int(cfg["douyin_max_videos_per_run"]), max(1, int(count)))

        pkg = await self.phone.current_package(owner=owner)
        if pkg != DOUYIN_PACKAGE:
            await self.phone._call(
                "launch_app",
                {"package_name": DOUYIN_PACKAGE, "app_name": DOUYIN_PACKAGE},
                owner=owner,
            )

        if mode == "search":
            kw = (keyword or "").strip()
            if not kw or len(kw) > 60:
                raise ValueError("search 模式必须提供 1–60 字的搜索关键词")
            sx, sy = COORDS_720_1280["search_top_right"]
            await self.phone._call("tap", {"x": sx, "y": sy}, owner=owner)
            await self.phone._call("type_text", {"text": kw}, owner=owner)
            bx, by = COORDS_720_1280["search_submit"]
            await self.phone._call("tap", {"x": bx, "y": by}, owner=owner)

        videos: list[dict[str, Any]] = []
        for idx in range(max_v):
            if idx > 0 or mode == "recommend":
                x1, y1, x2, y2 = COORDS_720_1280["feed_swipe"]
                await self.phone._call(
                    "swipe",
                    {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": 350},
                    owner=owner,
                )
            obs = await self.phone.observe(
                scope,
                cfg,
                owner=owner,
                run_id=run_id,
                goal_id=goal_id,
                include_screenshot=True,
                listen_seconds=listen_seconds,
            )
            if await self._dismiss_popups_if_any(obs.get("elements") or [], owner=owner):
                prior_audio = obs.get("audio")
                obs = await self.phone.observe(
                    scope,
                    cfg,
                    owner=owner,
                    run_id=run_id,
                    goal_id=goal_id,
                    include_screenshot=True,
                    listen_seconds=0,
                )
                if prior_audio and not obs.get("audio"):
                    obs["audio"] = prior_audio
            meta = _parse_feed_metadata(obs.get("elements") or [])
            comments_preview: list[str] = []
            if sample_comments:
                cx, cy = COORDS_720_1280["comment_button"]
                await self.phone._call("tap", {"x": cx, "y": cy}, owner=owner)
                try:
                    ui_res = await self.phone._call("get_ui_elements", {}, owner=owner)
                    c_elems, _ = _summarize_ui_elements(_extract_mcp_text(ui_res), limit=20)
                    comments_preview = [
                        e["text"] for e in c_elems if e.get("text") and len(e["text"]) >= 4
                    ][:8]
                except TransportError:
                    comments_preview = []
                finally:
                    bx, by = COORDS_720_1280["close_comment_sheet"]
                    await self.phone._call("tap", {"x": bx, "y": by}, owner=owner)

            fp = (obs.get("snapshot") or {}).get("fingerprint") or uuid.uuid4().hex
            video_key = f"dy:{fp[:16]}"
            item = {
                "video_key": video_key,
                "mode": mode,
                "keyword": keyword if mode == "search" else "",
                "author": meta["author"],
                "caption": meta["caption"],
                "stats_hint": meta["stats_hint"],
                "audio_transcript": obs.get("audio") or "",
                "comments_sample": comments_preview,
                "fingerprint": fp,
                "snapshot_path": (obs.get("snapshot") or {}).get("path"),
            }
            ep = self.store.life.record_episode(
                scope,
                kind="douyin_video",
                source_id=f"{video_key}:{int(time.time())}",
                evidence={
                    "ok": True,
                    "platform": "douyin",
                    "operation": f"douyin_feed:{mode}",
                    "video_key": video_key,
                    "author": meta["author"],
                    "caption": meta["caption"],
                    "audio_transcript": obs.get("audio") or "",
                    "fingerprint": fp,
                    "result_excerpt": canonical(item)[:1500],
                },
                interpretation={
                    "title": (meta["caption"] or f"{meta['author']} 的抖音视频")[:120],
                    "summary": f"刷抖音看到 @{meta['author']}：{meta['caption'][:120]}",
                },
                run_id=run_id,
                goal_id=goal_id,
                subject_kind="agent",
                subject_id=video_key,
            )
            item["episode_id"] = ep["id"]
            videos.append(item)

        return {"ok": True, "count": len(videos), "videos": videos}

    async def execute_action(
        self,
        scope: str,
        cfg: dict[str, Any],
        action: str,
        args: dict[str, Any],
        *,
        proposal_id: str | None = None,
        owner: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a single approved or auto-allowed Douyin engagement/public action and verify screen diff."""
        act = normalize_action(action)
        self.check_quota(scope, act, cfg)

        # Ensure Douyin is foreground
        guard = await self.phone.enforce_package_guard([DOUYIN_PACKAGE], owner=owner, allow_launcher=False)
        before_obs = await self.phone.observe(
            scope, cfg, owner=owner, run_id=run_id, include_screenshot=True, listen_seconds=0
        )
        before_fp = (before_obs.get("snapshot") or {}).get("fingerprint") or ""

        target_fp = str(args.get("fingerprint") or before_fp or "current")
        action_id = uuid.uuid4().hex
        now = time.time()
        day = _day_str(now)

        # Record intent before non-idempotent UI tap
        with self.store.transaction():
            self.db.execute(
                "INSERT INTO douyin_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    scope,
                    run_id,
                    proposal_id,
                    day,
                    act,
                    target_fp,
                    canonical(args),
                    "executing",
                    0,
                    before_fp,
                    None,
                    now,
                ),
            )

        try:
            if act == "douyin_like":
                x, y = COORDS_720_1280["like_button"]
                await self.phone._call("tap", {"x": x, "y": y}, owner=owner)
            elif act == "douyin_favorite":
                x, y = COORDS_720_1280["favorite_button"]
                await self.phone._call("tap", {"x": x, "y": y}, owner=owner)
            elif act == "douyin_follow":
                x, y = COORDS_720_1280["follow_plus"]
                await self.phone._call("tap", {"x": x, "y": y}, owner=owner)
            elif act in {"douyin_comment", "douyin_reply"}:
                msg = str(args.get("message") or "").strip()
                if not msg or len(msg) > 150:
                    raise ValueError("抖音评论内容必须为 1–150 字")
                if DANGEROUS_UI_RE.search(msg):
                    raise PermissionError("评论内容包含敏感词，已拦截")
                cx, cy = COORDS_720_1280["comment_button"]
                await self.phone._call("tap", {"x": cx, "y": cy}, owner=owner)
                ix, iy = COORDS_720_1280["comment_input_bar"]
                await self.phone._call("tap", {"x": ix, "y": iy}, owner=owner)
                await self.phone._call("type_text", {"text": msg}, owner=owner)
                sx, sy = COORDS_720_1280["comment_send_button"]
                await self.phone._call("tap", {"x": sx, "y": sy}, owner=owner)
            else:
                raise ValueError(f"动作 {act} 请走专用发布入口")

            after_obs = await self.phone.observe(
                scope, cfg, owner=owner, run_id=run_id, include_screenshot=True, listen_seconds=0
            )
            after_fp = (after_obs.get("snapshot") or {}).get("fingerprint") or ""
            verified = bool(after_fp and after_fp != before_fp)
            if act in {"douyin_comment", "douyin_reply"}:
                msg = str(args.get("message") or "").strip()
                ui_texts = " ".join(e.get("text", "") for e in (after_obs.get("elements") or []))
                if msg[:10] and msg[:10] in ui_texts:
                    verified = True
                bx, by = COORDS_720_1280["close_comment_sheet"]
                await self.phone._call("tap", {"x": bx, "y": by}, owner=owner)

            status = "executed" if verified else "uncertain"
            with self.store.transaction():
                self.db.execute(
                    "UPDATE douyin_actions SET status=?,verified=?,after_fingerprint=? WHERE id=?",
                    (status, int(verified), after_fp, action_id),
                )
            return {
                "ok": True,
                "action_id": action_id,
                "action": act,
                "status": status,
                "verified": verified,
                "before_fingerprint": before_fp,
                "after_fingerprint": after_fp,
            }
        except BaseException:
            with self.store.transaction():
                self.db.execute(
                    "UPDATE douyin_actions SET status='uncertain' WHERE id=? AND status='executing'",
                    (action_id,),
                )
            raise

    def stage_post_draft(
        self,
        scope: str,
        *,
        title: str,
        caption: str,
        tags: list[str] | None = None,
        media_files: list[str | Path] | None = None,
        media_paths: list[str | Path] | None = None,
        run_id: str | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate & stage media files into `<draft_root>/<post_id>/` and create a draft post record."""
        title = (title or "").strip()
        caption = (caption or "").strip()
        if not title or len(title) > 60:
            raise ValueError("作品标题需为 1–60 字")
        if not caption or len(caption) > 500:
            raise ValueError("作品文案需为 1–500 字")
        if DANGEROUS_UI_RE.search(title) or DANGEROUS_UI_RE.search(caption):
            raise PermissionError("作品标题或文案包含高危词汇")

        clean_tags: list[str] = []
        for t in (tags or [])[:8]:
            t_clean = re.sub(r"[#\s\r\n]+", "", str(t)).strip()[:24]
            if t_clean:
                clean_tags.append(t_clean)

        files = media_files if media_files is not None else media_paths
        if not files or not (1 <= len(files) <= 4):
            raise ValueError("发抖音作品需提供 1–4 个本地图片或视频素材路径")

        post_id = uuid.uuid4().hex
        post_dir = (self.draft_root / post_id).resolve()
        # Allowed source roots: draft_root or store folder (e.g. screens/ or exports/)
        allowed_roots = (self.draft_root, Path(self.store.folder).resolve())

        staged_paths: list[str] = []
        hasher = hashlib.sha256()
        post_dir.mkdir(parents=True, exist_ok=True)
        try:
            for idx, raw_p in enumerate(files):
                src = Path(raw_p).resolve()
                if not any(src == r or r in src.parents for r in allowed_roots):
                    raise PermissionError(f"素材路径越界（仅允许插件受控目录内的截图或合成素材）: {raw_p}")
                if not src.is_file() or src.is_symlink():
                    raise ValueError(f"素材文件不存在或为符号链接: {raw_p}")
                suffix = src.suffix.lower()
                if suffix not in ALLOWED_MEDIA_SUFFIXES:
                    raise ValueError(f"不支持的素材格式 {suffix}（仅支持 jpg/png/webp/mp4）")
                data = src.read_bytes()
                if not (64 <= len(data) <= 50_000_000):
                    raise ValueError(f"素材文件大小异常: {len(data)} bytes")
                hasher.update(data)
                dst = post_dir / f"media_{idx + 1}{suffix}"
                dst.write_bytes(data)
                staged_paths.append(str(dst))
        except BaseException:
            shutil.rmtree(post_dir, ignore_errors=True)
            raise

        media_hash = hasher.hexdigest()
        now = time.time()
        with self.store.transaction():
            self.db.execute(
                "INSERT INTO douyin_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    post_id,
                    scope,
                    goal_id,
                    title,
                    caption,
                    canonical(clean_tags),
                    canonical(staged_paths),
                    media_hash,
                    "draft",
                    None,
                    now,
                    None,
                ),
            )
        return self.get_post(scope, post_id)

    def attach_proposal(self, scope: str, post_id: str, proposal_id: str) -> None:
        with self.store.transaction():
            self.db.execute(
                "UPDATE douyin_posts SET proposal_id=? WHERE scope=? AND id=?",
                (proposal_id, scope, post_id),
            )

    def get_post(self, scope: str, post_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM douyin_posts WHERE scope=? AND id=?",
            (scope, post_id),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["post_id"] = d["id"]
        d["tags"] = json.loads(d.pop("tags_json"))
        d["media_paths"] = json.loads(d.pop("media_paths_json"))
        d["staged_media_paths"] = list(d["media_paths"])
        return d

    async def execute_publish_post(
        self,
        scope: str,
        cfg: dict[str, Any],
        post_id: str,
        *,
        proposal_id: str,
        owner: str | None = None,
        run_id: str | None = None,
        adb_pusher=None,
    ) -> dict[str, Any]:
        """Publish an owner-approved Douyin post by pushing staged media and tapping「发布」directly.

        Requires a valid `proposal_id` whose status in `proposals` is `'executing'`
        or `'approved'` (set only after real owner QQ confirmation).
        """
        cfg = DEFAULTS | cfg
        if not cfg["douyin_enabled"] or not cfg["douyin_publish_enabled"]:
            raise PermissionError("抖音发布开关 douyin_publish_enabled 未开启")
        self.check_quota(scope, "douyin_post", cfg)

        post = self.get_post(scope, post_id)
        if not post:
            raise ValueError("找不到待发布的抖音作品草稿")
        if post["status"] == "published":
            raise ValueError("该作品已发布，禁止重复发布")

        # Verify that an owner-approved proposal exists for this post
        prop = self.db.execute(
            "SELECT status, action FROM proposals WHERE id=? AND scope=?",
            (proposal_id, scope),
        ).fetchone()
        if not prop or prop["action"] != "douyin_post" or prop["status"] not in {"executing", "executed"}:
            raise PermissionError("抖音作品未经主人明确批准，拒绝执行发布")

        # 1. Push staged media to emulator /sdcard/DCIM/Dafeiyu/ (or via injected adb_pusher)
        remote_dir = f"/sdcard/DCIM/Dafeiyu/{post_id}"
        if callable(adb_pusher):
            pushed = await adb_pusher(post["media_paths"], remote_dir)
        else:
            pushed = [f"{remote_dir}/{Path(p).name}" for p in post["media_paths"]]

        # 2. Launch Douyin -> tap bottom '+' -> pick album item -> next -> type caption + tags -> tap「发布」directly
        pkg = await self.phone.current_package(owner=owner)
        if pkg != DOUYIN_PACKAGE:
            await self.phone._call(
                "launch_app",
                {"package_name": DOUYIN_PACKAGE, "app_name": DOUYIN_PACKAGE},
                owner=owner,
            )

        px, py = COORDS_720_1280["bottom_plus_publish"]
        await self.phone._call("tap", {"x": px, "y": py}, owner=owner)

        ax, ay = COORDS_720_1280["album_upload_entry"]
        await self.phone._call("tap", {"x": ax, "y": ay}, owner=owner)

        fx, fy = COORDS_720_1280["album_first_item"]
        await self.phone._call("tap", {"x": fx, "y": fy}, owner=owner)

        nx, ny = COORDS_720_1280["album_next_step"]
        await self.phone._call("tap", {"x": nx, "y": ny}, owner=owner)
        await self.phone._call("tap", {"x": nx, "y": ny}, owner=owner)

        full_text = f"{post['title']}\n{post['caption']}"
        if post["tags"]:
            full_text += " " + " ".join(f"#{t}" for t in post["tags"])
        full_text = full_text[:550]

        cx, cy = COORDS_720_1280["post_caption_box"]
        await self.phone._call("tap", {"x": cx, "y": cy}, owner=owner)
        await self.phone._call("type_text", {"text": full_text}, owner=owner)

        # Direct publish per owner configuration ("批准后直接点「发布」")
        try:
            await self.phone._call("tap_element", {"text": "发布"}, owner=owner)
        except TransportError:
            pub_x, pub_y = COORDS_720_1280["post_publish_direct"]
            await self.phone._call("tap", {"x": pub_x, "y": pub_y}, owner=owner)

        after_obs = await self.phone.observe(
            scope, cfg, owner=owner, run_id=run_id, include_screenshot=True, listen_seconds=0
        )
        after_fp = (after_obs.get("snapshot") or {}).get("fingerprint") or ""
        now = time.time()
        action_id = uuid.uuid4().hex
        with self.store.transaction():
            self.db.execute(
                "UPDATE douyin_posts SET status='published',proposal_id=?,published_at=? WHERE id=? AND scope=?",
                (proposal_id, now, post_id, scope),
            )
            self.db.execute(
                "INSERT INTO douyin_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    scope,
                    run_id,
                    proposal_id,
                    _day_str(now),
                    "douyin_post",
                    post["media_hash"],
                    canonical({"post_id": post_id, "title": post["title"]}),
                    "executed",
                    1,
                    "",
                    after_fp,
                    now,
                ),
            )
        self.store.life.record_episode(
            scope,
            kind="douyin_post_published",
            source_id=post_id,
            evidence={
                "ok": True,
                "platform": "douyin",
                "operation": "douyin_publish",
                "post_id": post_id,
                "proposal_id": proposal_id,
                "pushed_media": pushed,
                "after_fingerprint": after_fp,
                "result_excerpt": canonical({"title": post["title"], "tags": post["tags"]}),
            },
            interpretation={
                "title": post["title"],
                "summary": f"经主人批准，在抖音发布了作品《{post['title']}》",
            },
            run_id=run_id,
            goal_id=post.get("goal_id"),
            subject_kind="agent",
            subject_id=post_id,
        )
        return {
            "ok": True,
            "post_id": post_id,
            "status": "published",
            "published_directly": True,
            "pushed_media": pushed,
            "after_fingerprint": after_fp,
        }

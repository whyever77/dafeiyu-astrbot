"""Loopback-only, no dependencies: python -B -m deploy.memory_observatory.server."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .demo import demo_snapshot
from .model import InvalidSnapshot, normalize, project
from .livingmemory_reader import SourceError
from .activity import ActivityError, demo_room, empty_room

STATIC = Path(__file__).parent / "static"
MAX_BODY = 4 * 1024 * 1024
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
ASSETS.update({"/room": ("room.html", "text/html; charset=utf-8"),
               "/room.js": ("room.js", "text/javascript; charset=utf-8"),
               "/room.css": ("room.css", "text/css; charset=utf-8")})


class ObservatoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address=("127.0.0.1", 8766), *, source=None, activity_source=None):
        if address[0] != "127.0.0.1":
            raise ValueError("Only 127.0.0.1 is supported")
        self.snapshot = demo_snapshot()
        self.demo = True
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.source, self.connection = source, None
        self.activity_source = activity_source
        self.activity_snapshot = None
        self.activity_state = "unconfigured"
        self.activity_refresh_lock = threading.Lock()
        if activity_source is not None:
            try:
                self.activity_snapshot = activity_source.load()
                self.activity_state = "ready"
            except ActivityError:
                self.activity_state = "unavailable"
        if source is not None:
            # Fail startup on first-read failure; never silently show fiction.
            self.snapshot, self.connection = source.load()
            self.demo = False
        super().__init__(address, Handler)

    def view(self, scope=None, **kwargs):
        scopes = {m["scope"] for m in self.snapshot["memories"]}
        if scope not in scopes:
            scope = self.snapshot["memories"][0]["scope"] if scopes else None
        view = project(self.snapshot, scope=scope, **kwargs)
        view["demo"] = self.demo
        if self.connection is not None:
            view["connection"] = self.connection.copy()
        return view

    def room_view(self):
        if self.activity_snapshot is not None:
            state = self.activity_state
            if time.time() - self.activity_snapshot["as_of"] > 180:
                state = "stale"
            return {**self.activity_snapshot, "connection": state}
        if self.activity_source is None and self.demo:
            return demo_room()
        return empty_room(self.activity_state)


class Handler(BaseHTTPRequestHandler):
    server: ObservatoryServer

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        # Request URLs, imported text and memory queries are not logged.
        pass

    def send(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in hosts or (origin and origin != f"http://{host}"):
            self.send(403, {"error": "仅允许本机同源请求"})
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        if path in ASSETS:
            filename, mime = ASSETS[path]
            self.send(200, (STATIC / filename).read_bytes(), mime)
        elif path == "/api/health":
            self.send(200, {"application": "dafeiyu-memory-observatory",
                            "mode": "livingmemory" if self.server.source is not None else "local"})
        elif path == "/api/snapshot":
            with self.server.lock:
                view = self.server.view()
            self.send(200, view)
        elif path == "/api/room":
            with self.server.lock:
                view = self.server.room_view()
            self.send(200, view)
        elif path == "/api/example":
            # Clean interchange data, never an export of someone's imported snapshot.
            demo = demo_snapshot()
            self.send(200, demo)
        else:
            self.send(404, {"error": "未找到资源"})

    def do_POST(self):
        if not self.local_request():
            return
        if self.headers.get("X-Observatory") != "local" or self.headers.get_content_type() != "application/json":
            self.send(403, {"error": "需要本机界面的 JSON 请求"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
                self.send(413, {"error": "文件不得超过 4 MB"})
                return
            body = json.loads(self.rfile.read(length), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite")))
            if not isinstance(body, dict):
                raise InvalidSnapshot("请求必须是 JSON 对象")
            path = urlsplit(self.path).path
            if self.server.source is not None and path in ("/api/import", "/api/reset"):
                self.send(409, {"error": "当前连接真实只读源；请在另一个端口启动演示或导入模式。"})
                return
            if path == "/api/room/refresh":
                if body:
                    self.send(400, {"error": "活动来源只能在服务启动时配置"})
                    return
                if self.server.activity_source is None:
                    self.send(409, {"error": "尚未配置活动账本只读源"})
                    return
                if not self.server.activity_refresh_lock.acquire(blocking=False):
                    self.send(409, {"error": "正在同步活动记录"})
                    return
                try:
                    snapshot = self.server.activity_source.load()
                    with self.server.lock:
                        self.server.activity_snapshot = snapshot
                        self.server.activity_state = "ready"
                        view = self.server.room_view()
                except ActivityError:
                    with self.server.lock:
                        self.server.activity_state = "stale" if self.server.activity_snapshot else "unavailable"
                    self.send(503, {"error": "活动同步失败；旧记录不能证明当前状态。"})
                    return
                finally:
                    self.server.activity_refresh_lock.release()
            elif path == "/api/import":
                incoming = normalize(body)  # Complete validation before replacing any state.
                with self.server.lock:
                    self.server.snapshot, self.server.demo = incoming, False
                    view = project(incoming, scope=incoming["memories"][0]["scope"])
                    view["demo"] = False
            elif path == "/api/reset":
                with self.server.lock:
                    self.server.snapshot, self.server.demo = demo_snapshot(), True
                    view = project(self.server.snapshot, scope=self.server.snapshot["memories"][0]["scope"])
                    view["demo"] = True
            elif path == "/api/project":
                with self.server.lock:
                    view = project(self.server.snapshot, scope=body.get("scope"), days=body.get("days", 0),
                                   query=body.get("query", ""), rehearsed=body.get("rehearsed", []), pinned=body.get("pinned", []))
                    view["demo"] = self.server.demo
                    if self.server.connection is not None:
                        view["connection"] = self.server.connection.copy()
            elif path == "/api/livingmemory/refresh":
                if self.server.source is None:
                    self.send(409, {"error": "此服务未配置 LivingMemory 只读源"})
                    return
                if not self.server.refresh_lock.acquire(blocking=False):
                    self.send(409, {"error": "正在读取，请等待当前同步完成"})
                    return
                try:
                    snapshot, info = self.server.source.load()
                    with self.server.lock:
                        self.server.snapshot, self.server.connection = snapshot, info
                        view = self.server.view(body.get("scope"))
                except SourceError:
                    with self.server.lock:
                        self.server.connection = {**self.server.connection, "state": "stale"}
                    self.send(503, {"error": "同步失败；仍显示上次快照，不代表当前在线状态。"})
                    return
                finally:
                    self.server.refresh_lock.release()
            else:
                self.send(404, {"error": "未找到接口"})
                return
            self.send(200, view)
        except InvalidSnapshot as exc:
            self.send(400, {"error": str(exc)})
        except (ValueError, UnicodeError, TypeError, OverflowError):
            self.send(400, {"error": "JSON 格式或字段类型无效"})


def main():
    parser = argparse.ArgumentParser(description="大肥鱼 · 本地记忆星海（演示或显式连接只读源）")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--livingmemory-ssh", action="store_true", help="使用现有 SSH 配置只读连接 LivingMemory")
    activity = parser.add_mutually_exclusive_group()
    activity.add_argument("--life-data-dir", type=Path, help="只读活动账本目录（control.json + activity.sqlite3）")
    activity.add_argument("--life-ssh", action="store_true", help="使用现有 SSH 配置只读连接固定活动账本")
    args = parser.parse_args()
    source = None
    activity_source = None
    if args.life_data_dir:
        from .activity import LocalActivitySource
        activity_source = LocalActivitySource(args.life_data_dir)
    elif args.life_ssh:
        from .activity import SSHActivitySource
        try:
            activity_source = SSHActivitySource()
        except SourceError as exc:
            parser.exit(1, str(exc) + "\n")
    if args.livingmemory_ssh:
        from .livingmemory import SSHMemorySource
        try:
            source = SSHMemorySource()
        except SourceError as exc:
            parser.exit(1, str(exc) + "\n")
    try:
        server = ObservatoryServer(("127.0.0.1", args.port), source=source, activity_source=activity_source)
    except SourceError as exc:
        parser.exit(1, str(exc) + "\n")
    with server:
        print(f"Memory observatory: http://127.0.0.1:{server.server_port} (local, in-memory only)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

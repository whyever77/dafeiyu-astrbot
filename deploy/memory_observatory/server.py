"""Loopback-only, no dependencies: python -B -m deploy.memory_observatory.server."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .demo import demo_snapshot
from .model import InvalidSnapshot, normalize, project

STATIC = Path(__file__).parent / "static"
MAX_BODY = 4 * 1024 * 1024
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


class ObservatoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address=("127.0.0.1", 8766)):
        if address[0] != "127.0.0.1":
            raise ValueError("Only 127.0.0.1 is supported")
        self.snapshot = demo_snapshot()
        self.demo = True
        self.lock = threading.Lock()
        super().__init__(address, Handler)


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
        elif path == "/api/snapshot":
            with self.server.lock:
                first = self.server.snapshot["memories"][0]["scope"]
                view = project(self.server.snapshot, scope=first)
                view["demo"] = self.server.demo
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
            if path == "/api/import":
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
            else:
                self.send(404, {"error": "未找到接口"})
                return
            self.send(200, view)
        except InvalidSnapshot as exc:
            self.send(400, {"error": str(exc)})
        except (ValueError, UnicodeError, TypeError, OverflowError):
            self.send(400, {"error": "JSON 格式或字段类型无效"})


def main():
    parser = argparse.ArgumentParser(description="大肥鱼 · 本地记忆星海（不连接真实记忆）")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    with ObservatoryServer(("127.0.0.1", args.port)) as server:
        print(f"Memory observatory: http://127.0.0.1:{server.server_port} (local, in-memory only)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

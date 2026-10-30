"""Windows desktop entry: reuse or start the local, real read-only observatory."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
import webbrowser

URL = "http://127.0.0.1:8767"
ROOT = Path(__file__).resolve().parents[2]


class LaunchError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(path, limit=65536):
    # Local checks never use an HTTP proxy or follow another service's redirect.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(URL + path, timeout=2) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response too large")
        return body


def probe():
    """Return real/other/absent without logging or persisting snapshot content."""
    try:
        try:
            health = json.loads(fetch("/api/health"))
            return "real" if (health.get("application") == "dafeiyu-memory-observatory"
                              and health.get("mode") == "livingmemory") else "other"
        except HTTPError as exc:
            exc.close()
            if exc.code != 404:
                return "other"
        # Compatibility for an already running v0.2 process. Do not restart it
        # or confuse its demo/imported snapshot with a real LivingMemory source.
        if "<title>记忆星海 · 大肥鱼</title>" not in fetch("/").decode("utf-8"):
            return "other"
        snapshot = json.loads(fetch("/api/snapshot", 4 * 1024 * 1024))
        connection = snapshot.get("connection") or {}
        return "real" if snapshot.get("demo") is False and connection.get("enabled") is True else "other"
    except URLError as exc:
        if isinstance(exc, HTTPError):
            exc.close()
        reason = exc.reason
        return "absent" if isinstance(reason, ConnectionRefusedError) or getattr(reason, "errno", None) in (61, 111, 10061) else "other"
    except (OSError, ValueError, AttributeError, TypeError):
        return "other"


@contextmanager
def startup_lock():
    """Double clicks may open tabs, but must not race to start two servers."""
    if os.name != "nt":
        raise LaunchError("此桌面启动器仅用于 Windows。")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\DafeiyuMemoryObservatory-8767")
    if not handle:
        raise LaunchError("无法创建启动锁，请稍后再试。")
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 75000) in (0, 0x80)
        if not acquired:
            raise LaunchError("记忆星海仍在启动，请稍后再试。")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def launch(life_ssh=False):
    state = probe()
    if state == "other":
        raise LaunchError("8767 端口已占用，或当前服务不是可识别的真实记忆星海。请检查现有服务；不会自动关闭它。")
    if state == "absent":
        flags = {"creationflags": 0x08000000} if os.name == "nt" else {}
        command = [sys.executable, "-B", "-m", "deploy.memory_observatory.server",
                   "--port", "8767", "--livingmemory-ssh"]
        if life_ssh:
            command.append("--life-ssh")
        child = subprocess.Popen(command, cwd=ROOT,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, **flags)
        ready = False
        try:
            deadline = time.monotonic() + 55
            while time.monotonic() < deadline:
                if probe() == "real":
                    ready = True
                    break
                if child.poll() is not None:
                    break
                time.sleep(0.4)
        finally:
            if not ready and child.poll() is None:
                child.terminate()  # Only the child started by this click.
                child.wait(timeout=5)
        if not ready:
            raise LaunchError("未能启动真实记忆星海。请检查网络、已有 SSH 配置和 LivingMemory 服务，然后重试。不会切换为演示数据。")
    if not webbrowser.open(URL, new=2):
        raise LaunchError("记忆星海已就绪，但无法打开默认浏览器。请手动访问 " + URL)


def main():
    parser = argparse.ArgumentParser(description="大肥鱼 · 本地记忆星海桌面入口")
    parser.add_argument("--life-ssh", action="store_true", help="同时只读连接活动账本")
    args = parser.parse_args()
    try:
        with startup_lock():
            launch(life_ssh=args.life_ssh)
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, LaunchError) else "启动遇到本机错误，请检查 Python 安装和记忆星海目录。"
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "记忆星海", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

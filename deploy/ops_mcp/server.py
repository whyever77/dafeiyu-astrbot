#!/usr/bin/env python3
"""dafeiyu-admin: stdlib-only MCP stdio server for the dafeiyu cloud host.

Read-only by default. Every mutating tool requires a literal confirmation
token, and all calls are appended to ~/.dafeiyu-admin/audit.jsonl.

No third-party dependencies: it shells out to the Windows OpenSSH client.
Credentials (WebUI token, API keys) are never returned by this server.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

def _resolve_host() -> str:
    """SSH target for the cloud host.

    Never hardcode the real address in a public repo: set
    DAFEIYU_SSH_HOST (or put it in ~/.dafeiyu-admin/host, one line).
    """
    host = os.environ.get("DAFEIYU_SSH_HOST", "").strip()
    if not host:
        cfg = Path.home() / ".dafeiyu-admin" / "host"
        if cfg.is_file():
            host = cfg.read_text(encoding="utf-8").strip()
    return host or "root@your-dafeiyu-host"


HOST = _resolve_host()
HOST_ADDR = HOST.rsplit("@", 1)[-1]
KEY = Path.home() / ".ssh" / "ecs_dafeiyu"
SSH_BIN = shutil.which("ssh") or r"C:\Windows\System32\OpenSSH\ssh.exe"
SSH_BASE = [
    SSH_BIN,
    "-i", str(KEY),
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes",
    HOST,
]
STATE_DIR = Path.home() / ".dafeiyu-admin"
AUDIT = STATE_DIR / "audit.jsonl"
QR_PATH = STATE_DIR / "qr.png"
ALLOWED_CONTAINERS = {"astrbot", "napcat"}
MAX_IMAGE = 1_500_000

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _audit(tool: str, args: dict, ok: bool, detail: str = "") -> None:
    """Append a compact audit record. Never store tokens or message bodies."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        safe_args = {k: v for k, v in args.items() if k not in {"token", "password", "api_key"}}
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "tool": tool,
                "args": safe_args,
                "ok": ok,
                "detail": detail[:500],
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _run_ssh(remote: str, *, input_text: str | None = None, timeout: int = 30) -> tuple[int, str, str]:
    cmd = SSH_BASE + [remote]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **kwargs,
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"ssh timeout after {timeout}s"
    except Exception as exc:
        return 1, "", f"ssh failed: {exc}"


def _run_remote_py(script: str, *, timeout: int = 30) -> tuple[int, str, str]:
    return _run_ssh("python3 -", input_text=script.replace("__DAFEIYU_HOST__", HOST_ADDR), timeout=timeout)


def _text(s: str, err: bool = False) -> dict:
    return {"content": [{"type": "text", "text": s}], "isError": err}


def _json(obj: dict, err: bool = False) -> dict:
    return _text(json.dumps(obj, ensure_ascii=False, indent=2), err)


def _redact(s: str) -> str:
    """Hide one-time login URLs and tokens that sometimes appear in logs."""
    s = re.sub(r"(https://txz\.qq\.com/p\?k=)[^&\s]+", r"\1<redacted>", s)
    s = re.sub(r"(?i)(token=)[^&\s]+", r"\1<redacted>", s)
    return s


HEALTH_PY = r'''
import json, re, subprocess

def sh(cmd, t=10):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
        return (p.stdout or "").strip()
    except Exception:
        return ""

containers = []
for line in sh("docker ps --format '{{.Names}}|{{.Status}}'").splitlines():
    if "|" in line:
        n, s = line.split("|", 1)
        containers.append({"name": n, "status": s})

napcat = sh("docker logs --since 3h napcat 2>&1 | tail -n 400")
astrbot = sh("docker logs --since 1h astrbot 2>&1 | tail -n 400")

def hits(text, pats, limit=8):
    out = []
    for line in text.splitlines():
        line = re.sub(chr(27) + r"\[[0-9;]*m", "", line).strip()
        if not line or re.fullmatch(r"[0-9a-f]{64}", line):
            continue
        if any(re.search(p, line, re.I) for p in pats):
            out.append(line[:300])
    return out[-limit:]

login_lines = hits(napcat, [
    r"二维码已被扫描", r"登录成功", r"Login Success", r"账号登录成功",
    r"KickedOffLine", r"登录已失效", r"身份已失效",
    r"请扫描下面的二维码", r"Login Error",
])
ws_lines = hits(astrbot, [r"aiocqhttp", r"适配器已连接", r"WebSocket", r"whitelist"])

ports = []
for line in sh("ss -ltnp 2>/dev/null | grep -E ':(6099|6185|8009|8010|6199)\\b'").splitlines():
    ports.append(line.strip()[:300])

print(json.dumps({
    "host": "__DAFEIYU_HOST__",
    "time": sh("date '+%Y-%m-%d %H:%M:%S %Z'"),
    "containers": containers,
    "napcat_login_lines": login_lines,
    "astrbot_ws_lines": ws_lines,
    "ports": ports,
    "disk": sh("df -h / | tail -1"),
    "mem": sh("free -m | head -2"),
}, ensure_ascii=False))
'''

CRON_PY = r'''
import json, sqlite3
con = sqlite3.connect("file:/opt/dafeiyu/astrbot/data/data_v4.db?mode=ro", uri=True)
cols = ["name", "cron_expression", "timezone", "enabled", "next_run_time"]
rows = [dict(zip(cols, r)) for r in con.execute(
    "select name, cron_expression, timezone, enabled, next_run_time from cron_jobs"
)]
print(json.dumps(rows, ensure_ascii=False))
'''

WEBUI_PY = r'''
import json
p = "/opt/dafeiyu/napcat/config/webui.json"
d = json.load(open(p, encoding="utf-8"))
port = d.get("port", 6099)
print(json.dumps({
    "napcat_webui_base": f"http://__DAFEIYU_HOST__:{port}/webui",
    "napcat_port": port,
    "astrbot_panel": "http://__DAFEIYU_HOST__:6185",
    "quick_account": os.environ.get("DAFEIYU_BOT_QQ", ""),
    "main_account": os.environ.get("DAFEIYU_OWNER_QQ", ""),
    "token_note": "token is not returned; read it on the host only when needed.",
}, ensure_ascii=False))
'''


def tool_health(_args: dict) -> dict:
    rc, out, err = _run_remote_py(HEALTH_PY, timeout=45)
    if rc != 0 or not out:
        return _json({"ok": False, "error": err or "empty output", "rc": rc}, True)
    try:
        return _json({"ok": True, **json.loads(out)})
    except Exception as exc:
        return _json({"ok": False, "error": f"bad json: {exc}", "raw": out[:1000]}, True)


def tool_logs(args: dict) -> dict:
    container = str(args.get("container", "")).strip()
    if container not in ALLOWED_CONTAINERS:
        return _json({"ok": False, "error": "container must be one of: astrbot, napcat"}, True)

    since = str(args.get("since", "30m")).strip()
    if not re.fullmatch(r"\d{1,3}[smhd]", since):
        return _json({"ok": False, "error": "since must look like 30m / 2h / 1d"}, True)

    try:
        tail = int(args.get("tail", 200))
    except Exception:
        tail = 200
    tail = max(1, min(tail, 1000))

    grep = str(args.get("grep", "")).strip()
    if grep and (len(grep) > 200 or any(c in grep for c in "\r\n\0")):
        return _json({"ok": False, "error": "grep must be a single line, <=200 chars"}, True)

    parts = [f"docker logs --since {since} {container} 2>&1"]
    if grep:
        parts.append(f"grep -aE -- {shlex.quote(grep)}")
    parts.append(f"tail -n {tail}")
    rc, out, err = _run_ssh(" | ".join(parts), timeout=45)
    if rc != 0 and not out:
        return _json({"ok": False, "error": err or "docker logs failed", "rc": rc}, True)
    out = re.sub(chr(27) + r"\[[0-9;]*m", "", out)
    out = _redact(out)
    lines = out.splitlines()
    return _json({
        "ok": True,
        "container": container,
        "since": since,
        "grep": grep,
        "lines": len(lines),
        "text": "\n".join(lines),
    })


def tool_qr(_args: dict) -> dict:
    remote = (
        "docker cp napcat:/app/napcat/cache/qrcode.png /tmp/qrcode-dafeiyu.png 2>/dev/null; "
        "ls -l /tmp/qrcode-dafeiyu.png 2>/dev/null; "
        "base64 /tmp/qrcode-dafeiyu.png 2>/dev/null"
    )
    rc, out, err = _run_ssh(remote, timeout=30)
    lines = [x for x in out.splitlines() if x.strip()]
    if not lines:
        return _json({"ok": False, "error": err or "no qrcode.png on host", "rc": rc}, True)
    meta = lines[0]
    b64 = "".join(x.strip() for x in lines[1:] if x.strip())
    if not b64:
        return _json({"ok": False, "error": "base64 empty", "meta": meta}, True)
    try:
        raw = base64.b64decode(b64)
    except Exception as exc:
        return _json({"ok": False, "error": f"bad base64: {exc}"}, True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    QR_PATH.write_bytes(raw)
    parts = [{
        "type": "text",
        "text": json.dumps({
            "ok": True,
            "path": str(QR_PATH),
            "size": len(raw),
            "meta": meta,
            "note": "二维码约 2 分钟过期；请用手机 QQ 扫描并确认。",
        }, ensure_ascii=False),
    }]
    if len(raw) <= MAX_IMAGE:
        parts.append({"type": "image", "mimeType": "image/png", "data": b64})
    return {"content": parts, "isError": False}


def tool_plugins(_args: dict) -> dict:
    rc, out, err = _run_ssh("ls -1 /opt/dafeiyu/astrbot/data/plugins 2>/dev/null", timeout=20)
    if rc != 0 and not out:
        return _json({"ok": False, "error": err or "ls failed", "rc": rc}, True)
    return _json({"ok": True, "plugins": [x for x in out.splitlines() if x.strip()]})


def tool_cron_jobs(_args: dict) -> dict:
    rc, out, err = _run_remote_py(CRON_PY, timeout=20)
    if rc != 0 or not out:
        return _json({"ok": False, "error": err or "empty output", "rc": rc}, True)
    try:
        return _json({"ok": True, "jobs": json.loads(out)})
    except Exception as exc:
        return _json({"ok": False, "error": f"bad json: {exc}", "raw": out[:1000]}, True)


def tool_webui_info(_args: dict) -> dict:
    rc, out, err = _run_remote_py(WEBUI_PY, timeout=20)
    if rc != 0 or not out:
        return _json({"ok": False, "error": err or "empty output", "rc": rc}, True)
    try:
        return _json({"ok": True, **json.loads(out)})
    except Exception as exc:
        return _json({"ok": False, "error": f"bad json: {exc}", "raw": out[:1000]}, True)


def tool_restart(args: dict) -> dict:
    container = str(args.get("container", "")).strip()
    if container not in ALLOWED_CONTAINERS:
        return _json({"ok": False, "error": "container must be one of: astrbot, napcat"}, True)
    if str(args.get("confirm", "")) != "RESTART":
        return _json({"ok": False, "error": 'confirm must be exactly "RESTART"'}, True)
    rc, out, err = _run_ssh(f"docker restart {container}", timeout=90)
    if rc != 0:
        return _json({"ok": False, "error": err or "restart failed", "rc": rc, "output": out}, True)
    time.sleep(3)
    _, ps, _ = _run_ssh("docker ps --format '{{.Names}}|{{.Status}}'", timeout=20)
    return _json({"ok": True, "restarted": container, "output": out, "containers": ps})


def tool_heal_ws(args: dict) -> dict:
    if str(args.get("confirm", "")) != "HEAL":
        return _json({"ok": False, "error": 'confirm must be exactly "HEAL"'}, True)
    rc, out, err = _run_ssh("python3 /opt/dafeiyu/heal_ws.py", timeout=120)
    return _json({"ok": rc == 0, "rc": rc, "output": out, "error": err}, rc != 0)


def tool_compose_up(args: dict) -> dict:
    if str(args.get("confirm", "")) != "UP":
        return _json({"ok": False, "error": 'confirm must be exactly "UP"'}, True)
    rc, out, err = _run_ssh("cd /opt/dafeiyu && docker compose up -d", timeout=180)
    if rc != 0:
        return _json({"ok": False, "error": err or "compose up failed", "rc": rc, "output": out}, True)
    time.sleep(3)
    _, ps, _ = _run_ssh("docker ps --format '{{.Names}}|{{.Status}}'", timeout=20)
    return _json({"ok": True, "output": out, "containers": ps})


TOOLS = [
    {
        "name": "health",
        "description": "One-shot read-only health report for the dafeiyu host: containers, NapCat login log lines, AstrBot WS lines, listening ports, disk and memory. Start every diagnosis with this.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "logs",
        "description": "Read docker logs for astrbot or napcat. Read-only. Args: container (astrbot|napcat), since (e.g. 30m, 2h, 1d), grep (optional extended regex), tail (1-1000).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "container": {"type": "string", "enum": ["astrbot", "napcat"]},
                "since": {"type": "string", "default": "30m"},
                "grep": {"type": "string"},
                "tail": {"type": "integer", "default": 200, "minimum": 1, "maximum": 1000},
            },
            "required": ["container"],
            "additionalProperties": False,
        },
    },
    {
        "name": "qr",
        "description": "Fetch the latest NapCat login QR code into ~/.dafeiyu-admin/qr.png and return it inline. Read-only. The QR expires in about 2 minutes; the user must scan it with the secondary QQ account.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "plugins",
        "description": "List installed AstrBot plugins under /opt/dafeiyu/astrbot/data/plugins. Read-only.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cron_jobs",
        "description": "List AstrBot cron_jobs (name, cron expression, timezone, enabled, next_run_time) via a read-only SQLite connection. Read-only.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "webui_info",
        "description": "Return the NapCat WebUI base URL (no token), AstrBot panel URL, and the two QQ account numbers. Read-only. The token is intentionally omitted; read it on the host only when needed.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "restart",
        "description": 'Restart one container. Mutating: confirm must be exactly "RESTART". Prefer this over rebuilding. After restart, wait about 4 minutes before judging QQ login.',
        "inputSchema": {
            "type": "object",
            "properties": {
                "container": {"type": "string", "enum": ["astrbot", "napcat"]},
                "confirm": {"type": "string", "description": 'literal "RESTART"'},
            },
            "required": ["container", "confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "heal_ws",
        "description": 'Run /opt/dafeiyu/heal_ws.py to reconnect the NapCat OneBot WebSocket. Mutating: confirm must be exactly "HEAL".',
        "inputSchema": {
            "type": "object",
            "properties": {"confirm": {"type": "string", "description": 'literal "HEAL"'}},
            "required": ["confirm"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compose_up",
        "description": 'Bring the compose stack up. Mutating: confirm must be exactly "UP". Use only when a container is missing, not for routine logins.',
        "inputSchema": {
            "type": "object",
            "properties": {"confirm": {"type": "string", "description": 'literal "UP"'}},
            "required": ["confirm"],
            "additionalProperties": False,
        },
    },
]

DISPATCH = {
    "health": tool_health,
    "logs": tool_logs,
    "qr": tool_qr,
    "plugins": tool_plugins,
    "cron_jobs": tool_cron_jobs,
    "webui_info": tool_webui_info,
    "restart": tool_restart,
    "heal_ws": tool_heal_ws,
    "compose_up": tool_compose_up,
}


def call_tool(name: str, args: dict) -> dict:
    fn = DISPATCH.get(name)
    if fn is None:
        return _text(f"unknown tool: {name}", True)
    t0 = time.time()
    try:
        result = fn(args)
        ok = not result.get("isError", False)
        _audit(name, args, ok, f"{time.time() - t0:.2f}s")
        return result
    except Exception as exc:
        _audit(name, args, False, str(exc))
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, True)


def write_msg(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(mid, result: dict) -> None:
    write_msg({"jsonrpc": "2.0", "id": mid, "result": result})


def reply_err(mid, code: int, message: str) -> None:
    write_msg({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        try:
            if method == "initialize":
                proto = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
                reply(mid, {
                    "protocolVersion": proto,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dafeiyu-admin", "version": "1.0.0"},
                })
            elif method == "notifications/initialized":
                pass
            elif method == "ping":
                reply(mid, {})
            elif method == "tools/list":
                reply(mid, {"tools": TOOLS})
            elif method == "tools/call":
                params = msg.get("params") or {}
                res = call_tool(str(params.get("name") or ""), params.get("arguments") or {})
                reply(mid, res)
            elif mid is not None:
                reply_err(mid, -32601, f"method not found: {method}")
        except Exception as exc:
            if mid is not None:
                reply_err(mid, -32603, f"internal error: {exc}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        for name in ("health", "plugins", "cron_jobs", "webui_info"):
            print(f"== {name} ==")
            print(DISPATCH[name]({})["content"][0]["text"][:3000])
        raise SystemExit(0)
    main()

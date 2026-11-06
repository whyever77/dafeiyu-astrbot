#!/usr/bin/env bash
# Manage only the companion's Docker-gateway proxy, never AstrBot/NapCat or cron.
set -euo pipefail
action="${1:-status}"
gateway="${2:-172.18.0.1}"
script_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 - "$script_root" "$action" "$gateway" <<'PY'
import ipaddress
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root, action, gateway = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
address = ipaddress.ip_address(gateway)
if not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
    raise SystemExit("Use the actual private Docker gateway address")
if action not in {"start", "stop", "status", "logs"}:
    raise SystemExit("Usage: cloud_service.sh start|stop|status|logs [docker-gateway]")
os.umask(0o077)
state = root / "logs"
state.mkdir(exist_ok=True)
operation_lock = (state / ".proxy.lock").open("a")
fcntl.flock(operation_lock, fcntl.LOCK_EX)
pid_file = state / "proxy.pid.json"
log_file = state / "proxy.log"
command = [sys.executable, "-u", str(root / "proxy.py"), "--listen-host", gateway, "--port", "8011",
           "--allow-container", "astrbot"]

def owned_process():
    if not pid_file.exists():
        return None
    saved = json.loads(pid_file.read_text())
    pid = int(saved["pid"])
    try:
        actual = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except FileNotFoundError:
        return None
    expected = [part.encode() for part in saved["command"]]
    if actual[:-1] != expected or saved["command"][2] != str(root / "proxy.py"):
        raise SystemExit("PID belongs to a different process; no process was changed")
    return pid

pid = owned_process()
if action == "logs":
    print(log_file.read_text(errors="replace")[-32000:] if log_file.exists() else "No proxy log yet")
elif action == "status":
    print(f"Proxy process {'running pid=' + str(pid) if pid else 'not running'}; MCP and QQ readiness require separate checks")
elif action == "stop":
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            try:
                alive = Path(f"/proc/{pid}/cmdline").read_bytes()
            except FileNotFoundError:
                alive = b""
            if not alive:
                break
            time.sleep(0.1)
        else:
            raise SystemExit("Proxy has not stopped; PID record retained for inspection")
    pid_file.unlink(missing_ok=True)
    print("Companion proxy stopped; AstrBot, NapCat and PhoneMCP 8009 were not changed")
elif action == "start":
    if pid:
        print(f"Companion proxy already running pid={pid}")
    else:
        with log_file.open("ab") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        pid_file.write_text(json.dumps({"pid": process.pid, "command": command}))
        time.sleep(0.3)
        if process.poll() is not None:
            pid_file.unlink(missing_ok=True)
            raise SystemExit(f"Proxy exited; inspect {log_file}")
        print(f"Companion proxy started pid={process.pid}; inspect MCP readiness from AstrBot")
PY

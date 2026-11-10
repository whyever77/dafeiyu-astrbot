# -*- coding: utf-8 -*-
"""heal_ws 回归：连接判定不看时间窗、只动 AstrBot 连接、保留 enable 状态。

跑法（纯标准库，不需要 Docker / 服务器）：
    python tests/test_heal_ws.py
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "deploy", "heal_ws.py")

failures = []


def check(label, cond, detail=None):
    if cond:
        print("OK  ", label)
    else:
        failures.append(label)
        print("FAIL", label, "" if detail is None else detail)


def load():
    spec = importlib.util.spec_from_file_location("heal_ws_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = load()
real_napcat_call = mod.napcat_call  # 后面的用例会 monkeypatch，这里留一份原件
real_login = mod._login  # 后面的用例会 monkeypatch，这里留一份原件


class _Out:
    def __init__(self, rc, data=b""):
        self.returncode = rc
        self.stdout = data


# ---------- 1. /proc/net/tcp 解析 ----------
EST = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 3500007F:0035 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1
   1: 0200000A:1837 0100000A:9C40 01 00000000:00000000 00:00000000 00000000     0        0 23456 1
"""
check("proc: ESTABLISHED on 6199 -> connected", mod._established_from_proc(EST))

LISTEN_ONLY = """  sl  local_address rem_address   st
   0: 0200000A:1837 00000000:0000 0A 00000000:00000000 00:00000000 00000000
"""
check("proc: LISTEN-only is not connected", not mod._established_from_proc(LISTEN_ONLY))

OTHER_PORT = """  sl  local_address rem_address   st
   1: 0200000A:0BB8 0100000A:9C40 01 00000000:00000000 00:00000000 00000000
"""
check("proc: other port ignored", not mod._established_from_proc(OTHER_PORT))

V6 = """  sl  local_address rem_address   st
   1: 0000000000000000FFFF00000200000A:1837 0100000000000000FFFF00000100000A:9C40 01 00000000:00000000
"""
check("proc: ipv6 ESTABLISHED on 6199", mod._established_from_proc(V6))

# ---------- 2. socket 探测的三种结果 ----------
real_subprocess = mod.subprocess
try:
    mod.subprocess = types.SimpleNamespace(run=lambda *a, **k: _Out(0, EST.encode()))
    check("socket: established -> True", mod.connected_from_socket() is True)

    mod.subprocess = types.SimpleNamespace(run=lambda *a, **k: _Out(1))
    check("socket: exec failed -> None", mod.connected_from_socket() is None)
finally:
    mod.subprocess = real_subprocess

# ---------- 3. 日志兜底按先后顺序判断 ----------
def logs(text):
    mod.subprocess = types.SimpleNamespace(
        run=lambda *a, **k: _Out(0, text.encode("utf-8"))
    )
    try:
        return mod.connected_from_logs()
    finally:
        mod.subprocess = real_subprocess


check(
    "logs: connect then disconnect -> False (old bug)",
    not logs("适配器已连接\n其他日志\n适配器已断开\n"),
)
check("logs: disconnect then connect -> True", logs("适配器已断开\n适配器已连接\n"))
check("logs: no marker -> False", not logs("启动完成\n"))

# ---------- 4. 目标连接选择 ----------
check("target: ws://astrbot:6199/ws", mod.is_astrbot_client({"url": "ws://astrbot:6199/ws"}))
check("target: other port ignored", not mod.is_astrbot_client({"url": "ws://example:3001"}))
check("target: missing url ignored", not mod.is_astrbot_client({}))

# ---------- 5. main：只动目标、保留 enable、成功返回 0 ----------
CFG = {
    "network": {
        "websocketClients": [
            {"name": "astrbot", "enable": True, "url": "ws://astrbot:6199/ws"},
            {"name": "other", "enable": False, "url": "ws://example:3001"},
            {"name": "astrbot-disabled", "enable": False, "url": "ws://astrbot:6199/ws"},
        ]
    }
}


def run_main(conn_sequence, cfg=None):
    calls = []

    def fake_call(opener, cred, path, body=None):
        calls.append((path, body))
        if path.endswith("GetConfig"):
            return {"data": copy.deepcopy(cfg if cfg is not None else CFG)}
        return {"data": {}}

    seq = list(conn_sequence)

    def fake_conn():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    mod.napcat_call = fake_call
    mod._login = lambda: (None, "cred")
    mod.adapter_connected = fake_conn
    real_time = mod.time
    mod.time = types.SimpleNamespace(sleep=lambda s: None)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = mod.main()
    finally:
        mod.time = real_time
    return rc, calls


def enables(call):
    cfg = json.loads(call[1]["config"])
    return {c["name"]: c["enable"] for c in cfg["network"]["websocketClients"]}


rc, calls = run_main([False, True])
sets = [c for c in calls if c[0].endswith("SetConfig")]
check("main: success returns 0", rc == 0, rc)
check("main: one GetConfig first", calls[0][0].endswith("GetConfig"))
check("main: exactly two SetConfig", len(sets) == 2, len(sets))
check(
    "main: only enabled target toggled off",
    enables(sets[0])
    == {"astrbot": False, "other": False, "astrbot-disabled": False},
    enables(sets[0]),
)
check(
    "main: enables restored, others untouched",
    enables(sets[1])
    == {"astrbot": True, "other": False, "astrbot-disabled": False},
    enables(sets[1]),
)

# ---------- 6. main：30 秒没重连 -> 1，且配置已还原 ----------
rc, calls = run_main([False])
sets = [c for c in calls if c[0].endswith("SetConfig")]
check("main: unreconnected returns 1", rc == 1, rc)
check(
    "main: enables still restored on failure",
    enables(sets[-1])
    == {"astrbot": True, "other": False, "astrbot-disabled": False},
    enables(sets[-1]),
)

# ---------- 7. main：没有目标 / 目标本来就关闭 ----------
NO_TARGET = {"network": {"websocketClients": [{"name": "x", "enable": True, "url": "ws://x:3001"}]}}
rc, calls = run_main([False], NO_TARGET)
check("main: no target returns 1", rc == 1, rc)
check("main: no target writes nothing", not any(c[0].endswith("SetConfig") for c in calls))

OFF_TARGET = {
    "network": {
        "websocketClients": [{"name": "astrbot", "enable": False, "url": "ws://astrbot:6199/ws"}]
    }
}
rc, calls = run_main([False], OFF_TARGET)
check("main: disabled target returns 1", rc == 1, rc)
check("main: disabled target not enabled", not any(c[0].endswith("SetConfig") for c in calls))

# ---------- 8. 路径 / 容器 / WebUI 地址自动探测（macOS 套件与云端都靠这套） ----------
import tempfile
from pathlib import Path

kit = Path(tempfile.mkdtemp())
(kit / "napcat" / "config").mkdir(parents=True)
(kit / "napcat" / "config" / "webui.json").write_text('{"token": "t"}', encoding="utf-8")
(kit / ".env").write_text(
    "# 注释行\nNAPCAT_PORT=6100  # 面板\nNAPCAT_PORT_TAB=6101\t# 面板\nQQ_ACCOUNT=\"123\"\nASTRBOT_PORT=6186\n",
    encoding="utf-8",
)

check("env: 未加引号去行尾注释", mod._env_file_value(str(kit / ".env"), "NAPCAT_PORT") == "6100")
check("env: tab 后行尾注释", mod._env_file_value(str(kit / ".env"), "NAPCAT_PORT_TAB") == "6101")
check("env: 带引号去引号", mod._env_file_value(str(kit / ".env"), "QQ_ACCOUNT") == "123")
check("env: 注释里的 key 不算", mod._env_file_value(str(kit / ".env"), "注释行") is None)
check("env: 缺 key 返回 None", mod._env_file_value(str(kit / ".env"), "NOPE") is None)
check("env: 文件不存在返回 None", mod._env_file_value(str(kit / "nope.env"), "X") is None)

real_here = mod.HERE
real_cwd = os.getcwd()
try:
    mod.HERE = str(kit)
    os.chdir(str(kit))
    check("config: 套件布局自动找到",
          mod.config_path() == str(kit / "napcat" / "config" / "webui.json"), mod.config_path())
    check("kit_dir: 套件根目录", mod.kit_dir() == str(kit), mod.kit_dir())
    check("base: 读 .env 的 NAPCAT_PORT", mod.napcat_base() == "http://127.0.0.1:6100", mod.napcat_base())

    os.environ["DAFEIYU_NAPCAT_CONFIG"] = "/tmp/elsewhere/webui.json"
    check("config: 环境变量优先", mod.config_path() == "/tmp/elsewhere/webui.json", mod.config_path())
    os.environ["DAFEIYU_NAPCAT_BASE"] = "http://127.0.0.1:7000/"
    check("base: 环境变量优先且去尾斜杠", mod.napcat_base() == "http://127.0.0.1:7000", mod.napcat_base())
    os.environ["DAFEIYU_CONTAINER"] = "dafeiyu-macos-astrbot-1"
    check("container: 环境变量优先", mod.container_name() == "dafeiyu-macos-astrbot-1")
finally:
    mod.HERE = real_here
    os.chdir(real_cwd)
    for key in ("DAFEIYU_NAPCAT_CONFIG", "DAFEIYU_NAPCAT_BASE", "DAFEIYU_CONTAINER"):
        os.environ.pop(key, None)

# 容器探测：docker ps 返回 compose 项目前缀的容器名时要用它，失败才回退 astrbot
real_subprocess2 = mod.subprocess
try:
    mod.subprocess = types.SimpleNamespace(
        run=lambda *a, **k: _Out(0, b"dafeiyu-macos-astrbot-1\n"))
    check("container: 按 compose label 找到项目前缀容器",
          mod.container_name() == "dafeiyu-macos-astrbot-1", mod.container_name())
    mod.subprocess = types.SimpleNamespace(run=lambda *a, **k: _Out(1, b""))
    check("container: docker ps 失败回退 astrbot", mod.container_name() == "astrbot", mod.container_name())
finally:
    mod.subprocess = real_subprocess2

# ---------- 9. WebUI 未登录 / 非 0 code 时友好退出，不抛 KeyError ----------
class _FakeOpener:
    def open(self, req, timeout=0):
        return io.BytesIO(json.dumps({"code": -1, "message": "Not Login"}).encode())


class _ListOpener:
    def open(self, req, timeout=0):
        return io.BytesIO(b"[]")


try:
    real_napcat_call(_FakeOpener(), "cred", "/api/OB11Config/GetConfig")
    check("napcat_call: code!=0 抛 NapCatError", False)
except mod.NapCatError:
    check("napcat_call: code!=0 抛 NapCatError", True)


def run_main_error(exc):
    mod.napcat_call = lambda *a, **k: (_ for _ in ()).throw(exc)
    mod._login = lambda: (None, "cred")
    mod.adapter_connected = lambda: False
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = mod.main()
    return rc, out.getvalue()


rc, text = run_main_error(mod.NapCatError("/api/OB11Config/GetConfig -> Not Login"))
check("main: Not Login -> 1（不再 KeyError）", rc == 1, rc)
check("main: 提示先登录 QQ", "先登录" in text, text.strip()[:80])

try:
    real_napcat_call(_ListOpener(), "cred", "/api/OB11Config/GetConfig")
    check("napcat_call: 非 dict 响应抛 NapCatError", False)
except mod.NapCatError:
    check("napcat_call: 非 dict 响应抛 NapCatError", True)


real_build_opener = mod.urllib.request.build_opener
real_config_path = mod.config_path
try:
    mod.config_path = lambda: str(kit / "napcat" / "config" / "webui.json")
    mod.urllib.request.build_opener = lambda: _ListOpener()
    try:
        real_login()
        check("login: 非 dict 响应抛 NapCatError", False)
    except mod.NapCatError:
        check("login: 非 dict 响应抛 NapCatError", True)
finally:
    mod.urllib.request.build_opener = real_build_opener
    mod.config_path = real_config_path


rc, text = run_main_error(OSError("connection refused"))
check("main: WebUI 连接错误 -> 1（不裸栈）", rc == 1, rc)
check("main: 连接错误提示", "连不上" in text, text.strip()[:120])


def run_main_setconfig_error(exc):
    calls = []

    def fake_call(opener, cred, path, body=None):
        calls.append((path, body))
        if path.endswith("GetConfig"):
            return {"data": copy.deepcopy(CFG)}
        if path.endswith("SetConfig"):
            raise exc
        return {"data": {}}

    mod.napcat_call = fake_call
    mod._login = lambda: (None, "cred")
    mod.adapter_connected = lambda: False
    real_time = mod.time
    mod.time = types.SimpleNamespace(sleep=lambda s: None)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = mod.main()
    finally:
        mod.time = real_time
    return rc, calls, out.getvalue()


rc, calls, text = run_main_setconfig_error(mod.NapCatError("SetConfig -> boom"))
sets = [c for c in calls if c[0].endswith("SetConfig")]
check("main: SetConfig 报错 -> 1（不裸栈）", rc == 1, rc)
check("main: SetConfig 报错会尝试恢复", len(sets) == 2, len(sets))
check("main: SetConfig 报错提示", "操作失败" in text, text.strip()[:120])


def run_main_missing_data():
    def fake_call(opener, cred, path, body=None):
        if path.endswith("GetConfig"):
            return {"code": 0}
        return {"data": {}}

    mod.napcat_call = fake_call
    mod._login = lambda: (None, "cred")
    mod.adapter_connected = lambda: False
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = mod.main()
    return rc, out.getvalue()


rc, text = run_main_missing_data()
check("main: GetConfig 缺 data -> 1（不裸 KeyError）", rc == 1, rc)
check("main: GetConfig 缺 data 提示", "config 数据" in text, text.strip()[:120])

print()
if failures:
    print("结果：%d 项失败" % len(failures))
    sys.exit(1)
print("结果：全部通过 OK")

# -*- coding: utf-8 -*-
"""云端 NapCat <-> AstrBot WS 自愈（Docker 容器版）。

原理同本地 heal-connection.py：通过 NapCat WebUI API 把指向 AstrBot 的
websocket 客户端"关一下再开"，不重启 QQ、不需要重新扫码。

连接判定优先看 astrbot 容器内 6199 端口有没有 ESTABLISHED 连接；拿不到
（容器没起、没有 cat）时退回日志里最后一条连接/断开记录。不再用"最近 3
分钟日志里出现过已连接"这种时间窗判断——那样连上又断开会被当成正常，
连了 3 分钟没新日志又会被当成异常。

修复只动指向 AstrBot(6199) 的客户端，其余连接和各自的 enable 状态原样保留。
退出码：连接正常或修复成功 = 0；没修好、配置里没有目标 = 1。

用法：
    云端（/opt/dafeiyu）        python3 /opt/dafeiyu/heal_ws.py
    macOS 本机（deploy-macos）  python3 ../deploy/heal_ws.py   # 在 deploy-macos/ 里跑

不写死路径/容器名，按下面的顺序自动找（都可用环境变量覆盖）：

    配置    DAFEIYU_NAPCAT_CONFIG > <脚本目录>/napcat/config/webui.json
            > <脚本目录>/../deploy-macos/napcat/config/webui.json
            > /opt/dafeiyu/napcat/config/webui.json > ./napcat/config/webui.json
    容器    DAFEIYU_CONTAINER > `docker ps` 里 label com.docker.compose.service=astrbot
            的容器（deploy/ 的 astrbot 与 deploy-macos/ 的 dafeiyu-macos-astrbot-1 都能认）
    WebUI   DAFEIYU_NAPCAT_BASE > 读配置所在套件的 .env 里 NAPCAT_PORT
            > 默认 http://127.0.0.1:6099
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS_PORT = 6199
LOG_LINES = 500
DEFAULT_NAPCAT_PORT = 6099

# 兼容旧用法（外部脚本可能直接读这几个常量）；函数里优先走下面的自动探测。
BASE = "http://127.0.0.1:%d" % DEFAULT_NAPCAT_PORT
CONFIG = "/opt/dafeiyu/napcat/config/webui.json"
CONTAINER = "astrbot"


class NapCatError(RuntimeError):
    """NapCat WebUI 返回了非 0 code（未登录、配置读失败等）。"""


def _env_file_value(path, key):
    """读 .env 里的简单 KEY=VALUE（去 CRLF、去成对引号、未加引号的去行尾注释）。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
        for marker in (" #", "\t#"):
            value = value.split(marker, 1)[0]
        return value.strip()
    return None


def config_path():
    """NapCat webui.json 的位置（含 token，用来登录 WebUI）。"""
    env = os.environ.get("DAFEIYU_NAPCAT_CONFIG")
    if env:
        return env
    candidates = [
        os.path.join(HERE, "napcat", "config", "webui.json"),
        os.path.join(HERE, "..", "deploy-macos", "napcat", "config", "webui.json"),
        "/opt/dafeiyu/napcat/config/webui.json",
        os.path.join(os.getcwd(), "napcat", "config", "webui.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return os.path.normpath(candidates[0])


def kit_dir():
    """配置所在套件的根目录（<kit>/napcat/config/webui.json 往上三层）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(config_path()))))


def container_name():
    """astrbot 容器名：compose 项目名不同，按 label 查比写死稳。"""
    env = os.environ.get("DAFEIYU_CONTAINER")
    if env:
        return env
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", "label=com.docker.compose.service=astrbot",
             "--format", "{{.Names}}"],
            capture_output=True,
            timeout=10,
        )
        names = out.stdout.decode("utf-8", "ignore").split()
        if out.returncode == 0 and names:
            return names[0]
    except Exception:
        pass
    return CONTAINER


def napcat_base():
    """NapCat WebUI 地址（端口跟着套件 .env 的 NAPCAT_PORT 走）。"""
    env = os.environ.get("DAFEIYU_NAPCAT_BASE")
    if env:
        return env.rstrip("/")
    port = _env_file_value(os.path.join(kit_dir(), ".env"), "NAPCAT_PORT") or str(DEFAULT_NAPCAT_PORT)
    return "http://127.0.0.1:%s" % port


def _established_from_proc(text, port=WS_PORT):
    """在 /proc/net/tcp{,6} 的输出里找 local port 为 port 的 ESTABLISHED 连接。

    每行格式：sl local_address rem_address st ...
    st = 01 是 ESTABLISHED，0A 是 LISTEN。
    """
    hex_port = "%04X" % port
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0] == "sl":
            continue
        if fields[3] == "01" and fields[1].rsplit(":", 1)[-1] == hex_port:
            return True
    return False


def connected_from_socket():
    """True/False = 容器内连接状态；None = 探测不可用（容器没起、没有 cat 等）。"""
    try:
        out = subprocess.run(
            ["docker", "exec", container_name(), "cat", "/proc/net/tcp", "/proc/net/tcp6"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return _established_from_proc(out.stdout.decode("utf-8", "ignore"))


def connected_from_logs():
    """兜底：按日志里最后一条连接/断开记录判断，不看时间窗。"""
    try:
        out = subprocess.run(
            ["docker", "logs", "--tail", str(LOG_LINES), container_name()],
            capture_output=True,
            timeout=15,
        ).stdout.decode("utf-8", "ignore")
    except Exception:
        return False
    last_connected = -1
    last_broken = -1
    for i, line in enumerate(out.splitlines()):
        if "适配器已连接" in line:
            last_connected = i
        if any(m in line for m in ("适配器已断开", "适配器断开", "连接已断开")):
            last_broken = i
    return last_connected > last_broken


def adapter_connected():
    state = connected_from_socket()
    if state is not None:
        return state
    return connected_from_logs()


def is_astrbot_client(client):
    """配置里的客户端是不是指向 AstrBot 的 WS 端口。"""
    return str(WS_PORT) in str(client.get("url", ""))


def _login():
    tok = json.load(open(config_path(), encoding="utf-8")).get("token", "")
    h = hashlib.sha256((tok + ".napcat").encode()).hexdigest()
    opener = urllib.request.build_opener()
    req = urllib.request.Request(
        napcat_base() + "/api/auth/login",
        data=json.dumps({"hash": h}).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = json.load(opener.open(req, timeout=10))
    if not isinstance(resp, dict):
        raise NapCatError("登录 WebUI 返回格式异常：%.200r" % (resp,))
    cred = (resp.get("data") or {}).get("Credential")
    if not cred:
        msg = resp.get("message") or "没有 Credential（code=%s）" % resp.get("code")
        raise NapCatError("登录 WebUI 失败：%s" % msg)
    return opener, cred


def napcat_call(opener, cred, path, body=None):
    req = urllib.request.Request(
        napcat_base() + path,
        data=json.dumps(body or {}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cred,
        },
    )
    resp = json.load(opener.open(req, timeout=15))
    if not isinstance(resp, dict):
        raise NapCatError("%s -> 返回格式异常：%.200r" % (path, resp))
    if resp.get("code", 0) != 0:
        raise NapCatError("%s -> %s" % (path, resp.get("message", "未知错误")))
    return resp


def _set_config(opener, cred, cfg):
    napcat_call(
        opener,
        cred,
        "/api/OB11Config/SetConfig",
        {"config": json.dumps(cfg, ensure_ascii=False)},
    )


def main():
    if adapter_connected():
        print("连接正常，无需修复")
        return 0

    try:
        opener, cred = _login()
        resp = napcat_call(opener, cred, "/api/OB11Config/GetConfig")
        cfg = resp.get("data")
        if not isinstance(cfg, dict):
            raise NapCatError("GetConfig 返回里没有 config 数据")
    except FileNotFoundError:
        print("找不到 NapCat 配置：%s（可用 DAFEIYU_NAPCAT_CONFIG 指定）" % config_path())
        return 1
    except NapCatError as ex:
        print("NapCat WebUI 返回错误：%s" % ex)
        if "Not Login" in str(ex):
            print("QQ 还没登录（NapCat 里扫码后才会建立 WS），先登录再跑本脚本")
        return 1
    except (json.JSONDecodeError, UnicodeDecodeError) as ex:
        print("NapCat 配置或 WebUI 响应不是合法 JSON：%s（%s）" % (config_path(), ex))
        return 1
    except OSError as ex:
        print("连不上 NapCat WebUI 或无法读取配置：%s（容器没起 / NAPCAT_PORT 不对？）" % ex)
        return 1
    clients = cfg.get("network", {}).get("websocketClients") or []
    targets = [c for c in clients if is_astrbot_client(c)]
    if not targets:
        print("配置里没有指向 AstrBot(:%d) 的 websocket 客户端" % WS_PORT)
        return 1
    active = [c for c in targets if c.get("enable", True)]
    if not active:
        print("指向 AstrBot 的连接在配置里是关闭的（%d 条），未做修改" % len(targets))
        return 1

    saved = [c.get("enable", True) for c in active]

    def restore_enabled():
        for c, was in zip(active, saved):
            c["enable"] = was

    try:
        for c in active:
            c["enable"] = False
        _set_config(opener, cred, cfg)
        print("已断开旧连接（%d 条，其他 %d 条不动）..." % (len(active), len(clients) - len(active)))
        time.sleep(2)
    except (NapCatError, OSError) as ex:
        print("NapCat WebUI 操作失败：%s" % ex)
        restore_enabled()
        try:
            _set_config(opener, cred, cfg)
            print("已尝试恢复原 enable 状态")
        except (NapCatError, OSError) as restore_ex:
            print("恢复原 enable 状态也失败了：%s（请到 NapCat WebUI 手动检查）" % restore_ex)
        return 1

    restore_enabled()
    try:
        _set_config(opener, cred, cfg)
    except (NapCatError, OSError) as ex:
        print("NapCat WebUI 操作失败：%s（enable 已恢复为原值，但可能没写回 NapCat；请到 WebUI 检查）" % ex)
        return 1
    print("已重新连线，等待重连...")
    for i in range(15):
        time.sleep(2)
        if adapter_connected():
            print("修复成功（%d 秒重连）" % (i * 2 + 2))
            return 0
    print("30 秒内未重连成功")
    return 1


if __name__ == "__main__":
    sys.exit(main())

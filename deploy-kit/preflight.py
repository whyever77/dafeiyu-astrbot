#!/usr/bin/env python3
"""大肥鱼部署前置检查（preflight）

用法：
    python preflight.py                # 只检查，不改动任何东西
    python preflight.py --env .env     # 顺带检查 .env 里的密钥

检查项：
  1. Python 版本（插件要求 >= 3.10）
  2. git / ffmpeg 是否可用
  3. 关键端口是否空闲（6199 WS / 6185 面板 / 6099 NapCat WebUI）
  4. 可选依赖包是否装好（dashscope / imageio-ffmpeg / aiohttp）
  5. .env 里的密钥是否齐全（缺失只警告，不报错）

退出码：0 = 可以往下走；1 = 有硬性阻塞项。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import socket
import sys

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"

problems: list[str] = []
warnings: list[str] = []


def check_python() -> None:
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 10):
        print(f"{OK} Python {major}.{minor}（{platform.system()}）")
    else:
        print(f"{BAD} Python {major}.{minor} 太旧，插件要求 >= 3.10")
        problems.append("升级 Python 到 3.10 以上")


def check_binaries() -> None:
    path = shutil.which("git")
    if path:
        print(f"{OK} git: {path}")
    else:
        print(f"{WARN} 未找到 git — 安装 Git（拉取项目与插件）")
        warnings.append("缺 git：安装 Git")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        print(f"{OK} ffmpeg: {ffmpeg}")
        return
    # imageio-ffmpeg 自带一份二进制，装了它就不用系统 ffmpeg
    try:
        import imageio_ffmpeg

        print(f"{OK} ffmpeg: {imageio_ffmpeg.get_ffmpeg_exe()}（imageio-ffmpeg 自带）")
    except Exception:
        print(f"{WARN} 未找到 ffmpeg — pip install imageio-ffmpeg，或装系统 ffmpeg")
        warnings.append("缺 ffmpeg：pip install imageio-ffmpeg")


def check_ports() -> None:
    ports = {6199: "OneBot v11 WS（NapCat → AstrBot）", 6185: "AstrBot 面板", 6099: "NapCat WebUI"}
    for port, desc in ports.items():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.4)
        try:
            in_use = s.connect_ex(("127.0.0.1", port)) == 0
        finally:
            s.close()
        if in_use:
            print(f"{OK} 端口 {port} 已被占用（通常在跑：{desc}）")
        else:
            print(f"{WARN} 端口 {port} 空闲 — 若已完成安装，这里应该是占用状态（{desc}）")
            warnings.append(f"端口 {port} 没在监听：{desc} 可能还没启动")


def check_packages() -> None:
    for pkg in ("dashscope", "imageio_ffmpeg", "aiohttp"):
        if importlib.util.find_spec(pkg):
            print(f"{OK} 依赖 {pkg}")
        else:
            print(f"{WARN} 缺依赖 {pkg} — pip install -r requirements-plugin.txt")
            warnings.append(f"缺 Python 包 {pkg}")


def check_env(env_path: str | None) -> None:
    if not env_path:
        print(f"{WARN} 未指定 --env，跳过密钥检查")
        return
    if not os.path.exists(env_path):
        print(f"{WARN} 找不到 {env_path}（从 env.example 复制一份再填）")
        warnings.append("没有 .env，密钥还没配")
        return
    keys = {"DEEPSEEK_API_KEY": "对话主力", "DASHSCOPE_API_KEY": "看图/听写/语音"}
    values: dict[str, str] = {}
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    for key, desc in keys.items():
        val = values.get(key, "")
        if val and not val.startswith("<"):
            print(f"{OK} {key} 已填（{desc}）")
        else:
            print(f"{BAD} {key} 空着 — {desc} 用不了")
            problems.append(f"填好 {key}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None, help="要检查的 .env 路径")
    args = ap.parse_args()

    print("=== 大肥鱼部署前置检查 ===\n")
    check_python()
    check_binaries()
    check_ports()
    check_packages()
    check_env(args.env)

    print()
    if problems:
        print("阻塞项（必须解决）：")
        for p in problems:
            print(f"  - {p}")
    if warnings:
        print("提醒（不阻塞）：")
        for w in warnings:
            print(f"  - {w}")
    if not problems and not warnings:
        print("全部检查通过，可以按 README 继续。")
    elif not problems:
        print("没有阻塞项，可以继续（上面的提醒留意一下）。")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

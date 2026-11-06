#!/usr/bin/env python3
"""大肥鱼部署前置检查 + payload 组装（preflight）

只读检查 + 可选组装，标准库零依赖，跨 Linux / macOS / Windows 通用。

用法：
    python deploy-kit/preflight.py                        # 全面体检（默认阶段：install 前）
    python deploy-kit/preflight.py --stage post           # 装完后复查（端口应处于监听态）
    python deploy-kit/preflight.py --env .env             # 指定 .env 路径（默认若存在自动读）
    python deploy-kit/preflight.py --with-bili            # 连 B 站自主活动依赖一起查
    python deploy-kit/preflight.py --stage-payload payload # 干净组装插件 + 人格正文
    python deploy-kit/preflight.py --json                 # 机器可读输出

检查项：
  1. Python 版本（核心插件要求 >= 3.10；--with-bili 要求 >= 3.12 + Asia/Shanghai 时区）
  2. git / ffmpeg 是否可用（含 imageio-ffmpeg 自带二进制）
  3. 关键端口状态（6199 OneBot WS / 6185 AstrBot 面板 / 6099 NapCat WebUI）
  4. 插件依赖包（dashscope / imageio-ffmpeg / aiohttp；--with-bili 再查 Pillow / mcp）
  5. 仓库核心资产完整性（插件源码语法编译、表情表 JSON、人格正文、离线套件）
  6. .env 密钥与账号（识别模板占位符与格式问题；未传 --env 时若当前目录有 .env 也会提示）

退出码：0 = 无硬性阻塞；1 = 存在必须先解决的阻塞项。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import py_compile
import re
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Windows 终端若未开虚拟终端或是重定向管道，自动降级为纯符号，避免乱码
def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = k32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                k32.SetConsoleMode(handle, mode.value | 0x0004)
                return True
        except Exception:
            return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM"))
    return True


_COLOR = _use_color()
OK = "\033[32m✓\033[0m" if _COLOR else "[OK]"
BAD = "\033[31m✗\033[0m" if _COLOR else "[FAIL]"
WARN = "\033[33m!\033[0m" if _COLOR else "[WARN]"

PLACEHOLDER_RE = re.compile(
    r"^<.*>$|^your[_-]|^sk-xxx|^replace|^todo|^changeme|请填|你的|密钥|小号QQ",
    re.IGNORECASE,
)

CORE_PLUGINS = (
    "astrbot_plugin_face_convert",
    "astrbot_plugin_schedule_awareness",
)
BILI_PLUGIN = "astrbot_plugin_bili_activity"

STAGE_IGNORE = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.bak",
    "*.log",
    "*.db",
    "*.db-*",
    "*.sqlite*",
    "config.json",
    ".DS_Store",
)


class Report:
    def __init__(self, quiet: bool = False) -> None:
        self.problems: list[str] = []
        self.warnings: list[str] = []
        self.items: list[dict[str, Any]] = []
        self.quiet = quiet

    def ok(self, name: str, detail: str) -> None:
        self.items.append({"name": name, "status": "ok", "detail": detail})
        if not self.quiet:
            print(f"{OK} {detail}")

    def warn(self, name: str, detail: str, advice: str | None = None) -> None:
        self.items.append({"name": name, "status": "warn", "detail": detail})
        self.warnings.append(advice or detail)
        if not self.quiet:
            print(f"{WARN} {detail}")

    def fail(self, name: str, detail: str, fix: str | None = None) -> None:
        self.items.append({"name": name, "status": "fail", "detail": detail})
        self.problems.append(fix or detail)
        if not self.quiet:
            print(f"{BAD} {detail}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.problems,
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "items": list(self.items),
        }


def check_python(rep: Report, with_bili: bool = False, version_info: tuple[int, int] | None = None) -> None:
    major, minor = (version_info or sys.version_info[:2])[:2]
    need = (3, 12) if with_bili else (3, 10)
    tag = f"Python {major}.{minor}（{platform.system()}）"
    if (major, minor) >= need:
        rep.ok("python", tag)
    else:
        why = "B 站自主活动插件要求 >= 3.12" if with_bili else "插件要求 >= 3.10"
        rep.fail("python", f"{tag} 太旧，{why}", f"升级 Python 到 {need[0]}.{need[1]} 以上")

    if with_bili:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo("Asia/Shanghai")
            rep.ok("timezone", "时区数据 Asia/Shanghai 可用")
        except Exception:
            rep.fail(
                "timezone",
                "缺少 Asia/Shanghai 时区数据（Windows 常见）",
                "安装时区包：pip install tzdata",
            )


def check_binaries(rep: Report) -> None:
    git_path = shutil.which("git")
    if git_path:
        rep.ok("git", f"git: {git_path}")
    else:
        rep.warn("git", "未找到 git — 安装 Git（拉取项目与插件）", "缺 git：安装 Git")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        rep.ok("ffmpeg", f"ffmpeg: {ffmpeg}")
        return
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            rep.ok("ffmpeg", f"ffmpeg: {exe}（imageio-ffmpeg 自带）")
            return
    except Exception:
        pass
    rep.warn(
        "ffmpeg",
        "未找到 ffmpeg — 跑完 bootstrap（会装 imageio-ffmpeg）或装系统 ffmpeg 即可解读视频",
        "缺 ffmpeg：pip install imageio-ffmpeg（仅影响视频抽帧/抽音轨）",
    )


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def check_ports(rep: Report, stage: str = "install", probe=_port_open) -> None:
    ports = {
        6199: "OneBot v11 WS（NapCat → AstrBot）",
        6185: "AstrBot 面板",
        6099: "NapCat WebUI",
    }
    for port, desc in ports.items():
        in_use = bool(probe(port))
        if stage == "post":
            if in_use:
                rep.ok(f"port:{port}", f"端口 {port} 已监听（{desc}）")
            else:
                rep.warn(
                    f"port:{port}",
                    f"端口 {port} 未监听 — {desc} 似乎还没启动",
                    f"启动 {desc}（端口 {port}）",
                )
        else:
            if in_use:
                rep.ok(f"port:{port}", f"端口 {port} 已有服务在跑（{desc}）")
            else:
                rep.ok(f"port:{port}", f"端口 {port} 空闲可用（装好后由 {desc} 监听）")


def check_packages(rep: Report, with_bili: bool = False, finder=importlib.util.find_spec) -> None:
    core = (
        ("dashscope", "dashscope", "百炼 ASR 听写 / 视觉 / TTS"),
        ("imageio_ffmpeg", "imageio-ffmpeg", "自带 ffmpeg 二进制，视频抽帧/抽音轨"),
        ("aiohttp", "aiohttp", "媒体下载与离线视频测试"),
    )
    for mod_name, pip_name, usage in core:
        if finder(mod_name):
            rep.ok(f"pkg:{mod_name}", f"依赖 {pip_name}（{usage}）")
        else:
            rep.warn(
                f"pkg:{mod_name}",
                f"未装 {pip_name}（{usage}）— 跑 bootstrap 或 pip install -r deploy-kit/requirements-plugin.txt",
                f"缺 Python 包 {pip_name}（装进 AstrBot 运行环境）",
            )
    if with_bili:
        for mod_name, pip_name, usage in (
            ("PIL", "Pillow", "B 站卡片截图处理"),
            ("mcp", "mcp", "B 站插件连接 PhoneMCP / BrowserMCP"),
        ):
            if finder(mod_name):
                rep.ok(f"pkg:{mod_name}", f"可选依赖 {pip_name}（{usage}）")
            else:
                rep.warn(
                    f"pkg:{mod_name}",
                    f"未装 {pip_name}（{usage}）— pip install -r plugin/astrbot_plugin_bili_activity/requirements.txt",
                    f"B 站插件缺 {pip_name}",
                )


def extract_persona_prompt(md_text: str) -> str:
    """提取 `persona/大肥鱼-人格.md` 中第一个 `---` 分隔线后的系统提示词正文。"""
    lines = md_text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == "---":
            body = "\n".join(lines[idx + 1 :]).strip()
            if body:
                return body + "\n"
    return md_text.strip() + "\n"


def check_repo_assets(rep: Report, root: Path = ROOT, with_bili: bool = False) -> None:
    plugins = list(CORE_PLUGINS) + ([BILI_PLUGIN] if with_bili else [])
    for name in plugins:
        pdir = root / "plugin" / name
        main_py = pdir / "main.py"
        meta = pdir / "metadata.yaml"
        if not main_py.is_file() or not meta.is_file():
            rep.fail(f"asset:{name}", f"插件目录不完整：{pdir}", f"补齐 plugin/{name}/")
            continue
        try:
            py_compile.compile(str(main_py), doraise=True)
            rep.ok(f"asset:{name}", f"插件源码语法正常：plugin/{name}/main.py")
        except py_compile.PyCompileError as exc:
            rep.fail(f"asset:{name}", f"插件语法错误：{exc.msg}", f"修复 plugin/{name}/main.py")

    fmap = root / "plugin" / "astrbot_plugin_face_convert" / "face_map.json"
    try:
        data = json.loads(fmap.read_text(encoding="utf-8"))
        faces = data.get("faces") if isinstance(data, dict) and "faces" in data else data
        count = len(faces) if isinstance(faces, (dict, list)) else 0
        if count >= 50:
            rep.ok("asset:face_map", f"QQ 表情映射表完整（{count} 项）")
        else:
            rep.fail("asset:face_map", f"face_map.json 表情数异常（{count}）", "恢复 face_map.json")
    except Exception as exc:
        rep.fail("asset:face_map", f"face_map.json 读取失败：{exc}", "恢复 face_map.json")

    persona_md = root / "persona" / "大肥鱼-人格.md"
    if persona_md.is_file():
        prompt = extract_persona_prompt(persona_md.read_text(encoding="utf-8"))
        if "大肥鱼" in prompt and len(prompt) >= 500:
            rep.ok("asset:persona", f"人格提示词就绪（正文 {len(prompt)} 字）")
        else:
            rep.fail("asset:persona", "人格提示词内容异常", "检查 persona/大肥鱼-人格.md")
    else:
        rep.fail("asset:persona", "找不到 persona/大肥鱼-人格.md", "恢复 persona/大肥鱼-人格.md")


def _looks_placeholder(val: str) -> bool:
    v = val.strip()
    if not v:
        return True
    if PLACEHOLDER_RE.search(v):
        return True
    if any( ord(ch) > 127 for ch in v ):
        return True
    return False


def parse_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].strip()
        values[k.strip()] = v
    return values


def check_env(rep: Report, env_path: str | Path | None, root: Path = ROOT) -> None:
    explicit = env_path is not None
    path = Path(env_path) if env_path else (root / ".env")
    if not path.exists():
        if explicit:
            rep.fail("env", f"找不到指定的密钥文件：{path}", f"复制模板：cp deploy-kit/env.example {path}")
        else:
            rep.warn(
                "env",
                "未发现 .env（跳过密钥检查；需要核对密钥时跑：cp deploy-kit/env.example .env && python deploy-kit/preflight.py --env .env）",
                "还未创建 .env 备忘单（可选）",
            )
        return

    values = parse_env_file(path)
    required_keys = {
        "DEEPSEEK_API_KEY": "对话主力（DeepSeek）",
        "DASHSCOPE_API_KEY": "百炼看图 / 语音听写 / TTS（face_convert 也会直接读取该环境变量）",
    }
    for key, desc in required_keys.items():
        val = values.get(key, "")
        if not _looks_placeholder(val) and len(val) >= 12:
            masked = val[:5] + "…" + val[-3:] if len(val) > 10 else "***"
            rep.ok(f"env:{key}", f"{key} 已填（{masked}，{desc}）")
        else:
            rep.fail(f"env:{key}", f"{key} 未填或仍是模板占位符 — {desc} 用不了", f"在 {path} 中填好真实的 {key}")

    for qq_key, label in (("QQ_ACCOUNT", "机器人小号 QQ"), ("OWNER_QQ", "主人 QQ（填进 AstrBot admins_id）")):
        val = values.get(qq_key, "").strip()
        if not val or _looks_placeholder(val):
            rep.warn(f"env:{qq_key}", f"{qq_key} 未填（{label}）", f"建议在 {path} 记录 {qq_key}")
        elif not val.isdigit() or not (5 <= len(val) <= 11):
            rep.fail(f"env:{qq_key}", f"{qq_key}={val!r} 不是合法 QQ 号（应为 5～11 位纯数字）", f"修正 {qq_key}")
        else:
            rep.ok(f"env:{qq_key}", f"{qq_key} 已填（{val}，{label}）")

    bot_qq = values.get("QQ_ACCOUNT", "").strip()
    owner_qq = values.get("OWNER_QQ", "").strip()
    if bot_qq and owner_qq and bot_qq.isdigit() and bot_qq == owner_qq:
        rep.warn(
            "env:qq_same",
            "QQ_ACCOUNT 与 OWNER_QQ 相同 — 强烈建议用独立小号跑机器人，大号当主人",
            "机器人号与主人号相同，存在风控与自问自答风险",
        )


def stage_payload(dest: str | Path, root: Path = ROOT, include_bili: bool = False) -> dict[str, Any]:
    """把插件与人格干净组装到 dest 目录（排除缓存/运行时文件，生成可直接粘贴的纯正文人格）。"""
    dest_path = Path(dest).resolve()
    plugins_dst = dest_path / "plugins"
    persona_dst = dest_path / "persona"
    if dest_path.exists():
        shutil.rmtree(dest_path)
    plugins_dst.mkdir(parents=True, exist_ok=True)
    persona_dst.mkdir(parents=True, exist_ok=True)

    ignore = shutil.ignore_patterns(*STAGE_IGNORE)
    plugin_names = list(CORE_PLUGINS) + ([BILI_PLUGIN] if include_bili else [])
    staged_plugins: list[str] = []
    for name in plugin_names:
        src_dir = root / "plugin" / name
        if src_dir.is_dir():
            shutil.copytree(src_dir, plugins_dst / name, ignore=ignore)
            staged_plugins.append(name)

    for md in (root / "persona").glob("*.md"):
        shutil.copy2(md, persona_dst / md.name)

    persona_md = root / "persona" / "大肥鱼-人格.md"
    prompt_txt = persona_dst / "大肥鱼-system-prompt.txt"
    if persona_md.is_file():
        prompt_txt.write_text(
            extract_persona_prompt(persona_md.read_text(encoding="utf-8")),
            encoding="utf-8",
        )

    return {
        "dest": str(dest_path),
        "plugins": staged_plugins,
        "prompt_file": str(prompt_txt) if prompt_txt.is_file() else None,
    }


def run_checks(
    *,
    env_path: str | Path | None = None,
    stage: str = "install",
    with_bili: bool = False,
    quiet: bool = False,
    root: Path = ROOT,
) -> Report:
    rep = Report(quiet=quiet)
    if not quiet:
        title = "部署后复查（post）" if stage == "post" else "部署前体检（install）"
        print(f"=== 大肥鱼 · {title} ===\n")
    check_python(rep, with_bili=with_bili)
    check_binaries(rep)
    check_ports(rep, stage=stage)
    check_packages(rep, with_bili=with_bili)
    check_repo_assets(rep, root=root, with_bili=with_bili)
    check_env(rep, env_path=env_path, root=root)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="大肥鱼部署前置检查与 payload 组装工具")
    ap.add_argument("--env", default=None, help="要检查的 .env 路径（默认若当前目录有 .env 会自动读）")
    ap.add_argument(
        "--stage",
        choices=("install", "post"),
        default="install",
        help="install=安装前体检（端口空闲不报警）；post=安装后复查（端口应处于监听态）",
    )
    ap.add_argument("--with-bili", action="store_true", help="连同 B 站自主活动插件的依赖与时区一起检查/组装")
    ap.add_argument("--stage-payload", metavar="DIR", default=None, help="将插件与人格干净组装到指定目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果")
    args = ap.parse_args(argv)

    staged = None
    if args.stage_payload:
        staged = stage_payload(args.stage_payload, root=ROOT, include_bili=args.with_bili)
        if not args.json:
            print(f"{OK} 已组装 payload: {staged['dest']}")
            print(f"  插件（{len(staged['plugins'])} 个）：{', '.join(staged['plugins'])}")
            if staged.get("prompt_file"):
                print(f"  可直接粘贴的人格纯正文：{staged['prompt_file']}")
            print()

    rep = run_checks(
        env_path=args.env,
        stage=args.stage,
        with_bili=args.with_bili,
        quiet=args.json,
        root=ROOT,
    )
    if args.json:
        out = rep.to_dict()
        if staged:
            out["staged"] = staged
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if rep.problems else 0

    print()
    if rep.problems:
        print("阻塞项（必须先解决）：")
        for p in rep.problems:
            print(f"  - {p}")
    if rep.warnings:
        print("提醒（不阻塞当前步骤）：")
        for w in rep.warnings:
            print(f"  - {w}")
    if not rep.problems and not rep.warnings:
        print("全部检查通过，可以按 QUICKSTART.md / README.md 继续。")
    elif not rep.problems:
        print("没有硬性阻塞项，可以继续（上面的提醒按需处理）。")
    return 1 if rep.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

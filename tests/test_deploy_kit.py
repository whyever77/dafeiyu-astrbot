# -*- coding: utf-8 -*-
"""deploy-kit 回归测试：preflight 体检、.env 解析校验、payload 干净组装与人格正文剥离。

跑法（纯标准库，不需要外网或 AstrBot 环境）：
    python tests/test_deploy_kit.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = ROOT / "deploy-kit" / "preflight.py"

spec = importlib.util.spec_from_file_location("deploy_kit_preflight", PREFLIGHT_PATH)
assert spec and spec.loader
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# 1) Python 版本门禁：核心 >= 3.10，--with-bili >= 3.12
rep = pf.Report(quiet=True)
pf.check_python(rep, with_bili=False, version_info=(3, 10))
check("py-3.10-core-ok", not rep.problems, repr(rep.problems))

rep = pf.Report(quiet=True)
pf.check_python(rep, with_bili=False, version_info=(3, 9))
check("py-3.9-core-fail", len(rep.problems) == 1, repr(rep.problems))

rep = pf.Report(quiet=True)
pf.check_python(rep, with_bili=True, version_info=(3, 11))
check("py-3.11-bili-fail", any("3.12" in p for p in rep.problems), repr(rep.problems))

rep = pf.Report(quiet=True)
pf.check_python(rep, with_bili=True, version_info=(3, 12))
check("py-3.12-bili-ok", not rep.problems, repr(rep.problems))

# 2) 端口检查：install 阶段端口空闲是 OK（不报黄字警告）；post 阶段空闲才提醒
rep = pf.Report(quiet=True)
pf.check_ports(rep, stage="install", probe=lambda _p: False)
check("ports-install-free-no-warn", not rep.warnings and not rep.problems, repr(rep.warnings))

rep = pf.Report(quiet=True)
pf.check_ports(rep, stage="post", probe=lambda _p: False)
check("ports-post-free-warns", len(rep.warnings) == 3 and not rep.problems, repr(rep.warnings))

rep = pf.Report(quiet=True)
pf.check_ports(rep, stage="post", probe=lambda _p: True)
check("ports-post-listening-ok", not rep.warnings and not rep.problems, repr(rep.warnings))

# 3) .env 解析与模板占位符识别
with tempfile.TemporaryDirectory() as tmp:
    tdir = Path(tmp)

    # 3a) 直接把 env.example 复制成 .env 未改动 -> 必须拦截占位符
    env_tpl = tdir / ".env.template"
    shutil.copy2(ROOT / "deploy-kit" / "env.example", env_tpl)
    rep = pf.Report(quiet=True)
    pf.check_env(rep, env_tpl, root=tdir)
    check(
        "env-template-placeholders-rejected",
        len(rep.problems) == 2 and any("DEEPSEEK_API_KEY" in p for p in rep.problems),
        repr(rep.problems),
    )

    # 3b) 填写真实格式密钥 + 合法 QQ 号（含 export、引号、行尾注释）
    env_good = tdir / ".env.good"
    env_good.write_text(
        'export DEEPSEEK_API_KEY="sk-abcdef1234567890" # main chat\n'
        "DASHSCOPE_API_KEY=sk-9876543210abcdef\n"
        "QQ_ACCOUNT=123456789\n"
        "OWNER_QQ=987654321\n",
        encoding="utf-8",
    )
    rep = pf.Report(quiet=True)
    pf.check_env(rep, env_good, root=tdir)
    check("env-valid-passes", not rep.problems and not rep.warnings, repr((rep.problems, rep.warnings)))

    # 3c) 非数字 QQ 号报错；机器人小号与主人同号给提醒
    env_bad_qq = tdir / ".env.badqq"
    env_bad_qq.write_text(
        "DEEPSEEK_API_KEY=sk-abcdef1234567890\n"
        "DASHSCOPE_API_KEY=sk-9876543210abcdef\n"
        "QQ_ACCOUNT=not_a_qq\n"
        "OWNER_QQ=987654321\n",
        encoding="utf-8",
    )
    rep = pf.Report(quiet=True)
    pf.check_env(rep, env_bad_qq, root=tdir)
    check("env-invalid-qq-fails", any("QQ_ACCOUNT" in p for p in rep.problems), repr(rep.problems))

    env_same_qq = tdir / ".env.sameqq"
    env_same_qq.write_text(
        "DEEPSEEK_API_KEY=sk-abcdef1234567890\n"
        "DASHSCOPE_API_KEY=sk-9876543210abcdef\n"
        "QQ_ACCOUNT=123456789\n"
        "OWNER_QQ=123456789\n",
        encoding="utf-8",
    )
    rep = pf.Report(quiet=True)
    pf.check_env(rep, env_same_qq, root=tdir)
    check("env-same-qq-warns", not rep.problems and len(rep.warnings) == 1, repr(rep.warnings))

    # 3d) 显式指定不存在的 --env 报阻塞错误；未指定且当前目录无 .env 只提醒
    rep = pf.Report(quiet=True)
    pf.check_env(rep, tdir / "missing.env", root=tdir)
    check("env-explicit-missing-fails", len(rep.problems) == 1, repr(rep.problems))

    rep = pf.Report(quiet=True)
    pf.check_env(rep, None, root=tdir)
    check("env-default-missing-warns-only", not rep.problems and len(rep.warnings) == 1, repr(rep.warnings))

    # 3e) 未传 --env 但当前目录已存在 .env 时，自动检查它
    shutil.copy2(env_good, tdir / ".env")
    rep = pf.Report(quiet=True)
    pf.check_env(rep, None, root=tdir)
    check("env-auto-detected-in-root", not rep.problems and not rep.warnings, repr((rep.problems, rep.warnings)))

# 4) 仓库核心资产完整性检查
rep = pf.Report(quiet=True)
pf.check_repo_assets(rep, root=ROOT, with_bili=True)
check("repo-assets-intact", not rep.problems, repr(rep.problems))

# 5) payload 干净组装：不带 __pycache__/config.json，默认不塞 bili_activity，且生成纯正文提示词
with tempfile.TemporaryDirectory() as tmp:
    payload_dir = Path(tmp) / "payload"
    # 在源目录造一个假 pyc 确认不会拷进去
    pycache = ROOT / "plugin" / "astrbot_plugin_face_convert" / "__pycache__"
    pycache.mkdir(exist_ok=True)
    dummy_pyc = pycache / "_dummy.pyc"
    dummy_pyc.write_bytes(b"0")
    try:
        info = pf.stage_payload(payload_dir, root=ROOT, include_bili=False)
        check(
            "stage-default-core-plugins-only",
            info["plugins"] == ["astrbot_plugin_face_convert", "astrbot_plugin_schedule_awareness"],
            repr(info["plugins"]),
        )
        check(
            "stage-excludes-bili-by-default",
            not (payload_dir / "plugins" / "astrbot_plugin_bili_activity").exists(),
        )
        check(
            "stage-excludes-pycache",
            not list((payload_dir / "plugins").rglob("__pycache__"))
            and not list((payload_dir / "plugins").rglob("*.pyc")),
        )
        prompt_file = payload_dir / "persona" / "大肥鱼-system-prompt.txt"
        prompt_text = prompt_file.read_text(encoding="utf-8")
        check(
            "stage-extracts-clean-persona-prompt",
            prompt_text.startswith("你是「大肥鱼」") and "# 大肥鱼 · QQ 聊天人格设定" not in prompt_text,
            prompt_text[:60],
        )

        # --with-bili 时包含 3 个插件
        info_bili = pf.stage_payload(payload_dir, root=ROOT, include_bili=True)
        check(
            "stage-with-bili-includes-all-3",
            "astrbot_plugin_bili_activity" in info_bili["plugins"]
            and (payload_dir / "plugins" / "astrbot_plugin_bili_activity" / "main.py").is_file(),
            repr(info_bili["plugins"]),
        )
    finally:
        if dummy_pyc.exists():
            dummy_pyc.unlink()

# 6) CLI --json 模式输出合法 JSON
import io
from contextlib import redirect_stdout

buf = io.StringIO()
with redirect_stdout(buf):
    rc = pf.main(["--json"])
data = json.loads(buf.getvalue())
check("cli-json-valid", rc == 0 and data.get("ok") is True and isinstance(data.get("items"), list), repr(data))

print()
if failures:
    print("结果：%d 项失败" % len(failures))
    sys.exit(1)
print("结果：全部通过 OK")

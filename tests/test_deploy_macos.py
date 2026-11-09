# -*- coding: utf-8 -*-
"""deploy-macos 套件的静态回归：相对路径、.env 覆盖、脚本语法、补丁复用。

只校验仓库内文件的自洽性，不联网、不需要 Docker。
跑法：python tests/test_deploy_macos.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "deploy-macos"

REQUIRED_FILES = ("compose.yaml", ".env.example", "start.sh", "stop.sh", "env.sh", "README.md")
PATCH_FILES = ("dashscope_tts.py", "stage.py", "respond_stage.py", "chat_layout.py")
# compose 里允许有兜底默认值、不写进 .env.example 的变量
ENV_OPTIONAL = {"NAPCAT_UID", "NAPCAT_GID"}

# 端口映射：6185 / 6185:6185 / 127.0.0.1:6185:6185 / [::1]:6185:6185
PORT_PREFIX = re.compile(r"^(?:(?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-fA-F:]+\]|\d+):\d")


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def absolute_host_paths(compose_text: str) -> list[str]:
    """返回 compose 里以绝对路径作为宿主机来源的挂载条目。

    只跳过端口映射，不能因为「以引号开头」就跳过：带引号的
    ``- "/opt/foo:/data"`` 同样是绝对宿主路径。
    """
    offenders = []
    for line in compose_text.splitlines():
        entry = line.strip()
        if not entry.startswith("- "):
            continue
        value = _strip_quotes(entry[2:].strip())
        if PORT_PREFIX.match(value):
            continue
        source = value.split(":", 1)[0]
        if source.startswith("/"):
            offenders.append(value)
    return offenders


class DeployMacosStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = (KIT / "compose.yaml").read_text(encoding="utf-8")

    def test_required_files_exist(self) -> None:
        missing = [name for name in REQUIRED_FILES if not (KIT / name).is_file()]
        self.assertEqual(missing, [], f"deploy-macos 缺少文件: {missing}")

    def test_compose_declares_both_services(self) -> None:
        self.assertRegex(self.compose, r"(?m)^  astrbot:")
        self.assertRegex(self.compose, r"(?m)^  napcat:")

    def test_no_absolute_host_paths(self) -> None:
        """云上套件的 /opt/dafeiyu 之类绝对路径不能混进 macOS 套件。"""
        offenders = absolute_host_paths(self.compose)
        self.assertEqual(offenders, [], f"宿主路径必须相对本文件: {offenders}")

    def test_absolute_path_detection_handles_quoted_entries(self) -> None:
        """带引号的绝对宿主路径要被抓到；带引号的端口映射不能误报。"""
        sample = "\n".join(
            [
                "    ports:",
                '      - "127.0.0.1:6185:6185"',
                '      - "6185:6185"',
                '      - "[::1]:6185:6185"',
                "    volumes:",
                "      - ./data:/AstrBot/data",
                '      - "/opt/dafeiyu/astrbot/data:/AstrBot/data"',
                "      - '/opt/other:/data'",
            ]
        )
        self.assertEqual(
            absolute_host_paths(sample),
            ["/opt/dafeiyu/astrbot/data:/AstrBot/data", "/opt/other:/data"],
        )

    def test_reuses_repo_patches_without_copy(self) -> None:
        """补丁复用 ../deploy/patches，不复制第二份（避免两处漂移）。"""
        for name in PATCH_FILES:
            self.assertIn(f"../deploy/patches/{name}", self.compose)
            self.assertTrue((ROOT / "deploy" / "patches" / name).is_file(), name)
        self.assertIn("context: ../deploy", self.compose)
        self.assertFalse((KIT / "patches").exists(), "不要在 deploy-macos 里再放一份补丁")

    def test_env_example_covers_compose_variables(self) -> None:
        used = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", self.compose))
        env_text = (KIT / ".env.example").read_text(encoding="utf-8")
        declared = set(re.findall(r"(?m)^([A-Z_][A-Z0-9_]*)=", env_text))
        for key in ("ASTRBOT_PORT", "NAPCAT_PORT", "TZ", "QQ_ACCOUNT"):
            self.assertIn(key, used, f"compose 没用到 {key}")
            self.assertIn(key, declared, f".env.example 缺 {key}")
        self.assertLessEqual(used - ENV_OPTIONAL, declared, f"这些变量只能靠默认值: {used - ENV_OPTIONAL - declared}")

    def test_scripts_are_posix_bash(self) -> None:
        for name in ("start.sh", "stop.sh"):
            path = KIT / name
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("#!/usr/bin/env bash"), name)
            self.assertIn("set -euo pipefail", text, name)
            if shutil.which("bash"):
                done = subprocess.run(
                    ["bash", "-n", str(path)], capture_output=True, text=True, check=False
                )
                self.assertEqual(done.returncode, 0, f"{name} 语法错误: {done.stderr}")

    def test_purge_only_touches_kit_dirs(self) -> None:
        """--purge 只能删本套件的相对目录，不能误删仓库其他部分。"""
        stop = (KIT / "stop.sh").read_text(encoding="utf-8")
        self.assertIn("rm -rf -- ./data ./napcat", stop)
        self.assertNotIn("rm -rf /", stop)

    def test_readme_documents_key_steps(self) -> None:
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        for needle in ("host.docker.internal", "6185", "6099", "./start.sh", "PhoneMCP"):
            self.assertIn(needle, readme)

    def test_readme_documents_initial_credentials(self) -> None:
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        # NapCat 4.18.x 起 token 不再打印到日志：要告诉用户去哪读、怎么自己定
        self.assertIn("webui.json", readme)
        self.assertIn("WEBUI_TOKEN", readme)
        # AstrBot v4.24.4+ 初始密码只打印在启动日志
        self.assertIn("Initial password", readme)

    def test_env_example_qq_comment_is_accurate(self) -> None:
        """首次部署仍要扫码；快速登录只省后续重启。"""
        env_text = (KIT / ".env.example").read_text(encoding="utf-8")
        block = "\n".join(
            line for line in env_text.splitlines() if "扫码" in line or "快速登录" in line
        )
        self.assertIn("首次", block)
        self.assertIn("后续", block)

    def test_start_script_masks_qq_account(self) -> None:
        """AGENTS.md：不把 QQ 号写进日志/终端。"""
        start = (KIT / "start.sh").read_text(encoding="utf-8")
        self.assertNotIn("${QQ_ACCOUNT}", start)
        self.assertIn("已隐藏", start)

    def test_readiness_probe_rejects_http_errors(self) -> None:
        """端口被别的 HTTP 服务占用时，不能把任意响应当成 AstrBot 就绪。"""
        start = (KIT / "start.sh").read_text(encoding="utf-8")
        self.assertIn("curl -fsS", start)

    def test_compose_drops_container_name(self) -> None:
        """与云上套件同名会撞容器名；容器名交给 compose 项目名。"""
        self.assertNotIn("container_name:", self.compose)

    def test_readme_warns_same_host_conflict(self) -> None:
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        self.assertIn("同一台 Docker 主机", readme)

    def test_env_get_strips_inline_comment(self) -> None:
        helper = KIT / "env.sh"
        self.assertTrue(helper.is_file(), "缺少 deploy-macos/env.sh")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text(
                "ASTRBOT_PORT=6186  # 面板端口\r\n"
                'NAPCAT_PORT="6100 # 引号内保留"\n'
                "HASH=abc#def\n"
                "EMPTY=\n",
                encoding="utf-8",
            )
            script = (
                f'source "{helper}" && '
                'printf "%s\\n" "$(env_get ASTRBOT_PORT)" "$(env_get NAPCAT_PORT)" '
                '"$(env_get HASH)" "$(env_get EMPTY)"'
            )
            done = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                check=False,
                cwd=tmp,
                env={**os.environ, "ENV_FILE": str(Path(tmp) / ".env")},
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(
                done.stdout.splitlines(),
                ["6186", "6100 # 引号内保留", "abc#def", ""],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

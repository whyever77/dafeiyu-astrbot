# -*- coding: utf-8 -*-
"""deploy-macos 套件的静态回归：相对路径、.env 覆盖、脚本语法、补丁复用。

只校验仓库内文件的自洽性，不联网、不需要 Docker。
跑法：python tests/test_deploy_macos.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "deploy-macos"

REQUIRED_FILES = ("compose.yaml", ".env.example", "start.sh", "stop.sh", "README.md")
PATCH_FILES = ("dashscope_tts.py", "stage.py", "respond_stage.py", "chat_layout.py")
# compose 里允许有兜底默认值、不写进 .env.example 的变量
ENV_OPTIONAL = {"NAPCAT_UID", "NAPCAT_GID"}


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
        offenders = []
        for line in self.compose.splitlines():
            entry = line.strip()
            if not entry.startswith("- "):
                continue
            value = entry[2:].strip()
            if value[:1] in ("'", '"'):
                continue  # 端口映射，不是宿主路径
            source = value.split(":", 1)[0]
            if source.startswith("/"):
                offenders.append(value)
        self.assertEqual(offenders, [], f"宿主路径必须相对本文件: {offenders}")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

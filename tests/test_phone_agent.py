"""Unit tests for M1 Universal Phone Controller (PhoneAgent).

Covers:
1. Strict Bilibili + Douyin package allowlist & forbidden package auto-home guard
2. Automatic fallback to screenshot when uiautomator dump fails on dynamic video screens
3. High-risk UI text guard (payment, password, real-name auth, uninstall)
4. Per-run step quota & BiliActivity tool wiring (df_phone_observe, df_phone_act)
"""
from __future__ import annotations

import base64
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from mcp.types import CallToolResult, ImageContent, TextContent
from PIL import Image

import test_bili_activity as fixtures

main, core, SCOPE = fixtures.main, fixtures.core, fixtures.SCOPE
pa = types.SimpleNamespace(**main.PhoneAgent.__init__.__globals__)
PhoneGate, TransportError = main.PhoneGate, main.TransportError


def make_png(color=(40, 90, 160), size=(720, 1280)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def text_res(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)])


def img_res(raw: bytes) -> CallToolResult:
    return CallToolResult(
        content=[ImageContent(type="image", data=base64.b64encode(raw).decode(), mimeType="image/png")]
    )


class FakePhoneMCP:
    def __init__(self, pkg: str = "tv.danmaku.bili", ui_xml: str = "", fail_ui: bool = False):
        self.pkg = pkg
        self.ui_xml = ui_xml or (
            '<node text="推荐" resource-id="tv.danmaku.bili:id/tab" class="android.widget.TextView" '
            'clickable="true" bounds="[100,80][220,140]" />'
        )
        self.fail_ui = fail_ui
        self.calls: list[tuple[str, dict]] = []
        self.screen_bytes = make_png()

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name in {"get_current_app", "get_current_package"}:
            return text_res(f"Current package: {self.pkg}")
        if name == "get_ui_elements":
            if self.fail_ui:
                raise TransportError("ERROR: could not get idle state")
            return text_res(self.ui_xml)
        if name == "get_screenshot":
            return img_res(self.screen_bytes)
        if name == "listen_audio":
            return text_res("视频里正在讲猫咪日常")
        if name == "launch_app":
            self.pkg = args.get("package_name", self.pkg)
            return text_res(f"Launched {self.pkg}")
        if name == "go_home":
            self.pkg = "com.android.launcher3"
            return text_res("Went home")
        return text_res("ok")


class PhoneAgentConfigTests(unittest.TestCase):
    def test_only_bilibili_and_douyin_are_allowed(self):
        pa.validate_config(pa.DEFAULTS)
        self.assertEqual(pa.resolve_package("B站"), "tv.danmaku.bili")
        self.assertEqual(pa.resolve_package("抖音"), "com.ss.android.ugc.aweme")
        for forbidden in ("com.android.settings", "com.eg.android.AlipayGphone", "com.tencent.mm", "unknown.app"):
            with self.assertRaises(ValueError):
                pa.validate_config(pa.DEFAULTS | {"phone_allowed_packages": ["tv.danmaku.bili", forbidden]})
            with self.assertRaises((ValueError, PermissionError)):
                pa.resolve_package(forbidden)


class PhoneAgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = core.Store(self.folder.name)
        self.gate = PhoneGate()
        self.mcp = FakePhoneMCP()
        self.agent = pa.PhoneAgent(
            self.store,
            self.gate,
            lambda: self.mcp,
            Path(self.folder.name) / "screens",
        )
        self.cfg = main.DEFAULTS | {
            "owner_scope": SCOPE,
            "owner_id": "12345",
            "enabled": True,
            "life_enabled": True,
            "phone_free_explore_enabled": True,
            "dnd_ranges": [],
        }

    async def asyncTearDown(self):
        self.store.close()
        self.folder.cleanup()

    async def test_forbidden_and_unlisted_packages_trigger_auto_home(self):
        for bad_pkg in ("com.eg.android.AlipayGphone", "com.android.settings", "com.unknown.browser"):
            self.mcp.pkg = bad_pkg
            self.mcp.calls.clear()
            with self.assertRaises(PermissionError):
                await self.agent.observe(SCOPE, self.cfg)
            tool_names = [c[0] for c in self.mcp.calls]
            self.assertIn("go_home", tool_names)

    async def test_dynamic_video_screen_falls_back_to_screenshot_and_records_episode(self):
        self.mcp.pkg = "com.ss.android.ugc.aweme"
        self.mcp.fail_ui = True
        obs = await self.agent.observe(
            SCOPE, self.cfg, run_id="run1", include_screenshot=False, listen_seconds=5
        )
        self.assertEqual(obs["package"], "com.ss.android.ugc.aweme")
        self.assertEqual(obs["ui_status"], "ui_dump_failed_fallback")
        self.assertIsNotNone(obs["snapshot"])
        self.assertEqual(obs["audio"], "视频里正在讲猫咪日常")
        self.assertTrue(obs["episode_id"])

    async def test_dangerous_ui_hazards_block_tap_and_type(self):
        self.mcp.pkg = "com.ss.android.ugc.aweme"
        self.mcp.ui_xml = (
            '<node text="确认支付 ¥99.00" resource-id="com.ss.android.ugc.aweme:id/pay_btn" '
            'clickable="true" bounds="[200,900][520,1000]" />'
        )
        with self.assertRaises(PermissionError):
            await self.agent.act(SCOPE, self.cfg, "tap", {"x": 360, "y": 950}, run_id="run1")
        with self.assertRaises(PermissionError):
            await self.agent.act(SCOPE, self.cfg, "tap_element", {"text": "确认支付"}, run_id="run1")
        with self.assertRaises(PermissionError):
            await self.agent.act(SCOPE, self.cfg, "type_text", {"text": "输入密码123456"}, run_id="run1")

    async def test_step_quota_and_safe_actions_succeed(self):
        cfg = self.cfg | {"phone_max_steps_per_run": 2}
        r1 = await self.agent.act(SCOPE, cfg, "launch_app", {"app": "抖音"}, run_id="run_q")
        self.assertTrue(r1["ok"])
        self.assertEqual(self.mcp.pkg, "com.ss.android.ugc.aweme")
        r2 = await self.agent.act(SCOPE, cfg, "swipe", {"direction": "up"}, run_id="run_q")
        self.assertTrue(r2["ok"])
        with self.assertRaises(PermissionError):
            await self.agent.act(SCOPE, cfg, "swipe", {"direction": "up"}, run_id="run_q")

    async def test_out_of_bounds_tap_is_rejected_against_actual_screen(self):
        cfg = self.cfg | {"phone_max_steps_per_run": 5}
        await self.agent.act(SCOPE, cfg, "launch_app", {"app": "抖音"}, run_id="oob")
        with self.assertRaises(ValueError):
            await self.agent.act(SCOPE, cfg, "tap", {"x": 559, "y": 1440}, run_id="oob")
        result = await self.agent.act(SCOPE, cfg, "tap", {"x": 360, "y": 640}, run_id="oob")
        self.assertTrue(result["ok"])

    async def test_plugin_tools_df_phone_observe_and_act(self):
        plugin = main.BiliActivity.__new__(main.BiliActivity)
        plugin.folder = Path(self.folder.name)
        plugin.store = self.store
        plugin.gate = self.gate
        plugin.control = Path(self.folder.name, "control.json")
        plugin.control.write_text(json.dumps(self.cfg))
        plugin.snapshots, plugin.run_videos = {}, {}
        plugin.live = None
        plugin.phone = lambda: self.mcp

        ev = fixtures.FakeEvent()
        obs_raw = await plugin.phone_observe(ev, include_screenshot=True, listen_seconds=0)
        obs = json.loads(obs_raw)
        self.assertEqual(obs["package"], "tv.danmaku.bili")

        act_raw = await plugin.phone_act(ev, "launch_app", json.dumps({"app": "douyin"}))
        act = json.loads(act_raw)
        self.assertTrue(act["ok"])
        self.assertEqual(act["package"], "com.ss.android.ugc.aweme")

        home_raw = await plugin.phone_act(ev, "go_home", "{}")
        home_res = json.loads(home_raw)
        self.assertTrue(home_res["ok"])
        self.assertEqual(home_res["action"], "go_home")
        self.assertEqual(self.mcp.pkg, "com.android.launcher3")
        with self.assertRaises(ValueError):
            await plugin.phone_act(ev, "close_app", "{}")


if __name__ == "__main__":
    unittest.main()

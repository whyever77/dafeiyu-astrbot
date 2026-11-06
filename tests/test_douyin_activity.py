"""Unit and integration tests for M2 & M3 Douyin Browsing, Engagement & Direct Post Publishing.

Covers:
1. Douyin feed browsing (recommend/search), popup dismissal, audio loopback, and douyin_video episode recording
2. Compatibility of douyin_video episodes with df_interest and Workshop source citation
3. Douyin engagement proposals (like, comment, follow, favorite) across approval / auto_low_risk / auto_public modes & daily quotas
4. Douyin post creation (df_douyin_create_post): path traversal rejection, mandatory owner approval, and direct「发布」tap upon owner consent
"""
from __future__ import annotations

import asyncio
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
dy_mod = types.SimpleNamespace(**main.DouyinController.__init__.__globals__)
PhoneGate, TransportError = main.PhoneGate, main.TransportError


def make_png(color=(20, 40, 80), size=(720, 1280)) -> bytes:
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


class FakeDouyinPhoneMCP:
    def __init__(self):
        self.pkg = "com.ss.android.ugc.aweme"
        self.calls: list[tuple[str, dict]] = []
        self.shot_counter = 0
        self.popup_shown = True

    async def call(self, name: str, args: dict):
        self.calls.append((name, args))
        if name in {"get_current_app", "get_current_package"}:
            return text_res(f"Current package: {self.pkg}")
        if name == "launch_app":
            self.pkg = args.get("package_name", self.pkg)
            return text_res(f"Launched {self.pkg}")
        if name == "get_ui_elements":
            items = [
                {"text": "@非遗手艺人", "resource_id": "com.ss.android.ugc.aweme:id/author", "clickable": True},
                {"text": "古法宣纸制作全过程，历经百道工序", "resource_id": "com.ss.android.ugc.aweme:id/desc", "clickable": False},
                {"text": "12.5w", "resource_id": "com.ss.android.ugc.aweme:id/like_count", "clickable": True},
            ]
            if self.popup_shown:
                items.append({"text": "我知道了", "clickable": True})
            return text_res(json.dumps(items, ensure_ascii=False))
        if name == "tap_element" and args.get("text") == "我知道了":
            self.popup_shown = False
            return text_res("dismissed")
        if name == "get_screenshot":
            self.shot_counter += 1
            color = ((self.shot_counter * 47) % 255, (self.shot_counter * 83) % 255, 120)
            return img_res(make_png(color=color))
        if name == "listen_audio":
            return text_res("解说：宣纸选料讲究青檀皮与沙田稻草")
        return text_res("ok")


class DouyinPolicyTests(unittest.TestCase):
    def test_douyin_post_never_auto_executes(self):
        for mode in ("approval", "auto_low_risk", "auto_public"):
            cfg = main.DEFAULTS | {
                "douyin_enabled": True,
                "douyin_engagement_actions": True,
                "douyin_public_interactions": True,
                "douyin_publish_enabled": True,
                "douyin_interaction_mode": mode,
                "douyin_auto_public": True,
            }
            self.assertFalse(dy_mod.auto_allowed("douyin_post", cfg))
            self.assertEqual(
                dy_mod.auto_allowed("douyin_like", cfg),
                mode in {"auto_low_risk", "auto_public"},
            )
            self.assertEqual(
                dy_mod.auto_allowed("douyin_comment", cfg),
                mode == "auto_public",
            )


class DouyinIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.plugin = main.BiliActivity.__new__(main.BiliActivity)
        self.plugin.folder = Path(self.folder.name)
        self.plugin.store = core.Store(self.folder.name)
        self.plugin.gate = PhoneGate()
        self.plugin._interaction_lock = asyncio.Lock()
        self.plugin.control = Path(self.folder.name, "control.json")
        self.mcp = FakeDouyinPhoneMCP()
        self.plugin.phone = lambda: self.mcp
        self.delivery = types.SimpleNamespace(send=AsyncMock(return_value={"message_id": 501, "verified": True}))
        self.plugin.sender = lambda: self.delivery
        self.plugin.snapshots, self.plugin.run_videos = {}, {}
        self.plugin.live = None
        self.plugin.flush_memory = AsyncMock()
        self.config = main.DEFAULTS | {
            "owner_scope": SCOPE,
            "owner_id": "12345",
            "enabled": True,
            "life_enabled": True,
            "workshop_enabled": True,
            "douyin_enabled": True,
            "douyin_engagement_actions": True,
            "douyin_public_interactions": True,
            "douyin_publish_enabled": True,
            "douyin_interaction_mode": "approval",
            "dnd_ranges": [],
        }
        self.save_cfg()

    def save_cfg(self):
        self.plugin.control.write_text(json.dumps(self.config))

    async def asyncTearDown(self):
        self.plugin.store.close()
        self.folder.cleanup()

    async def test_feed_browsing_popup_dismissal_and_interest_workshop_compatibility(self):
        ev = fixtures.FakeEvent()
        raw = await self.plugin.douyin_feed(ev, mode="recommend", count=1, listen_seconds=5, sample_comments=True)
        res = json.loads(raw)
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 1)
        video = res["videos"][0]
        self.assertEqual(video["author"], "非遗手艺人")
        self.assertIn("古法宣纸", video["caption"])
        self.assertIn("青檀皮", video["audio_transcript"])
        self.assertFalse(self.mcp.popup_shown)
        ep_id = video["episode_id"]

        # 1. Can record agent interest from douyin_video episode
        int_raw = await self.plugin.interest(ev, ep_id, "传统非遗造纸", 1, "工序很讲究，很有质感")
        int_res = json.loads(int_raw)
        self.assertTrue(int_res["saved"])

        # 2. Can cite douyin_video episode in Workshop
        goal = self.plugin.store.life.create_goal(SCOPE, "goal-dy-1", "研究传统宣纸工艺", "抖音探索", "写篇笔记")
        ws = self.plugin.workshop_store()
        work = ws.create(SCOPE, goal["id"], "宣纸笔记", "req-dy-1")
        body = (
            f"## 资料观察\n抖音创作者介绍了青檀皮与沙田稻草选料 [E:{ep_id}]。\n\n"
            "## 我的想法\n百道工序凝聚了匠心。\n\n"
            "## 待核实\n具体蒸煮时长还需对比更多资料。"
        )
        ws.revise(SCOPE, work["id"], body, [ep_id], 0, "rev-dy-1")
        chk = ws.check(SCOPE, work["id"])
        self.assertTrue(chk["passed"])

    async def test_engagement_approval_auto_low_risk_and_quota(self):
        ev = fixtures.FakeEvent(event_id="msg-like-1")
        # In approval mode, like creates an offered proposal and sends QQ message
        prop_raw = await self.plugin.douyin_propose(
            ev, "like", json.dumps({"target_author": "非遗手艺人"}), "很喜欢这个手艺视频"
        )
        prop = json.loads(prop_raw)
        self.assertEqual(prop["status"], "awaiting_user")
        self.delivery.send.assert_awaited_once()

        # Owner approves in QQ
        approve_ev = fixtures.FakeEvent(event_id="owner-approve-1", text="批准")
        await self.plugin.incoming(approve_ev)
        approve_ev.send.assert_awaited_once()
        reply_text = approve_ev.send.call_args[0][0]
        self.assertIn("抖音上的操作已经点好并截屏确认了", reply_text)

        # Switch to auto_low_risk: like executes immediately without waiting for owner reply
        self.config["douyin_interaction_mode"] = "auto_low_risk"
        self.config["douyin_max_likes_per_day"] = 2
        self.save_cfg()
        ev2 = fixtures.FakeEvent(event_id="msg-like-2")
        auto_raw = await self.plugin.douyin_propose(
            ev2, "like", json.dumps({"target_author": "另一个创作者"}), "顺手点个赞"
        )
        auto_res = json.loads(auto_raw)
        self.assertEqual(auto_res["authorization"], "owner_configuration")
        self.assertTrue(auto_res["verified"])

        # Third like hits daily quota (2/2)
        ev3 = fixtures.FakeEvent(event_id="msg-like-3")
        quota_raw = await self.plugin.douyin_propose(
            ev3, "like", json.dumps({"target_author": "第三个创作者"}), "再点个赞"
        )
        quota_res = json.loads(quota_raw)
        self.assertFalse(quota_res["proposal_created"])
        self.assertIn("已达上限", quota_res["reason"])

    async def test_create_post_path_guard_and_direct_publish_on_owner_approval(self):
        # 1. Path outside controlled folder is rejected
        outside = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        outside.write(make_png())
        outside.close()
        try:
            ev_bad = fixtures.FakeEvent(event_id="post-bad")
            with self.assertRaises(PermissionError):
                await self.plugin.douyin_create_post(
                    ev_bad,
                    title="越界测试",
                    caption="不应该允许外部路径",
                    media_paths=json.dumps([outside.name]),
                    tags=json.dumps(["测试"]),
                )
        finally:
            Path(outside.name).unlink(missing_ok=True)

        # 2. Valid screenshot inside store folder stages cleanly and ALWAYS awaits owner approval
        self.config["douyin_interaction_mode"] = "auto_public"
        self.config["douyin_auto_public"] = True
        self.save_cfg()

        screens_dir = Path(self.folder.name) / "screens"
        screens_dir.mkdir(parents=True, exist_ok=True)
        valid_img = screens_dir / "shot1.png"
        valid_img.write_bytes(make_png(color=(120, 60, 200)))

        ev_post = fixtures.FakeEvent(event_id="post-ok-1")
        post_raw = await self.plugin.douyin_create_post(
            ev_post,
            title="今日看宣纸手艺有感",
            caption="记录一下在虚拟手机上看到的传统造纸工艺～",
            media_paths=json.dumps([str(valid_img)]),
            tags=json.dumps(["非遗", "大肥鱼日常"]),
            reason="想发条作品记录下今天学到的东西",
        )
        post_res = json.loads(post_raw)
        self.assertEqual(post_res["status"], "awaiting_user")
        post_id = post_res["post_id"]
        proposal_id = post_res["proposal_id"]

        # Unapproved direct execution is blocked
        dy = self.plugin.douyin_controller()
        with self.assertRaises(PermissionError):
            await dy.execute_publish_post(SCOPE, self.config, post_id, proposal_id=proposal_id)

        # Owner replies "批准发布" -> executes publish and taps「发布」directly
        self.mcp.calls.clear()
        approve_ev = fixtures.FakeEvent(event_id="owner-approve-post", text="批准发布")
        await self.plugin.incoming(approve_ev)
        approve_ev.send.assert_awaited_once()
        msg = approve_ev.send.call_args[0][0]
        self.assertIn("直接点「发布」发出去啦", msg)

        # Verify「发布」button was tapped directly on the phone
        self.assertIn(("tap_element", {"device_id": "emulator-5554", "text": "发布"}), self.mcp.calls)
        saved_post = dy.get_post(SCOPE, post_id)
        self.assertEqual(saved_post["status"], "published")


if __name__ == "__main__":
    unittest.main()

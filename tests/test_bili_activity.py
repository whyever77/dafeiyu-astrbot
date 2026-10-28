"""Activity/consent regression tests. Fakes do not establish live QQ acceptance.

Run: .venv/bin/python tests/test_bili_activity.py
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin/astrbot_plugin_bili_activity"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


package = types.ModuleType("bili_activity_test")
package.__path__ = [str(PLUGIN)]
sys.modules[package.__name__] = package
core = load_module(package.__name__ + ".core", PLUGIN / "core.py")


class Filter:
    EventMessageType = types.SimpleNamespace(ALL="ALL")

    def __getattr__(self, name):
        return lambda *a, **kw: lambda fn: fn


class FunctionTool:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class ToolSet:
    def __init__(self, tools=None):
        self.tools = list(tools or [])

    def names(self):
        return [t.name for t in self.tools]

    def add_tool(self, tool):
        self.tools.append(tool)

    def remove_tool(self, name):
        self.tools = [t for t in self.tools if t.name != name]


def load_plugin():
    class Star:
        def __init__(self, context):
            self.context = context

    attrs = {
        "astrbot": {},
        "astrbot.api": {"logger": logging.getLogger("bili-test")},
        "astrbot.api.event": {"AstrMessageEvent": object, "filter": Filter()},
        "astrbot.api.provider": {"ProviderRequest": object},
        "astrbot.api.star": {"Context": object, "Star": Star,
                             "StarTools": object, "register": lambda *a, **kw: lambda cls: cls},
        "astrbot.core": {},
        "astrbot.core.agent": {},
        "astrbot.core.agent.tool": {"FunctionTool": FunctionTool, "ToolSet": ToolSet},
    }
    modules = {}
    for name, values in attrs.items():
        modules[name] = types.ModuleType(name)
        vars(modules[name]).update(values)
    with patch.dict(sys.modules, modules):
        return load_module(package.__name__ + ".main", PLUGIN / "main.py")


main = load_plugin()
SCOPE = "qq:FriendMessage:12345"
OTHER_SCOPE = "qq:GroupMessage:12345"


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = core.Store(self.folder.name)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def proposal(self, **kwargs):
        row, _ = self.store.propose(SCOPE, "12345", "follow", kwargs or {"uid": 99})
        return row

    def offer(self, row):
        self.store.offered(row["id"], {"message_id": 10})

    def test_approval_requires_delivered_offer_same_actor_same_scope(self):
        row = self.proposal()
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "e1"))
        with self.assertRaises(ValueError):
            self.store.offered(row["id"], {"verified": True})
        self.offer(row)
        self.assertIsNone(self.store.decide(OTHER_SCOPE, "12345", "可以", "e1"))
        self.assertIsNone(self.store.decide(SCOPE, "stranger", "可以", "e1"))
        # Re-delivery of a pre-offer message must not turn into new consent.
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "e1"))
        approved = self.store.decide(SCOPE, "12345", "可以", "e2")
        self.assertEqual(approved["status"], "executing")
        self.assertEqual(approved["args"], {"uid": 99})

    def test_refusal_never_becomes_executable(self):
        row = self.proposal()
        self.offer(row)
        self.assertEqual(self.store.decide(SCOPE, "12345", "算了", "e1")["status"], "rejected")
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "e2"))

    def test_duplicate_wakeup_executes_at_most_once(self):
        row = self.proposal()
        self.offer(row)
        self.assertIsNotNone(self.store.decide(SCOPE, "12345", "可以", "e1"))
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "e1"))
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "e2"))
        self.store.finish_proposal(row["id"], {"verified": True})
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "e3"))

    def test_duplicate_old_qq_answer_cannot_approve_new_proposal(self):
        first = self.proposal()
        self.offer(first)
        self.store.decide(SCOPE, "12345", "可以", "same-event")
        self.store.finish_proposal(first["id"], {"verified": True})
        second = self.proposal(uid=101)
        self.offer(second)
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "same-event"))
        self.assertIsNotNone(self.store.decide(SCOPE, "12345", "可以", "new-event"))

    def test_expired_offer_does_not_execute(self):
        with patch.object(core.time, "time", return_value=100):
            row, _ = self.store.propose(SCOPE, "12345", "follow", {"uid": 99}, ttl=20)
            self.offer(row)
        with patch.object(core.time, "time", return_value=121):
            self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "late"))
            new, fresh = self.store.propose(SCOPE, "12345", "follow", {"uid": 100})
            self.assertTrue(fresh)
            self.assertNotEqual(row["id"], new["id"])

    def test_delayed_answer_created_before_offer_is_not_consent(self):
        with patch.object(core.time, "time", return_value=100):
            row = self.proposal()
            self.offer(row)
        with patch.object(core.time, "time", return_value=110):
            self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "delayed", event_time=90))
            self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "delayed", event_time=110))
            self.assertIsNotNone(self.store.decide(SCOPE, "12345", "可以", "new", event_time=110))

    def test_new_request_can_propose_reversible_action_again(self):
        first, _ = self.store.propose(SCOPE, "12345", "follow", {"uid": 99}, request_key="old-run")
        self.offer(first)
        self.store.decide(SCOPE, "12345", "可以", "old-answer")
        self.store.finish_proposal(first["id"], {"verified": True})
        second, fresh = self.store.propose(SCOPE, "12345", "follow", {"uid": 99}, request_key="new-run")
        self.assertTrue(fresh)
        self.assertNotEqual(first["id"], second["id"])

    def test_empty_or_unknown_video_identity_is_rejected(self):
        for cid in (None, "", 0, "0"):
            with self.subTest(cid=cid), self.assertRaises(ValueError):
                self.store.video(SCOPE, {"bvid": "BV1", "cid": cid})

    def test_restarted_memory_write_keeps_ambiguous_result(self):
        self.store.queue_memory(SCOPE, "own preference", "ambiguous-memory")
        self.store.db.execute("UPDATE memories SET status='sending'")
        self.store.close()
        self.store = core.Store(self.folder.name)
        self.assertEqual(self.store.db.execute("SELECT status FROM memories").fetchone()[0], "uncertain")

    def test_args_are_frozen_before_model_can_change_them(self):
        args = {"uid": 99}
        row, _ = self.store.propose(SCOPE, "12345", "follow", args)
        args["uid"] = 101
        self.offer(row)
        self.assertEqual(self.store.decide(SCOPE, "12345", "可以", "e")["args"], {"uid": 99})

    def test_repeated_proposal_after_success_does_not_repeat_write(self):
        row = self.proposal()
        self.offer(row)
        self.store.decide(SCOPE, "12345", "可以", "e")
        self.store.finish_proposal(row["id"], {"verified": True})
        repeated, fresh = self.store.propose(SCOPE, "12345", "follow", {"uid": 99})
        self.assertFalse(fresh)
        self.assertEqual(repeated["id"], row["id"])

    def test_restart_preserves_ambiguous_write_and_send(self):
        row = self.proposal()
        self.offer(row)
        self.store.decide(SCOPE, "12345", "可以", "e")
        self.assertTrue(self.store.delivery(SCOPE, "send", "screenshot", "hash", {}))
        self.store.close()
        self.store = core.Store(self.folder.name)
        self.assertEqual(self.store.db.execute("SELECT status FROM proposals").fetchone()[0], "uncertain")
        self.assertEqual(self.store.db.execute("SELECT status FROM deliveries").fetchone()[0], "uncertain")
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "retry"))
        self.assertFalse(self.store.delivery(SCOPE, "send", "screenshot", "hash", {}))

    def test_automatic_duplicate_screen_suppressed_but_new_explicit_request_allowed(self):
        self.assertTrue(self.store.delivery(SCOPE, "auto1", "screenshot", "hash", {}, True))
        self.store.delivered("auto1", {"message_id": 1})
        self.assertFalse(self.store.delivery(SCOPE, "auto2", "screenshot", "hash", {}, True))
        self.assertTrue(self.store.delivery(SCOPE, "manual1", "screenshot", "hash", {}, False))
        self.assertFalse(self.store.delivery(SCOPE, "manual1", "screenshot", "different", {}, False))

    def test_private_and_group_video_history_are_isolated(self):
        self.store.video(SCOPE, {"bvid": "BV1", "cid": 1, "agent_thought": "my interest"})
        self.assertEqual(self.store.history(OTHER_SCOPE), [])
        self.assertEqual(self.store.history(SCOPE)[0]["agent_thought"], "my interest")
        self.store.queue_memory(SCOPE, "agent interest", "memory-1")
        self.store.queue_memory(SCOPE, "must not overwrite", "memory-1")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memories").fetchone()[0], 1)

    def test_dnd_wraps_midnight_in_shanghai(self):
        tz = ZoneInfo("Asia/Shanghai")
        for hour, minute in ((23, 0), (1, 0), (7, 59)):
            self.assertTrue(core.quiet_now([["23:00", "08:00"]], datetime(2026, 10, 20, hour, minute, tzinfo=tz)))
        for hour, minute in ((22, 59), (8, 0)):
            self.assertFalse(core.quiet_now([["23:00", "08:00"]], datetime(2026, 10, 20, hour, minute, tzinfo=tz)))

    def test_ambiguous_messages_are_not_consents(self):
        for value in ("可以吗", "你觉得可以吗", "如果可以", "引用：可以", "不可以", ""):
            self.assertIsNone(core.decision(value))

    def test_expanded_consent_words(self):
        for value in ("批", "批准", "批准点赞", "批准执行", "确认", "确认执行", "同意执行"):
            self.assertEqual(core.decision(value), "approve")
        for value in ("拒绝", "不批准", "先不要", "别点"):
            self.assertEqual(core.decision(value), "reject")


class FakeEvent:
    def __init__(self, scope=SCOPE, user="12345", event_id="qq1", run_id=None, text=""):
        self.unified_msg_origin = scope
        self.message_obj = types.SimpleNamespace(message_id=event_id, message_str=text)
        self.message_str = text
        self._user, self._extra = user, {"df_run_id": run_id}
        self._stopped = False
        self.send = AsyncMock()

    def get_sender_id(self):
        return self._user

    def get_extra(self, key):
        return self._extra.get(key)

    def stop_event(self):
        self._stopped = True

    def is_stopped(self):
        return self._stopped

    def plain_result(self, value):
        return value


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.plugin = main.BiliActivity.__new__(main.BiliActivity)
        self.plugin.folder = Path(self.folder.name)
        self.plugin.store = core.Store(self.folder.name)
        self.plugin.control = Path(self.folder.name, "control.json")
        self.config = main.DEFAULTS | {"owner_scope": SCOPE, "owner_id": "12345", "enabled": True, "dnd_ranges": []}
        self.plugin.control.write_text(json.dumps(self.config))
        self.plugin.snapshots, self.plugin.run_videos = {}, {}
        self.plugin.live = None
        self.delivery = types.SimpleNamespace(send=AsyncMock(return_value={"message_id": 101, "verified": True}))
        self.plugin.sender = lambda: self.delivery

    async def asyncTearDown(self):
        self.plugin.store.close()
        self.folder.cleanup()

    def snapshot(self, age=0, owner=None):
        shot = types.SimpleNamespace(path="/cloud/screenshot.jpg", fingerprint="pixels", captured_at=time.time() - age,
                                     metadata=lambda: {"captured_at": time.time() - age})
        self.plugin.snapshots["shot"] = (shot, SCOPE, owner)
        return shot

    async def test_share_requires_current_scope_and_activity_ownership(self):
        self.snapshot(owner="run1")
        with self.assertRaises(PermissionError):
            await self.plugin.share(FakeEvent(run_id="run2"), "shot", "hi")
        with self.assertRaises(PermissionError):
            await self.plugin.share(FakeEvent(scope=OTHER_SCOPE, run_id="run1"), "shot", "hi")
        self.delivery.send.assert_not_called()

    async def test_share_rejects_stale_image_and_preserves_no_send(self):
        self.snapshot(age=61)
        with self.assertRaises(ValueError):
            await self.plugin.share(FakeEvent(), "shot", "old")
        self.delivery.send.assert_not_called()

    async def test_unknown_send_is_never_retried_on_duplicate_call(self):
        self.snapshot()
        self.delivery.send.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.plugin.share(FakeEvent(), "shot", "hi")
        result = await self.plugin.share(FakeEvent(), "shot", "hi")
        self.assertEqual(result["status"], "duplicate_suppressed")
        self.assertEqual(self.delivery.send.await_count, 1)
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM deliveries").fetchone()[0], "uncertain")

    async def test_share_returns_receipt_and_browsing_can_continue(self):
        self.snapshot(owner="run")
        receipt = await self.plugin.share(FakeEvent(run_id="run"), "shot", "刚看到的")
        self.assertEqual(receipt["message_id"], 101)
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM deliveries").fetchone()[0], "sent")
        # Sharing does not stop an event or consume the active lease/run identity.
        event = FakeEvent(run_id="run")
        self.assertFalse(event.is_stopped())

    async def test_media_identity_mismatch_cannot_enter_history(self):
        backend = types.SimpleNamespace(json_call=AsyncMock(return_value={"identity": {"bvid": "BVother", "cid": 2}}))
        self.plugin.bili = lambda *args: backend
        with self.assertRaises(ValueError):
            await self.plugin.watch(FakeEvent(), "BVexpected", "1")
        self.assertEqual(self.plugin.store.history(SCOPE), [])

    async def test_asr_only_runs_when_subtitles_absent_and_partial_frames_survive(self):
        payload = {"identity": {"bvid": "BVexpected", "cid": 1}, "range": [0, 120],
                   "subtitles": [{"from": 0, "to": 20, "content": "字幕"}],
                   "frames": [{"at": i, "data": base64.b64encode(b"frame").decode()} for i in range(5)],
                   "audios": [{"start": 0, "end": 60, "data": base64.b64encode(b"audio").decode()}]}
        self.plugin.bili = lambda *args: types.SimpleNamespace(json_call=AsyncMock(return_value=payload))
        face = types.SimpleNamespace(_vl_describe=AsyncMock(side_effect=["first four observed", TimeoutError()]),
                                     _asr_file=AsyncMock())
        self.plugin.face_plugin = lambda: face
        result = json.loads(await self.plugin.watch(FakeEvent(), "BVexpected", "1"))
        self.assertEqual(result["observations"][0]["text"], "first four observed")
        self.assertEqual(len(result["notes"]), 1)
        face._asr_file.assert_not_called()
        self.assertEqual(self.plugin.store.history(SCOPE)[0]["subtitles"], payload["subtitles"])

    async def test_tool_result_contains_image_without_sending_qq(self):
        path = Path(self.folder.name, "screen.jpg")
        path.write_bytes(b"image-content")
        shot = types.SimpleNamespace(path=str(path), captured_at=time.time(), mime_type="image/jpeg", video=None, video_verified=False)
        self.plugin.capture = AsyncMock(return_value=("new-shot", shot))
        result = await self.plugin.screen(FakeEvent())
        self.assertEqual([item.type for item in result.content], ["text", "image"])
        self.assertFalse(json.loads(result.content[0].text)["qq_sent"])
        self.delivery.send.assert_not_called()

    async def test_foreground_phone_wrapper_respects_actual_tool_schema(self):
        original = types.SimpleNamespace(name="list_devices", description="devices",
                                         parameters={"type": "object", "properties": {}}, active=True,
                                         mcp_tool=types.SimpleNamespace(name="list_devices"))
        self.plugin.gate = types.SimpleNamespace(call=AsyncMock(return_value="ok"))
        self.plugin.phone = lambda: "mcp-client"
        tool = main.GuardedPhoneTool(original, self.plugin)
        await tool.call(types.SimpleNamespace(context=types.SimpleNamespace(event=FakeEvent())))
        self.assertEqual(self.plugin.gate.call.await_args.args[2], {})

    async def test_sharing_obeys_switch_changed_after_capture(self):
        self.snapshot()
        self.config["screenshot_enabled"] = False
        self.plugin.control.write_text(json.dumps(self.config))
        with self.assertRaises(PermissionError):
            await self.plugin.share(FakeEvent(), "shot", "current")
        self.delivery.send.assert_not_called()

    async def test_cancellation_preserves_subtitles_before_vision_completes(self):
        payload = {"identity": {"bvid": "BVexpected", "cid": 1}, "range": [0, 120],
                   "subtitles": [{"from": 0, "to": 20, "content": "已取到字幕"}],
                   "frames": [{"at": 1, "data": base64.b64encode(b"frame").decode()}]}
        self.plugin.bili = lambda *args: types.SimpleNamespace(json_call=AsyncMock(return_value=payload))
        started = asyncio.Event()

        async def stalled_vision(frames):
            started.set()
            await asyncio.Event().wait()

        self.plugin.face_plugin = lambda: types.SimpleNamespace(_vl_describe=stalled_vision, _asr_file=AsyncMock())
        task = asyncio.create_task(self.plugin.watch(FakeEvent(), "BVexpected", "1"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        value = self.plugin.store.history(SCOPE)[0]
        self.assertEqual(value["subtitles"], payload["subtitles"])
        self.assertEqual(value["observations"], [])

    async def test_missing_subtitles_invokes_existing_asr(self):
        payload = {"identity": {"bvid": "BVexpected", "cid": 1}, "range": [0, 60],
                   "subtitles": [], "frames": [],
                   "audios": [{"start": 0, "end": 60, "data": base64.b64encode(b"audio").decode()}]}
        self.plugin.bili = lambda *args: types.SimpleNamespace(json_call=AsyncMock(return_value=payload))
        face = types.SimpleNamespace(_vl_describe=AsyncMock(), _asr_file=AsyncMock(return_value="真实音轨转写"))
        self.plugin.face_plugin = lambda: face
        result = json.loads(await self.plugin.watch(FakeEvent(), "BVexpected", "1", end=60))
        self.assertEqual(face._asr_file.await_count, 1)
        self.assertEqual(result["observations"], [{"kind": "asr", "range": [0, 60], "text": "真实音轨转写"}])

    async def test_video_limit_stops_fourth_video_before_backend(self):
        self.plugin.run_videos["run"] = {"one", "two", "three"}
        backend = types.SimpleNamespace(json_call=AsyncMock())
        self.plugin.bili = lambda *args: backend
        result = await self.plugin.watch(FakeEvent(run_id="run"), "four", "1")
        self.assertIn("上限", result)
        backend.json_call.assert_not_called()

    async def test_oversized_frame_rejected_before_vision_and_subtitles_remain(self):
        payload = {"identity": {"bvid": "BVexpected", "cid": 1}, "range": [0, 60],
                   "subtitles": [{"from": 0, "to": 1, "content": "字幕"}],
                   "frames": [{"at": 0, "data": base64.b64encode(b"x" * 500001).decode()}]}
        self.plugin.bili = lambda *args: types.SimpleNamespace(json_call=AsyncMock(return_value=payload))
        face = types.SimpleNamespace(_vl_describe=AsyncMock(), _asr_file=AsyncMock())
        self.plugin.face_plugin = lambda: face
        with self.assertRaises(ValueError):
            await self.plugin.watch(FakeEvent(), "BVexpected", "1", end=60)
        face._vl_describe.assert_not_called()
        self.assertEqual(self.plugin.store.history(SCOPE)[0]["subtitles"], payload["subtitles"])

    async def test_activity_note_does_not_claim_unread_position(self):
        self.plugin.store.video(SCOPE, {"bvid": "BVexpected", "cid": 1, "range": [10, 60], "observations": []})
        with self.assertRaises(ValueError):
            await self.plugin.activity_note(FakeEvent(), "BVexpected", "1", 600, "自己的感想")

    async def test_activity_note_requires_evidence_within_requested_range(self):
        value = {"bvid": "BVexpected", "cid": 1, "range": [10, 60], "observations": []}
        self.plugin.store.video(SCOPE, value)
        with self.assertRaises(ValueError):
            await self.plugin.activity_note(FakeEvent(), "BVexpected", "1", 30, "自己的感想")
        value["observations"] = [{"kind": "frames", "times": [10, 20], "text": "画面内容"}]
        self.plugin.store.video(SCOPE, value)
        with self.assertRaises(ValueError):
            await self.plugin.activity_note(FakeEvent(), "BVexpected", "1", 30, "自己的感想")
        await self.plugin.activity_note(FakeEvent(), "BVexpected", "1", 20, "自己的感想")
        saved = self.plugin.store.history(SCOPE)[0]
        self.assertEqual(saved["position"], 20)
        self.assertEqual(saved["position_kind"], "analyzed_sample")
        self.assertEqual(saved["preference_owner"], "agent")

    async def test_true_owner_scope_is_required_even_for_background_event(self):
        with self.assertRaises(PermissionError):
            self.plugin.check_owner(FakeEvent(scope=OTHER_SCOPE, run_id="run"))
        with self.assertRaises(PermissionError):
            self.plugin.check_owner(FakeEvent(user="stranger"))

    async def test_malformed_dnd_config_does_not_silently_enable_activity(self):
        self.config["dnd_ranges"] = [["25:00", "08:00"]]
        self.plugin.control.write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):
            self.plugin.cfg()

    async def test_runtime_dnd_blocks_background_share_but_not_explicit_request(self):
        self.snapshot(owner="run")
        with patch.object(main, "quiet_now", return_value=True):
            with self.assertRaises(PermissionError):
                await self.plugin.share(FakeEvent(run_id="run"), "shot", "quiet")
            with self.assertRaises(PermissionError):
                await self.plugin.share_thought(FakeEvent(run_id="run"), "quiet")
            self.delivery.send.assert_not_called()
            self.snapshot(owner=None)
            receipt = await self.plugin.share(FakeEvent(), "shot", "你刚要的截图", passive=True)
            self.assertEqual(receipt["message_id"], 101)

    async def test_scheduler_cancels_own_activity_when_dnd_starts(self):
        live = types.SimpleNamespace(done=lambda: False, cancel=Mock())
        self.plugin.live = live

        async def end_after_iteration(seconds):
            raise asyncio.CancelledError

        with patch.object(main, "quiet_now", return_value=True), patch.object(main.asyncio, "sleep", end_after_iteration):
            with self.assertRaises(asyncio.CancelledError):
                await self.plugin.schedule()
        live.cancel.assert_called_once()

    def test_periodic_interval_stays_within_jitter_bounds(self):
        cfg = main.DEFAULTS | {"check_minutes": 120, "check_jitter_minutes": 60}
        values = [self.plugin.periodic_interval(cfg) for _ in range(200)]
        self.assertTrue(all(60 * 60 <= value <= 180 * 60 for value in values))
        self.assertGreater(max(values) - min(values), 0)

    async def test_scheduler_defers_first_periodic_run(self):
        self.config.update(periodic_enabled=True, one_shot_verified=True,
                           check_minutes=120, check_jitter_minutes=60)
        self.plugin.control.write_text(json.dumps(self.config))
        self.plugin.run_activity = AsyncMock()

        async def end_after_iteration(seconds):
            raise asyncio.CancelledError

        with patch.object(main.asyncio, "sleep", end_after_iteration):
            with self.assertRaises(asyncio.CancelledError):
                await self.plugin.schedule()
        self.plugin.run_activity.assert_not_called()
        self.assertIsNotNone(self.plugin.store.get_setting("next_check_at"))

    async def test_scheduler_does_not_start_periodic_before_acceptance(self):
        self.config.update(periodic_enabled=True, one_shot_verified=False)
        self.plugin.control.write_text(json.dumps(self.config))
        self.plugin.run_activity = AsyncMock()

        async def end_after_iteration(seconds):
            raise asyncio.CancelledError

        with patch.object(main.asyncio, "sleep", end_after_iteration):
            with self.assertRaises(asyncio.CancelledError):
                await self.plugin.schedule()
        self.plugin.run_activity.assert_not_called()

    async def test_scheduler_does_not_start_with_unknown_dnd(self):
        self.config.update(once_at=time.time() - 1, dnd_ranges=None)
        self.plugin.control.write_text(json.dumps(self.config))
        self.plugin.run_activity = AsyncMock()

        async def end_after_iteration(seconds):
            raise asyncio.CancelledError

        with patch.object(main.asyncio, "sleep", end_after_iteration):
            with self.assertRaises(asyncio.CancelledError):
                await self.plugin.schedule()
        self.plugin.run_activity.assert_not_called()

    async def test_native_agent_retains_hooks_persona_and_tool_restrictions(self):
        calls, observed = [], {}
        allowed = FunctionTool(name="df_bili_read", active=True)
        forbidden = FunctionTool(name="raw_account_write", active=True)
        self.plugin.context = types.SimpleNamespace(
            get_config=lambda **kw: {"provider_settings": {"current_config": True}},
            conversation_manager="real-conversation-manager",
            get_llm_tool_manager=lambda: types.SimpleNamespace(get_full_tool_set=lambda: ToolSet([allowed, forbidden])),
        )

        class Runner:
            state = "done"

            async def step_until_done(self, steps):
                calls.append("run")
                observed["steps"] = steps
                yield None

            def get_final_llm_resp(self):
                return types.SimpleNamespace(completion_text="安静结束")

        async def build(**kwargs):
            calls.append("build")
            req = kwargs["req"]
            req.system_prompt = "当前真实人格"
            req.func_tool = ToolSet([allowed])  # Persona permitted tools.
            observed["req"] = req
            observed["config"] = kwargs["config"]
            self.assertFalse(kwargs["apply_reset"])

            async def reset():
                calls.append("reset")
                observed["tool_names"] = req.func_tool.names()

            return types.SimpleNamespace(agent_runner=Runner(), provider_request=req, reset_coro=reset())

        async def hook(event, name, *args):
            calls.append(name)
            if args:
                args[0].system_prompt += "\n当前会话记忆"

        persist = AsyncMock()
        attrs = {
            "astrbot": {}, "astrbot.core": {}, "astrbot.core.agent": {},
            "astrbot.core.astr_main_agent": {
                "MainAgentBuildConfig": lambda **kw: types.SimpleNamespace(**kw),
                "_get_session_conv": AsyncMock(return_value=types.SimpleNamespace(history='[{"role":"user","content":"最近聊天"}]')),
                "build_main_agent": build,
            },
            "astrbot.core.pipeline": {}, "astrbot.core.pipeline.context_utils": {"call_event_hook": hook},
            "astrbot.core.star": {}, "astrbot.core.star.star_handler": {
                "EventType": types.SimpleNamespace(OnWaitingLLMRequestEvent="waiting", OnLLMRequestEvent="request")},
            "astrbot.core.agent.runners": {}, "astrbot.core.agent.runners.base": {"AgentState": types.SimpleNamespace(ERROR="error")},
            "astrbot.core.utils": {}, "astrbot.core.utils.history_saver": {"persist_agent_history": persist},
        }
        modules = {}
        for name, values in attrs.items():
            modules[name] = types.ModuleType(name)
            vars(modules[name]).update(values)
        with patch.dict(sys.modules, modules), patch.object(main, "ProviderRequest", lambda **kw: types.SimpleNamespace(**kw)):
            result = await self.plugin.agent(FakeEvent(run_id="run"), "本轮提示", {"df_bili_read"}, max_steps=12)
        self.assertEqual(result, "安静结束")
        self.assertEqual(calls, ["waiting", "build", "request", "reset", "run"])
        self.assertEqual(observed["tool_names"], ["df_bili_read"])
        self.assertIn("当前真实人格", observed["req"].system_prompt)
        self.assertIn("当前会话记忆", observed["req"].system_prompt)
        self.assertEqual(observed["req"].contexts[0]["content"], "最近聊天")
        self.assertEqual(observed["config"].timezone, "Asia/Shanghai")
        self.assertFalse(observed["config"].add_cron_tools)
        # The conversation was read before a potentially long activity. Saving
        # that old history here would overwrite foreground chat received meanwhile.
        persist.assert_not_awaited()


class ToolImageTrimTests(unittest.TestCase):
    @staticmethod
    def tool_image(path):
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": f"[Image from tool 'get_screenshot', path='{path}']"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            ],
        }

    def test_keeps_only_newest_tool_screenshot_and_preserves_user_images(self):
        contexts = [
            {"role": "user", "content": "最近聊天"},
            self.tool_image("old-1.jpg"),
            self.tool_image("old-2.jpg"),
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "主人发的图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
                ],
            },
            self.tool_image("newest.jpg"),
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "[Image from tool 'get_screenshot', path='no-data.jpg']"},
                ],
            },
        ]
        removed = main.trim_tool_image_history(contexts, keep=1)
        self.assertEqual(removed, 2)
        self.assertEqual(contexts[1]["content"], main.TOOL_IMAGE_PLACEHOLDER)
        self.assertEqual(contexts[2]["content"], main.TOOL_IMAGE_PLACEHOLDER)
        self.assertEqual(contexts[3]["content"][0]["text"], "主人发的图")
        self.assertEqual(contexts[4]["content"][0]["text"].split("path='")[1], "newest.jpg']")
        self.assertIn("no-data.jpg", contexts[5]["content"][0]["text"])

    def test_keep_zero_removes_all_tool_screenshots(self):
        contexts = [self.tool_image("a.jpg"), self.tool_image("b.jpg")]
        self.assertEqual(main.trim_tool_image_history(contexts, keep=0), 2)
        self.assertEqual(contexts, [{"role": "user", "content": main.TOOL_IMAGE_PLACEHOLDER}] * 2)

    def test_provider_message_objects_are_converted_and_trimmed(self):
        class Message:
            def __init__(self, data):
                self.data = data

            def model_dump(self):
                return self.data

        contexts = [
            Message(self.tool_image("old.jpg")),
            Message({"role": "assistant", "content": "继续"}),
            Message(self.tool_image("new.jpg")),
        ]
        trimmed = main.trim_tool_image_contexts(contexts, keep=1)
        self.assertEqual(trimmed[0]["content"], main.TOOL_IMAGE_PLACEHOLDER)
        self.assertEqual(trimmed[1], {"role": "assistant", "content": "继续"})
        self.assertIn("new.jpg", trimmed[2]["content"][0]["text"])

    def test_strip_image_parts_removes_all_images_for_context_retry(self):
        contexts = [
            self.tool_image("old.jpg"),
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "主人发的图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}},
                ],
            },
            {"role": "assistant", "content": "还在"},
        ]
        stripped = main.strip_image_parts(contexts)
        self.assertEqual(len(stripped), 3)
        for message in stripped[:2]:
            self.assertFalse(
                any(
                    isinstance(part, dict) and part.get("type") == "image_url"
                    for part in message["content"]
                )
            )
        self.assertEqual(stripped[1]["content"][0]["text"], "主人发的图")
        self.assertEqual(stripped[2], {"role": "assistant", "content": "还在"})

    def test_non_list_context_is_ignored(self):
        self.assertEqual(main.trim_tool_image_history(None), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

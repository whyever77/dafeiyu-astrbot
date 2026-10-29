"""Exercise the patched community sender class with fake QQ/TTS and temp state."""
from __future__ import annotations
import ast
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_bili_activity as fixtures
import test_bili_contact as contact_tests

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "deploy/patches/proactive_chat"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with patch.dict(sys.modules, {"astrbot": types.ModuleType("astrbot"), "astrbot.api": types.SimpleNamespace(logger=Mock())}):
    bridge = load("test_contact_bridge", PATCH / "contact_bridge.py")
installer = load("test_contact_install", PATCH / "install_contact.py")


class Plain:
    def __init__(self, text):
        self.text = text


class Record:
    def __init__(self, file):
        self.file = file


def actual_class(filename, name, namespace):
    # Isolate only framework imports, not the production sender implementation.
    tree = ast.parse((PATCH / filename).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    future = ast.parse("from __future__ import annotations").body[0]
    exec(compile(ast.Module(body=[future, cls], type_ignores=[]), str(PATCH / filename), "exec"), namespace)
    return namespace[name]


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = contact_tests.ContactIntegrationTests.asyncSetUp
    asyncTearDown = contact_tests.ContactIntegrationTests.asyncTearDown
    save_config = contact_tests.ContactIntegrationTests.save_config
    begin = contact_tests.ContactIntegrationTests.begin

    def setup_bridge(self):
        self.config.update(contact_coordination_enabled=True, contact_owner_quiet_minutes=5)
        self.save_config()
        self.plugin.context = types.SimpleNamespace(dafeiyu_contact_v1=fixtures.main.ContactAPI(self.plugin),
                                                    get_using_tts_provider=lambda **k: None)
        self.community = types.SimpleNamespace(context=self.plugin.context, telemetry=None,
            config_service=types.SimpleNamespace(get_session_config=lambda scope: self.session_cfg),
            session_service=types.SimpleNamespace(get_session_log_str=lambda *args: "test scope"))
        self.session_cfg = {"tts_settings": {"enable_tts": False}, "segmented_reply_settings": {"enable": False}}
        self.event = types.SimpleNamespace(send=AsyncMock(return_value=True), clear_result=Mock())
        namespace = {"coordinated_send": bridge.coordinated_send, "before_dispatch": bridge.before_dispatch,
            "Plain": Plain, "Record": Record, "MessageChain": lambda c: types.SimpleNamespace(chain=c),
            "logger": Mock(), "EventType": None, "is_group_umo": lambda s: False,
            "asyncio": types.SimpleNamespace(sleep=AsyncMock()), "re": __import__("re")}
        self.sender = actual_class("sender_service.py", "SenderService", namespace)(self.community)
        self.path_patch = patch.object(bridge, "control_path", return_value=self.plugin.control)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def status(self):
        return self.plugin.contact_ledger().status(fixtures.SCOPE, self.config)

    async def test_real_sender_segments_count_as_one_report_without_claiming_receipt(self):
        self.setup_bridge()
        self.session_cfg["segmented_reply_settings"] = {"enable": True, "split_mode": "words", "split_words": ["。"]}
        self.sender.calc_interval = AsyncMock(return_value=0)
        self.assertTrue(await self.sender.send_proactive_message(fixtures.SCOPE, "第一句。第二句。", self.event))
        self.assertEqual(self.event.send.await_count, 2)
        row = self.status()["recent"][0]
        self.assertEqual(self.status()["used"], 1)
        self.assertEqual(row["status"], "reported")
        self.assertIsNone(row["message_id"])
        with self.assertRaises(PermissionError):
            await self.plugin.share_thought(self.begin(), "生活侧也想发")
        self.delivery.send.assert_not_awaited()

    async def test_owner_arrival_during_tts_cancels_before_io_without_falling_back_to_text(self):
        self.setup_bridge()
        async def audio(text):
            await self.plugin.incoming(fixtures.FakeEvent(text="等一下", event_id="owner-arrived"))
            return "fake.wav"
        self.session_cfg["tts_settings"]["enable_tts"] = True
        self.plugin.context.get_using_tts_provider = lambda **k: types.SimpleNamespace(get_audio=audio)
        self.assertFalse(await self.sender.send_proactive_message(fixtures.SCOPE, "准备开口", self.event))
        self.event.send.assert_not_awaited()
        self.assertEqual(self.status()["used"], 0)
        self.assertEqual(self.status()["recent"][0]["status"], "cancelled")

    async def test_switch_change_after_generation_stops_before_send(self):
        self.setup_bridge()
        async def decorate(event, components):
            self.config["contact_coordination_enabled"] = False
            self.save_config()
            return components, True
        self.sender.run_decorating_hooks = decorate
        self.assertFalse(await self.sender.send_proactive_message(fixtures.SCOPE, "生成后改开关", self.event))
        self.event.send.assert_not_awaited()
        self.assertEqual(self.status()["used"], 0)

    async def test_cancel_after_dispatch_is_unknown_and_content_is_not_replayed(self):
        self.setup_bridge()
        self.event.send.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.sender.send_proactive_message(fixtures.SCOPE, "只发一次", self.event)
        self.assertEqual(self.status()["recent"][0]["status"], "uncertain")
        self.assertFalse(await self.sender.send_proactive_message(fixtures.SCOPE, "只发一次", self.event))
        self.assertEqual(self.event.send.await_count, 1)

    async def test_missing_or_reloading_coordinator_blocks_and_disabled_keeps_legacy(self):
        self.setup_bridge()
        self.plugin.context.dafeiyu_contact_v1.active = False
        self.assertFalse(await self.sender.send_proactive_message(fixtures.SCOPE, "协调器停了", self.event))
        del self.plugin.context.dafeiyu_contact_v1
        self.assertFalse(await self.sender.send_proactive_message(fixtures.SCOPE, "协调器缺失", self.event))
        self.event.send.assert_not_awaited()
        self.config["contact_coordination_enabled"] = False
        self.save_config()
        self.assertTrue(await self.sender.send_proactive_message(fixtures.SCOPE, "保持旧行为", self.event))
        self.event.send.assert_awaited_once()

    async def test_other_scope_does_not_consume_owner_capacity(self):
        self.setup_bridge()
        self.assertTrue(await self.sender.send_proactive_message("qq:FriendMessage:999", "其他会话", self.event))
        self.assertEqual(self.status()["used"], 0)

    async def test_tts_and_text_share_one_reservation(self):
        self.setup_bridge()
        self.session_cfg["tts_settings"].update(enable_tts=True, always_send_text=True)
        self.plugin.context.get_using_tts_provider = lambda **k: types.SimpleNamespace(get_audio=AsyncMock(return_value="fake.wav"))
        self.assertTrue(await self.sender.send_proactive_message(fixtures.SCOPE, "有语音的分享", self.event))
        self.assertEqual(self.event.send.await_count, 2)
        self.assertEqual(self.status()["used"], 1)

    async def test_generation_preflight_skips_provider_when_quota_exhausted(self):
        self.setup_bridge()
        self.config["contact_daily_limit"] = 0
        self.save_config()
        llm = actual_class("llm_service.py", "LlmService", {"preflight": bridge.preflight})(self.community)
        # This minimal plugin has no provider; reaching generation would raise.
        self.assertEqual(await llm.generate_llm_response(fixtures.SCOPE, {}, [], "hello", 0), (None, ""))
        self.event.send.assert_not_awaited()

    async def test_concurrent_sources_reserve_before_first_await(self):
        self.setup_bridge()
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(chain):
            entered.set()
            await release.wait()
            return True
        self.event.send.side_effect = send
        task = asyncio.create_task(self.sender.send_proactive_message(fixtures.SCOPE, "社区先发", self.event))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            with self.assertRaises(PermissionError):
                await self.plugin.share_thought(self.begin(), "生活也想说")
            self.delivery.send.assert_not_awaited()
        finally:
            release.set()
            self.assertTrue(await task)
        self.assertEqual(self.status()["used"], 1)

    async def test_owner_reply_after_first_segment_stops_remaining_segments(self):
        self.setup_bridge()
        self.session_cfg["segmented_reply_settings"] = {"enable": True, "split_mode": "words", "split_words": ["。"]}
        self.sender.calc_interval = AsyncMock(return_value=0)
        async def first(chain):
            self.plugin.contact_ledger().owner_message(fixtures.SCOPE, "reply")
            return True
        self.event.send.side_effect = first
        self.assertFalse(await self.sender.send_proactive_message(fixtures.SCOPE, "第一句。第二句。", self.event))
        self.event.send.assert_awaited_once()
        self.assertEqual(self.status()["used"], 1)
        self.assertEqual(self.status()["recent"][0]["status"], "uncertain")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "plugin"
        (self.root / "core/services").mkdir(parents=True)
        (self.root / "core/adapters").mkdir()
        for rel, src in installer.FILES.items():
            if src == "contact_bridge.py":
                continue
            text = (PATCH / src).read_text(encoding="utf-8")
            if src == "sender_service.py":
                text = text.replace("\nfrom .contact_bridge import coordinated_send, before_dispatch\n", "")
                text = text.replace("    @coordinated_send\n", "")
                text = text.replace("        before_dispatch(self.plugin, session_id)\n\n", "")
            else:
                text = text.replace("from .contact_bridge import preflight\n", "")
                text = text.replace('        if not preflight(plugin, session_id):\n            return None, ""\n', "")
            (self.root / rel).write_text(text, encoding="utf-8")
        (self.root / "core/adapters/astrbot_provider.py").write_bytes((PATCH / "astrbot_provider.py").read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def test_pinned_hashes_check_without_writes_install_backup_and_idempotency(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*.py")}
        _, plan = installer.check(self.root)
        self.assertEqual([p["state"] for p in plan], ["install"] * 3)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*.py")})
        backup = Path(installer.install(self.root))
        for rel, content in before.items():
            if "services" in rel.parts:
                self.assertEqual((backup / rel).read_bytes(), content)
        self.assertIsNone(installer.install(self.root))

    def test_unrecognized_source_is_refused_without_backup_or_overwrite(self):
        target = self.root / "core/services/sender_service.py"
        target.write_text("# different upstream revision\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            installer.install(self.root)
        self.assertEqual(target.read_text(), "# different upstream revision\n")
        self.assertEqual(list(self.root.glob(".dafeiyu*")), [])
        self.assertFalse((self.root / "core/services/contact_bridge.py").exists())

    def test_copy_failure_restores_original_files(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*.py")}
        original = installer.shutil.copyfile
        def fail_bridge(src, dst):
            if Path(src).name == "contact_bridge.py":
                raise OSError("test interruption")
            return original(src, dst)
        with patch.object(installer.shutil, "copyfile", side_effect=fail_bridge), self.assertRaises(OSError):
            installer.install(self.root)
        for rel, data in before.items():
            self.assertEqual((self.root / rel).read_bytes(), data)
        self.assertFalse((self.root / "core/services/contact_bridge.py").exists())


if __name__ == "__main__":
    unittest.main()

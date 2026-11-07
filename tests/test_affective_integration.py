# -*- coding: utf-8 -*-
"""Affective Core Milestone 2 (M2) integration tests.

Tests physical externalization channels:
1. TTS voice dynamics (dashscope_tts instruction, speech_rate, pitch_rate)
2. Bubble layout limits (chat_layout group_bubbles)
3. Face conversion mood gating (face_convert cheerful face suppression and face limits)
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

PLUGIN_ROOT = os.path.join(REPO_ROOT, "plugin")
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

# Minimal astrbot stubs for offline execution
for mod_name in [
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.api.star",
    "astrbot.api.provider",
    "astrbot.core",
    "astrbot.core.message",
    "astrbot.core.message.components",
    "astrbot.core.utils",
    "astrbot.core.utils.astrbot_path",
    "astrbot.core.utils.datetime_utils",
    "astrbot.core.provider",
    "astrbot.core.provider.entities",
    "astrbot.core.provider.provider",
    "astrbot.core.provider.register",
    "astrbot.core.provider.sources",
    "astrbot.core.star",
    "astrbot.core.star.session_llm_manager",
]:
    if mod_name not in sys.modules:
        m = types.ModuleType(mod_name)
        m.__path__ = []
        sys.modules[mod_name] = m

sys.modules["astrbot.api.provider"].ProviderRequest = object

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

sys.modules["astrbot.core.agent"] = types.ModuleType("astrbot.core.agent")
sys.modules["astrbot.core.agent"].__path__ = []
agent_tool = types.ModuleType("astrbot.core.agent.tool")
agent_tool.FunctionTool = FunctionTool
agent_tool.ToolSet = ToolSet
sys.modules["astrbot.core.agent.tool"] = agent_tool

# Mock components
class MockPlain:
    def __init__(self, text: str):
        self.text = text

class MockFace:
    def __init__(self, id: int):
        self.id = id

class MockRecord:
    def __init__(self, file: str = "mock.wav", text: str = ""):
        self.file = file
        self.text = text

class MockTTSProvider:
    def __init__(self, provider_config: dict, provider_settings: dict):
        self.provider_config = provider_config
        self.provider_settings = provider_settings
        self._model = provider_config.get("model", "")
        self.request_headers = {"User-Agent": "test"}

    def set_model(self, m: str):
        self._model = m

    def get_model(self) -> str:
        return self._model

sys.modules["astrbot.core.message.components"].Plain = MockPlain
sys.modules["astrbot.core.message.components"].Face = MockFace
sys.modules["astrbot.core.message.components"].Record = MockRecord
sys.modules["astrbot.core.message.components"].At = object
sys.modules["astrbot.core.message.components"].Image = object
sys.modules["astrbot.core.message.components"].Reply = object
sys.modules["astrbot.core.message.components"].Video = object
sys.modules["astrbot.core.utils.astrbot_path"].get_astrbot_temp_path = lambda: os.path.join(REPO_ROOT, ".tmp_tts_test")
sys.modules["astrbot.core.utils.datetime_utils"].generate_timestamp_id = lambda: "test_stamp"
sys.modules["astrbot.core.star.session_llm_manager"].SessionServiceManager = object
class _Star:
    def __init__(self, *a, **k):
        self.context = a[0] if a else None

sys.modules["astrbot.api.star"].Context = object
sys.modules["astrbot.api.star"].Star = _Star
sys.modules["astrbot.api.star"].StarTools = types.SimpleNamespace(get_data_dir=lambda *a, **k: "/tmp")
sys.modules["astrbot.api.star"].register = lambda *a, **k: (lambda cls: cls)
class _Filter:
    EventMessageType = types.SimpleNamespace(ALL="ALL")

    def __getattr__(self, _):
        def deco(*a, **k):
            def wrap(fn):
                return fn
            return wrap
        return deco

sys.modules["astrbot.api.event"].AstrMessageEvent = object
sys.modules["astrbot.api.event"].filter = _Filter()
sys.modules["astrbot.api"].logger = MagicMock()
sys.modules["astrbot.core.provider.entities"].ProviderType = types.SimpleNamespace(TEXT_TO_SPEECH="tts")
sys.modules["astrbot.core.provider.provider"].TTSProvider = MockTTSProvider
sys.modules["astrbot.core.provider.register"].register_provider_adapter = lambda *a, **k: (lambda cls: cls)

# Mock dashscope SDK if not installed
import importlib.machinery

if "dashscope" not in sys.modules:
    dash_mod = types.ModuleType("dashscope")
    dash_mod.__spec__ = importlib.machinery.ModuleSpec("dashscope", None)
    dash_mod.api_key = ""
    audio_mod = types.ModuleType("dashscope.audio")
    audio_mod.__spec__ = importlib.machinery.ModuleSpec("dashscope.audio", None)
    tts_v2_mod = types.ModuleType("dashscope.audio.tts_v2")
    tts_v2_mod.__spec__ = importlib.machinery.ModuleSpec("dashscope.audio.tts_v2", None)
    tts_v2_mod.AudioFormat = types.SimpleNamespace(WAV_24000HZ_MONO_16BIT="wav")
    tts_v2_mod.SpeechSynthesizer = MagicMock
    audio_mod.tts_v2 = tts_v2_mod
    dash_mod.audio = audio_mod
    aigc_mod = types.ModuleType("dashscope.aigc")
    aigc_mod.__spec__ = importlib.machinery.ModuleSpec("dashscope.aigc", None)
    mm_mod = types.ModuleType("dashscope.aigc.multimodal_conversation")
    mm_mod.__spec__ = importlib.machinery.ModuleSpec("dashscope.aigc.multimodal_conversation", None)
    mm_mod.MultiModalConversation = MagicMock()
    aigc_mod.multimodal_conversation = mm_mod
    dash_mod.aigc = aigc_mod
    sys.modules["dashscope"] = dash_mod
    sys.modules["dashscope.audio"] = audio_mod
    sys.modules["dashscope.audio.tts_v2"] = tts_v2_mod
    sys.modules["dashscope.aigc"] = aigc_mod
    sys.modules["dashscope.aigc.multimodal_conversation"] = mm_mod
elif getattr(sys.modules["dashscope"], "__spec__", None) is None:
    sys.modules["dashscope"].__spec__ = importlib.machinery.ModuleSpec("dashscope", None)


def _ensure_runtime_stubs():
    dash = sys.modules.get("dashscope")
    if dash is not None and getattr(dash, "__spec__", None) is None:
        dash.__spec__ = importlib.machinery.ModuleSpec("dashscope", None)
    for sub in ["dashscope.audio", "dashscope.audio.tts_v2", "dashscope.aigc", "dashscope.aigc.multimodal_conversation"]:
        if sub in sys.modules and getattr(sys.modules[sub], "__spec__", None) is None:
            sys.modules[sub].__spec__ = importlib.machinery.ModuleSpec(sub, None)

    star = sys.modules.get("astrbot.api.star")
    if star is not None and not hasattr(star, "StarTools"):
        star.StarTools = types.SimpleNamespace(get_data_dir=lambda *a, **k: "/tmp")

    ev = sys.modules.get("astrbot.api.event")
    if ev is not None:
        f = getattr(ev, "filter", None)
        if f is None or not hasattr(f, "EventMessageType"):
            class _Filter:
                EventMessageType = types.SimpleNamespace(ALL="ALL")
                def __getattr__(self, _):
                    return lambda *a, **k: (lambda fn: fn)
            ev.filter = _Filter()
        elif not hasattr(f.EventMessageType, "ALL"):
            f.EventMessageType.ALL = "ALL"


def setUpModule():
    _ensure_runtime_stubs()

# Import target components
from astrbot_plugin_affective_core import get_bridge, set_bridge
import astrbot_plugin_affective_core as _affective_pkg
for _name in ("data", "data.plugins"):
    if _name not in sys.modules:
        _mod = types.ModuleType(_name)
        _mod.__path__ = []
        sys.modules[_name] = _mod
sys.modules["data.plugins.astrbot_plugin_affective_core"] = _affective_pkg
from deploy.patches import chat_layout

# Load dashscope_tts as astrbot.core.provider.sources.dashscope_tts
tts_path = os.path.join(REPO_ROOT, "deploy", "patches", "dashscope_tts.py")
spec = importlib.util.spec_from_file_location("astrbot.core.provider.sources.dashscope_tts", tts_path)
dash_tts_mod = importlib.util.module_from_spec(spec)
sys.modules["astrbot.core.provider.sources.dashscope_tts"] = dash_tts_mod
spec.loader.exec_module(dash_tts_mod)
ProviderDashscopeTTSAPI = dash_tts_mod.ProviderDashscopeTTSAPI


class MockBridge:
    def __init__(self, profile: dict | None = None):
        self._profile = profile or {}

    def expression_profile(self, scope: str | None = None, *, now: float | None = None) -> dict:
        return dict(self._profile)


class TTSChannelTests(unittest.TestCase):
    def setUp(self):
        set_bridge(None)
        self.cfg = {
            "model": "qwen-audio-3.1-tts-instruct-flash",
            "api_key": "test_key",
            "dashscope_tts_voice": "Cherry",
            "dashscope_instruction": "少女音",
            "dashscope_speech_rate": 1.0,
            "dashscope_pitch_rate": 1.0,
        }
        self.provider = ProviderDashscopeTTSAPI(self.cfg, {})

    def tearDown(self):
        set_bridge(None)

    def test_default_style_instruction_without_bridge(self):
        style = self.provider._style_instruction()
        self.assertEqual(style, "少女音")

    def test_dynamic_style_instruction_with_bridge(self):
        bridge = MockBridge({
            "tts_instruction": "语气轻快元气，带着藏不住的小得意",
            "speech_rate": 1.06,
            "pitch_rate": 1.03,
        })
        set_bridge(bridge)

        style = self.provider._style_instruction(
            instruction="语气轻快元气，带着藏不住的小得意，少女音",
            speech_rate=1.06,
            pitch_rate=1.03,
        )
        self.assertIn("语气轻快元气", style)
        self.assertIn("少女音", style)
        self.assertIn("语速约为常速的 1.06 倍", style)
        self.assertIn("音调比默认略高", style)

    def test_concurrency_instance_immutability(self):
        """Dynamic kwargs must not mutate instance self attributes."""
        orig_inst = self.provider.instruction
        orig_sr = self.provider.speech_rate
        orig_pr = self.provider.pitch_rate

        self.provider._style_instruction(
            instruction="赌气撒娇",
            speech_rate=0.92,
            pitch_rate=0.97,
        )

        self.assertEqual(self.provider.instruction, orig_inst)
        self.assertEqual(self.provider.speech_rate, orig_sr)
        self.assertEqual(self.provider.pitch_rate, orig_pr)

    def test_call_qwen_tts_passes_dynamic_instructions(self):
        with patch.object(dash_tts_mod, "MultiModalConversation") as mock_mm:
            mock_mm.call.return_value = MagicMock()
            self.provider._call_qwen_tts(
                "qwen3-tts-instruct-flash",
                "你好呀",
                instruction="语气轻快元气",
                speech_rate=1.06,
                pitch_rate=1.03,
            )
            mock_mm.call.assert_called_once()
            kwargs = mock_mm.call.call_args[1]
            self.assertIn("instructions", kwargs)
            self.assertIn("语气轻快元气", kwargs["instructions"])
            self.assertIn("1.06", kwargs["instructions"])


class LayoutChannelTests(unittest.TestCase):
    def test_group_bubbles_respects_max_bubbles_2(self):
        chain = [
            MockPlain("第一句。"),
            MockPlain("第二句。"),
            MockPlain("第三句。"),
            MockPlain("第四句。"),
        ]
        bubbles = chat_layout.group_bubbles(chain, MockPlain, MockRecord, max_bubbles=2)
        self.assertEqual(len(bubbles), 2)
        self.assertEqual(len(bubbles[0]), 1)
        self.assertEqual(bubbles[0][0].text, "第一句。")
        self.assertEqual(len(bubbles[1]), 3)
        self.assertEqual([c.text for c in bubbles[1]], ["第二句。", "第三句。", "第四句。"])

    def test_group_bubbles_respects_max_bubbles_3(self):
        chain = [
            MockPlain("第一句。"),
            MockPlain("第二句。"),
            MockPlain("第三句。"),
            MockPlain("第四句。"),
        ]
        bubbles = chat_layout.group_bubbles(chain, MockPlain, MockRecord, max_bubbles=3)
        self.assertEqual(len(bubbles), 3)
        self.assertEqual(bubbles[0][0].text, "第一句。")
        self.assertEqual(bubbles[1][0].text, "第二句。")
        self.assertEqual([c.text for c in bubbles[2]], ["第三句。", "第四句。"])


class FaceConvertChannelTests(unittest.TestCase):
    def setUp(self):
        _ensure_runtime_stubs()
        set_bridge(None)
        main_path = os.path.join(REPO_ROOT, "plugin", "astrbot_plugin_face_convert", "main.py")
        f_spec = importlib.util.spec_from_file_location("face_convert_mod", main_path)
        self.mod = importlib.util.module_from_spec(f_spec)
        f_spec.loader.exec_module(self.mod)
        self.plugin = self.mod.FaceConvertPlugin(MagicMock())
        self.plugin.face_map = {"旺柴": 100, "呲牙": 101, "委屈": 102, "难过": 103}
        self.plugin.pattern = self.mod.re.compile(r"\[([A-Za-z一-鿿]{1,6})\]")
        self.plugin._face_history = {}

    def tearDown(self):
        set_bridge(None)

    def _make_event(self, text: str):
        PlainCls = getattr(self.mod, "Plain", MockPlain)
        event = MagicMock()
        event.unified_msg_origin = "test_origin"
        result = MagicMock()
        result.chain = [PlainCls(text)]
        result.is_llm_result.return_value = True
        event.get_result.return_value = result
        return event, result

    def test_cheerful_faces_suppressed_when_mood_low_or_pout(self):
        bridge = MockBridge({
            "max_faces": 1,
            "suppress_cheerful_faces": True,
        })
        set_bridge(bridge)

        # Message contains cheerful [旺柴] and non-cheerful [委屈]
        event, result = self._make_event("哼，才不理你呢[旺柴]！心里好[委屈]呀。")
        asyncio.run(self.plugin.convert_faces(event))

        face_ids = [c.id for c in result.chain if hasattr(c, "id")]
        plain_texts = "".join(c.text for c in result.chain if hasattr(c, "text"))

        self.assertNotIn("旺柴", plain_texts)
        self.assertNotIn("[旺柴]", plain_texts)
        self.assertEqual(face_ids, [102])  # 102 is 委屈

    def test_normal_mood_permits_cheerful_faces_up_to_cap(self):
        bridge = MockBridge({
            "max_faces": 2,
            "suppress_cheerful_faces": False,
        })
        set_bridge(bridge)

        event, result = self._make_event("今天超开心[旺柴]！你看[呲牙]！还有[委屈]。")
        asyncio.run(self.plugin.convert_faces(event))

        face_ids = [c.id for c in result.chain if hasattr(c, "id")]
        self.assertEqual(face_ids, [100, 101])

    def test_no_bridge_defaults_gracefully(self):
        set_bridge(None)
        event, result = self._make_event("今天还行[旺柴]！")
        asyncio.run(self.plugin.convert_faces(event))

        face_ids = [c.id for c in result.chain if hasattr(c, "id")]
        self.assertEqual(face_ids, [100])


class MemoryChannelTests(unittest.TestCase):
    def setUp(self):
        _ensure_runtime_stubs()
        set_bridge(None)
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        from plugin.astrbot_plugin_bili_activity import core, memory_policy, main
        self.store = core.Store(self.temp.name)
        self.policy = memory_policy.MemoryPolicy(self.store)
        self.cfg = main.DEFAULTS | {"life_enabled": True, "enabled": True, "life_memory_enabled": True}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
        set_bridge(None)

    def test_salience_multiplier_extends_detail_phase(self):
        import time
        now = time.time()
        row_normal = {
            "occurred_at": now - 12 * 86400,
            "kind": "read",
            "subject_kind": "agent",
            "goal_id": None,
            "interpretation": {"thought": "普通刷帖"},
        }
        phase_norm, _ = self.policy._phase(row_normal, self.cfg, now)
        self.assertEqual(phase_norm, "gist")

        row_salient = {
            "occurred_at": now - 12 * 86400,
            "kind": "read",
            "subject_kind": "agent",
            "goal_id": None,
            "interpretation": {
                "thought": "超惊喜的重大发现",
                "affect_tag": {"salience": 3.0, "felt_tone": "当时觉得眼前一亮、特别雀跃"},
            },
        }
        phase_salient, _ = self.policy._phase(row_salient, self.cfg, now)
        self.assertEqual(phase_salient, "detail")

    def test_felt_tone_preserved_in_gist(self):
        row = {
            "kind": "read",
            "evidence": {"bvid": "BV1xx"},
            "interpretation": {
                "thought": "视频很精彩",
                "affect_tag": {"salience": 2.0, "felt_tone": "特别雀跃"},
            },
        }
        gist = self.policy._gist(row)
        self.assertEqual(gist.get("felt_tone"), "特别雀跃")


class PlannerChannelTests(unittest.TestCase):
    def setUp(self):
        _ensure_runtime_stubs()
        set_bridge(None)
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        from plugin.astrbot_plugin_bili_activity import core, planner, main
        self.store = core.Store(self.temp.name)
        self.planner = planner.LifePlanner(self.store)
        self.cfg = main.DEFAULTS | {"life_enabled": True, "enabled": True, "life_decision_enabled": True}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
        set_bridge(None)

    def test_planner_topic_cooldown_and_study_bias(self):
        import time
        bridge = MockBridge({
            "planner_bias": {
                "study_write_delta": 3,
                "prefer_light_or_rest": False,
                "cooldown_topics": ["熬夜头疼"],
            }
        })
        set_bridge(bridge)

        now = time.time()
        choice = self.planner.choose("test_scope", self.cfg, now=now)
        self.assertIsNotNone(choice)


class ContactChannelTests(unittest.TestCase):
    def setUp(self):
        _ensure_runtime_stubs()
        set_bridge(None)
        import tempfile
        from datetime import datetime
        from zoneinfo import ZoneInfo
        self.temp = tempfile.TemporaryDirectory()
        from plugin.astrbot_plugin_bili_activity import core, contact, main
        self.store = core.Store(self.temp.name)
        self.ledger = contact.ContactLedger(self.store)
        self.cfg = main.DEFAULTS | {
            "owner_scope": "test:FriendMessage:12345",
            "owner_id": "12345",
            "dnd_ranges": [],
            "contact_coordination_enabled": True,
            "contact_owner_quiet_minutes": 60,
            "contact_cooldown_minutes": 60,
            "contact_daily_limit": 5,
            "contact_unanswered_limit": 3,
        }
        self.now = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
        set_bridge(None)

    def test_contact_suppress_ordinary_share_when_care_due(self):
        from plugin.astrbot_plugin_bili_activity import contact
        bridge = MockBridge({
            "contact_drive": 0.8,
            "suppress_ordinary_share": True,
        })
        set_bridge(bridge)

        with self.assertRaises(contact.ContactBlocked) as ctx:
            self.ledger.reserve(
                "test:FriendMessage:12345",
                "life",
                "test_key",
                "thought",
                "a" * 64,
                self.cfg,
                self.now,
            )
        self.assertIn("牵挂", str(ctx.exception))



class ProductionImportPathTests(unittest.TestCase):
    def test_no_repo_package_path_in_production_code(self):
        offenders = []
        root = Path(REPO_ROOT)
        for base in (root / "plugin", root / "deploy" / "patches"):
            for path in base.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                if ("plugin.astrbot_plugin_affective_core" in source
                        or "from astrbot_plugin_affective_core import get_bridge" in source):
                    offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])



class NeutralProfileCompatibilityTests(unittest.TestCase):
    def test_neutral_profile_does_not_override_user_config(self):
        from astrbot_plugin_affective_core.dynamics import expression_profile
        profile = expression_profile(None)
        self.assertIsNone(profile["speech_rate"])
        self.assertIsNone(profile["pitch_rate"])
        self.assertIsNone(profile["max_bubbles"])
        self.assertIsNone(profile["max_faces"])
        self.assertEqual(profile["tts_instruction"], "")


if __name__ == "__main__":
    unittest.main()

"""Unit and integration tests for Affective Core (Milestone 1).

Covers:
- SQLite storage, transactions, care threads, and event bounding
- Circadian homeostasis, overnight sleep reset, and awake longing
- Tsundere Softening Gate & deterministic cue screening
- Life event pulse mapping & topic cooldowns
- LLM JSON appraisal parsing and timeout fallback
- Idempotent EmotionAI data migration
- Outbound leak scrubbing and expression profile generation
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_ROOT = os.path.join(REPO_ROOT, "plugin")
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

# Mock astrbot framework imports if not present in test environment
if "astrbot" not in sys.modules:
    class FilterMock:
        EventMessageType = types.SimpleNamespace(ALL="ALL")

        def __getattr__(self, name):
            return lambda *a, **kw: lambda fn: fn

    class StarMock:
        def __init__(self, context):
            self.context = context

    class StarToolsMock:
        @staticmethod
        def get_data_dir(name=""):
            return Path(tempfile.gettempdir()) / name

    mock_attrs = {
        "astrbot": {},
        "astrbot.api": {"logger": logging.getLogger("affective-test")},
        "astrbot.api.event": {"AstrMessageEvent": object, "filter": FilterMock()},
        "astrbot.api.star": {
            "Context": object,
            "Star": StarMock,
            "StarTools": StarToolsMock,
            "register": lambda *a, **kw: lambda cls: cls,
        },
    }
    for mname, mval in mock_attrs.items():
        mod = types.ModuleType(mname)
        vars(mod).update(mval)
        sys.modules[mname] = mod

from astrbot_plugin_affective_core import BRIDGE_ATTR, BRIDGE_VERSION
from astrbot_plugin_affective_core.appraisal import (
    build_appraisal_prompt,
    parse_appraisal_json,
    should_skip_llm_appraisal,
    appraise_owner_message,
)
from astrbot_plugin_affective_core.core import (
    AffectiveStore,
    GLOBAL_SOCIAL_SCOPE,
    normalize_config,
    sanitize_label,
    topics_overlap,
)
from astrbot_plugin_affective_core.dynamics import (
    apply_life_event,
    apply_owner_turn,
    awake_seconds_between,
    circadian_baseline,
    expression_profile,
    local_fast_screen,
    new_owner_state,
    render_owner_prompt_memo,
    render_social_prompt_memo,
    settle_owner_state,
    should_surface_care,
    strip_sender_tag,
    update_social_turn,
    wake_cycle_day,
)
from astrbot_plugin_affective_core.main import (
    AffectiveCorePlugin,
    clean_legacy_contexts,
    scrub_affective_leaks,
)
from astrbot_plugin_affective_core.migration import (
    compute_migrated_valence,
    compute_migrated_warmth,
    migrate_from_emotionai,
    normalize_legacy_record,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCOPE = "aiocqhttp:FriendMessage:248093506"
OWNER_ID = "248093506"


def make_ts(year=2026, month=11, day=7, hour=14, minute=0, second=0) -> float:
    return datetime(year, month, day, hour, minute, second, tzinfo=SHANGHAI).timestamp()


class CoreStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="affective-core-")
        self.folder = Path(self.temp_dir.name)
        self.store = AffectiveStore(self.folder)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_database_initialization_and_meta(self):
        self.assertTrue((self.folder / "affective.db").is_file())
        self.assertEqual(self.store.get_meta("schema_version"), 1)
        self.store.set_meta("custom_key", {"active": True, "count": 42})
        self.assertEqual(self.store.get_meta("custom_key"), {"active": True, "count": 42})

    def test_owner_state_roundtrip_and_clamping(self):
        raw = {
            "scope": SCOPE,
            "owner_id": OWNER_ID,
            "valence": 5.0,  # should clamp to 1.0
            "arousal": -2.0,  # should clamp to 0.0
            "longing": 1.5,   # should clamp to 1.0
            "pout": -0.5,     # should clamp to 0.0
            "share_urge": 0.4,
            "warmth_bond": 1200.0,  # should clamp to 1000.0
            "dominant_tag": "calm",
            "softened_mode": "",
            "last_settled_at": 1000.0,
            "last_interaction_at": 900.0,
            "last_sleep_reset_day": "2026-11-07",
            "consecutive_rejects": 2,
            "consecutive_neg_stimuli": 1,
            "interaction_count": 5,
            "extra": {"cooldown_topics": {"topic1": 2000.0}},
        }
        saved = self.store.save_owner_state(raw)
        self.assertEqual(saved["valence"], 1.0)
        self.assertEqual(saved["arousal"], 0.0)
        self.assertEqual(saved["longing"], 1.0)
        self.assertEqual(saved["pout"], 0.0)
        self.assertEqual(saved["warmth_bond"], 1000.0)

        fetched = self.store.get_owner_state(SCOPE)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["scope"], SCOPE)
        self.assertEqual(fetched["owner_id"], OWNER_ID)
        self.assertEqual(fetched["extra"]["cooldown_topics"], {"topic1": 2000.0})

    def test_social_state_default_and_persistence(self):
        default_guest = self.store.get_social_state("group:123", "99999")
        self.assertEqual(default_guest["familiarity"], 0.0)
        self.assertEqual(default_guest["vibe"], 0.0)
        self.assertFalse(self.store.is_user_blacklisted("group:123", "99999"))

        self.store.save_social_state({
            "scope": "group:123",
            "user_id": "99999",
            "familiarity": 15.5,
            "vibe": 0.35,
            "is_blacklisted": 1,
            "interaction_count": 3,
        })
        updated = self.store.get_social_state("group:123", "99999")
        self.assertEqual(updated["familiarity"], 15.5)
        self.assertEqual(updated["vibe"], 0.35)
        self.assertTrue(self.store.is_user_blacklisted("group:123", "99999"))

    def test_care_threads_lifecycle_and_overlap_dedup(self):
        t0 = make_ts(2026, 11, 7, 10, 0)
        th1 = self.store.upsert_care_thread(
            SCOPE,
            "赶项目熬夜头疼",
            category="health",
            salience=0.8,
            ttl_hours=24,
            now=t0,
        )
        self.assertIsNotNone(th1)
        self.assertEqual(th1["status"], "active")

        # Second update with overlapping topic merges into the existing thread
        th2 = self.store.upsert_care_thread(
            SCOPE,
            "昨晚熬夜头疼",
            category="health",
            salience=0.9,
            ttl_hours=36,
            now=t0 + 1800,
        )
        self.assertEqual(th2["id"], th1["id"])
        self.assertEqual(th2["salience"], 0.9)

        active = self.store.active_care_threads(SCOPE, now=t0 + 1800)
        self.assertEqual(len(active), 1)

        # Marking prompted increments count and updates timestamp
        self.store.mark_care_prompted(SCOPE, th1["id"], now=t0 + 3600)
        active_after = self.store.active_care_threads(SCOPE, now=t0 + 3600)
        self.assertEqual(active_after[0]["prompt_count"], 1)

        # Resolving care thread
        resolved = self.store.resolve_care_threads(SCOPE, [th1["id"]], note="主人说好多了", now=t0 + 4000)
        self.assertEqual(resolved, [th1["id"]])
        self.assertEqual(len(self.store.active_care_threads(SCOPE, now=t0 + 4000)), 0)

    def test_care_threads_bound_limit_eviction(self):
        t0 = make_ts(2026, 11, 7, 10, 0)
        for i in range(5):
            self.store.upsert_care_thread(
                SCOPE,
                f"独立完全不重合的主题事项 {i}",
                salience=0.2 * (i + 1),
                limit=3,
                now=t0 + i * 10,
            )
        active = self.store.active_care_threads(SCOPE, limit=5, now=t0 + 100)
        self.assertLessEqual(len(active), 3)

    def test_event_recording_and_fifo_bounding(self):
        t0 = make_ts(2026, 11, 7, 12, 0)
        self.store.record_event(SCOPE, "test_source", "test_kind", {"key": "val"}, now=t0)
        events = self.store.recent_events(SCOPE, limit=5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["key"], "val")


class DynamicsAndCircadianTests(unittest.TestCase):
    def test_circadian_phases(self):
        t_sleep = make_ts(hour=23, minute=45)
        c_sleep = circadian_baseline(t_sleep, {"wake_hour": 8, "sleep_hour": 23})
        self.assertTrue(c_sleep["sleeping"])
        self.assertEqual(c_sleep["phase"], "sleeping")

        t_morn = make_ts(hour=9, minute=0)
        c_morn = circadian_baseline(t_morn, {"wake_hour": 8, "sleep_hour": 23})
        self.assertFalse(c_morn["sleeping"])
        self.assertEqual(c_morn["phase"], "morning")

        t_day = make_ts(hour=14, minute=30)
        c_day = circadian_baseline(t_day, {"wake_hour": 8, "sleep_hour": 23})
        self.assertEqual(c_day["phase"], "daytime")
        self.assertGreater(c_day["arousal"], 0.60)

    def test_awake_seconds_between_excludes_sleep(self):
        # 22:00 to next day 10:00 (12h elapsed, sleep 23:00 to 08:00 = 9h sleep) -> 3h awake
        t0 = make_ts(day=7, hour=22, minute=0)
        t1 = make_ts(day=8, hour=10, minute=0)
        awake = awake_seconds_between(t0, t1, wake_hour=8, sleep_hour=23)
        self.assertAlmostEqual(awake, 3 * 3600.0, delta=60.0)

    def test_overnight_negative_reset_clears_pout_and_negative_valence(self):
        t_night = make_ts(day=7, hour=22, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t_night)
        st["valence"] = -0.65
        st["pout"] = 0.85
        st["consecutive_rejects"] = 3
        st["consecutive_neg_stimuli"] = 2
        st["last_interaction_at"] = t_night
        st["last_settled_at"] = t_night
        st["last_sleep_reset_day"] = wake_cycle_day(t_night, 8)

        # Wake up next morning at 09:30 (>3h silence + crossed wake cycle day)
        t_morning = make_ts(day=8, hour=9, minute=30)
        settled, notes = settle_owner_state(st, {"wake_hour": 8, "sleep_hour": 23}, now=t_morning)
        self.assertIn("overnight_sleep_reset", notes)
        self.assertGreaterEqual(settled["valence"], 0.05)
        self.assertEqual(settled["pout"], 0.0)
        self.assertEqual(settled["consecutive_rejects"], 0)
        self.assertEqual(settled["consecutive_neg_stimuli"], 0)

    def test_longing_accumulates_only_after_four_awake_hours(self):
        t0 = make_ts(day=7, hour=10, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        st["longing"] = 0.05
        st["last_interaction_at"] = t0
        st["last_settled_at"] = t0

        # After 2 hours: silence < 4h, longing does NOT rise
        st_2h, _ = settle_owner_state(st, now=t0 + 2 * 3600)
        self.assertEqual(st_2h["longing"], 0.05)

        # After 8 awake hours: 4 hours past threshold, longing slowly charges up
        st_8h, _ = settle_owner_state(st, now=t0 + 8 * 3600)
        self.assertGreater(st_8h["longing"], 0.25)


class SofteningGateTests(unittest.TestCase):
    def test_serious_task_softens_pout_and_boosts_focus(self):
        t0 = make_ts(hour=14, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        st["pout"] = 0.70
        st["valence"] = -0.10

        appraisal = {
            "intent": "serious_task",
            "user_emotion": "focused",
            "user_distress": 0.0,
            "delta": {"valence": 0.05, "arousal": 0.10, "pout": -0.40, "warmth": 0.5},
        }
        updated, summary = apply_owner_turn(st, appraisal, now=t0)
        self.assertEqual(summary["softening_gate"], "serious_task")
        self.assertEqual(updated["softened_mode"], "serious_task")
        self.assertLess(updated["pout"], 0.30)
        self.assertEqual(updated["dominant_tag"], "focused")

    def test_coaxing_or_apology_melts_pout_immediately(self):
        t0 = make_ts(hour=14, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        st["pout"] = 0.80

        appraisal = {
            "intent": "coaxing_affection",
            "user_emotion": "playful",
            "user_distress": 0.0,
            "delta": {"valence": 0.25, "arousal": 0.05, "pout": -0.60, "warmth": 2.0},
        }
        updated, summary = apply_owner_turn(st, appraisal, now=t0)
        self.assertEqual(summary["softening_gate"], "coaxed")
        self.assertEqual(updated["softened_mode"], "coaxed")
        self.assertLess(updated["pout"], 0.15)
        self.assertGreater(updated["valence"], 0.30)

    def test_user_distress_triggers_tender_care(self):
        t0 = make_ts(hour=14, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        st["pout"] = 0.60

        appraisal = {
            "intent": "venting",
            "user_emotion": "tired",
            "user_distress": 0.75,
            "delta": {"valence": -0.05, "arousal": -0.10, "pout": -0.80, "warmth": 2.0},
        }
        updated, summary = apply_owner_turn(st, appraisal, now=t0)
        self.assertEqual(summary["softening_gate"], "comforting")
        self.assertEqual(updated["pout"], 0.0)
        self.assertEqual(updated["dominant_tag"], "tender_care")

    def test_long_waiting_gap_sparks_mild_pout_on_ordinary_hello(self):
        t0 = make_ts(hour=14, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        st["longing"] = 0.75  # waited a long time
        st["pout"] = 0.0

        appraisal = {
            "intent": "casual",
            "user_emotion": "calm",
            "user_distress": 0.0,
            "delta": {"valence": 0.02, "arousal": 0.02, "pout": 0.0, "warmth": 0.5},
        }
        updated, _ = apply_owner_turn(st, appraisal, now=t0)
        self.assertGreater(updated["pout"], 0.15)
        # Longing releases into reassurance
        self.assertLess(updated["longing"], 0.20)


class LifeEventPulseTests(unittest.TestCase):
    def test_positive_interest_and_work_check_boost_share_urge(self):
        t0 = make_ts(hour=15, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        st["share_urge"] = 0.10
        updated, summary = apply_life_event(st, "interest_positive", {"topic": "双人解谜游戏机制"}, now=t0)
        self.assertGreater(updated["share_urge"], 0.30)
        self.assertGreater(updated["valence"], 0.30)

    def test_negative_stimuli_triggers_topic_cooldown(self):
        t0 = make_ts(hour=15, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        u1, _ = apply_life_event(st, "ad_or_rumor_blocked", {"topic": "营销号带货"}, now=t0)
        self.assertEqual(u1["consecutive_neg_stimuli"], 1)

        u2, _ = apply_life_event(u1, "ad_or_rumor_blocked", {"topic": "营销号带货"}, now=t0 + 60)
        self.assertEqual(u2["consecutive_neg_stimuli"], 2)
        cooldowns = u2["extra"].get("cooldown_topics") or {}
        self.assertIn("营销号带货", cooldowns)

    def test_proposal_rejections_scale_pout(self):
        t0 = make_ts(hour=15, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        u1, _ = apply_life_event(st, "proposal_rejected", now=t0)
        self.assertEqual(u1["consecutive_rejects"], 1)
        self.assertAlmostEqual(u1["pout"], 0.12, places=2)

        u2, _ = apply_life_event(u1, "proposal_rejected", now=t0 + 300)
        self.assertEqual(u2["consecutive_rejects"], 2)
        self.assertGreater(u2["pout"], 0.30)


class AppraisalTests(unittest.IsolatedAsyncioTestCase):
    def test_should_skip_llm_appraisal(self):
        self.assertTrue(should_skip_llm_appraisal("")[0])
        self.assertTrue(should_skip_llm_appraisal("/心情")[0])
        self.assertTrue(should_skip_llm_appraisal("暂停活动")[0])
        self.assertTrue(should_skip_llm_appraisal("可以")[0])
        self.assertTrue(should_skip_llm_appraisal("算了")[0])
        self.assertTrue(should_skip_llm_appraisal("截屏看看")[0])
        self.assertTrue(should_skip_llm_appraisal("你好", is_background_run=True)[0])
        self.assertTrue(should_skip_llm_appraisal("你好", appraisal_mode="math_only")[0])
        self.assertFalse(should_skip_llm_appraisal("今天头有点疼，先歇会儿")[0])

    def test_parse_appraisal_json_valid_and_markdown_unwrap(self):
        raw = '```json\n{"user_emotion":"tired","user_distress":0.65,"intent":"venting","delta":{"valence":-0.05,"arousal":-0.10,"pout":-0.5,"warmth":2.0},"care_open":{"topic":"赶项目熬夜头疼","category":"health","ttl_hours":36},"care_resolved_ids":["care_123"]}\n```'
        parsed = parse_appraisal_json(raw, valid_care_ids={"care_123"})
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["user_emotion"], "tired")
        self.assertEqual(parsed["intent"], "venting")
        self.assertEqual(parsed["care_open"]["topic"], "赶项目熬夜头疼")
        self.assertEqual(parsed["care_resolved_ids"], ["care_123"])

    def test_parse_appraisal_json_corrupted_returns_none(self):
        self.assertIsNone(parse_appraisal_json("Not a json at all"))
        self.assertIsNone(parse_appraisal_json("{broken_json: true}"))

    async def test_appraise_owner_message_timeout_degrades_to_fast_screen(self):
        t0 = make_ts(hour=14, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)

        mock_provider = AsyncMock()
        async def hang(*args, **kwargs):
            await asyncio.sleep(5.0)
            return MagicMock(completion_text="{}")
        mock_provider.text_chat = hang

        result = await appraise_owner_message(
            mock_provider,
            "帮我看看这段代码报错 Traceback (most recent call last):",
            st,
            [],
            {"appraisal_timeout_seconds": 0.2},
            now=t0,
        )
        self.assertEqual(result["fallback_reason"], "timeout")
        self.assertEqual(result["intent"], "serious_task")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="affective-mig-")
        self.root = Path(self.temp_dir.name)
        self.plugin_data = self.root / "astrbot_plugin_affective_core"
        self.store = AffectiveStore(self.plugin_data)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_idempotent_migration_from_legacy_emotionai_format(self):
        legacy_data = {
            f"{SCOPE}_{OWNER_ID}": {
                "joy": 25, "trust": 15, "fear": 0, "surprise": 5, "sadness": 0,
                "disgust": 0, "anger": 0, "anticipation": 10, "pride": 12,
                "guilt": 0, "shame": 0, "envy": 0,
                "favor": 85, "intimacy": 60, "is_blacklisted": False,
                "interaction_count": 128, "last_interaction": 1700000000.0,
            },
            "group:456_9999": {
                "joy": 5, "trust": 2, "favor": 20, "intimacy": 10,
                "is_blacklisted": False, "interaction_count": 10,
            },
            "group:456_8888": {
                "favor": -50, "intimacy": 0, "is_blacklisted": True,
            },
        }
        legacy_dir = self.root / "astrbot_plugin_emotionai"
        legacy_dir.mkdir(parents=True)
        data_file = legacy_dir / "user_emotion_data.json"
        data_file.write_text(json.dumps(legacy_data, ensure_ascii=False), encoding="utf-8")

        res1 = migrate_from_emotionai(
            self.store,
            self.root,
            owner_scope=SCOPE,
            owner_id=OWNER_ID,
        )
        self.assertTrue(res1["migrated"])
        self.assertEqual(res1["receipt"]["social_migrated"], 2)
        self.assertEqual(res1["receipt"]["blacklisted_migrated"], 1)

        # Owner state was seeded with computed warmth
        owner_st = self.store.get_owner_state(SCOPE)
        self.assertIsNotNone(owner_st)
        self.assertGreater(owner_st["warmth_bond"], 600.0)
        self.assertEqual(owner_st["interaction_count"], 128)

        # Blacklisted user preserved
        self.assertTrue(self.store.is_user_blacklisted("group:456", "8888"))

        # Re-running migration is idempotent
        res2 = migrate_from_emotionai(
            self.store,
            self.root,
            owner_scope=SCOPE,
            owner_id=OWNER_ID,
        )
        self.assertFalse(res2["migrated"])
        self.assertEqual(res2["reason"], "already_migrated")
        # Original file untouched
        self.assertTrue(data_file.is_file())


class LeakScrubbingAndPromptMemoTests(unittest.TestCase):
    def test_scrub_affective_leaks_removes_internal_memos_and_scores(self):
        dirty = (
            "【当下心境】刚在手机上逛到有意思的东西，心情轻快得意。\n"
            "【心里惦记】之前主人提过赶项目熬夜头疼。\n"
            "好的呀主人！[好感度:+2][愉悦度:0.65]\n"
            "<thought>内部推理思考</thought>\n"
            "<mood>happy</mood>\n"
            "这是正常的回答文字。"
        )
        cleaned = scrub_affective_leaks(dirty)
        self.assertNotIn("当下心境", cleaned)
        self.assertNotIn("心里惦记", cleaned)
        self.assertNotIn("[好感度", cleaned)
        self.assertNotIn("<thought>", cleaned)
        self.assertNotIn("<mood>", cleaned)
        self.assertIn("好的呀主人！", cleaned)
        self.assertIn("这是正常的回答文字。", cleaned)

    def test_scrub_affective_leaks_preserves_code_fences(self):
        code_block = (
            "【当下心境】安稳舒展。\n"
            "给你看看这段 XML 样本：\n"
            "```xml\n<mood>custom_user_tag</mood>\n```\n"
            "以上是代码片段。"
        )
        cleaned = scrub_affective_leaks(code_block)
        self.assertNotIn("【当下心境】", cleaned)
        self.assertIn("```xml\n<mood>custom_user_tag</mood>\n```", cleaned)

    def test_render_owner_prompt_memo_has_zero_numbers_or_xml(self):
        t0 = make_ts(hour=14, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        st["pout"] = 0.55
        care = {"topic": "感冒咳嗽", "category": "health"}
        memo = render_owner_prompt_memo(st, care, now=t0)
        self.assertIn("【当下心境】", memo)
        self.assertIn("【心里惦记】", memo)
        self.assertNotIn("<", memo)
        self.assertNotIn(">", memo)
        self.assertNotIn("0.", memo)
        self.assertNotIn("好感度", memo)

    def test_expression_profile_outputs_deterministic_channels(self):
        t0 = make_ts(hour=14, minute=0)
        st = new_owner_state(SCOPE, OWNER_ID, now=t0)
        st["pout"] = 0.60
        exp = expression_profile(st, None, now=t0)
        self.assertEqual(exp["mode"], "tsundere_pout")
        self.assertEqual(exp["max_bubbles"], 2)
        self.assertEqual(exp["max_faces"], 1)
        self.assertTrue(exp["suppress_cheerful_faces"])
        self.assertIn("撒娇赌气", exp["tts_instruction"])


class PluginMainIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="affective-plugin-")
        self.folder = Path(self.temp_dir.name)
        self.mock_context = MagicMock()
        self.plugin = AffectiveCorePlugin(
            self.mock_context,
            {
                "owner_scope": SCOPE,
                "owner_id": OWNER_ID,
                "appraisal_mode": "math_only",
            },
            data_dir=self.folder,
        )

    def tearDown(self):
        self.plugin.store.close()
        self.temp_dir.cleanup()

    def test_bridge_registration_on_context(self):
        self.assertTrue(hasattr(self.mock_context, BRIDGE_ATTR))
        bridge = getattr(self.mock_context, BRIDGE_ATTR)
        self.assertEqual(bridge.version, BRIDGE_VERSION)
        snap = bridge.get_snapshot(SCOPE)
        self.assertIsNotNone(snap)
        self.assertIn("expression", snap)

    async def test_owner_private_vs_group_isolation(self):
        owner_event = MagicMock()
        owner_event.unified_msg_origin = SCOPE
        owner_event.get_sender_id.return_value = OWNER_ID
        owner_event.get_group_id.return_value = None
        owner_event.message_str = "你好呀大肥鱼"
        owner_event.message_obj = types.SimpleNamespace(message_id="msg_101")
        owner_event.get_extra.return_value = None

        req_owner = types.SimpleNamespace(system_prompt="", contexts=[])
        await self.plugin.inject_affective_context(owner_event, req_owner)
        self.assertIn("【当下心境】", req_owner.system_prompt)
        self.assertNotIn("【当场互动基调】", req_owner.system_prompt)

        group_event = MagicMock()
        group_event.unified_msg_origin = "aiocqhttp:GroupMessage:888888"
        group_event.get_sender_id.return_value = "777777"
        group_event.get_group_id.return_value = "888888"
        group_event.message_str = "大肥鱼早上好"
        group_event.message_obj = types.SimpleNamespace(message_id="msg_202")
        group_event.get_extra.return_value = None

        req_group = types.SimpleNamespace(system_prompt="", contexts=[])
        await self.plugin.inject_affective_context(group_event, req_group)
        self.assertIn("【当场互动基调】", req_group.system_prompt)
        self.assertNotIn("【当下心境】", req_group.system_prompt)
        self.assertNotIn(OWNER_ID, req_group.system_prompt)

    async def test_mood_command_output(self):
        card = self.plugin.format_mood_card(SCOPE)
        self.assertIn("大肥鱼心境中枢 · 只读诊断", card)
        self.assertIn("愉悦度", card)
        self.assertIn("想念度", card)
        self.assertIn("外化通道参数预览", card)


    def test_cross_thread_access_with_check_same_thread_false(self):
        import threading
        store = AffectiveStore(self.folder / "thread_db")
        try:
            st = new_owner_state(SCOPE, OWNER_ID)
            store.save_owner_state(st)

            errors = []
            def worker():
                try:
                    for _ in range(10):
                        with store.transaction():
                            current = store.get_owner_state(SCOPE)
                            current["interaction_count"] += 1
                            store.save_owner_state(current)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            final_state = store.get_owner_state(SCOPE)
            self.assertEqual(final_state["interaction_count"], 40)
        finally:
            store.close()

    async def test_turn_deduplication_without_message_id(self):
        # Event without message_obj or message_id
        ev = MagicMock()
        ev.unified_msg_origin = SCOPE
        ev.get_sender_id.return_value = OWNER_ID
        ev.get_group_id.return_value = None
        ev.message_str = "主人来了"
        ev.message_obj = None
        ev.get_extra.return_value = None

        key1 = self.plugin._turn_key(ev)
        key2 = self.plugin._turn_key(ev)
        self.assertIsNotNone(key1)
        self.assertEqual(key1, key2)

        req1 = types.SimpleNamespace(system_prompt="", contexts=[])
        await self.plugin.inject_affective_context(ev, req1)
        prompt1 = req1.system_prompt
        self.assertIn("【当下心境】", prompt1)

        # Immediate re-entry (e.g. tool loop continuation) for the exact same event
        req2 = types.SimpleNamespace(system_prompt="", contexts=[])
        await self.plugin.inject_affective_context(ev, req2)
        self.assertEqual(req2.system_prompt, prompt1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

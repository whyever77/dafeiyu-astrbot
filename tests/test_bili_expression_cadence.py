"""Offline foreground thought cadence: exposure is not a delivery receipt."""
import importlib
import json
import types
import unittest
from unittest.mock import patch

import test_bili_activity as fixtures
import test_bili_life_integration as life_tests
import test_bili_self_expression as expression_tests

main, core = fixtures.main, fixtures.core
SCOPE, OTHER = fixtures.SCOPE, fixtures.OTHER_SCOPE
expression = expression_tests.expression
cadence = importlib.import_module("bili_activity_test.expression_cadence")
NOW = 1800000000
FIRST = "留着以后聊的双人机关观察"
SECOND = "还没来得及说的建造配合想法"


def held(store, excerpt=FIRST, fingerprint="first", *, scope=SCOPE, at=NOW - 30, **extra):
    details = {"kind": "thought", "fingerprint": fingerprint,
               "delivery_id": "thought:held:" + fingerprint, "excerpt": excerpt,
               "reason": "主人刚在聊天，先留出空闲"} | extra
    store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                     (at, scope, "share_held", core.canonical(details)))


def offers(store, scope=SCOPE):
    return [(row["time"], json.loads(row["details"])) for row in store.db.execute(
        "SELECT time,details FROM events WHERE scope=? AND kind=? ORDER BY seq",
        (scope, cadence.EVENT_KIND))]


class ExpressionCadenceTests(unittest.TestCase):
    tearDown = expression_tests.ExpressionTests.tearDown
    snapshot = expression_tests.ExpressionTests.snapshot
    goal = expression_tests.ExpressionTests.goal
    checked_work = expression_tests.ExpressionTests.checked_work

    def setUp(self):
        expression_tests.ExpressionTests.setUp(self)
        self.cfg["enabled"] = True

    def context(self, message_id, *, now=NOW, scope=SCOPE, cfg=None):
        return cadence.ExpressionCadence(self.store).context(
            scope, self.cfg if cfg is None else cfg, message_id, now=now)

    def test_snapshot_identity_is_stable_and_review_stays_pure_read(self):
        held(self.store)
        held(self.store, SECOND, "second", at=NOW - 20)
        before = list(self.store.db.iterdump())
        value = self.snapshot(now=NOW)
        rows = value["held_thoughts"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertRegex(row["held_id"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(rows[0]["held_id"], rows[1]["held_id"])
        prompt = self.view.context(SCOPE, self.cfg, now=NOW)
        self.assertIn(FIRST, prompt)
        self.assertIn(SECOND, prompt)
        for row in rows:
            self.assertNotIn(row["held_id"], prompt)
        self.assertEqual(list(self.store.db.iterdump()), before)
        original = {row["excerpt"]: row["held_id"] for row in rows}
        held(self.store, FIRST, "first", at=NOW - 1)
        repeated = self.snapshot(now=NOW)["held_thoughts"]
        self.assertEqual({row["excerpt"]: row["held_id"] for row in repeated}, original)

    def test_each_new_message_gets_at_most_one_available_thought(self):
        held(self.store)
        held(self.store, SECOND, "second", at=NOW - 20)
        first = self.context("message-one")
        self.assertEqual(sum(text in first for text in (FIRST, SECOND)), 1)
        second = self.context("message-two", now=NOW + 1)
        self.assertEqual(sum(text in second for text in (FIRST, SECOND)), 1)
        self.assertNotEqual(FIRST in first, FIRST in second)
        third = self.context("message-three", now=NOW + 2)
        self.assertNotIn(FIRST, third)
        self.assertNotIn(SECOND, third)
        self.assertEqual(len(offers(self.store)), 3)
        self.assertIsNone(offers(self.store)[-1][1]["held_id"])
        self.assertEqual(len(self.snapshot(now=NOW + 2)["held_thoughts"]), 2)

    def test_same_message_keeps_choice_after_new_candidate_arrives(self):
        held(self.store)
        first = self.context("same-message")
        held(self.store, SECOND, "second", at=NOW + 1)
        again = self.context("same-message", now=NOW + 2)
        self.assertIn(FIRST, first)
        self.assertIn(FIRST, again)
        self.assertNotIn(SECOND, again)
        self.assertEqual(len(offers(self.store)), 1)

    def test_empty_choice_cannot_change_mid_message(self):
        held(self.store)
        self.context("used-first")
        empty = self.context("wait-message", now=NOW + 1)
        self.assertNotIn(FIRST, empty)
        self.assertIsNone(offers(self.store)[-1][1]["held_id"])
        held(self.store, SECOND, "second", at=NOW + 2)
        again = self.context("wait-message", now=NOW + 3)
        self.assertNotIn(FIRST, again)
        self.assertNotIn(SECOND, again)
        self.assertEqual(len(offers(self.store)), 2)
        self.assertIn(SECOND, self.context("next-message", now=NOW + 3))

    def test_six_hour_candidate_cooldown_boundary(self):
        self.assertEqual(cadence.COOLDOWN_SECONDS, 6 * 3600)
        held(self.store)
        self.assertIn(FIRST, self.context("initial"))
        self.assertNotIn(FIRST, self.context("too-early", now=NOW + 6 * 3600 - 1))
        self.assertIn(FIRST, self.context("eligible", now=NOW + 6 * 3600))
        self.assertNotIn(FIRST, self.context("fresh-message", now=NOW + 6 * 3600 + 1))

    def test_repeated_share_held_fingerprint_does_not_reset_cooldown(self):
        held(self.store)
        self.context("already-offered")
        original_id = self.snapshot(now=NOW)["held_thoughts"][0]["held_id"]
        held(self.store, "同一份想法的不同摘录", "first", at=NOW + 1,
             delivery_id="thought:other-run:first")
        self.assertEqual(self.snapshot(now=NOW + 2)["held_thoughts"][0]["held_id"], original_id)
        value = self.context("new-chat", now=NOW + 2)
        self.assertNotIn(FIRST, value)
        self.assertNotIn("同一份想法的不同摘录", value)
        self.assertIsNone(offers(self.store)[-1][1]["held_id"])
        self.assertIn("同一份想法的不同摘录", self.view.context(SCOPE, self.cfg, now=NOW + 2))

    def test_receipt_is_hash_only_context_evidence_at_supplied_time(self):
        held(self.store)
        self.context("private-chat-message-123", now=NOW + 10)
        rows = offers(self.store)
        self.assertEqual(len(rows), 1)
        at, details = rows[0]
        self.assertEqual(at, NOW + 10)
        self.assertEqual(set(details), {"message_key", "held_id", "state"})
        self.assertRegex(details["message_key"], r"^[0-9a-f]{64}$")
        self.assertRegex(details["held_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(details["state"], "context_only")
        self.assertNotIn("private-chat-message-123", core.canonical(details))
        self.assertNotIn(FIRST, core.canonical(details))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM deliveries").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
        self.assertFalse(self.snapshot(now=NOW + 10)["held_thoughts"][0]["qq_sent"])

    def test_missing_or_unstable_message_identity_never_exposes_or_records(self):
        held(self.store)
        for message_id in (None, "", "   ", True, False, 1.5, [], {}, "x" * 257):
            with self.subTest(message_id=message_id):
                value = self.context(message_id)
                self.assertNotIn(FIRST, value)
                self.assertIn(expression.MARKER, value)
                self.assertEqual(offers(self.store), [])

    def test_integer_and_max_length_message_ids_are_supported(self):
        held(self.store)
        self.assertIn(FIRST, self.context(123))
        self.assertIn(FIRST, self.context("x" * 256, now=NOW + 6 * 3600))
        self.assertEqual(len(offers(self.store)), 2)

    def test_disabled_review_and_empty_history_do_not_write_events(self):
        self.assertNotIn(FIRST, self.context("no-history"))
        self.assertEqual(offers(self.store), [])
        held(self.store)
        for cfg in (main.DEFAULTS, self.cfg | {"self_expression_enabled": False},
                    self.cfg | {"life_enabled": False}):
            with self.subTest(cfg=cfg):
                self.assertEqual(self.context("disabled", cfg=cfg), "")
        self.assertEqual(offers(self.store), [])

    def test_global_disabled_keeps_review_without_automatic_thought_exposure(self):
        held(self.store)
        value = self.context("disabled", cfg=self.cfg | {"enabled": False})
        self.assertNotIn(FIRST, value)
        self.assertIn(expression.MARKER, value)
        self.assertEqual(offers(self.store), [])

    def test_future_receipt_does_not_bypass_candidate_cooldown(self):
        held(self.store)
        self.context("future-clock", now=NOW + 120)
        self.assertNotIn(FIRST, self.context("clock-corrected", now=NOW))
        self.assertIsNone(offers(self.store)[-1][1]["held_id"])

    def test_invalid_receipt_fails_closed_without_affecting_explicit_review(self):
        held(self.store)
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
            (NOW, SCOPE, cadence.EVENT_KIND, "not-json"))
        self.assertNotIn(FIRST, self.context("corrupt-ledger"))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind=?",
            (cadence.EVENT_KIND,)).fetchone()[0], 1)
        self.assertIn(FIRST, self.view.context(SCOPE, self.cfg, now=NOW))

    def test_incomplete_receipt_scan_fails_closed(self):
        held(self.store)
        details = core.canonical({"message_key": "another-message", "held_id": None,
                                  "state": "context_only"})
        self.store.db.executemany("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
            [(NOW, SCOPE, cadence.EVENT_KIND, details)] * (cadence.SCAN_LIMIT + 1))
        self.assertNotIn(FIRST, self.context("overflow-ledger"))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind=?",
            (cadence.EVENT_KIND,)).fetchone()[0], cadence.SCAN_LIMIT + 1)
        self.assertIn(FIRST, self.view.context(SCOPE, self.cfg, now=NOW))

    def test_scope_isolates_candidates_message_keys_and_cooldowns(self):
        held(self.store)
        held(self.store, SECOND, "first", scope=OTHER)
        own = self.context("same-id")
        other = self.context("same-id", scope=OTHER)
        self.assertIn(FIRST, own)
        self.assertNotIn(SECOND, own)
        self.assertIn(SECOND, other)
        self.assertNotIn(FIRST, other)
        self.assertEqual(len(offers(self.store)), 1)
        self.assertEqual(len(offers(self.store, OTHER)), 1)
        self.assertNotIn(FIRST, self.context("next-id"))

    def test_reopening_store_preserves_replay_and_cooldown(self):
        held(self.store)
        self.context("before-restart")
        self.store.close()
        self.store = core.Store(self.temp.name)
        self.view = expression.SelfExpression(self.store)
        self.assertIn(FIRST, self.context("before-restart", now=NOW + 1))
        self.assertEqual(len(offers(self.store)), 1)
        self.assertNotIn(FIRST, self.context("after-restart", now=NOW + 2))

    def test_expired_thought_is_not_reintroduced_by_message_replay(self):
        held(self.store)
        self.context("original")
        after_window = NOW + 48 * 3600 + 1
        self.assertNotIn(FIRST, self.context("original", now=after_window))
        self.assertNotIn(FIRST, self.context("fresh", now=after_window))
        self.assertEqual(len(offers(self.store)), 1)
        self.assertEqual(self.store.db.execute(
            "SELECT count(*) FROM events WHERE kind='share_held'").fetchone()[0], 1)

    def test_sent_thought_is_not_reintroduced_by_message_replay(self):
        held(self.store)
        self.context("original")
        self.store.delivery(SCOPE, "thought:held:first", "thought", "first", {}, True)
        self.store.delivered("thought:held:first", {"message_id": 201})
        self.assertNotIn(FIRST, self.context("original", now=NOW + 1))
        self.assertEqual(len(offers(self.store)), 1)

    def test_changed_work_revision_invalidates_cached_choice(self):
        source, work, _ = self.checked_work()
        held(self.store, "第一版待分享笔记", work["body_hash"], kind="work",
             work_id=work["id"], revision=1, goal_id=work["goal_id"],
             delivery_id=f"work:{work['id']}:1")
        self.assertIn("第一版待分享笔记", self.context("work-message"))
        self.store.db.execute("UPDATE creative_works SET revision=2 WHERE id=?", (work["id"],))
        self.assertNotIn("第一版待分享笔记", self.context("work-message", now=NOW + 1))
        self.assertEqual(len(offers(self.store)), 1)

    def test_corrected_work_source_invalidates_cached_choice(self):
        source, work, _ = self.checked_work()
        held(self.store, "来源待核对笔记", work["body_hash"], kind="work",
             work_id=work["id"], revision=1, goal_id=work["goal_id"],
             delivery_id=f"work:{work['id']}:1")
        self.assertIn("来源待核对笔记", self.context("work-message"))
        expression_tests.episode(self.store, goal_id=work["goal_id"], supersedes_id=source["id"],
                                 evidence={"operation": "info", "result_excerpt": '{"ok":false}'})
        self.assertNotIn("来源待核对笔记", self.context("work-message", now=NOW + 1))

    def test_thought_mentioned_matches_various_phrasings_and_rejects_unrelated(self):
        c = {"excerpt": FIRST, "goal_title": "合作建造"}
        self.assertTrue(cadence.thought_mentioned(c, "主人我刚才看视频，有个双人机关观察特别巧妙！"))
        self.assertTrue(cadence.thought_mentioned(c, "关于合作建造，我留了一段双人机关的想法"))
        self.assertFalse(cadence.thought_mentioned(c, "好的，我先去修个 bug：TypeError: NoneType"))
        self.assertFalse(cadence.thought_mentioned(c, "今天天气真好，出去散步吧"))
        self.assertFalse(cadence.thought_mentioned(c, ""))
        self.assertFalse(cadence.thought_mentioned(None, "双人机关观察"))

    def test_discussed_thought_is_acknowledged_and_permanently_cleared(self):
        held(self.store)
        prompt = self.context("message-one")
        self.assertIn(FIRST, prompt)
        cad = cadence.ExpressionCadence(self.store)
        reply = "主人，我之前看到一个双人机关观察，里面的配合好默契啊！"
        ack = cad.acknowledge_if_discussed(SCOPE, reply, now=NOW + 1)
        self.assertTrue(ack)

        ack_rows = self.store.db.execute(
            "SELECT time, details FROM events WHERE scope=? AND kind=?",
            (SCOPE, cadence.ACK_EVENT_KIND),
        ).fetchall()
        self.assertEqual(len(ack_rows), 1)
        details = json.loads(ack_rows[0]["details"])
        self.assertEqual(details["excerpt"], FIRST)

        self.assertEqual(self.snapshot(now=NOW + 2)["held_thoughts"], [])
        self.assertNotIn(FIRST, self.context("message-later", now=NOW + 6 * 3600 + 1))

    def test_unmentioned_thought_remains_held_for_later_retry(self):
        held(self.store)
        self.assertIn(FIRST, self.context("message-one"))
        cad = cadence.ExpressionCadence(self.store)
        reply = "好的，这是二分查找的实现：def binary_search(): ..."
        ack = cad.acknowledge_if_discussed(SCOPE, reply, now=NOW + 1)
        self.assertFalse(ack)

        self.assertEqual(len(self.snapshot(now=NOW + 2)["held_thoughts"]), 1)
        self.assertNotIn(FIRST, self.context("during-cooldown", now=NOW + 3600))
        self.assertIn(FIRST, self.context("after-cooldown", now=NOW + 6 * 3600))

    def test_work_thought_acknowledged_and_cleared_with_delivery_update(self):
        source, work, _ = self.checked_work()
        held(self.store, "整理好啦 — 《小笔记》第1版摘录", work["body_hash"], kind="work",
             work_id=work["id"], revision=1, goal_id=work["goal_id"],
             delivery_id=f"work:{work['id']}:1")
        self.store.db.execute("INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?,?,?,?)",
                              (f"work:{work['id']}:1", SCOPE, "work", work["body_hash"], "blocked", NOW - 10, None, "{}"))
        self.assertIn("整理好啦 — 《小笔记》第1版摘录", self.context("work-message"))
        cad = cadence.ExpressionCadence(self.store)
        reply = "主人，我整理好啦，这是小笔记的草稿你看看怎么样"
        ack = cad.acknowledge_if_discussed(SCOPE, reply, now=NOW + 1)
        self.assertTrue(ack)

        row = self.store.db.execute("SELECT status FROM deliveries WHERE id=?", (f"work:{work['id']}:1",)).fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(self.snapshot(now=NOW + 2)["held_thoughts"], [])

    def test_event_attribute_attachment_and_cleanup(self):
        held(self.store)
        event = fixtures.FakeEvent(event_id="evt-with-attr")
        self.assertIn(FIRST, cadence.ExpressionCadence(self.store).context(SCOPE, self.cfg, "evt-with-attr", now=NOW, event=event))
        self.assertTrue(hasattr(event, "_df_cadence_selected"))
        self.assertEqual(event._df_cadence_selected["excerpt"], FIRST)

        cad = cadence.ExpressionCadence(self.store)
        ack = cad.acknowledge_if_discussed(SCOPE, "这是那个双人机关观察", event=event, now=NOW + 1)
        self.assertTrue(ack)
        self.assertFalse(hasattr(event, "_df_cadence_selected"))

    def test_goal_title_alone_does_not_acknowledge_held_thought(self):
        candidate = {
            "held_id": "h_title_test",
            "goal_id": "g_test",
            "goal_title": "研究合作机制",
            "excerpt": "双人机关的建造规则与协作配合细节",
        }
        reply_title_only = "我目前正在执行的任务是研究合作机制，请主人放心。"
        self.assertFalse(cadence.thought_mentioned(candidate, reply_title_only))

        cad = cadence.ExpressionCadence(self.store)
        event = fixtures.FakeEvent()
        event._df_cadence_selected = candidate
        ack = cad.acknowledge_if_discussed(SCOPE, reply_title_only, event=event, now=NOW + 1)
        self.assertFalse(ack)
        ack_count = self.store.db.execute(
            "SELECT count(*) FROM events WHERE scope=? AND kind=?",
            (SCOPE, cadence.ACK_EVENT_KIND),
        ).fetchone()[0]
        self.assertEqual(ack_count, 0)

        reply_with_fragment = "关于研究合作机制，我刚才看了双人机关的建造规则，很有意思！"
        self.assertTrue(cadence.thought_mentioned(candidate, reply_with_fragment))
        ack2 = cad.acknowledge_if_discussed(SCOPE, reply_with_fragment, event=event, now=NOW + 2)
        self.assertTrue(ack2)

    def test_acknowledge_delivery_only_updates_blocked_status(self):
        self.store.db.execute(
            "CREATE TABLE IF NOT EXISTS deliveries ("
            "id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL, "
            "fingerprint TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, "
            "receipt TEXT, meta TEXT NOT NULL)"
        )
        self.store.db.execute("INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?,?,?,?)",
                              ("del_blocked", SCOPE, "thought", "fp_b", "blocked", NOW - 10, None, "{}"))
        self.store.db.execute("INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?,?,?,?)",
                              ("del_sending", SCOPE, "thought", "fp_s", "sending", NOW - 10, core.canonical({"in_flight": True}), "{}"))
        self.store.db.execute("INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?,?,?,?)",
                              ("del_sent", SCOPE, "thought", "fp_ok", "sent", NOW - 10, core.canonical({"message_id": "qq_real_receipt"}), "{}"))

        cad = cadence.ExpressionCadence(self.store)

        cand_blocked = {"held_id": "h_b", "delivery_id": "del_blocked", "excerpt": "测试阻断的分享内容"}
        ev_b = fixtures.FakeEvent()
        ev_b._df_cadence_selected = cand_blocked
        self.assertTrue(cad.acknowledge_if_discussed(SCOPE, "这是测试阻断的分享内容", event=ev_b, now=NOW + 1))
        row_b = self.store.db.execute("SELECT status, receipt FROM deliveries WHERE id='del_blocked'").fetchone()
        self.assertEqual(row_b["status"], "sent")
        self.assertIn("chat_discussed", row_b["receipt"])

        cand_sending = {"held_id": "h_s", "delivery_id": "del_sending", "excerpt": "测试在途的分享内容"}
        ev_s = fixtures.FakeEvent()
        ev_s._df_cadence_selected = cand_sending
        self.assertTrue(cad.acknowledge_if_discussed(SCOPE, "这是测试在途的分享内容", event=ev_s, now=NOW + 2))
        row_s = self.store.db.execute("SELECT status, receipt FROM deliveries WHERE id='del_sending'").fetchone()
        self.assertEqual(row_s["status"], "sending")
        self.assertEqual(json.loads(row_s["receipt"]), {"in_flight": True})

        cand_sent = {"held_id": "h_ok", "delivery_id": "del_sent", "excerpt": "测试已发出的分享内容"}
        ev_ok = fixtures.FakeEvent()
        ev_ok._df_cadence_selected = cand_sent
        self.assertTrue(cad.acknowledge_if_discussed(SCOPE, "这是测试已发出的分享内容", event=ev_ok, now=NOW + 3))
        row_ok = self.store.db.execute("SELECT status, receipt FROM deliveries WHERE id='del_sent'").fetchone()
        self.assertEqual(row_ok["status"], "sent")
        self.assertEqual(json.loads(row_ok["receipt"]), {"message_id": "qq_real_receipt"})

    def test_repeated_excerpt_segment_does_not_double_count(self):
        candidate = {
            "held_id": "h_dup",
            "excerpt": "可以啊，真的可以啊",
        }
        reply_single = "可以啊，我觉得没问题。"
        self.assertFalse(cadence.thought_mentioned(candidate, reply_single))

    def test_short_excerpt_requires_full_match(self):
        cand_short = {
            "held_id": "h_short",
            "excerpt": "记得注意休息",
        }
        self.assertFalse(cadence.thought_mentioned(cand_short, "主人平时注意休息哦"))
        self.assertTrue(cadence.thought_mentioned(cand_short, "主人记得注意休息哦"))

        cand_generic = {
            "held_id": "h_generic",
            "excerpt": "注意休息",
        }
        self.assertFalse(cadence.thought_mentioned(cand_generic, "主人注意休息"))
        cand_generic_with_goal = {
            "held_id": "h_generic_goal",
            "excerpt": "注意休息",
            "goal_title": "合作机制研究",
        }
        self.assertTrue(cadence.thought_mentioned(cand_generic_with_goal, "关于合作机制研究，主人注意休息"))

    def test_message_key_mismatch_does_not_acknowledge_wrong_thought(self):
        # Seed A and select for msg_A
        held(self.store, "想法A：深海热泉生物群", "fp_a", at=NOW - 30)
        self.context("msg_A", now=NOW - 25)

        # Seed B and select for msg_B
        held(self.store, "想法B：珊瑚白化修复策略", "fp_b", at=NOW - 20)
        self.context("msg_B", now=NOW - 15)

        key_a = core.digest([SCOPE, "msg_A"])
        key_b = core.digest([SCOPE, "msg_B"])

        cad = cadence.ExpressionCadence(self.store)
        reply_a = "我之前看到关于深海热泉生物群的资料，很有趣！"

        # Calling ack for msg_B (which holds thought B) must NOT acknowledge thought A
        ack_b = cad.acknowledge_if_discussed(SCOPE, reply_a, now=NOW, message_key=key_b)
        self.assertFalse(ack_b)

        # Calling ack for msg_A (which holds thought A) correctly acknowledges thought A
        ack_a = cad.acknowledge_if_discussed(SCOPE, reply_a, now=NOW, message_key=key_a)
        self.assertTrue(ack_a)

    def test_non_string_excerpt_or_goal_does_not_throw(self):
        self.assertFalse(cadence.thought_mentioned({"excerpt": 123}, "123"))
        self.assertFalse(cadence.thought_mentioned({"excerpt": None}, "None"))
        self.assertFalse(cadence.thought_mentioned({"excerpt": "测试内容", "goal_title": 456}, "456"))
        self.assertFalse(cadence.thought_mentioned(None, "hello"))
        self.assertFalse(cadence.thought_mentioned({}, None))



class ExpressionCadenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = life_tests.LifeIntegrationTests.asyncSetUp
    asyncTearDown = life_tests.LifeIntegrationTests.asyncTearDown
    save_config = life_tests.LifeIntegrationTests.save_config
    enable = expression_tests.ExpressionIntegrationTests.enable
    request = staticmethod(expression_tests.ExpressionIntegrationTests.request)

    def seed(self):
        self.enable()
        held(self.plugin.store)

    async def hook(self, event):
        req = self.request()
        # test_bili_activity.load_plugin() imports the plugin inside a temporary
        # sys.modules patch, so main.py uses module objects different from the
        # ones imported at the top of this file. Patch the clock in main's
        # module globals as well, otherwise fixture timestamps look like future
        # records and are filtered out.
        cadence_globals = fixtures.main.ExpressionCadence.context.__globals__
        with patch.object(expression, "_clock", return_value=NOW), \
                patch.object(cadence, "_clock", return_value=NOW), \
                patch.dict(cadence_globals, {"_clock": lambda _: NOW}):
            await self.plugin.guard_phone(event, req)
        return req.system_prompt

    async def test_owner_foreground_reuses_same_message_without_repeated_exposure(self):
        self.seed()
        first = await self.hook(fixtures.FakeEvent(event_id="chat-one"))
        replay = await self.hook(fixtures.FakeEvent(event_id="chat-one"))
        next_message = await self.hook(fixtures.FakeEvent(event_id="chat-two"))
        self.assertTrue(first.startswith("原有人格\nEmotionAI 原有上下文"))
        self.assertIn(FIRST, first)
        self.assertIn(FIRST, replay)
        self.assertNotIn(FIRST, next_message)
        self.assertEqual(len(offers(self.plugin.store)), 2)
        self.assertEqual(self.calls, [])
        self.delivery.send.assert_not_called()
        self.plugin.flush_memory.assert_not_called()

    async def test_missing_event_message_id_never_exposes_or_records(self):
        self.seed()
        event = fixtures.FakeEvent(event_id=None)
        value = await self.hook(event)
        self.assertNotIn(FIRST, value)
        self.assertEqual(offers(self.plugin.store), [])

    async def test_background_and_reply_runs_never_expose_or_record_held_thoughts(self):
        self.seed()
        self.plugin.life_runs.add("background")
        self.plugin.reply_runs = {"reply": {}}
        for run_id in ("background", "reply", "other-autonomous-run"):
            with self.subTest(run_id=run_id):
                value = await self.hook(fixtures.FakeEvent(run_id=run_id))
                self.assertNotIn(FIRST, value)
                self.assertEqual(offers(self.plugin.store), [])
        self.delivery.send.assert_not_called()

    async def test_nonowner_group_and_disabled_hooks_do_not_record(self):
        self.seed()
        for event in (fixtures.FakeEvent(scope=OTHER), fixtures.FakeEvent(user="98765")):
            with self.subTest(scope=event.unified_msg_origin, user=event.get_sender_id()):
                self.assertNotIn(FIRST, await self.hook(event))
                self.assertEqual(offers(self.plugin.store), [])
        self.config["self_expression_enabled"] = False
        self.save_config()
        self.assertNotIn(FIRST, await self.hook(fixtures.FakeEvent()))
        self.assertEqual(offers(self.plugin.store), [])

    async def test_dnd_and_life_pause_do_not_expose_or_record(self):
        self.seed()
        with patch.object(main, "quiet_now", return_value=True):
            self.assertNotIn(FIRST, await self.hook(fixtures.FakeEvent()))
        self.plugin.store.setting("life_paused:" + SCOPE, True)
        self.assertNotIn(FIRST, await self.hook(fixtures.FakeEvent()))
        self.assertEqual(offers(self.plugin.store), [])

    async def test_review_tool_keeps_candidates_without_consuming_them(self):
        self.seed()
        held(self.plugin.store, SECOND, "second", at=NOW - 20)
        await self.hook(fixtures.FakeEvent(event_id="foreground"))
        before = list(self.plugin.store.db.iterdump())
        expression_globals = fixtures.main.SelfExpression.snapshot.__globals__
        with patch.object(expression, "_clock", return_value=NOW), \
                patch.dict(expression_globals, {"_clock": lambda _: NOW}):
            review = json.loads(await self.plugin.self_reflect(fixtures.FakeEvent()))
        self.assertEqual(len(review["held_thoughts"]), 2)
        self.assertEqual(list(self.plugin.store.db.iterdump()), before)
        self.assertEqual(len(offers(self.plugin.store)), 1)
        self.assertEqual(self.calls, [])
        self.delivery.send.assert_not_called()

    async def test_decorating_result_acknowledges_discussed_thought(self):
        self.seed()
        event = fixtures.FakeEvent(event_id="chat-turn")
        req = self.request()
        cadence_globals = fixtures.main.ExpressionCadence.context.__globals__
        with patch.object(expression, "_clock", return_value=NOW), \
                patch.object(cadence, "_clock", return_value=NOW), \
                patch.dict(cadence_globals, {"_clock": lambda _: NOW}):
            await self.plugin.guard_phone(event, req)
        self.assertIn(FIRST, req.system_prompt)
        self.assertTrue(hasattr(event, "_df_cadence_selected"))

        result = types.SimpleNamespace(
            is_llm_result=lambda: True,
            get_plain_text=lambda: "主人，我之前看到一段双人机关观察，想和你聊聊！",
        )
        event.get_result = lambda: result
        with patch.object(expression, "_clock", return_value=NOW + 1), \
                patch.object(cadence, "_clock", return_value=NOW + 1), \
                patch.dict(cadence_globals, {"_clock": lambda _: NOW + 1}):
            await self.plugin.check_held_thoughts_discussed(event)

        view = cadence.SelfExpression(self.plugin.store)
        self.assertEqual(view.snapshot(SCOPE, self.plugin.cfg(), now=NOW + 2)["held_thoughts"], [])

        req2 = self.request()
        with patch.object(expression, "_clock", return_value=NOW + 6 * 3600 + 1), \
                patch.object(cadence, "_clock", return_value=NOW + 6 * 3600 + 1), \
                patch.dict(cadence_globals, {"_clock": lambda _: NOW + 6 * 3600 + 1}):
            await self.plugin.guard_phone(fixtures.FakeEvent(event_id="later-chat"), req2)
        self.assertNotIn(FIRST, req2.system_prompt)

    async def test_decorating_result_ignores_unrelated_reply(self):
        self.seed()
        event = fixtures.FakeEvent(event_id="chat-turn-unrelated")
        req = self.request()
        cadence_globals = fixtures.main.ExpressionCadence.context.__globals__
        with patch.object(expression, "_clock", return_value=NOW), \
                patch.object(cadence, "_clock", return_value=NOW), \
                patch.dict(cadence_globals, {"_clock": lambda _: NOW}):
            await self.plugin.guard_phone(event, req)
        self.assertIn(FIRST, req.system_prompt)

        result = types.SimpleNamespace(
            is_llm_result=lambda: True,
            get_plain_text=lambda: "好的，我已经帮你处理好了这个文件。",
        )
        event.get_result = lambda: result
        with patch.object(expression, "_clock", return_value=NOW + 1), \
                patch.object(cadence, "_clock", return_value=NOW + 1), \
                patch.dict(cadence_globals, {"_clock": lambda _: NOW + 1}):
            await self.plugin.check_held_thoughts_discussed(event)

        view = cadence.SelfExpression(self.plugin.store)
        self.assertEqual(len(view.snapshot(SCOPE, self.plugin.cfg(), now=NOW + 2)["held_thoughts"]), 1)

    async def test_decorating_result_guard_conditions_and_exception_handling(self):
        self.seed()
        view = cadence.SelfExpression(self.plugin.store)

        # 1. Other scope (group message)
        ev_group = fixtures.FakeEvent(event_id="t1", scope=OTHER)
        ev_group.get_result = lambda: types.SimpleNamespace(is_llm_result=lambda: True, get_plain_text=lambda: FIRST)
        await self.plugin.check_held_thoughts_discussed(ev_group)
        self.assertEqual(len(view.snapshot(SCOPE, self.plugin.cfg(), now=NOW + 1)["held_thoughts"]), 1)

        # 2. Other sender
        ev_other = fixtures.FakeEvent(event_id="t2", user="99999")
        ev_other.get_result = lambda: types.SimpleNamespace(is_llm_result=lambda: True, get_plain_text=lambda: FIRST)
        await self.plugin.check_held_thoughts_discussed(ev_other)
        self.assertEqual(len(view.snapshot(SCOPE, self.plugin.cfg(), now=NOW + 1)["held_thoughts"]), 1)

        # 3. df_run_id present (autonomous run)
        ev_run = fixtures.FakeEvent(event_id="t3", run_id="auto_run_1")
        ev_run.get_result = lambda: types.SimpleNamespace(is_llm_result=lambda: True, get_plain_text=lambda: FIRST)
        await self.plugin.check_held_thoughts_discussed(ev_run)
        self.assertEqual(len(view.snapshot(SCOPE, self.plugin.cfg(), now=NOW + 1)["held_thoughts"]), 1)

        # 4. quiet_now or life_paused
        ev_paused = fixtures.FakeEvent(event_id="t4")
        ev_paused.get_result = lambda: types.SimpleNamespace(is_llm_result=lambda: True, get_plain_text=lambda: FIRST)
        self.plugin.store.setting("life_paused:" + SCOPE, True)
        await self.plugin.check_held_thoughts_discussed(ev_paused)
        self.assertEqual(len(view.snapshot(SCOPE, self.plugin.cfg(), now=NOW + 1)["held_thoughts"]), 1)
        self.plugin.store.setting("life_paused:" + SCOPE, False)

        with patch.object(main, "quiet_now", return_value=True):
            await self.plugin.check_held_thoughts_discussed(ev_paused)
        self.assertEqual(len(view.snapshot(SCOPE, self.plugin.cfg(), now=NOW + 1)["held_thoughts"]), 1)

        # 5. acknowledge_if_discussed raises exception -> caught and does not throw
        ev_err = fixtures.FakeEvent(event_id="t5")
        ev_err.get_result = lambda: types.SimpleNamespace(is_llm_result=lambda: True, get_plain_text=lambda: FIRST)
        with patch.object(cadence.ExpressionCadence, "acknowledge_if_discussed", side_effect=RuntimeError("db locked")):
            await self.plugin.check_held_thoughts_discussed(ev_err)



if __name__ == "__main__":
    unittest.main()

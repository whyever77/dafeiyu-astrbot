"""Offline foreground thought cadence: exposure is not a delivery receipt."""
import importlib
import json
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


if __name__ == "__main__":
    unittest.main()

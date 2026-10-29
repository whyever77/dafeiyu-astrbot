"""No real QQ: shared accounting, lifetime boundaries and source integration."""
import asyncio
import importlib
import json
import tempfile
import time
import types
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch

import test_bili_activity as fixtures
import test_bili_life_integration as life_fixtures
import test_bili_life_ops as ops_fixtures

main, core = fixtures.main, fixtures.core
contact = importlib.import_module("bili_activity_test.contact")
SCOPE, FakeEvent = fixtures.SCOPE, fixtures.FakeEvent


class ContactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Store(self.temp.name)
        self.ledger = contact.ContactLedger(self.store)
        self.cfg = main.DEFAULTS | {"owner_scope": SCOPE, "owner_id": "12345", "dnd_ranges": [],
            "contact_coordination_enabled": True, "contact_owner_quiet_minutes": 0,
            "contact_cooldown_minutes": 1, "contact_unanswered_limit": 20}
        self.now = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def reserve(self, key, source="life", text=None):
        return self.ledger.reserve(SCOPE, source, key, "chat" if source == "proactive_chat" else "thought",
                                   contact.content_hash(text or key), self.cfg, self.now)

    def sent(self, key, source="life"):
        ticket = self.reserve(key, source)
        self.ledger.dispatch(ticket, self.cfg, self.now)
        self.ledger.finish(ticket, "sent", "101", self.now)
        return ticket

    def test_both_sources_share_cooldown_and_daily_limit(self):
        self.cfg["contact_daily_limit"] = 2
        self.sent("life")
        with self.assertRaisesRegex(PermissionError, "刚主动联系"):
            self.reserve("chat", "proactive_chat")
        self.now += 61
        self.sent("chat", "proactive_chat")
        self.now += 61
        with self.assertRaisesRegex(PermissionError, "额度"):
            self.reserve("third")
        self.assertEqual(self.ledger.status(SCOPE, self.cfg, self.now)["used"], 2)

    def test_second_connection_cannot_reserve_same_capacity(self):
        self.cfg["contact_daily_limit"] = 1
        self.reserve("one")
        other = core.Store(self.temp.name)
        try:
            with self.assertRaises(PermissionError):
                contact.ContactLedger(other).reserve(SCOPE, "proactive_chat", "two", "chat",
                    contact.content_hash("two"), self.cfg, self.now)
        finally:
            other.close()

    def test_same_key_replay_and_changed_arguments_are_refused(self):
        self.sent("one")
        with self.assertRaises(PermissionError):
            self.reserve("one")
        with self.assertRaisesRegex(ValueError, "内容不能改变"):
            self.reserve("one", text="changed")

    def test_unknown_send_survives_restart_and_blocks_same_content_next_day(self):
        ticket = self.reserve("one", text="same")
        self.ledger.dispatch(ticket, self.cfg, self.now)
        self.ledger.finish(ticket, "uncertain", now=self.now)
        self.store.close()
        self.store = core.Store(self.temp.name)
        self.ledger = contact.ContactLedger(self.store)
        self.now += 86401
        with self.assertRaisesRegex(PermissionError, "相同内容"):
            self.reserve("new-key", "proactive_chat", "same")
        self.reserve("different", "proactive_chat")

    def test_shanghai_day_resets_capacity_without_clearing_recent_cooldown(self):
        self.cfg["contact_daily_limit"] = 1
        self.now = datetime(2026, 10, 29, 23, 59, 50, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
        self.sent("one")
        self.now += 20
        self.assertEqual(self.ledger.status(SCOPE, self.cfg, self.now)["used"], 0)
        with self.assertRaisesRegex(PermissionError, "刚主动联系"):
            self.reserve("two")
        self.now += 61
        self.reserve("two")

    def test_unanswered_cap_and_real_owner_event_reopens_after_quiet_period(self):
        self.cfg.update(contact_unanswered_limit=1, contact_owner_quiet_minutes=5)
        self.sent("one")
        self.now += 61
        with self.assertRaisesRegex(PermissionError, "没有收到主人回复"):
            self.reserve("two")
        self.ledger.owner_message(SCOPE, "human", self.now)
        with self.assertRaisesRegex(PermissionError, "主人刚在聊天"):
            self.reserve("two")
        self.now += 301
        self.ledger.owner_message(SCOPE, "human", self.now)  # QQ replay is not a fresh response.
        self.reserve("two")

    def test_cancel_before_dispatch_releases_capacity_after_dispatch_does_not(self):
        ticket = self.reserve("before")
        self.ledger.finish(ticket, "uncertain", now=self.now)
        self.assertEqual(self.ledger.status(SCOPE, self.cfg, self.now)["used"], 0)
        ticket = self.reserve("after")
        self.ledger.dispatch(ticket, self.cfg, self.now)
        self.ledger.finish(ticket, "cancelled", now=self.now)
        self.assertEqual(self.ledger.status(SCOPE, self.cfg, self.now)["recent"][0]["status"], "uncertain")

    def test_reserved_crash_expires_without_a_permanent_slot_deadlock(self):
        self.reserve("abandoned-before-io")
        self.now += 601
        self.reserve("next")
        self.assertEqual(self.ledger.status(SCOPE, self.cfg, self.now)["used"], 1)

    def test_dispatch_crash_cooldown_does_not_slide_forward_on_each_rejection(self):
        self.cfg["contact_cooldown_minutes"] = 10
        ticket = self.reserve("crash")
        self.ledger.dispatch(ticket, self.cfg, self.now)
        self.now += 601
        with self.assertRaises(PermissionError):
            self.reserve("next")
        self.now += 601
        self.reserve("next")
        self.assertEqual(self.store.db.execute("SELECT status FROM contact_attempts WHERE id=?", (ticket,)).fetchone()[0], "uncertain")

    def test_dispatch_rechecks_switch_dnd_owner_message_and_midnight_capacity(self):
        ticket = self.reserve("one")
        for changes in ({"contact_coordination_enabled": False}, {"dnd_ranges": [["00:00", "00:00"]]}, {"contact_daily_limit": 0}):
            with self.subTest(changes=changes), self.assertRaises(PermissionError):
                self.ledger.dispatch(ticket, self.cfg | changes, self.now)
        self.ledger.owner_message(SCOPE, "arrived", self.now)
        with self.assertRaises(PermissionError):
            self.ledger.dispatch(ticket, self.cfg | {"contact_owner_quiet_minutes": 5}, self.now)
        self.assertIsNone(self.store.db.execute("SELECT dispatched FROM contact_attempts").fetchone()[0])

    def test_scope_isolation_and_redacted_status(self):
        self.sent("one")
        self.assertEqual(self.ledger.status("qq:FriendMessage:999", self.cfg, self.now)["recent"], [])
        with self.assertRaises(PermissionError):
            self.ledger.check("qq:GroupMessage:12345", self.cfg, self.now)
        status = json.dumps(self.ledger.status(SCOPE, self.cfg, self.now))
        self.assertNotIn("fingerprint", status)
        self.assertNotIn('"text"', status)
        self.assertNotIn('"key"', status)

    def test_missing_receipt_is_unknown_and_transport_boolean_is_only_reported(self):
        ticket = self.reserve("one")
        self.ledger.dispatch(ticket, self.cfg, self.now)
        self.ledger.finish(ticket, "sent", now=self.now)
        self.assertEqual(self.ledger.status(SCOPE, self.cfg, self.now)["recent"][0]["status"], "uncertain")
        self.now += 61
        ticket = self.reserve("two", "proactive_chat")
        self.ledger.dispatch(ticket, self.cfg, self.now)
        self.ledger.finish(ticket, "reported", now=self.now)
        row = self.ledger.status(SCOPE, self.cfg, self.now)["recent"][0]
        self.assertEqual(row["status"], "reported")
        self.assertIsNone(row["message_id"])

    def test_old_config_and_strict_limits(self):
        self.assertFalse(main.DEFAULTS["contact_coordination_enabled"])
        for name, value in (("contact_daily_limit", -1), ("contact_daily_limit", True),
                            ("contact_coordination_enabled", "false"), ("contact_cooldown_minutes", 0)):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                contact.validate_config(self.cfg | {name: value})


class ContactIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = life_fixtures.LifeIntegrationTests.asyncSetUp
    asyncTearDown = life_fixtures.LifeIntegrationTests.asyncTearDown
    save_config = life_fixtures.LifeIntegrationTests.save_config
    begin = life_fixtures.LifeIntegrationTests.begin
    create = life_fixtures.LifeIntegrationTests.create

    def enable(self):
        self.config.update(contact_coordination_enabled=True, contact_owner_quiet_minutes=0)
        self.save_config()

    async def test_life_share_blocks_next_source_but_not_foreground_reply(self):
        self.enable()
        event = self.begin()
        await self.plugin.share_thought(event, "真实活动里的感想")
        api = main.ContactAPI(self.plugin)
        with self.assertRaises(PermissionError):
            api.reserve(SCOPE, "chat", "再聊一句")
        await self.plugin.share_thought(FakeEvent(), "主人主动问的答复")
        self.assertEqual(self.delivery.send.await_count, 2)
        self.assertEqual(self.plugin.contact_ledger().status(SCOPE, self.config)["used"], 1)

    async def test_inflight_cancellation_is_unknown_and_owner_event_is_scoped(self):
        self.enable()
        event = self.begin()
        self.delivery.send.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.plugin.share_thought(event, "已开始发送")
        self.assertEqual(self.plugin.contact_ledger().status(SCOPE, self.config)["recent"][0]["status"], "uncertain")
        await self.plugin.incoming(FakeEvent(user="999", text="假的主人"))
        self.assertEqual(self.plugin.contact_ledger().owner_time(SCOPE), 0)
        await self.plugin.incoming(FakeEvent(text="在呢", event_id="human"))
        self.assertGreater(self.plugin.contact_ledger().owner_time(SCOPE), 0)

    async def test_blocked_background_proposal_does_not_wait_for_unseen_approval(self):
        self.enable()
        event = self.begin()
        await self.create(event)
        await self.plugin.share_thought(event, "刚分享过")
        with self.assertRaises(PermissionError):
            await self.plugin.propose(event, "follow", '{"uid":99}', "还想关注")
        rows = self.plugin.store.db.execute("SELECT status FROM proposals").fetchall()
        self.assertTrue(all(r["status"] == "blocked" for r in rows))
        self.assertEqual(self.delivery.send.await_count, 1)

    async def test_disabled_coordination_does_not_create_contact_tables(self):
        await self.plugin.share_thought(self.begin(), "原来的分享流程")
        self.assertIsNone(self.plugin.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='contact_attempts'").fetchone())
        self.delivery.send.assert_awaited_once()

    async def test_enabled_coordination_keeps_owner_approval_and_single_execution(self):
        self.enable()
        event = self.begin()
        await self.create(event)
        await self.plugin.propose(event, "follow", '{"uid":99}', "想关注")
        self.assertEqual(sum(n == "bili_execute" for n, _ in self.calls), 0)
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM proposals").fetchone()[0], "offered")
        await self.plugin.incoming(FakeEvent(event_id="approve", text="可以"))
        await self.plugin.incoming(FakeEvent(event_id="approve", text="可以"))
        self.assertEqual(sum(n == "bili_execute" for n, _ in self.calls), 1)
        self.assertEqual(self.plugin.contact_ledger().status(SCOPE, self.config)["used"], 1)


class ContactOpsTests(unittest.TestCase):
    setUp = ops_fixtures.LifeOpsTests.setUp
    tearDown = ops_fixtures.LifeOpsTests.tearDown
    run_ops = ops_fixtures.LifeOpsTests.run_ops
    config = ops_fixtures.LifeOpsTests.config

    def test_legacy_readonly_status_and_optin_preserve_unrelated_permissions(self):
        result = json.loads(self.run_ops("contacts"))
        self.assertFalse(result["enabled"])
        self.assertEqual(result["recent"], [])
        self.assertFalse(self.folder.exists())
        self.config(custom="keep")
        self.run_ops("switch", "contact_coordination_enabled", "on")
        cfg = ops_fixtures.ops.load_control(self.folder)
        self.assertTrue(cfg["contact_coordination_enabled"])
        self.assertEqual(cfg["custom"], "keep")
        self.assertFalse(cfg["enabled"])
        self.assertFalse(cfg["public_interactions"])
        self.run_ops("configure", "--contact-daily-limit", "0")
        self.assertEqual(ops_fixtures.ops.load_control(self.folder)["contact_daily_limit"], 0)


if __name__ == "__main__":
    unittest.main()

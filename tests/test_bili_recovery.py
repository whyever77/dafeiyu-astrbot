"""Offline recovery and cancellation regressions; no live accounts or transports."""
import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import test_bili_activity as fixtures
import test_bili_interact as integration

main, SCOPE, VIDEO = fixtures.main, fixtures.SCOPE, integration.VIDEO
InteractionBlocked = main.InteractionBlocked


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = integration.ControllerTests.asyncSetUp
    asyncTearDown = integration.ControllerTests.asyncTearDown
    save = integration.ControllerTests.save
    propose = integration.ControllerTests.propose
    approve = integration.ControllerTests.approve

    def row(self, pid):
        return dict(self.plugin.store.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone())

    async def unknown(self):
        self.api.timeout_post = True
        result = await self.propose()
        await self.approve()
        self.api.timeout_post = False
        self.assertEqual(self.row(result["proposal_id"])["status"], "uncertain")
        return result["proposal_id"]

    async def command(self, text, event_id="inspect", **kwargs):
        event = fixtures.FakeEvent(text=text, event_id=event_id, **kwargs)
        await self.plugin.incoming(event)
        return event

    async def inspect(self, pid):
        await self.command("/bili互动 清理不确定 " + pid)
        return self.plugin.store.get_setting("interaction_review:" + pid)

    async def resolve(self, pid, choice="abandoned"):
        review = await self.inspect(pid)
        return await self.command(f"/bili互动 清理不确定 {pid} {choice} 确认 {review['token']}", "confirm")

    async def executing(self):
        result = await self.propose("like", VIDEO | {"like": 1})
        return self.plugin.store.decide(SCOPE, "12345", "可以", "approve-direct")

    async def test_owner_resolution_releases_slot_without_resending_or_erasing_evidence(self):
        pid = await self.unknown()
        original = self.row(pid)["result"]
        before = await self.server._tool_manager.call_tool("bili_operation", {"operation_id": pid})
        await self.resolve(pid)
        self.assertEqual(self.row(pid)["status"], "resolved_abandoned")
        self.assertEqual(self.row(pid)["result"], original)
        self.assertEqual(await self.server._tool_manager.call_tool("bili_operation", {"operation_id": pid}), before)
        self.assertEqual(self.plugin.interactions().status(SCOPE, self.config)["pending"], [])
        result = await self.propose("like", VIDEO | {"like": 1}, fixtures.FakeEvent(event_id="new"))
        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(len(self.api.posts), 1)
        with self.assertRaises(InteractionBlocked):
            self.plugin.interactions().check(SCOPE, "new", "comment", VIDEO, self.config)
        self.assertEqual(self.plugin.store.db.execute("SELECT SUM(count) FROM daily_actions").fetchone()[0], 1)

    async def test_manual_verified_keeps_machine_verification_false(self):
        pid = await self.unknown()
        await self.resolve(pid, "verified")
        self.assertEqual(self.row(pid)["status"], "resolved_verified")
        self.assertFalse(json.loads(self.row(pid)["result"])["verified"])
        event = self.plugin.store.db.execute("SELECT details FROM events WHERE kind='uncertain_resolved'").fetchone()
        self.assertEqual(json.loads(event[0])["source"], "owner_confirmation")
        await self.command("/bili互动 清理不确定 " + pid + " verified 确认 old", "duplicate")
        self.assertEqual(len(self.api.posts), 1)

    async def test_reply_watch_resumes_after_owner_resolves_uncertain(self):
        await self.propose()
        await self.approve()
        self.api.timeout_post = True
        value = await self.propose("like", VIDEO | {"like": 1}, fixtures.FakeEvent(event_id="like"))
        await self.approve("like-approval")
        self.api.timeout_post = False
        self.config.update(reply_watch_enabled=True, chat_cooldown_seconds=0)
        self.save()
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: fixtures.FakeEvent(run_id=run_id))
        self.plugin.agent = AsyncMock()
        original = self.server._tool_manager.call_tool
        async def backend(name, args):
            if name == "bili_read" and args["operation"] == "replies":
                return {**VIDEO, "replies": [{"rpid": "1000", "root_rpid": "901", "uid": "99",
                    "ctime": time.time(), "message": "补充一个细节，因为这里有2个不同版本，所以参数需要核对。"}], "next_page": None}
            return await original(name, args)
        self.backend.json_call.side_effect = backend
        await self.plugin.poll_replies()
        self.plugin.agent.assert_not_awaited()
        await self.resolve(value["proposal_id"])
        await self.plugin.poll_replies()
        self.plugin.agent.assert_awaited_once()
        self.assertEqual(len(self.api.posts), 2)

    async def test_resolution_requires_fresh_explicit_owner_confirmation(self):
        pid = await self.unknown()
        await self.command(f"/bili互动 清理不确定 {pid} abandoned 确认 forged")
        self.assertEqual(self.row(pid)["status"], "uncertain")
        review = await self.inspect(pid)
        await self.command(f"/bili互动 清理不确定 {pid} abandoned 确认 {review['token']}", "foreign", user="stranger")
        self.assertEqual(self.row(pid)["status"], "uncertain")
        review["expires"] = 0
        self.plugin.store.setting("interaction_review:" + pid, review)
        await self.command(f"/bili互动 清理不确定 {pid} abandoned 确认 {review['token']}", "expired")
        self.assertEqual(self.row(pid)["status"], "uncertain")

    async def test_changed_ledger_invalidates_review(self):
        pid = await self.unknown()
        review = await self.inspect(pid)
        self.plugin.store.db.execute("UPDATE proposals SET result=? WHERE id=?", ('{"verified":false,"rpid":"999"}', pid))
        await self.command(f"/bili互动 清理不确定 {pid} abandoned 确认 {review['token']}", "changed")
        self.assertEqual(self.row(pid)["status"], "uncertain")

    async def test_resolved_unknown_like_does_not_authorize_same_payload_retry(self):
        self.api.timeout_post = True
        value = await self.propose("like", VIDEO | {"like": 1})
        await self.approve()
        self.api.timeout_post = False
        await self.resolve(value["proposal_id"])
        result = await self.propose("like", VIDEO | {"like": 1}, fixtures.FakeEvent(event_id="retry"))
        self.assertFalse(result["proposal_created"])
        self.assertIn("禁止重复", result["reason"])
        self.assertEqual(len(self.api.posts), 1)

    async def test_ledger_inspection_is_read_only_and_redacted(self):
        pid = await self.unknown()
        event = await self.command("/bili互动 清理不确定 " + pid)
        shown = event.send.await_args.args[0]
        self.assertIn("operation", shown)
        self.assertNotIn("secret-session", shown)
        self.assertNotIn("csrf", shown)
        self.assertEqual(len(self.api.posts), 1)
        self.backend.json_call.side_effect = TimeoutError("private")
        event = await self.command("/bili互动 清理不确定 " + pid, "offline")
        self.assertIn("不可读", event.send.await_args.args[0])
        self.assertEqual(self.row(pid)["status"], "uncertain")

    async def test_automatic_owner_boundary_rejection_finishes_blocked(self):
        self.config["interaction_mode"] = "auto_low_risk"
        self.save()
        preview = await self.server._tool_manager.call_tool("bili_preview", {"action": "like", "args": VIDEO | {"like": 1}})
        proposal, _ = self.plugin.store.propose(SCOPE, "12345", "like", preview["args"])
        with patch.object(self.plugin, "check_owner", side_effect=[self.plugin.cfg(), PermissionError("private")]):
            result = await self.plugin.automatic_proposal(fixtures.FakeEvent(), proposal, preview["target"])
        self.assertEqual(result["result"], "blocked")
        self.assertEqual(self.row(proposal["id"])["status"], "blocked")
        self.assertEqual(self.api.posts, [])

    async def test_cancel_during_spacing_is_blocked_without_attempt(self):
        proposal = await self.executing()
        self.plugin.store.setting("interaction_next:" + SCOPE, time.time() + 20)
        with patch.object(main.asyncio, "sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.plugin.execute_interaction(fixtures.FakeEvent(), proposal)
        self.assertEqual(self.row(proposal["id"])["status"], "blocked")
        self.assertEqual(self.plugin.store.db.execute("SELECT COUNT(*) FROM interactions").fetchone()[0], 0)
        self.assertEqual(self.api.posts, [])

    async def test_cancel_while_waiting_for_lock_is_blocked(self):
        proposal = await self.executing()
        async with self.plugin._interaction_lock:
            task = asyncio.create_task(self.plugin.execute_interaction(fixtures.FakeEvent(), proposal))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.row(proposal["id"])["status"], "blocked")
        self.assertEqual(self.api.posts, [])

    async def test_disabled_during_spacing_is_blocked(self):
        proposal = await self.executing()
        with patch.object(self.plugin, "check_owner", side_effect=[self.plugin.cfg(), PermissionError("stopped")]):
            await self.plugin.execute_interaction(fixtures.FakeEvent(), proposal)
        self.assertEqual(self.row(proposal["id"])["status"], "blocked")
        self.assertEqual(self.api.posts, [])

    async def test_cancel_after_dispatch_remains_uncertain(self):
        proposal = await self.executing()
        self.backend.json_call.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.plugin.execute_interaction(fixtures.FakeEvent(), proposal)
        self.assertEqual(self.row(proposal["id"])["status"], "uncertain")

    async def test_verified_visibility_errors_do_not_apply_account_risk(self):
        for code in (-101, -352, -412):
            value = self.plugin.interactions().finish(SCOPE, "run", "unused", "comment", VIDEO,
                {"verified": True, "visibility": "unknown", "risk_code": code}, self.config)
            self.assertFalse(value["risk_applied"])
        self.assertIsNone(self.plugin.store.get_setting("interaction_login:" + SCOPE))
        self.assertIsNone(self.plugin.store.get_setting("interaction_cooldown:" + SCOPE))

    def life_event(self):
        self.config.update(life_enabled=True, workshop_enabled=True, one_shot_verified=True)
        self.save()
        run_id = "read-during-cooldown"
        self.plugin.life_runs = {run_id}
        goal = self.plugin.store.life.create_goal(SCOPE, "Read fixture", "Check read permission", "Read info", "read-goal")
        self.plugin.store.life.claim_goal(SCOPE, goal["id"], run_id)
        self.plugin.run_goals = {run_id: goal["id"]}
        self.plugin.activity_budget().start_run(SCOPE, run_id, 60, self.config)
        return fixtures.FakeEvent(run_id=run_id)

    async def test_life_reads_bypass_interaction_cooldown_and_invalid_login(self):
        event = self.life_event()
        state = self.plugin.interactions()
        state.risk(SCOPE, "earlier-run", -412, self.config)
        until = self.plugin.store.get_setting("interaction_cooldown:" + SCOPE)
        self.assertGreater(until, time.time())
        for login in ("valid", "invalid"):
            self.plugin.store.setting("interaction_login:" + SCOPE, login)
            for operation in ("info", "comments"):
                with self.subTest(login=login, operation=operation):
                    before = self.backend.json_call.await_count
                    value = json.loads(await self.plugin.read(event, operation, VIDEO))
                    self.assertIsInstance(value, dict)
                    self.assertEqual(self.backend.json_call.await_count, before + 1)
                    self.backend.json_call.assert_awaited_with("bili_read", {"operation": operation, "args": VIDEO})
                    self.assertEqual(self.plugin.store.get_setting("interaction_cooldown:" + SCOPE), until)
                    self.assertEqual(self.plugin.store.get_setting("interaction_login:" + SCOPE), login)
        self.assertEqual(self.api.posts, [])

    async def test_cooldown_still_blocks_proposal_execute_and_reply_poll(self):
        proposal = await self.executing()
        self.config["reply_watch_enabled"] = True
        self.save()
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: fixtures.FakeEvent(run_id=run_id))
        self.plugin.agent = AsyncMock()
        self.plugin.interactions().risk(SCOPE, "earlier-run", -412, self.config)
        self.backend.json_call.reset_mock()
        event = fixtures.FakeEvent(run_id="blocked-run")
        offered = await self.propose("like", VIDEO | {"like": 1}, event)
        self.assertFalse(offered["proposal_created"])
        self.assertIn("冷却", offered["reason"])
        result = await self.plugin.execute_interaction(event, proposal)
        self.assertEqual(result["result"], "blocked")
        self.assertEqual(self.row(proposal["id"])["status"], "blocked")
        await self.plugin.poll_replies()
        poll = self.plugin.store.db.execute("SELECT details FROM events WHERE kind='reply_poll' ORDER BY seq DESC LIMIT 1").fetchone()
        self.assertEqual(json.loads(poll[0])["status"], "blocked")
        self.backend.json_call.assert_not_awaited()
        self.plugin.agent.assert_not_awaited()
        self.assertEqual(self.api.posts, [])

    async def test_read_exemption_preserves_owner_and_background_permissions(self):
        event = self.life_event()
        self.plugin.interactions().risk(SCOPE, "earlier-run", -412, self.config)
        for other in (fixtures.FakeEvent(scope=fixtures.OTHER_SCOPE), fixtures.FakeEvent(user="stranger")):
            with self.assertRaises(PermissionError):
                await self.plugin.read(other, "info", VIDEO)
        for key, value in (("enabled", False), ("dnd_ranges", [["00:00", "00:00"]]), ("life_enabled", False)):
            original = self.config[key]
            self.config[key] = value
            self.save()
            with self.assertRaises(PermissionError):
                await self.plugin.read(event, "info", VIDEO)
            self.config[key] = original
        self.config["workshop_enabled"] = False
        self.save()
        self.plugin.writing_runs = {"writing"}
        with self.assertRaises(PermissionError):
            await self.plugin.read(fixtures.FakeEvent(run_id="writing"), "info", VIDEO)
        self.plugin.method_runs = {"method"}
        with self.assertRaises(PermissionError):
            await self.plugin.read(fixtures.FakeEvent(run_id="method"), "info", VIDEO)
        self.plugin.run_goals.clear()
        with self.assertRaises(ValueError):
            await self.plugin.read(event, "info", VIDEO)
        self.backend.json_call.assert_not_awaited()

    async def test_status_can_refresh_login_in_stopped_run_without_resuming_writes(self):
        state = self.plugin.interactions()
        state.risk(SCOPE, "run", -101, self.config)
        state.risk(SCOPE, "run", -412, self.config)
        event = fixtures.FakeEvent(run_id="run")
        status = json.loads(await self.plugin.interaction_status(event))
        self.assertEqual(status["login"], "valid")
        self.assertGreater(status["cooldown_remaining_seconds"], 0)
        with self.assertRaises(InteractionBlocked):
            state.guard(SCOPE, "run")
        with self.assertRaises(PermissionError):
            await self.plugin.interaction_status(fixtures.FakeEvent(user="stranger"))

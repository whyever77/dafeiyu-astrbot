"""Offline interaction contracts: real controller/SQLite/Executor, fake Bili/QQ."""
import asyncio
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import test_bili_activity as fixtures
import test_bili_service as service_fixtures
from bili_api import BiliAPI, BiliError, Executor
from service import create_server, validated_args
from bili_activity_test.interactions import Interactions, InteractionBlocked, safe_result, validate_config

core, main, SCOPE = fixtures.core, fixtures.main, fixtures.SCOPE
BVID, COOKIES = service_fixtures.BVID, service_fixtures.COOKIES
VIDEO = {"bvid": BVID, "cid": 11}


class InteractAPI(service_fixtures.FixtureAPI):
    def __init__(self, folder):
        super().__init__(folder)
        self.liked, self.coins, self.dm_visible = 0, 0, True
        self.dm, self.risk_code, self.fail_verify = None, None, False

    async def request(self, path, params=None, method="GET", data=None, **kwargs):
        if method == "POST" and self.risk_code is not None:
            self.posts.append((path, data.copy()))
            raise BiliError("SESSDATA=do-not-leak https://secret.invalid", code=self.risk_code)
        if path in {"/x/v2/view/like", "/x/web-interface/coin/add", "/x/v2/dm/post"}:
            self.calls.append((path, params, method, data))
            self.posts.append((path, data.copy()))
            if self.timeout_post:
                raise TimeoutError("bili_jct=do-not-leak")
            if path.endswith("view/like"):
                self.liked = data["like"]
            elif path.endswith("coin/add"):
                self.coins += data["multiply"]
                if data["select_like"]:
                    self.liked = 1
            else:
                self.dm = data.copy()
                return {"dmid_str": "1234567890123456789"}
            return {}
        if path.endswith("archive/has/like"):
            if self.fail_verify:
                raise TimeoutError("full body and token")
            return self.liked
        if path.endswith("archive/coins"):
            return {"multiply": self.coins}
        return await super().request(path, params, method, data, **kwargs)

    async def get_bytes(self, url, limit):
        if self.dm and self.dm_visible and url.endswith(".xml"):
            return f'<i><d p="{self.dm["progress"] / 1000},1,25,1,0,0,u,1234567890123456789">{self.dm["msg"]}</d></i>'.encode()
        return await super().get_bytes(url, limit)


class ActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = InteractAPI(self.temp.name)
        self.executor = Executor(self.api, Path(self.temp.name) / "operations.sqlite3")

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_like_and_unlike_read_numeric_zero_and_replay(self):
        for index, liked in enumerate((1, 0)):
            args = validated_args("like", VIDEO | {"like": liked}, "42")
            value = await self.executor.execute(f"like-action-{index}", "like", args)
            self.assertTrue(value["verified"])
            self.assertEqual(self.api.posts[-1][1]["aid"], 123)
            self.assertEqual(self.api.posts[-1][1]["like"], liked)
            again = await self.executor.execute(f"like-action-{index}", "like", args)
            self.assertTrue(again["replayed"])
            self.assertEqual(len(self.api.posts), index + 1)
        with self.assertRaises(ValueError):
            await self.executor.execute("like-action-0", "like", VIDEO | {"like": 0})

    async def test_real_request_preserves_zero_and_only_numeric_error_code(self):
        for code in (0, 12014, -412, -352, -101):
            async def chunks(_):
                yield json.dumps({"code": code, "data": 0, "message": "SESSDATA=secret https://private"}).encode()
            response = MagicMock(status=200)
            response.content.iter_chunked = chunks
            response_cm = MagicMock()
            response_cm.__aenter__ = AsyncMock(return_value=response)
            client = MagicMock()
            client.request.return_value = response_cm
            session_cm = MagicMock()
            session_cm.__aenter__ = AsyncMock(return_value=client)
            with patch("bili_api.aiohttp.ClientSession", return_value=session_cm), patch.object(self.api, "_throttle", new=AsyncMock()):
                if code == 0:
                    self.assertEqual(await BiliAPI.request(self.api, "/x/web-interface/archive/has/like"), 0)
                else:
                    with self.assertRaises(BiliError) as error:
                        await BiliAPI.request(self.api, "/x/web-interface/archive/has/like")
                    self.assertEqual(error.exception.code, code)
                    self.assertNotIn("secret", str(error.exception))
                    self.assertNotIn("https", str(error.exception))

    async def test_coins_baseline_increment_and_optional_like(self):
        args = validated_args("coin", VIDEO | {"multiply": 2, "select_like": 1}, "42")
        value = await self.executor.execute("coins-action-1", "coin", args)
        self.assertTrue(value["verified"])
        self.assertEqual(self.api.posts[0][1]["multiply"], 2)
        self.assertEqual(self.api.liked, 1)
        self.assertTrue((await self.executor.execute("coins-action-1", "coin", args))["replayed"])
        self.assertEqual(len(self.api.posts), 1)
        with self.assertRaises(ValueError):
            await self.api.prepare_write("coin", VIDEO | {"multiply": 1})

    async def test_coin_does_not_verify_preexisting_total_or_missing_like(self):
        identity = await self.api.identity(BVID, 11)
        identity["coins_before"] = 1
        self.api.coins = 1
        self.assertFalse((await self.api.verify("coin", {"multiply": 1}, {}, identity))["verified"])
        self.api.coins = 2
        self.assertFalse((await self.api.verify("coin", {"multiply": 1, "select_like": 1}, {}, identity))["verified"])

    async def test_danmaku_payload_milliseconds_and_readback_id(self):
        args = validated_args("danmaku", VIDEO | {"message": "补充说明", "progress": 1250, "color": 0, "fontsize": 18}, "42")
        value = await self.executor.execute("danmaku-action", "danmaku", args)
        self.assertTrue(value["verified"])
        self.assertEqual(value["visibility"], "public")
        path, data = self.api.posts[0]
        self.assertEqual(path, "/x/v2/dm/post")
        self.assertEqual((data["oid"], data["progress"], data["color"], data["fontsize"]), (11, 1250, 0, 18))
        self.assertTrue((await self.executor.execute("danmaku-action", "danmaku", args))["replayed"])
        with self.assertRaises(ValueError):
            await self.executor.execute("danmaku-action", "danmaku", args | {"message": "changed"})
        self.assertEqual(len(self.api.posts), 1)

    async def test_delayed_danmaku_is_unknown_and_not_retried(self):
        self.api.dm_visible = False
        args = validated_args("danmaku", VIDEO | {"message": "说明", "progress": 0}, "42")
        result = await self.executor.execute("delayed-danmaku", "danmaku", args)
        self.assertFalse(result["verified"])
        self.assertEqual(result["visibility"], "unknown")
        self.api.dm_visible = True
        again = await Executor(self.api, self.executor.state_path).execute("delayed-danmaku", "danmaku", args)
        self.assertFalse(again["verified"])
        self.assertEqual(len(self.api.posts), 1)

    async def test_new_action_validation_rejects_invalid_fields_and_numbers(self):
        cases = [("like", {"like": v}) for v in (-1, 2, True, 1.0, "01")]
        cases += [("coin", {"multiply": v}) for v in (0, 3, True, "2.0")]
        cases += [("coin", {"multiply": 1, "select_like": 2})]
        cases += [("danmaku", {"message": "text", "progress": 1, field: value}) for field, value in (
            ("progress", -1), ("progress", True), ("progress", 0.5), ("color", 16777216),
            ("fontsize", 19), ("mode", 2), ("pool", 1), ("message", ""))]
        for action, extra in cases:
            with self.subTest(action=action, extra=extra), self.assertRaises(ValueError):
                validated_args(action, VIDEO | extra, "42")
        for action, extra in (("like", {"like": 1}), ("coin", {"multiply": 1}),
                              ("danmaku", {"message": "text", "progress": 120001})):
            with self.assertRaises(ValueError):
                await self.api.prepare_write(action, VIDEO | extra | ({"cid": 999} if action != "danmaku" else {}))
        self.assertEqual(self.api.posts, [])

    async def test_preview_all_new_actions_is_exact_and_does_not_post(self):
        server = create_server(self.temp.name, api=self.api)
        for action, extra in (("like", {"like": 0}), ("coin", {"multiply": 1}),
                              ("danmaku", {"message": "text", "progress": 0})):
            result = await server._tool_manager.call_tool("bili_preview", {"action": action, "args": VIDEO | extra})
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["args"]["account_uid"], "42")
            self.assertEqual(result["target"]["aid"], 123)
            self.assertEqual(result["target"]["cid"], 11)
            self.assertEqual(result["args"] | extra, result["args"])
            self.assertNotIn(COOKIES["bili_jct"], json.dumps(result))
        self.assertEqual(self.api.posts, [])

    async def test_risk_codes_survive_executor_and_service_without_body(self):
        server = create_server(self.temp.name, api=self.api)
        for code in (12014, -412, -352, -101):
            self.api.risk_code = code
            args = validated_args("like", VIDEO | {"like": 1}, "42")
            value = await server._tool_manager.call_tool("bili_execute", {
                "operation_id": "risk-code-" + str(code), "action": "like", "args": args})
            self.assertEqual(value["risk_code"], code)
            self.assertFalse(value["verified"])
            self.assertNotIn("SESSDATA", json.dumps(value))
            self.assertNotIn("https", json.dumps(value))

    async def test_verify_failure_and_timeout_replay_never_post_again(self):
        for index, field in enumerate(("fail_verify", "timeout_post")):
            setattr(self.api, field, True)
            args = validated_args("like", VIDEO | {"like": 1}, "42")
            result = await self.executor.execute(f"unknown-new-{index}", "like", args)
            self.assertFalse(result["verified"])
            self.assertNotIn("token", json.dumps(result))
            await self.executor.execute(f"unknown-new-{index}", "like", args)
            self.assertEqual(len(self.api.posts), index + 1)
            setattr(self.api, field, False)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Store(self.temp.name)
        self.state = Interactions(self.store)
        self.cfg = copy.deepcopy(main.DEFAULTS)
        self.now = datetime(2026, 10, 26, 23, 59, 59, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def reserve(self, pid="p1", action="like", args=None, run="run", now=None, scope=SCOPE):
        self.state.reserve(scope, run, pid, action, args or VIDEO | {"like": 1}, self.cfg, now=self.now if now is None else now)

    def test_per_run_cap_cannot_be_reset_by_new_day(self):
        for n in range(3):
            self.reserve(pid=str(n))
        with self.assertRaisesRegex(InteractionBlocked, "max_actions_per_run"):
            self.reserve(pid="4", now=self.now + 2)
        self.reserve(pid="5", run="another", now=self.now + 2)

    def test_total_day_cap_resets_at_shanghai_midnight(self):
        self.cfg["max_actions_per_day"] = 1
        self.reserve()
        with self.assertRaisesRegex(InteractionBlocked, "max_actions_per_day"):
            self.reserve(pid="p2", run="new")
        self.reserve(pid="p3", run="new", now=self.now + 1)
        self.assertEqual(self.state.status(SCOPE, self.cfg, self.now + 1)["total"]["count"], 1)

    def test_every_action_specific_daily_limit_including_zero(self):
        for action, field in (("comment", "max_comments_per_day"), ("reply", "max_replies_per_day"),
                              ("danmaku", "max_danmaku_per_day"), ("like", "max_likes_per_day"), ("coin", "max_coins_per_day")):
            self.cfg[field] = 0
            with self.subTest(action=action), self.assertRaisesRegex(InteractionBlocked, field):
                self.reserve(action=action)
        self.assertEqual(self.state.status(SCOPE, self.cfg, self.now)["total"]["count"], 0)

    def test_comment_danmaku_reply_follow_business_dedup(self):
        for action, args in (("comment", VIDEO | {"message": "first"}), ("danmaku", VIDEO | {"message": "dm"}),
                              ("reply", VIDEO | {"root_rpid": 2, "parent_rpid": 3}), ("follow", {"uid": 5})):
            self.reserve(pid=action, action=action, args=args, run=action)
            self.state.finish(SCOPE, action, action, action, args, {"verified": True}, self.cfg, now=self.now)
            with self.subTest(action=action), self.assertRaisesRegex(InteractionBlocked, "重复"):
                self.reserve(pid=action + "2", action=action, args=args | {"message": "changed"}, run="new", now=self.now + 86400)

    def test_reply_different_parent_and_scope_are_independent(self):
        self.reserve(action="reply", args=VIDEO | {"root_rpid": 2, "parent_rpid": 3})
        self.reserve(pid="p2", action="reply", args=VIDEO | {"root_rpid": "2", "parent_rpid": 4})
        self.reserve(pid="p3", action="reply", args=VIDEO | {"root_rpid": 2, "parent_rpid": 3}, scope="other")

    def test_unknown_retains_charge_and_dedup_after_restart(self):
        self.reserve(action="comment", args=VIDEO | {"message": "first"})
        self.store.close()
        self.store = core.Store(self.temp.name)
        self.state = Interactions(self.store)
        self.assertEqual(self.state.status(SCOPE, self.cfg, self.now)["total"]["count"], 1)
        with self.assertRaises(InteractionBlocked):
            self.reserve(pid="retry", action="comment", args=VIDEO | {"message": "second"})

    def test_same_id_different_arguments_refused_and_no_double_charge(self):
        self.reserve()
        with self.assertRaises(InteractionBlocked):
            self.reserve()
        with self.assertRaises(ValueError):
            self.reserve(args=VIDEO | {"like": 0})
        self.assertEqual(self.state.status(SCOPE, self.cfg, self.now)["total"]["count"], 1)

    def test_second_connection_observes_atomic_reservation(self):
        self.cfg["max_actions_per_day"] = 1
        self.reserve()
        other = core.Store(self.temp.name)
        try:
            with self.assertRaises(InteractionBlocked):
                Interactions(other).reserve(SCOPE, "another", "p2", "like", VIDEO | {"like": 0}, self.cfg, self.now)
        finally:
            other.close()

    def test_all_risk_codes_stop_run_and_persist_cooldown(self):
        for code in (12014, -412, -352):
            scope = SCOPE + str(code)
            self.state.risk(scope, "run", code, self.cfg, self.now)
            with self.assertRaises(InteractionBlocked):
                self.state.guard(scope, "next", self.now + 1)
            with self.assertRaises(InteractionBlocked):
                self.state.guard(scope, "run", self.now + 3601)
            self.state.guard(scope, "new", self.now + 3601)
            self.assertEqual(self.state.status(scope, self.cfg, self.now)["cooldown_remaining_seconds"], 3600)

    def test_login_invalid_requires_explicit_status_refresh(self):
        self.state.risk(SCOPE, "run", -101, self.cfg, self.now)
        self.assertEqual(self.state.status(SCOPE, self.cfg, self.now)["login"], "invalid")
        with self.assertRaisesRegex(InteractionBlocked, "扫码"):
            self.state.guard(SCOPE, "new", self.now + 86400)

    def test_status_last_five_and_safe_event_fields(self):
        self.cfg["max_actions_per_run"] = 10
        for n in range(6):
            self.reserve(pid=str(n))
            self.state.finish(SCOPE, "run", str(n), "like", VIDEO, {"verified": True, "rpid": 123,
                "visibility": "public", "body": COOKIES, "error": "https://secret", "dmid": "bili_jct"}, self.cfg, self.now)
        recent = self.state.status(SCOPE, self.cfg, self.now)["recent"]
        self.assertEqual(len(recent), 5)
        self.assertEqual(recent[0]["proposal_id"], "5")
        events = [json.loads(r[0]) for r in self.store.db.execute("SELECT details FROM events WHERE kind='interaction_result'")]
        self.assertTrue({"proposal_id", "action", "bvid", "cid", "rpid", "result", "verified", "visibility", "risk_code"} <= events[0].keys())
        self.assertNotIn("secret", json.dumps(events))
        self.assertNotIn("bili_jct", json.dumps(events))
        self.assertEqual(self.state.status("other", self.cfg, self.now)["recent"], [])

    def test_schema_backup_and_legacy_success_imported_once(self):
        legacy_temp = tempfile.TemporaryDirectory()
        old = core.Store(legacy_temp.name)
        try:
            row, _ = old.propose(SCOPE, "12345", "comment", VIDEO | {"message": "old"})
            old.offered(row["id"], {"message_id": 1})
            old.decide(SCOPE, "12345", "可以", "ok")
            old.finish_proposal(row["id"], {"verified": True, "rpid": "123"})
            state = Interactions(old)
            with closing(sqlite3.connect(state.schema_backup)) as backup:
                self.assertIsNone(backup.execute("SELECT 1 FROM sqlite_master WHERE name='interactions'").fetchone())
                self.assertEqual(backup.execute("SELECT status FROM proposals").fetchone()[0], "verified")
            with self.assertRaisesRegex(InteractionBlocked, "重复"):
                state.check(SCOPE, "new", "comment", VIDEO, self.cfg)
            Interactions(old)
            self.assertEqual(old.db.execute("SELECT COUNT(*) FROM interactions").fetchone()[0], 1)
        finally:
            old.close()
            legacy_temp.cleanup()


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.PluginTests.asyncSetUp(self)
        self.config.update(public_interactions=True, collection_management=True, engagement_actions=True,
                           action_interval_ms=[0, 0])
        self.save()
        self.api = InteractAPI(self.folder.name)
        self.server = create_server(self.folder.name, api=self.api)
        self.backend = AsyncMock()
        self.backend.json_call.side_effect = self.server._tool_manager.call_tool
        self.plugin.bili = lambda *args: self.backend
        self.plugin.flush_memory = AsyncMock()

    async def asyncTearDown(self):
        await fixtures.PluginTests.asyncTearDown(self)

    def save(self):
        self.plugin.control.write_text(json.dumps(self.config))

    async def propose(self, action="comment", args=None, event=None):
        return json.loads(await self.plugin.propose(event or fixtures.FakeEvent(), action,
                          args or VIDEO | {"message": "有信息量的具体评论"}, "想补充说明"))

    async def approve(self, event_id="approved"):
        event = fixtures.FakeEvent(event_id=event_id, text="可以")
        await self.plugin.incoming(event)
        return event

    async def test_new_actions_require_real_owner_approval(self):
        for n, (action, args) in enumerate((("like", {"like": 0}), ("coin", {"multiply": 1}),
                                            ("danmaku", {"message": "补充", "progress": 0}))):
            proposal = await self.propose(action, VIDEO | args, fixtures.FakeEvent(event_id=str(n)))
            self.assertEqual(proposal["status"], "awaiting_user")
            self.assertEqual(len(self.api.posts), n)
            offered_text = self.delivery.send.await_args.args[1]
            self.assertIn(BVID, offered_text)
            await self.approve(str(n) + "approved")
            await self.approve(str(n) + "approved")
            self.assertEqual(len(self.api.posts), n + 1)

    async def test_second_comment_refused_with_clear_reason(self):
        await self.propose()
        answer = await self.approve()
        self.assertIn("visibility=", answer.send.await_args.args[0])
        result = await self.propose(args=VIDEO | {"message": "不同正文也不重复"}, event=fixtures.FakeEvent(event_id="new"))
        self.assertFalse(result["proposal_created"])
        self.assertIn("重复", result["reason"])
        self.assertEqual(len(self.api.posts), 1)

    async def test_second_reply_same_target_refused(self):
        args = VIDEO | {"message": "回复内容", "root_rpid": 900, "parent_rpid": 900}
        await self.propose("reply", args)
        await self.approve()
        # Restore remote fixture target so preview can verify it again.
        self.api.reply.update(rpid=900, root=0, parent=0)
        result = await self.propose("reply", args | {"message": "再次回复"}, fixtures.FakeEvent(event_id="next"))
        self.assertFalse(result["proposal_created"])
        self.assertEqual(len(self.api.posts), 1)

    async def test_quota_rechecked_at_approval_and_frees_blocked_slot(self):
        await self.propose()
        self.config["max_actions_per_day"] = 0
        self.save()
        answer = await self.approve()
        self.assertIn("额度", answer.send.await_args.args[0])
        self.assertEqual(self.api.posts, [])
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM proposals").fetchone()[0], "blocked")

    async def test_risk_blocks_next_proposal_and_reports_code(self):
        self.api.risk_code = 12014
        await self.propose("like", VIDEO | {"like": 1})
        answer = await self.approve()
        self.assertIn("冷却", answer.send.await_args.args[0])
        value = await self.propose("like", VIDEO | {"like": 0}, fixtures.FakeEvent(event_id="next"))
        self.assertFalse(value["proposal_created"])
        self.assertEqual(len(self.api.posts), 1)
        status = json.loads(await self.plugin.interaction_status(fixtures.FakeEvent()))
        self.assertGreater(status["cooldown_remaining_seconds"], 0)
        self.assertEqual(status["recent"][0]["risk_code"], 12014)

    async def test_login_loss_notifies_owner_then_status_checks_relogin(self):
        self.api.risk_code = -101
        await self.propose("like", VIDEO | {"like": 1})
        answer = await self.approve()
        self.assertIn("重新扫码", answer.send.await_args.args[0])
        self.assertEqual(self.plugin.store.get_setting("interaction_login:" + SCOPE), "invalid")
        self.api.risk_code = None
        status = json.loads(await self.plugin.interaction_status(fixtures.FakeEvent()))
        self.assertEqual(status["login"], "valid")

    async def test_switch_disabled_while_waiting_stops_post(self):
        await self.propose("like", VIDEO | {"like": 1})
        self.plugin.store.setting("interaction_next:" + SCOPE, main.time.time() + 2)

        async def wait(_):
            self.config["engagement_actions"] = False
            self.save()

        with patch.object(main.asyncio, "sleep", side_effect=wait) as sleep:
            await self.approve()
            sleep.assert_awaited_once()
        self.assertEqual(self.api.posts, [])

    async def test_legacy_control_loads_defaults_without_rewriting(self):
        legacy = {"owner_scope": SCOPE, "owner_id": "12345", "dnd_ranges": []}
        text = json.dumps(legacy)
        self.plugin.control.write_text(text)
        cfg = self.plugin.cfg()
        self.assertEqual(cfg["interaction_mode"], "approval")
        for key in ("engagement_actions", "public_interactions", "collection_management", "auto_public"):
            self.assertFalse(cfg[key])
        self.assertEqual(self.plugin.control.read_text(), text)
        for action, args in (("like", {"like": 1}), ("coin", {"multiply": 1}), ("danmaku", {"message": "hi", "progress": 0})):
            self.assertIn("未开启", await self.plugin.propose(fixtures.FakeEvent(), action, VIDEO | args, "test"))
        self.assertEqual(self.api.posts, [])

    async def test_config_types_and_status_scope_fail_closed(self):
        for key, value in (("engagement_actions", "false"), ("max_likes_per_day", True),
                           ("interaction_mode", "unknown"), ("action_interval_ms", [4000, 1500]),
                           ("risk_cooldown_minutes", 0)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_config(main.DEFAULTS | {key: value})
        with self.assertRaises(PermissionError):
            await self.plugin.interaction_status(fixtures.FakeEvent(scope=fixtures.OTHER_SCOPE))


if __name__ == "__main__":
    unittest.main()

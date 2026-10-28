"""Reviewed Web API contracts, independent wire expectations and safe readback."""
import asyncio
import copy
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import test_bili_activity as fixtures
import test_bili_interact as integration
from bili_api import BiliAPI, BiliError, Executor, private_json
from service import validated_args

VIDEO, SCOPE, main = integration.VIDEO, fixtures.SCOPE, fixtures.main


class ContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = integration.ActionTests.asyncSetUp
    asyncTearDown = integration.ActionTests.asyncTearDown

    def test_malformed_optional_metadata_is_filtered(self):
        value = main.safe_result({"verified": True, "risk_code": -101, "risk_source": [], "like_ok": {"private": "body"}})
        self.assertIsNone(value["risk_code"])
        self.assertIsNone(value["risk_source"])
        self.assertIsNone(value["like_ok"])
        self.assertNotIn("private", json.dumps(value))

    async def test_like_one_is_web_like_and_never_unlike(self):
        args = validated_args("like", VIDEO | {"like": 1}, "42")
        result = await self.executor.execute("web-like-one", "like", args)
        self.assertEqual(self.api.posts[0][0], "/x/web-interface/archive/like")
        self.assertEqual(self.api.posts[0][1]["like"], 1)
        self.assertEqual(self.api.liked, 1)
        self.assertTrue(result["verified"])

    async def test_missing_buvid_refuses_like_and_coin_before_any_post(self):
        self.api.spi = {}
        for action, args in (("like", {"like": 1}), ("coin", {"multiply": 1})):
            with self.subTest(action=action), self.assertRaisesRegex(BiliError, "buvid3 missing; re-login") as error:
                await self.executor.execute("missing-" + action, action, VIDEO | args)
            self.assertIsNone(error.exception.code)
        self.assertEqual(self.api.posts, [])

    async def test_spi_failure_is_sanitized_without_account_risk(self):
        original = self.api.request
        async def request(path, *args, **kwargs):
            if path.endswith("finger/spi"):
                raise BiliError("SESSDATA=secret https://private", code=-412)
            return await original(path, *args, **kwargs)
        self.api.request = request
        with self.assertRaises(BiliError) as caught:
            await self.api.prepare_write("like", VIDEO | {"like": 1})
        self.assertIsNone(caught.exception.code)
        self.assertEqual(str(caught.exception), "buvid3 missing; re-login")
        self.assertEqual(self.api.posts, [])

    async def test_spi_persists_flat_and_wrapped_credentials_without_losing_metadata(self):
        for wrapped in (False, True):
            cookies = dict(integration.COOKIES)
            document = ({"cookies": cookies} if wrapped else cookies) | {"refresh_token": "keep", "updated_at": 42, "custom": [1]}
            private_json(self.api.cookie_file, document)
            await self.api.ensure_buvid3()
            after = json.loads(self.api.cookie_file.read_text(encoding="utf-8"))
            target = after["cookies"] if wrapped else after
            self.assertEqual(target["buvid3"], "fixture-buvid3")
            self.assertEqual(target["buvid4"], "fixture-buvid4")
            self.assertEqual({k: after[k] for k in ("refresh_token", "updated_at", "custom")}, {"refresh_token": "keep", "updated_at": 42, "custom": [1]})
            self.assertEqual(target["SESSDATA"], cookies["SESSDATA"])
        value = await self.executor.execute("with-fingerprint", "like", VIDEO | {"like": 1})
        self.assertTrue(value["verified"])
        self.assertEqual(len(self.api.posts), 1)
        self.assertTrue((await self.api.read("status", {}))["buvid3_present"])

    async def test_spi_preserves_padded_and_encoded_fingerprints_in_both_formats(self):
        metadata = {"refresh_token": "keep", "updated_at": 42, "custom": [1]}
        for wrapped in (False, True):
            for buvid4 in ("fixture_-.buvid4==", "fixture_-.buvid4%3D%3D",
                           "fixture_-.buvid4+bBvg==", "fixture_-.buvid4/bBvg==",
                           "x" * 254 + "==", "x" * 256):
                with self.subTest(wrapped=wrapped, buvid4=buvid4):
                    cookies = dict(integration.COOKIES)
                    document = ({"cookies": cookies} if wrapped else cookies) | metadata
                    private_json(self.api.cookie_file, document)
                    self.api.spi = {"b_3": "fixture_-.buvid3%3D", "b_4": buvid4}
                    await self.api.ensure_buvid3()
                    after = json.loads(self.api.cookie_file.read_text(encoding="utf-8"))
                    target = after["cookies"] if wrapped else after
                    self.assertEqual(target["buvid3"], self.api.spi["b_3"])
                    self.assertEqual(target.get("buvid4"), buvid4)
                    self.assertEqual({k: after[k] for k in metadata}, metadata)
                    self.assertEqual(target["SESSDATA"], integration.COOKIES["SESSDATA"])
        # Persisted fingerprints support both engagement actions without another SPI.
        self.api.spi = {}
        for action, args in (("like", {"like": 1}), ("coin", {"multiply": 1})):
            value = await self.executor.execute("saved-fingerprint-" + action, action, VIDEO | args)
            self.assertTrue(value["verified"])
        self.assertEqual(len(self.api.posts), 2)

    async def test_spi_rejects_invalid_fields_without_writing_or_posting(self):
        invalid = ["", None, 123, "x" * 257, "x===", "x=y", "==", "x;y", "x,y", "x y"]
        invalid += ["x" + chr(code) + "y" for code in (*range(32), 127)]
        for wrapped in (False, True):
            document = ({"cookies": dict(integration.COOKIES)} if wrapped else dict(integration.COOKIES))
            private_json(self.api.cookie_file, document | {"refresh_token": "keep", "updated_at": 42})
            before = self.api.cookie_file.read_bytes()
            for field in ("b_3", "b_4"):
                for value in invalid:
                    with self.subTest(wrapped=wrapped, field=field, value=value):
                        self.api.spi = {"b_3": "valid-buvid3", "b_4": "valid-buvid4==", field: value}
                        with self.assertRaises(BiliError) as caught:
                            await self.api.prepare_write("like", VIDEO | {"like": 1})
                        self.assertEqual(str(caught.exception), "buvid3 missing; re-login")
                        self.assertIsNone(caught.exception.code)
                        self.assertEqual(self.api.cookie_file.read_bytes(), before)
        self.assertEqual(self.api.posts, [])

    async def test_spi_allows_absent_optional_buvid4(self):
        self.api.spi = {"b_3": "valid-buvid3"}
        await self.api.ensure_buvid3()
        self.assertEqual(self.api.cookies()["buvid3"], "valid-buvid3")
        self.assertNotIn("buvid4", self.api.cookies())

    async def test_existing_buvid_skips_spi_and_concurrent_provision_is_single_flight(self):
        original = self.api.request
        calls = []
        async def request(path, *args, **kwargs):
            if path.endswith("finger/spi"):
                calls.append(kwargs)
                await asyncio.sleep(0)
            return await original(path, *args, **kwargs)
        self.api.request = request
        await asyncio.gather(self.api.ensure_buvid3(), self.api.ensure_buvid3())
        await self.api.ensure_buvid3()
        self.assertEqual(calls, [{"authenticated": False}])

    async def test_spi_merge_rereads_credentials_after_network_wait(self):
        original = self.api.request
        async def request(path, *args, **kwargs):
            if path.endswith("finger/spi"):
                private_json(self.api.cookie_file, {"cookies": integration.COOKIES | {"SESSDATA": "refreshed"}, "refresh_token": "new"})
            return await original(path, *args, **kwargs)
        self.api.request = request
        await self.api.ensure_buvid3()
        value = json.loads(self.api.cookie_file.read_text())
        self.assertEqual(value["refresh_token"], "new")
        self.assertEqual(value["cookies"]["SESSDATA"], "refreshed")

    async def test_status_reports_fingerprint_even_when_login_expired(self):
        self.assertFalse((await self.api.read("status", {}))["buvid3_present"])
        await self.api.ensure_buvid3()
        self.api.request = AsyncMock(side_effect=BiliError("private", code=-101))
        status = await self.api.read("status", {})
        self.assertFalse(status["logged_in"])
        self.assertTrue(status["buvid3_present"])

    async def test_danmaku_length_boundary_and_required_video_identifier(self):
        args = validated_args("danmaku", VIDEO | {"message": "字" * 99, "progress": 1250}, "42")
        _, payload, _ = await self.api.prepare_write("danmaku", args)
        self.assertEqual(payload["aid"], 123)
        self.assertEqual(payload["oid"], 11)
        self.assertEqual(payload["progress"], 1250)
        self.assertGreater(payload["rnd"], 0)
        for size in (100, 101):
            with self.assertRaises(ValueError):
                validated_args("danmaku", VIDEO | {"message": "字" * size, "progress": 0}, "42")
        self.assertEqual(self.api.posts, [])

    async def test_danmaku_accepted_is_not_readback_or_visibility_and_replay_keeps_receipt(self):
        self.api.dm_accepted, self.api.dm_visible = True, False
        args = validated_args("danmaku", VIDEO | {"message": "补充", "progress": 0}, "42")
        value = await self.executor.execute("accepted-danmaku", "danmaku", args)
        self.assertTrue(value["accepted"])
        self.assertFalse(value["verified"])
        self.assertEqual(value["visibility"], "unknown")
        replay = await Executor(self.api, self.executor.state_path).execute("accepted-danmaku", "danmaku", args)
        self.assertTrue(replay["replayed"])
        self.assertTrue(replay["accepted"])
        self.assertEqual(len(self.api.posts), 1)

    async def test_danmaku_xml_failure_keeps_acceptance_but_truthy_flag_is_not_enough(self):
        identity = await self.api.identity(VIDEO["bvid"], 11)
        self.api.read = AsyncMock(side_effect=TimeoutError("private"))
        args = {"message": "text", "progress": 0}
        value = await self.api.verify("danmaku", args, {"dmid": 123, "visible": True}, identity)
        self.assertTrue(value["accepted"])
        self.assertFalse(value["verified"])
        for response in ({"visible": True}, {"dmid": 0, "visible": True}, {"dmid": 123, "visible": 1}):
            self.assertFalse((await self.api.verify("danmaku", args, response, identity))["accepted"])

    async def test_coin_like_read_failure_does_not_erase_coin_verification(self):
        self.api.fail_verify = True
        args = validated_args("coin", VIDEO | {"multiply": 1, "select_like": 1}, "42")
        value = await self.executor.execute("coin-like-timeout", "coin", args)
        self.assertTrue(value["verified"])
        self.assertIsNone(value["like_ok"])
        self.assertTrue((await self.executor.execute("coin-like-timeout", "coin", args))["replayed"])
        self.assertEqual(len(self.api.posts), 1)

    async def test_wbi_known_documentation_vector(self):
        with patch("bili_api.time.time", return_value=1702204169):
            params = await self.api.wbi_params({"foo": "114", "bar": "514", "zab": 1919810}, authenticated=False)
        self.assertEqual(params["w_rid"], "8f6f2b5b3d485fe1886cec6a0be8c5d4")
        self.assertEqual(params["wts"], "1702204169")

    async def test_real_danmaku_request_signs_url_and_keeps_approved_body(self):
        _, data, _ = await self.api.prepare_write("danmaku", VIDEO | {"message": "a!'()*文字", "progress": 0})
        original = copy.deepcopy(data)
        async def chunks(_):
            yield b'{"code":0,"data":{"dmid":123,"visible":true}}'
        response = MagicMock(status=200)
        response.content.iter_chunked = chunks
        response_cm = MagicMock()
        response_cm.__aenter__ = AsyncMock(return_value=response)
        client = MagicMock()
        client.request.return_value = response_cm
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=client)
        with patch("bili_api.aiohttp.ClientSession", return_value=session_cm), patch.object(self.api, "_throttle", new=AsyncMock()):
            await BiliAPI.request(self.api, "/x/v2/dm/post", method="POST", data=data)
        call = client.request.call_args
        self.assertEqual(call.args, ("POST", "https://api.bilibili.com/x/v2/dm/post"))
        self.assertEqual(call.kwargs["data"], original)
        self.assertTrue({"wts", "w_rid", "csrf", "web_location"} <= call.kwargs["params"].keys())
        self.assertEqual(client.request.call_count, 1)

    async def test_comment_visibility_only_runs_after_successful_detail_readback(self):
        identity = await self.api.identity(VIDEO["bvid"], 11)
        self.api.comment_visibility = AsyncMock(return_value=("public", None))
        value = await self.api.verify("comment", {"message": "wrong"}, {"rpid": 900}, identity)
        self.assertFalse(value["verified"])
        self.assertEqual(value["visibility"], "unknown")
        self.api.comment_visibility.assert_not_awaited()

    async def test_anonymous_risk_is_unknown_without_risk_code(self):
        identity = await self.api.identity(VIDEO["bvid"], 11)
        for code in (-101, -352, -412):
            self.api.read = AsyncMock(side_effect=BiliError("private", code=code))
            value = await self.api.verify("comment", {"message": "text"}, {"rpid": 900}, identity)
            self.assertTrue(value["verified"])
            self.assertEqual(value["visibility"], "unknown")
            self.assertIsNone(value["risk_code"])
            self.assertEqual(self.api.read.await_args.args[1]["mode"], 2)

    async def test_comment_wbi_mode_and_anonymous_identity_requests(self):
        recorded = []
        original = self.api.request
        async def request(path, params=None, **kwargs):
            recorded.append((path, params, kwargs))
            return await original(path, params, **kwargs)
        self.api.request = request
        await self.api.read("comments", VIDEO | {"mode": 2}, anonymous=True)
        self.assertTrue(all(kwargs.get("authenticated") is False for _, _, kwargs in recorded))
        self.assertEqual(recorded[-1][0], "/x/v2/reply/wbi/main")
        self.assertEqual(recorded[-1][1]["mode"], "2")
        self.assertIn("w_rid", recorded[-1][1])
        await self.api.read("comments", VIDEO)
        self.assertEqual(recorded[-1][1]["mode"], "3")

    async def test_visibility_follows_new_offset_and_missing_is_unknown(self):
        identity = await self.api.identity(VIDEO["bvid"], 11)
        self.api.read = AsyncMock(side_effect=[{"comments": [], "next_cursor": 0, "next_offset": "next"},
                                               {"comments": [], "is_end": True}])
        self.assertEqual(await self.api.comment_visibility(identity, "900"), ("unknown", None))
        self.assertEqual(self.api.read.await_args.args[1]["pagination_offset"], "next")


class ControllerContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = integration.ControllerTests.asyncSetUp
    asyncTearDown = integration.ControllerTests.asyncTearDown
    save = integration.ControllerTests.save
    propose = integration.ControllerTests.propose
    approve = integration.ControllerTests.approve

    async def test_accepted_danmaku_releases_pending_but_remains_deduped(self):
        self.api.dm_accepted, self.api.dm_visible = True, False
        value = await self.propose("danmaku", VIDEO | {"message": "补充", "progress": 0})
        event = await self.approve()
        self.assertIn("尚未回读", event.send.await_args.args[0])
        row = self.plugin.store.db.execute("SELECT status FROM proposals WHERE id=?", (value["proposal_id"],)).fetchone()
        self.assertEqual(row[0], "accepted")
        status = json.loads(await self.plugin.interaction_status(fixtures.FakeEvent()))
        self.assertEqual(status["pending"], [])
        self.assertTrue(status["recent"][0]["accepted"])
        self.assertFalse(status["recent"][0]["verified"])
        self.assertEqual(status["recent"][0]["visibility"], "unknown")
        again = await self.propose("danmaku", VIDEO | {"message": "再发", "progress": 0}, fixtures.FakeEvent(event_id="again"))
        self.assertFalse(again["proposal_created"])
        fresh = await self.propose("like", VIDEO | {"like": 1}, fixtures.FakeEvent(event_id="fresh"))
        self.assertEqual(fresh["status"], "awaiting_user")
        self.assertEqual(len(self.api.posts), 1)

    async def test_coin_optional_like_metadata_reaches_status_and_events(self):
        self.api.fail_verify = True
        await self.propose("coin", VIDEO | {"multiply": 1, "select_like": 1})
        event = await self.approve()
        self.assertIn("like_ok=null", event.send.await_args.args[0])
        value = json.loads(await self.plugin.interaction_status(fixtures.FakeEvent()))
        self.assertTrue(value["recent"][0]["verified"])
        self.assertIsNone(value["recent"][0]["like_ok"])
        self.assertTrue(value["buvid3_present"])
        events = self.plugin.store.db.execute("SELECT details FROM events WHERE kind='interaction_result'").fetchall()
        self.assertIsNone(json.loads(events[-1][0])["like_ok"])

    async def test_verified_coin_can_still_report_real_authenticated_login_failure(self):
        original = self.api.request
        async def request(path, *args, **kwargs):
            if path.endswith("archive/has/like"):
                raise BiliError("private", code=-101)
            return await original(path, *args, **kwargs)
        self.api.request = request
        await self.propose("coin", VIDEO | {"multiply": 1, "select_like": 1})
        await self.approve()
        self.assertEqual(self.plugin.store.get_setting("interaction_login:" + SCOPE), "invalid")
        value = json.loads(self.plugin.store.db.execute("SELECT result FROM proposals").fetchone()[0])
        self.assertTrue(value["verified"])
        self.assertTrue(value["risk_applied"])

    async def test_anonymous_visibility_failure_never_invalidates_login_or_cools_down(self):
        original = self.api.read
        async def read(operation, args, **kwargs):
            if kwargs.get("anonymous"):
                raise BiliError("private", code=self.visibility_code)
            return await original(operation, args, **kwargs)
        self.api.read = read
        for index, code in enumerate((-101, -352, -412)):
            # Independent scopes are not needed: use separate valid fixture videos.
            self.api.info["bvid"] = "BV1xx411c7m" + "DEF"[index]
            self.visibility_code = code
            await self.propose(args=VIDEO | {"bvid": self.api.info["bvid"], "message": "内容"}, event=fixtures.FakeEvent(event_id=str(code)))
            event = await self.approve("approval" + str(code))
            self.assertNotIn("重新扫码", event.send.await_args.args[0])
            self.assertNotIn("进入冷却", event.send.await_args.args[0])
        self.assertIsNone(self.plugin.store.get_setting("interaction_login:" + SCOPE))
        self.assertIsNone(self.plugin.store.get_setting("interaction_cooldown:" + SCOPE))

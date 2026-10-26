"""Explicit auto policy and owner commands retain approval as the default."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_bili_activity as fixtures
import test_bili_interact as integration
import test_bili_ops as ops_fixtures
from bili_activity_test.interactions import auto_allowed, validate_config

main, SCOPE = fixtures.main, fixtures.SCOPE
VIDEO = integration.VIDEO


class ModePolicyTests(unittest.TestCase):
    def test_mode_switch_action_and_positive_limit_matrix(self):
        base = main.DEFAULTS | {"public_interactions": True, "engagement_actions": True, "collection_management": True}
        for mode in ("approval", "auto_low_risk", "auto_public"):
            for flag in (False, True):
                cfg = base | {"interaction_mode": mode, "auto_public": flag}
                for action in main.WRITES:
                    expected = ((action in {"like", "coin"} and mode != "approval") or
                                (action in {"comment", "reply", "danmaku"} and mode == "auto_public" and flag))
                    with self.subTest(mode=mode, flag=flag, action=action):
                        self.assertEqual(auto_allowed(action, {}, cfg), expected)
        cfg = base | {"interaction_mode": "auto_public", "auto_public": True}
        for key in ("public_interactions", "auto_public", "max_actions_per_day", "max_actions_per_run", "max_comments_per_day"):
            self.assertFalse(auto_allowed("comment", {}, cfg | {key: False if type(cfg[key]) is bool else 0}))

    def test_allowlists_use_verified_video_owner_and_title(self):
        cfg = main.DEFAULTS | {"public_interactions": True, "interaction_mode": "auto_public", "auto_public": True,
                               "allowed_uids": [5], "allowed_keywords": ["教程"]}
        target = {"owner": {"mid": 5}, "title": "Python教程"}
        self.assertTrue(auto_allowed("comment", target, cfg))
        for bad in ({}, target | {"owner": {"mid": 6}}, target | {"title": "无关内容"}):
            self.assertFalse(auto_allowed("comment", bad, cfg))
        for key, value in (("allowed_uids", [True]), ("allowed_uids", ["-1"]),
                           ("allowed_keywords", [""]), ("allowed_keywords", "教程")):
            with self.assertRaises(ValueError):
                validate_config(cfg | {key: value})


class AutoControllerTests(unittest.IsolatedAsyncioTestCase):
    save = integration.ControllerTests.save
    propose = integration.ControllerTests.propose
    approve = integration.ControllerTests.approve

    async def asyncSetUp(self):
        await integration.ControllerTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await integration.ControllerTests.asyncTearDown(self)

    async def test_default_approval_and_auto_low_risk_boundary(self):
        await self.propose("like", VIDEO | {"like": 1})
        self.assertEqual(self.api.posts, [])
        await self.approve()
        self.config["interaction_mode"] = "auto_low_risk"
        self.save()
        result = await self.propose("like", VIDEO | {"like": 0}, fixtures.FakeEvent(event_id="next"))
        self.assertEqual(result["authorization"], "owner_configuration")
        self.assertTrue(result["verified"])
        self.assertEqual(len(self.api.posts), 2)
        result = await self.propose(event=fixtures.FakeEvent(event_id="public"))
        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(len(self.api.posts), 2)

    async def test_auto_public_requires_flag_and_records_policy_not_user_answer(self):
        self.config.update(interaction_mode="auto_public", auto_public=False)
        self.save()
        result = await self.propose()
        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(self.api.posts, [])
        await self.plugin.incoming(fixtures.FakeEvent(event_id="reject", text="算了"))
        self.config["auto_public"] = True
        self.save()
        result = await self.propose(event=fixtures.FakeEvent(event_id="second"))
        self.assertEqual(result["authorization"], "owner_configuration")
        self.assertTrue(result["verified"])
        self.assertEqual(len(self.api.posts), 1)
        row = self.plugin.store.db.execute("SELECT * FROM proposals WHERE id=?", (result["proposal_id"],)).fetchone()
        self.assertEqual(row["decision_event"], "policy:auto_public")
        self.assertIsNone(row["offer_receipt"])
        self.assertEqual(self.plugin.store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)
        policy = json.loads(self.plugin.store.db.execute("SELECT details FROM events WHERE kind='policy_approval'").fetchone()[0])
        self.assertFalse(policy["owner_message"])

    async def test_scope_allowlist_mismatch_falls_back_to_exact_owner_approval(self):
        self.config.update(interaction_mode="auto_public", auto_public=True, allowed_uids=[999])
        self.save()
        value = await self.propose()
        self.assertEqual(value["status"], "awaiting_user")
        self.assertEqual(self.api.posts, [])
        await self.approve()
        self.assertEqual(len(self.api.posts), 1)

    async def test_collection_management_always_requires_approval(self):
        self.config.update(interaction_mode="auto_public", auto_public=True)
        self.save()
        result = await self.propose("follow", {"uid": 5})
        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(self.api.posts, [])

    async def test_auto_mode_changed_during_delay_blocks_without_claiming_success(self):
        self.config.update(interaction_mode="auto_low_risk")
        self.save()
        self.plugin.interactions()
        self.plugin.store.setting("interaction_next:" + SCOPE, main.time.time() + 2)
        async def delay(_):
            self.config["interaction_mode"] = "approval"
            self.save()
        with patch.object(main.asyncio, "sleep", side_effect=delay):
            result = await self.propose("like", VIDEO | {"like": 1})
        self.assertFalse(result["verified"])
        self.assertEqual(result["result"], "blocked")
        self.assertEqual(self.api.posts, [])

    async def test_auto_risk_stops_round_and_notifies_owner(self):
        self.config.update(interaction_mode="auto_low_risk")
        self.save()
        self.api.risk_code = -352
        first = await self.propose("like", VIDEO | {"like": 1})
        second = await self.propose("like", VIDEO | {"like": 0}, fixtures.FakeEvent(event_id="second"))
        self.assertEqual(first["risk_code"], -352)
        self.assertFalse(second["proposal_created"])
        self.assertIn("冷却", self.delivery.send.await_args.args[1])
        self.assertEqual(len(self.api.posts), 1)

    async def test_per_run_limit_applies_to_automatic_actions(self):
        self.config.update(interaction_mode="auto_low_risk", max_actions_per_run=1)
        self.save()
        await self.propose("like", VIDEO | {"like": 1}, fixtures.FakeEvent(run_id="run"))
        result = await self.propose("like", VIDEO | {"like": 0}, fixtures.FakeEvent(run_id="run"))
        self.assertIn("max_actions_per_run", result["reason"])
        self.assertEqual(len(self.api.posts), 1)

    async def test_owner_commands_preserve_unknown_config_and_backup(self):
        self.config["user_custom"] = "preserve"
        self.save()
        original = self.plugin.control.read_bytes()
        for n, command in enumerate(("开", "模式 auto_low_risk", "模式 auto_public", "状态", "额度", "关")):
            event = fixtures.FakeEvent(event_id=f"command{n}", text="/bili互动 " + command)
            await self.plugin.incoming(event)
            self.assertTrue(event.is_stopped())
            event.send.assert_awaited_once()
        self.assertFalse(self.plugin.cfg()["public_interactions"])
        self.assertFalse(self.plugin.cfg()["engagement_actions"])
        self.assertEqual(self.plugin.cfg()["interaction_mode"], "approval")
        self.assertEqual(self.plugin.cfg()["user_custom"], "preserve")
        self.assertIn(original, [p.read_bytes() for p in (self.plugin.folder / "config_backups").glob("*.json")])
        before = self.plugin.control.read_bytes()
        await self.plugin.incoming(fixtures.FakeEvent(user="other", text="/bili互动 开"))
        self.assertEqual(self.plugin.control.read_bytes(), before)
        self.assertEqual(self.api.posts, [])


class ConfigOpsTests(unittest.TestCase):
    def test_ops_defaults_match_plugin_and_legacy_init_preserves_file(self):
        for key, value in main.DEFAULTS.items():
            self.assertEqual(ops_fixtures.ops.DEFAULTS[key], value, key)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "control.json"
            original = '{"custom":true}'
            path.write_text(original)
            ops_fixtures.ops.main(["--data-dir", folder, "init"])
            self.assertEqual(path.read_text(), original)

    def test_ops_interaction_configuration_and_validation_preserve_existing(self):
        with tempfile.TemporaryDirectory() as folder:
            args = ["--data-dir", folder]
            ops_fixtures.ops.main(args + ["init"])
            ops_fixtures.ops.main(args + ["configure", "--interaction-mode", "auto_public", "--allowed-uids", '["5"]',
                "--allowed-keywords", '["教程"]', "--action-interval-ms", "2000", "6000", "--learn-prompt", "优先版本差异"])
            cfg = ops_fixtures.ops.load_control(Path(folder))
            self.assertFalse(cfg["auto_public"])
            self.assertEqual(cfg["allowed_uids"], ["5"])
            self.assertEqual(cfg["action_interval_ms"], [2000, 6000])
            original = (Path(folder) / "control.json").read_bytes()
            for options in (["--allowed-uids", "[true]"], ["--allowed-keywords", '[""]'],
                            ["--action-interval-ms", "5", "2"], ["--max-coins-per-day", "-1"]):
                with self.assertRaises(ValueError):
                    ops_fixtures.ops.main(args + ["configure", *options])
                self.assertEqual((Path(folder) / "control.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

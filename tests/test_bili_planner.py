"""Offline life decisions against existing ledgers; no live account or model."""
import asyncio
import importlib
import json
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import test_bili_activity as fixtures
import test_bili_life_integration as life_fixtures
import test_bili_life_ops as ops_fixtures

main, core = fixtures.main, fixtures.core
SCOPE, OTHER, FakeEvent = fixtures.SCOPE, fixtures.OTHER_SCOPE, fixtures.FakeEvent
Workshop = importlib.import_module("bili_activity_test.workshop").Workshop


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Store(self.temp.name)
        self.planner = main.LifePlanner(self.store)
        self.cfg = main.DEFAULTS | {"life_enabled": True, "life_decision_enabled": True}
        self.now = time.time()
        self.serial = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def goal(self, source="goal", scope=SCOPE, status=None):
        goal = self.store.life.create_goal(scope, "研究合作机制", "想弄清建造规则", "比较实机资料", source)
        if status:
            goal = self.store.life.update_goal(scope, goal["id"], status=status)
        return goal

    def choose(self, **kwargs):
        return self.planner.choose(SCOPE, self.cfg, now=self.now, **kwargs)

    def attempted(self, choice, status="completed", scope=SCOPE):
        self.serial += 1
        run = "test-run-" + str(self.serial)
        self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (run, scope, self.now, self.now, status, "synthetic"))
        self.planner.record(scope, run, choice)

    def interest(self, topic, *, polarity=1, subject="agent"):
        self.serial += 1
        return self.store.life._record_episode(SCOPE, kind="interest", source_id=str(self.serial),
            subject_kind=subject, evidence={"bvid": "BV" + str(self.serial)},
            interpretation={"interest": {"topic": topic, "polarity": polarity}})

    def test_empty_life_selects_bounded_exploration_without_creating_an_experience(self):
        decision = self.choose()
        self.assertEqual(decision["action"], "explore")
        self.assertTrue(decision["decision_only"])
        self.assertEqual(self.store.life.goals(SCOPE), [])
        self.assertEqual(self.store.life.episodes(SCOPE), [])
        self.assertEqual(self.planner.recent(SCOPE), [])

    def test_unfinished_goal_beats_generic_exploration_and_keeps_reason(self):
        goal = self.goal()
        decision = self.choose()
        self.assertEqual((decision["action"], decision["goal_id"]), ("research", goal["id"]))
        self.assertIn(goal["title"], decision["reason"])
        self.assertIn({"reason": "unfinished_goal", "points": 60}, decision["factors"])
        self.assertEqual(self.store.life.get_goal(SCOPE, goal["id"])["status"], "ready")

    def test_blocked_approval_and_other_scope_are_never_resumed(self):
        blocked = self.goal("blocked", status="blocked")
        waiting = self.goal("waiting")
        self.store.life.claim_goal(SCOPE, waiting["id"], "pending")
        self.store.life.update_goal(SCOPE, waiting["id"], status="waiting_approval", run_id="pending")
        self.goal("other", scope=OTHER)
        decision = self.choose()
        self.assertEqual(decision["action"], "explore")
        self.assertEqual({x["reason"] for x in decision["excluded"]}, {"goal_blocked", "goal_waiting_approval"})
        self.assertEqual(self.store.life.get_goal(SCOPE, blocked["id"])["status"], "blocked")
        self.assertEqual(self.store.life.get_goal(SCOPE, waiting["id"])["status"], "waiting_approval")

    def test_capacity_waits_instead_of_creating_a_ninth_goal(self):
        for i in range(8):
            self.goal(str(i), status="blocked")
        self.assertEqual(self.choose()["action"], "rest")

    def test_only_own_positive_interest_can_seed_exploration(self):
        self.interest("主人爱好", subject="user")
        self.interest("讨厌的话题", polarity=-1)
        evidence = self.interest("解谜游戏")
        decision = self.choose()
        self.assertEqual(decision["topic"], "解谜游戏")
        self.assertEqual(decision["evidence_ids"], [evidence["id"]])

    def test_own_interest_can_prioritize_a_matching_goal(self):
        matched = self.goal("matched")
        self.store.life.create_goal(SCOPE, "整理风景", "有待整理", "看看画面", "other")
        self.interest("合作机制")
        self.assertEqual(self.choose()["goal_id"], matched["id"])

    def test_repeat_exploration_rests_and_rest_ticks_do_not_erase_cooldown(self):
        self.attempted(self.choose())
        for i in range(40):
            choice = self.choose()
            self.assertEqual(choice["action"], "rest")
            self.planner.record(SCOPE, "rest-" + str(i), choice)
        self.assertEqual(self.choose()["action"], "rest")
        self.now += self.cfg["life_repeat_cooldown_minutes"] * 60 + 1
        self.assertEqual(self.choose()["action"], "explore")

    def test_two_unchanged_attempts_rest_then_new_evidence_unlocks_goal(self):
        self.attempted(self.choose())  # Exploration is also cooling down.
        goal = self.goal()
        for _ in range(2):
            choice = self.choose()
            self.assertEqual(choice["goal_id"], goal["id"])
            self.attempted(choice)
        self.assertEqual(self.choose()["action"], "rest")
        self.store.life.record_episode(SCOPE, goal_id=goal["id"], kind="read", source_id="new-source",
            evidence={"operation": "info", "result_excerpt": "synthetic source"})
        self.assertEqual(self.choose()["goal_id"], goal["id"])

    def test_lifecycle_summaries_do_not_count_as_new_progress(self):
        goal = self.goal()
        self.store.life.record_episode(SCOPE, goal_id=goal["id"], kind="read", source_id="source",
            evidence={"operation": "info", "result_excerpt": "synthetic"})
        signature = self.choose()["fingerprint"]
        for i in range(55):
            self.store.life.record_episode(SCOPE, goal_id=goal["id"], kind="run_end", source_id=str(i),
                evidence={"run_id": str(i)}, subject_kind="system")
        self.assertEqual(self.choose()["fingerprint"], signature)

    def test_rereading_same_video_with_new_ids_and_times_is_not_progress(self):
        goal = self.goal()
        signatures = []
        for i in range(3):
            row = self.store.life.record_episode(SCOPE, goal_id=goal["id"], kind="video", source_id=str(i),
                evidence={"bvid": "BVsame", "cid": "1", "observations": [{"text": "same scene"}], "captured_at": i})
            self.store.life.update_goal(SCOPE, goal["id"], checkpoint={"episode_id": row["id"], "bvid": "BVsame"})
            signatures.append(self.choose()["fingerprint"])
        self.assertEqual(len(set(signatures)), 1)

    def test_duplicate_read_json_and_failed_legacy_reads_do_not_change_progress(self):
        goal = self.goal()
        signatures = []
        for i, excerpt in enumerate(('{"title":"same"}', '{ "title": "same" }')):
            self.store.life.record_episode(SCOPE, goal_id=goal["id"], kind="read", source_id=str(i),
                evidence={"operation": "info", "result_excerpt": excerpt})
            signatures.append(self.choose()["fingerprint"])
        self.store.life.record_episode(SCOPE, goal_id=goal["id"], kind="read", source_id="failed",
            evidence={"operation": "info", "result_excerpt": '{"ok":false}'})
        signatures.append(self.choose()["fingerprint"])
        self.assertEqual(len(set(signatures)), 1)

    def test_failed_or_interrupted_attempt_backs_off_without_new_progress(self):
        for status in ("failed", "interrupted", "timeout", "cancelled"):
            with self.subTest(status=status):
                goal = self.goal(status)
                choice = self.choose()
                self.attempted(choice, status)
                self.assertNotEqual(self.choose()["goal_id"], choice["goal_id"])

    def test_three_continuations_allow_a_change_of_activity(self):
        goal = self.goal()
        for i in range(3):
            choice = self.choose()
            self.assertEqual(choice["action"], "research")
            self.attempted(choice)
            self.store.life.update_goal(SCOPE, goal["id"], next_step="new step " + str(i))
        self.assertEqual(self.choose()["action"], "explore")

    def test_history_survives_restart_and_cannot_leak_across_scopes(self):
        self.attempted(self.choose())
        self.store.close()
        self.store = core.Store(self.temp.name)
        self.planner = main.LifePlanner(self.store)
        self.assertEqual(self.choose()["action"], "rest")
        self.assertEqual(self.planner.recent(OTHER), [])
        self.assertEqual(self.planner.choose(OTHER, self.cfg, now=self.now)["action"], "explore")

    def test_writer_uses_saved_work_while_phone_is_busy(self):
        self.cfg["workshop_enabled"] = True
        goal = self.goal()
        work = Workshop(self.store).create(SCOPE, goal["id"], "笔记", "work")
        decision = self.choose(phone_busy=True)
        self.assertEqual((decision["action"], decision["work_id"]), ("writing", work["id"]))
        self.cfg["workshop_enabled"] = False
        self.assertEqual(self.choose(phone_busy=True)["action"], "rest")

    def test_work_requiring_sources_uses_research_not_offline_writing(self):
        self.cfg["workshop_enabled"] = True
        goal = self.goal()
        workshop = Workshop(self.store)
        work = workshop.create(SCOPE, goal["id"], "笔记", "work")
        workshop.set_phase(SCOPE, work["id"], "research")
        self.assertEqual(self.choose()["action"], "research")
        self.assertEqual(self.choose(phone_busy=True)["action"], "rest")

    def test_invalid_config_is_not_silently_coerced(self):
        for key, value in (("life_decision_enabled", "false"), ("life_repeat_cooldown_minutes", True),
                           ("life_repeat_cooldown_minutes", 0), ("life_repeat_cooldown_minutes", 1441)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.planner.choose(SCOPE, self.cfg | {key: value}, now=self.now)


class PlannerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    save_config = life_fixtures.LifeIntegrationTests.save_config
    asyncTearDown = life_fixtures.LifeIntegrationTests.asyncTearDown

    async def asyncSetUp(self):
        await life_fixtures.LifeIntegrationTests.asyncSetUp(self)
        self.config["life_decision_enabled"] = True
        self.save_config()
        self.plugin.make_event = AsyncMock(side_effect=lambda run_id: FakeEvent(run_id=run_id))
        self.plugin.agent = AsyncMock(return_value="没有值得分享的新进展。")

    def goal(self, source="goal", status=None):
        goal = self.plugin.store.life.create_goal(SCOPE, "研究合作机制", "想弄清规则", "读取实机资料", source)
        return self.plugin.store.life.update_goal(SCOPE, goal["id"], status=status) if status else goal

    async def test_selected_goal_is_claimed_and_actual_reads_supply_evidence(self):
        goal = self.goal()

        async def agent(event, prompt, tools, max_steps):
            self.assertEqual(self.plugin.active_goal(event)["id"], goal["id"])
            self.assertIn("df_life_status", tools)
            self.assertIn("本轮生活安排", prompt)
            await self.plugin.read(event, "info", {"bvid": "BVexample"})
            return "已读取资料，尚未形成结论。"

        self.plugin.agent.side_effect = agent
        await self.plugin.run_activity("periodic")
        row = self.plugin.life_planner().recent(SCOPE)[0]
        self.assertEqual((row["goal_id"], row["run_status"]), (goal["id"], "completed"))
        self.assertEqual(self.plugin.store.life.get_goal(SCOPE, goal["id"])["status"], "paused")
        self.assertTrue(any(e["kind"] == "read" for e in self.plugin.store.life.episodes(SCOPE, goal_id=goal["id"])))
        self.delivery.send.assert_not_called()

    async def test_rest_has_no_model_device_send_reservation_or_fake_memory(self):
        for i in range(8):
            self.goal(str(i), "blocked")
        await self.plugin.run_activity("periodic")
        self.plugin.agent.assert_not_called()
        self.plugin.make_event.assert_not_called()
        self.plugin.flush_memory.assert_not_called()
        self.assertEqual(self.calls, [])
        self.delivery.send.assert_not_called()
        for table in ("runs", "episodes", "memories", "activity_budget_runs"):
            self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        result = json.loads(await self.plugin.life_status(FakeEvent()))
        self.assertEqual(result["decisions"][0]["run_status"], "rested")

    async def test_offline_writing_keeps_foreground_phone_lease(self):
        self.config["workshop_enabled"] = True
        self.save_config()
        goal = self.goal()
        Workshop(self.plugin.store).create(SCOPE, goal["id"], "笔记", "work")
        async with self.plugin.gate.lease("foreground"):
            await self.plugin.run_activity("periodic")
            self.assertEqual(self.plugin.gate.owner, "foreground")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plugin.life_planner().recent(SCOPE)[0]["action"], "writing")

    async def test_disabling_decisions_blocks_new_tools_and_cleans_up_run(self):
        self.goal()

        async def agent(event, prompt, tools, max_steps):
            self.config["life_decision_enabled"] = False
            self.save_config()
            before = len(self.calls)
            with self.assertRaises(PermissionError):
                await self.plugin.read(event, "home")
            self.assertEqual(len(self.calls), before)
            return "开关已关闭。"

        self.plugin.agent.side_effect = agent
        await self.plugin.run_activity("periodic")
        self.assertEqual(self.plugin.decision_runs, set())
        self.assertFalse(any(g["status"] == "running" for g in self.plugin.store.life.goals(SCOPE)))

    async def test_cancel_preserves_progress_and_marks_attempt_cancelled(self):
        goal = self.goal()
        started = asyncio.Event()

        async def agent(*args, **kwargs):
            started.set()
            await asyncio.Future()

        self.plugin.agent.side_effect = agent
        task = asyncio.create_task(self.plugin.run_activity("periodic"))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.plugin.life_planner().recent(SCOPE)[0]["run_status"], "cancelled")
        self.assertEqual(self.plugin.store.life.get_goal(SCOPE, goal["id"])["status"], "paused")
        self.assertEqual(self.plugin.decision_runs, set())

    async def test_dnd_pause_and_chat_cooldown_do_not_plan_or_execute(self):
        self.goal()
        with patch.object(main, "quiet_now", return_value=True):
            await self.plugin.run_activity("periodic")
        self.plugin.store.setting("life_paused:" + SCOPE, True)
        await self.plugin.run_activity("periodic")
        self.plugin.store.setting("life_paused:" + SCOPE, False)
        self.config["chat_cooldown_seconds"] = 300
        self.save_config()
        self.plugin.store.setting("last_owner_message:" + SCOPE, time.time())
        await self.plugin.run_activity("periodic")
        self.assertEqual(self.plugin.life_planner().recent(SCOPE), [])
        self.plugin.agent.assert_not_called()
        self.assertEqual(self.calls, [])

    async def test_exhausted_budget_does_not_plan_or_execute(self):
        self.plugin.activity_budget().start_run(SCOPE, "used", 30 * 60, self.config)
        await self.plugin.run_activity("periodic")
        self.assertEqual(self.plugin.life_planner().recent(SCOPE), [])
        self.plugin.agent.assert_not_called()
        self.assertEqual(self.calls, [])

    async def test_status_is_owner_only_and_does_not_start_activity(self):
        for event in (FakeEvent(scope=OTHER), FakeEvent(user="999")):
            with self.assertRaises(PermissionError):
                await self.plugin.life_status(event)
        self.config["life_enabled"] = False
        self.save_config()
        self.assertFalse(json.loads(await self.plugin.life_status(FakeEvent()))["enabled"])
        self.assertEqual(self.calls, [])

    async def test_old_config_disables_planner_and_preserves_legacy_execution(self):
        del self.config["life_decision_enabled"]
        del self.config["life_repeat_cooldown_minutes"]
        self.save_config()
        self.assertFalse(self.plugin.cfg()["life_decision_enabled"])
        await self.plugin.run_activity("once")
        self.plugin.agent.assert_awaited_once()
        self.assertEqual(self.plugin.life_planner().recent(SCOPE), [])


class PlannerOpsTests(unittest.TestCase):
    setUp = ops_fixtures.LifeOpsTests.setUp
    tearDown = ops_fixtures.LifeOpsTests.tearDown
    run_ops = ops_fixtures.LifeOpsTests.run_ops
    config = ops_fixtures.LifeOpsTests.config

    def test_switch_requires_existing_life_acceptance_and_preserves_permissions(self):
        self.config()
        with self.assertRaises(ValueError):
            self.run_ops("switch", "life_decision_enabled", "on")
        self.config(life_enabled=True, one_shot_verified=True, custom="keep")
        self.run_ops("switch", "life_decision_enabled", "on")
        cfg = ops_fixtures.ops.load_control(self.folder)
        self.assertTrue(cfg["life_decision_enabled"])
        for key in ("enabled", "periodic_enabled", "public_interactions", "engagement_actions", "collection_management"):
            self.assertFalse(cfg[key])
        self.assertEqual(cfg["custom"], "keep")
        self.run_ops("switch", "life_decision_enabled", "off")
        self.assertFalse(ops_fixtures.ops.load_control(self.folder)["life_decision_enabled"])

    def test_missing_state_queries_are_readonly_and_old_config_defaults_off(self):
        self.assertEqual(json.loads(self.run_ops("life-decisions")), [])
        self.assertFalse(self.folder.exists())
        self.config()
        store = core.Store(self.folder)
        try:
            goal = store.life.create_goal(SCOPE, "test", "test", "test", "test")
            store.life.claim_goal(SCOPE, goal["id"], "running")
            planner = main.LifePlanner(store)
            choice = planner.choose(SCOPE, main.DEFAULTS, now=time.time())
            planner.record(SCOPE, "decision", choice)
            rows = json.loads(self.run_ops("life-decisions"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_status"], "not_started")
            self.assertEqual(store.life.get_goal(SCOPE, goal["id"])["status"], "running")
            self.assertFalse(json.loads(self.run_ops("status"))["control"]["life_decision_enabled"])
        finally:
            store.close()

    def test_cooldown_configuration_validates_and_preserves_unknown_fields(self):
        self.config(custom="keep")
        for value in ("0", "1441"):
            with self.assertRaises(ValueError):
                self.run_ops("configure", "--life-repeat-cooldown-minutes", value)
        self.run_ops("configure", "--life-repeat-cooldown-minutes", "240")
        self.assertEqual(ops_fixtures.ops.load_control(self.folder)["life_repeat_cooldown_minutes"], 240)
        self.assertEqual(ops_fixtures.ops.load_control(self.folder)["custom"], "keep")


if __name__ == "__main__":
    unittest.main()

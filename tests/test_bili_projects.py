"""Offline bounded projects, recovery and real controller scheduling."""
import asyncio
import importlib
import json
import sqlite3
import tempfile
import time
import types
import unittest
from contextlib import closing
from datetime import datetime
from unittest.mock import AsyncMock, patch

import test_bili_activity as fixtures
import test_bili_life_ops as ops_tests
import test_bili_planner as planner_tests

main, core = fixtures.main, fixtures.core
SCOPE, OTHER = fixtures.SCOPE, fixtures.OTHER_SCOPE
projects = importlib.import_module("bili_activity_test.projects")
Workshop = importlib.import_module("bili_activity_test.workshop").Workshop


def config():
    return main.DEFAULTS | {"life_enabled": True, "life_decision_enabled": True,
                           "workshop_enabled": True, "life_projects_enabled": True}


def create_goal(store, key="seed", scope=SCOPE):
    return store.life.create_goal(scope, "研究合作机制"+key, "想查清具体限制", "读取资料并写笔记", key)


def complete_work(store, goal, key="source"):
    source = store.life.record_episode(goal["scope"], kind="read", source_id=key, goal_id=goal["id"],
        evidence={"operation": "info", "result_excerpt": json.dumps({"bvid": "BV"+key, "description": "真实模拟资料"})})
    workshop = Workshop(store)
    work = workshop.create(goal["scope"], goal["id"], "阶段作品", "work-"+goal["id"])
    body = f"## 资料观察\n来源中的演示。[E:{source['id']}]\n## 我的想法\n想再作比较。\n## 待核实\n还需独立来源。"
    work = workshop.revise(goal["scope"], work["id"], body, [source["id"]], 0, "revision-"+goal["id"])
    store.life.update_goal(goal["scope"], goal["id"], status="completed")
    store.db.execute("UPDATE goals SET updated_at=? WHERE id=?", (time.time()-25*3600, goal["id"]))
    return source, work


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Store(self.temp.name)
        self.projects = projects.Projects(self.store)
        self.cfg = config()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_partial_schema_does_not_block_life_planner(self):
        self.store.db.execute("CREATE TABLE life_projects (id TEXT PRIMARY KEY)")
        goal = create_goal(self.store)
        choice = main.LifePlanner(self.store).choose(SCOPE, self.cfg)
        self.assertIn(choice["action"], {"research", "writing", "rest", "project_review"})
        self.assertIsNone(self.projects.goal_gate(SCOPE, goal["id"], self.cfg))
        self.assertEqual(self.projects.due(SCOPE, self.cfg), [])

    def seed(self, **cfg):
        self.cfg.update(cfg)
        goal = create_goal(self.store)
        project = self.projects.create(SCOPE, goal["id"], "持续观察合作设计", "想形成自己的系列笔记", self.cfg)
        source, work = complete_work(self.store, goal)
        return project, goal, source, work

    def review(self, project, question="下一次比较不同合作人数"):
        return self.projects.review(SCOPE, project["id"], 1, "记下了这份资料的观察", "仍需别的来源验证", question, self.cfg)

    def test_reading_legacy_store_does_not_migrate_or_create_workshop(self):
        before = list(self.store.db.iterdump())
        self.assertEqual(self.projects.list(SCOPE, self.cfg), [])
        self.assertEqual(self.projects.due(SCOPE, self.cfg), [])
        self.assertIsNone(self.projects.goal_gate(SCOPE, "unknown", self.cfg))
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_additive_schema_backup_preserves_old_ledger(self):
        goal = create_goal(self.store)
        self.projects.create(SCOPE, goal["id"], "主题", "原因", self.cfg)
        self.assertTrue(self.projects.schema_backup.exists())
        with closing(sqlite3.connect(self.projects.schema_backup)) as backup:
            self.assertEqual(backup.execute("SELECT count(*) FROM goals").fetchone()[0], 1)
            self.assertIsNone(backup.execute("SELECT 1 FROM sqlite_master WHERE name='life_projects'").fetchone())
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_future_and_partial_schema_fail_without_mutation(self):
        self.projects.ready(create=True)
        self.store.db.execute("UPDATE project_meta SET value='99'")
        before = list(self.store.db.iterdump())
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            self.projects.list(SCOPE, self.cfg)
        self.assertEqual(before, list(self.store.db.iterdump()))
        self.store.db.execute("DROP TABLE project_reviews")
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.projects.list(SCOPE, self.cfg)

    def test_creation_replay_parameter_mismatch_and_scope(self):
        goal = create_goal(self.store)
        first = self.projects.create(SCOPE, goal["id"], "主题", "原因", self.cfg)
        self.assertEqual(self.projects.create(SCOPE, goal["id"], "主题", "原因", self.cfg)["id"], first["id"])
        with self.assertRaises(ValueError):
            self.projects.create(SCOPE, goal["id"], "另一个主题", "原因", self.cfg)
        self.assertEqual(self.projects.list(OTHER, self.cfg), [])
        with self.assertRaises(ValueError):
            self.projects.read(OTHER, first["id"], self.cfg)
        with self.assertRaises(ValueError):
            self.projects.create(OTHER, goal["id"], "主题", "原因", self.cfg)

    def test_active_limit_counts_paused_projects(self):
        for i in range(2):
            goal = create_goal(self.store, str(i))
            p = self.projects.create(SCOPE, goal["id"], "主题"+str(i), "原因", self.cfg)
            self.projects.state(SCOPE, p["id"], "pause", self.cfg)
        goal = create_goal(self.store, "third")
        with self.assertRaisesRegex(ValueError, "existing project"):
            self.projects.create(SCOPE, goal["id"], "第三个", "原因", self.cfg)

    def test_delay_no_early_review_then_due_without_mutations(self):
        project, goal, source, work = self.seed()
        self.store.db.execute("UPDATE goals SET updated_at=? WHERE id=?", (time.time(), goal["id"]))
        before = list(self.store.db.iterdump())
        self.assertEqual(self.projects.due(SCOPE, self.cfg), [])
        with self.assertRaisesRegex(ValueError, "rest"):
            self.review(project)
        self.assertEqual(list(self.store.db.iterdump()), before)
        due = self.projects.due(SCOPE, self.cfg, now=time.time()+86401)
        self.assertEqual(due[0]["task"], "review")
        self.assertEqual(due[0]["work"]["id"], work["id"])
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_read_portfolio_validates_current_sources_without_writing_cache(self):
        project, goal, source, work = self.seed()
        before = list(self.store.db.iterdump())
        result = self.projects.read(SCOPE, project["id"], self.cfg)
        self.assertTrue(result["stages"][0]["portfolio_valid"])
        self.assertEqual(result["stages"][0]["work"]["revision"], 1)
        self.assertEqual(before, list(self.store.db.iterdump()))
        self.store.life.record_episode(SCOPE, kind="read", source_id="fix", goal_id=goal["id"], supersedes_id=source["id"],
            evidence={"operation": "info", "result_excerpt": "new data"})
        self.assertFalse(self.projects.read(SCOPE, project["id"], self.cfg)["stages"][0]["portfolio_valid"])
        self.assertEqual(self.projects.due(SCOPE, self.cfg), [])
        with self.assertRaises(ValueError):
            self.review(project)

    def test_review_is_immutable_and_does_not_publish_or_memorize(self):
        project, goal, source, work = self.seed()
        result = self.review(project)
        self.assertEqual(result["stages"][0]["review"]["work_id"], work["id"])
        self.assertEqual(result["status"], "active")
        self.review(project)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_reviews").fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.projects.review(SCOPE, project["id"], 1, "改写", "局限", "下一问题", self.cfg)
        for table in ("memories", "deliveries", "proposals"):
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM "+table).fetchone()[0], 0)

    def test_new_question_must_not_repeat_existing_stage(self):
        project, goal, source, work = self.seed()
        with self.assertRaisesRegex(ValueError, "repeats"):
            self.review(project, goal["title"])

    def test_advance_creates_one_ready_goal_and_replay_is_stable(self):
        project, goal, source, work = self.seed()
        self.review(project)
        result = self.projects.advance(SCOPE, project["id"], 1, self.cfg)
        next_id = result["stages"][-1]["goal_id"]
        self.assertEqual(self.store.life.get_goal(SCOPE, next_id)["status"], "ready")
        self.assertEqual(self.projects.advance(SCOPE, project["id"], 1, self.cfg)["stages"][-1]["goal_id"], next_id)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM goals").fetchone()[0], 2)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_empty_question_and_stage_cap_close_without_new_goal(self):
        project, goal, source, work = self.seed(project_max_stages=1)
        self.assertEqual(self.review(project)["status"], "completed")
        with self.assertRaises(ValueError):
            self.projects.advance(SCOPE, project["id"], 1, self.cfg)
        self.assertEqual(self.projects.due(SCOPE, self.cfg), [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM goals").fetchone()[0], 1)

    def test_review_without_followup_closes_project(self):
        project, goal, source, work = self.seed()
        self.assertEqual(self.review(project, "")["status"], "completed")

    def test_stale_review_cannot_spawn_a_new_goal(self):
        project, goal, source, work = self.seed()
        self.review(project)
        self.store.db.execute("UPDATE creative_revisions SET body_hash='changed' WHERE work_id=?", (work["id"],))
        with self.assertRaises(ValueError):
            self.projects.advance(SCOPE, project["id"], 1, self.cfg)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM goals").fetchone()[0], 1)

    def test_crash_after_goal_creation_is_gated_and_recovers_without_duplicate(self):
        project, goal, source, work = self.seed()
        self.review(project)
        with patch.object(self.projects, "_link", side_effect=RuntimeError("simulated crash")), self.assertRaises(RuntimeError):
            self.projects.advance(SCOPE, project["id"], 1, self.cfg)
        orphan = self.store.life.goals(SCOPE)[0]
        self.assertFalse(self.projects.goal_gate(SCOPE, orphan["id"], self.cfg)["allowed"])
        self.store.close()
        self.store = core.Store(self.temp.name)
        self.projects = projects.Projects(self.store)
        self.assertEqual(self.projects.due(SCOPE, self.cfg)[0]["task"], "advance")
        result = self.projects.advance(SCOPE, project["id"], 1, self.cfg)
        self.assertEqual(result["stages"][-1]["goal_id"], orphan["id"])
        self.assertTrue(self.projects.goal_gate(SCOPE, orphan["id"], self.cfg)["allowed"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM goals").fetchone()[0], 2)

    def test_goal_capacity_retains_reservation_until_capacity_available(self):
        project, goal, source, work = self.seed()
        self.review(project)
        extra = [create_goal(self.store, str(i)) for i in range(8)]
        with self.assertRaisesRegex(ValueError, "existing goal"):
            self.projects.advance(SCOPE, project["id"], 1, self.cfg)
        self.assertEqual(self.projects.read(SCOPE, project["id"], self.cfg)["stages"][-1]["goal_status"], "pending_creation")
        self.store.life.update_goal(SCOPE, extra[0]["id"], status="cancelled")
        result = self.projects.advance(SCOPE, project["id"], 1, self.cfg)
        self.assertEqual(len(result["stages"]), 2)

    def test_pause_disable_and_archive_do_not_release_unfinished_goal(self):
        goal = create_goal(self.store)
        p = self.projects.create(SCOPE, goal["id"], "主题", "原因", self.cfg)
        self.projects.state(SCOPE, p["id"], "pause", self.cfg)
        self.assertFalse(self.projects.goal_gate(SCOPE, goal["id"], self.cfg)["allowed"])
        self.assertEqual(self.store.life.get_goal(SCOPE, goal["id"])["status"], "ready")
        with self.assertRaises(ValueError):
            self.projects.state(SCOPE, p["id"], "archive", self.cfg)
        self.projects.state(SCOPE, p["id"], "resume", self.cfg)
        self.assertFalse(self.projects.goal_gate(SCOPE, goal["id"], self.cfg | {"life_projects_enabled": False})["allowed"])
        self.store.life.update_goal(SCOPE, goal["id"], status="cancelled")
        self.assertEqual(self.projects.state(SCOPE, p["id"], "archive", self.cfg)["status"], "archived")

    def test_waiting_approval_and_blocked_cannot_become_review(self):
        goal = create_goal(self.store)
        p = self.projects.create(SCOPE, goal["id"], "主题", "原因", self.cfg)
        self.store.life.claim_goal(SCOPE, goal["id"], "run")
        self.store.life.update_goal(SCOPE, goal["id"], status="waiting_approval", run_id="run")
        self.assertEqual(self.projects.due(SCOPE, self.cfg), [])
        with self.assertRaises(ValueError):
            self.review(p)
        self.assertEqual(self.store.life.get_goal(SCOPE, goal["id"])["status"], "waiting_approval")

    def test_planner_review_respects_pause_off_and_repetition(self):
        project, goal, source, work = self.seed()
        planner = main.LifePlanner(self.store)
        choice = planner.choose(SCOPE, self.cfg, phone_busy=True)
        self.assertEqual(choice["action"], "project_review")
        self.assertIsNone(choice["goal_id"])
        self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", ("failed-review", SCOPE, time.time(), time.time(), "failed", "mock"))
        planner.record(SCOPE, "failed-review", choice)
        self.assertEqual(planner.choose(SCOPE, self.cfg, phone_busy=True)["action"], "rest")
        self.projects.state(SCOPE, project["id"], "pause", self.cfg)
        self.assertEqual(self.projects.due(SCOPE, self.cfg), [])
        self.assertEqual(self.projects.due(SCOPE, self.cfg | {"life_projects_enabled": False}), [])

    def test_sleep_still_rests_and_wind_down_only_reviews_existing_work(self):
        project, goal, source, work = self.seed()
        at = lambda hour: datetime.fromisoformat("2026-11-02T"+hour+":00+08:00").timestamp()
        self.store.db.execute("UPDATE goals SET updated_at=? WHERE id=?", (at("00:00")-86401, goal["id"]))
        cfg = self.cfg | {"life_rhythm_enabled": True}
        planner = main.LifePlanner(self.store)
        before = list(self.store.db.iterdump())
        self.assertEqual(planner.choose(SCOPE, cfg, now=at("23:30"))["action"], "rest")
        choice = planner.choose(SCOPE, cfg, now=at("22:15"), phone_busy=True)
        self.assertEqual(choice["action"], "project_review")
        self.assertEqual(choice["project_id"], project["id"])
        self.assertEqual(choice["rhythm"]["phase"], "wind_down")
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_two_stages_keep_versions_and_close_at_frozen_limit_after_restart(self):
        project, goal, source, work = self.seed(project_max_stages=2)
        self.review(project)
        second = self.projects.advance(SCOPE, project["id"], 1, self.cfg)["stages"][-1]
        next_goal = self.store.life.get_goal(SCOPE, second["goal_id"])
        other_source, other_work = complete_work(self.store, next_goal, "second-source")
        self.store.close()
        self.store = core.Store(self.temp.name)
        self.projects = projects.Projects(self.store)
        self.cfg["project_max_stages"] = 6  # An existing project's limit is frozen.
        self.assertEqual(self.projects.due(SCOPE, self.cfg)[0]["position"], 2)
        result = self.projects.review(SCOPE, project["id"], 2, "比较了两阶段资料", "仍有未确认的问题", "以后再研究另一个主题", self.cfg)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["max_stages"], 2)
        self.assertEqual([s["work"]["id"] for s in result["stages"]], [work["id"], other_work["id"]])
        self.assertEqual([json.loads(s["review"]["sources_json"]) for s in result["stages"]], [[source["id"]], [other_source["id"]]])
        with self.assertRaises(ValueError):
            self.projects.advance(SCOPE, project["id"], 2, self.cfg)
        self.assertEqual(self.projects.due(SCOPE, self.cfg), [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM goals").fetchone()[0], 2)

    def test_planner_filters_linked_goals_when_project_off_without_blocking_other_goals(self):
        goal = create_goal(self.store)
        self.projects.create(SCOPE, goal["id"], "主题", "原因", self.cfg)
        other = create_goal(self.store, "other")
        choice = main.LifePlanner(self.store).choose(SCOPE, self.cfg | {"life_projects_enabled": False})
        self.assertEqual(choice["goal_id"], other["id"])

    def test_invalid_config_rejected(self):
        for change in ({"life_projects_enabled": "false"}, {"project_active_limit": 0},
                       {"project_max_stages": True}, {"project_max_stages": 7}, {"project_review_delay_hours": 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                projects.validate_config(self.cfg | change)


class ProjectIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = planner_tests.PlannerIntegrationTests.asyncSetUp
    asyncTearDown = planner_tests.PlannerIntegrationTests.asyncTearDown
    save_config = planner_tests.PlannerIntegrationTests.save_config

    def enable(self):
        self.config.update(life_projects_enabled=True, workshop_enabled=True)
        self.save_config()

    def seed(self):
        self.enable()
        goal = create_goal(self.plugin.store)
        p = projects.Projects(self.plugin.store).create(SCOPE, goal["id"], "持续项目", "想弄清楚", self.config)
        source, work = complete_work(self.plugin.store, goal)
        return p, goal, source, work

    async def test_review_round_is_offline_bounded_and_can_prepare_next_stage(self):
        project, goal, source, work = self.seed()

        async def agent(event, prompt, tools, max_steps):
            self.assertIsNone(self.plugin.active_goal(event))
            self.assertEqual(tools, {projects.TOOL, "df_workshop", "df_recall", "df_life_status"})
            self.assertIn("这一轮只回看", prompt)
            await self.plugin.project(event, "review", project_id=project["id"], stage=1,
                                      reflection="记下观察", limitations="需要继续核实", next_question="换一个来源比较")
            await self.plugin.project(event, "advance", project_id=project["id"], stage=1)
            return "已准备下个阶段，这轮没有开始研究。"

        self.plugin.agent.side_effect = agent
        async with self.plugin.gate.lease("foreground"):
            await self.plugin.run_activity("periodic")
            self.assertEqual(self.plugin.gate.owner, "foreground")
        self.assertEqual(self.calls, [])
        self.delivery.send.assert_not_called()
        self.assertEqual(self.plugin.project_review_runs, {})
        head = projects.Projects(self.plugin.store).read(SCOPE, project["id"], self.config)["stages"][-1]
        self.assertEqual(head["goal_status"], "ready")
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(self.plugin.life_planner().recent(SCOPE)[0]["run_status"], "completed")

    async def test_old_config_default_off_and_hook_hides_tool(self):
        for key in projects.DEFAULTS:
            self.config.pop(key, None)
        self.save_config()
        self.assertFalse(self.plugin.cfg()["life_projects_enabled"])
        req = types.SimpleNamespace(system_prompt="", contexts=[], func_tool=fixtures.ToolSet([
            fixtures.FunctionTool(name=projects.TOOL, active=True)]))
        await self.plugin.guard_phone(fixtures.FakeEvent(), req)
        self.assertNotIn(projects.TOOL, req.func_tool.names())
        with self.assertRaises(PermissionError):
            await self.plugin.project(fixtures.FakeEvent())

    async def test_owner_and_public_reply_boundaries(self):
        self.enable()
        self.plugin.reply_runs = {"reply": {}}
        self.config.update(reply_watch_enabled=True, public_interactions=True)
        self.save_config()
        for event in (fixtures.FakeEvent(scope=OTHER), fixtures.FakeEvent(user="98765"), fixtures.FakeEvent(run_id="reply")):
            with self.assertRaises(PermissionError):
                await self.plugin.project(event)
        self.assertEqual(self.calls, [])

    async def test_foreground_configuration_rechecked_after_preemption(self):
        self.enable()
        goal = create_goal(self.plugin.store)

        async def disable(reason):
            self.config["life_projects_enabled"] = False
            self.save_config()

        self.plugin.pause_background = disable
        with self.assertRaises(PermissionError):
            await self.plugin.project(fixtures.FakeEvent(), "create", goal_id=goal["id"], title="主题", why="原因")
        self.assertFalse(projects.Projects(self.plugin.store).ready())

    async def test_review_run_cannot_mutate_other_project_or_create_new_project(self):
        project, goal, source, work = self.seed()
        self.plugin.project_review_runs = {"review": {"project_id": project["id"], "project_stage": 1}}
        event = fixtures.FakeEvent(run_id="review")
        for kwargs in ({"action": "create", "goal_id": goal["id"]}, {"action": "pause", "project_id": "other"},
                       {"action": "review", "project_id": project["id"], "stage": 2}):
            with self.assertRaises(PermissionError):
                await self.plugin.project(event, **kwargs)

    async def test_project_stop_during_run_blocks_further_tools(self):
        project, goal, source, work = self.seed()

        async def agent(event, *args, **kwargs):
            self.config["life_projects_enabled"] = False
            self.save_config()
            with self.assertRaises(PermissionError):
                await self.plugin.project(event, "review", project_id=project["id"], reflection="想法", limitations="限制")
            return "已经停止。"

        self.plugin.agent.side_effect = agent
        await self.plugin.run_activity("periodic")
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM project_reviews").fetchone()[0], 0)
        self.assertEqual(self.plugin.project_review_runs, {})

    async def test_dnd_pause_and_budget_still_prevent_review_round(self):
        self.seed()
        with patch.object(main, "quiet_now", return_value=True):
            await self.plugin.run_activity("periodic")
        self.plugin.store.setting("life_paused:"+SCOPE, True)
        await self.plugin.run_activity("periodic")
        self.plugin.store.setting("life_paused:"+SCOPE, False)
        self.plugin.activity_budget().start_run(SCOPE, "exhausted", 30*60, self.config)
        await self.plugin.run_activity("periodic")
        self.plugin.agent.assert_not_called()
        self.assertEqual(self.calls, [])

    async def test_cancelled_review_keeps_work_and_no_fake_completion(self):
        project, goal, source, work = self.seed()
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
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM project_reviews").fetchone()[0], 0)
        self.assertEqual(projects.Projects(self.plugin.store).read(SCOPE, project["id"], self.config)["status"], "active")
        self.assertEqual(self.plugin.life_planner().recent(SCOPE)[0]["run_status"], "cancelled")
        self.assertEqual(self.plugin.project_review_runs, {})

    async def test_normal_life_run_can_attach_only_its_own_current_goal(self):
        self.enable()
        own = create_goal(self.plugin.store)
        other = create_goal(self.plugin.store, "other")
        self.plugin.store.life.claim_goal(SCOPE, own["id"], "run")
        self.plugin.run_goals["run"] = own["id"]
        self.plugin.life_runs.add("run")
        event = fixtures.FakeEvent(run_id="run")
        with self.assertRaises(PermissionError):
            await self.plugin.project(event, "create", goal_id=other["id"], title="主题", why="原因")
        result = json.loads(await self.plugin.project(event, "create", title="主题", why="原因"))
        self.assertEqual(result["stages"][0]["goal_id"], own["id"])
        self.assertEqual(self.calls, [])


class ProjectOpsTests(unittest.TestCase):
    setUp = ops_tests.LifeOpsTests.setUp
    tearDown = ops_tests.LifeOpsTests.tearDown
    config = ops_tests.LifeOpsTests.config
    run_ops = ops_tests.LifeOpsTests.run_ops

    def test_defaults_prerequisites_and_switch_does_not_expand_permissions(self):
        for key, value in projects.DEFAULTS.items():
            self.assertEqual(main.DEFAULTS[key], value)
            self.assertEqual(ops_tests.ops.DEFAULTS[key], value)
        self.config()
        with self.assertRaises(ValueError):
            self.run_ops("switch", "life_projects_enabled", "on")
        path = self.config(life_enabled=True, workshop_enabled=True, life_decision_enabled=True, one_shot_verified=True, custom="keep")
        self.run_ops("switch", "life_projects_enabled", "on")
        value = json.loads(path.read_text())
        self.assertTrue(value["life_projects_enabled"])
        self.assertEqual(value["custom"], "keep")
        for key in ("enabled", "periodic_enabled", "public_interactions", "engagement_actions", "collection_management"):
            self.assertFalse(value[key])
        self.run_ops("switch", "life_projects_enabled", "off")
        self.assertFalse(json.loads(path.read_text())["life_projects_enabled"])

    def test_bad_limits_do_not_change_control(self):
        path = self.config(life_enabled=True, workshop_enabled=True, life_decision_enabled=True, one_shot_verified=True, project_max_stages=99)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.run_ops("switch", "life_projects_enabled", "on")
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

"""Cross-day controller journey with native Agent adapter and scripted providers.

All accounts, model turns and LivingMemory acknowledgements are fakes. Advancing
the clock tests date-dependent code, not autonomous model quality or real time.
"""
import json
import sys
import types
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import test_bili_activity as fixtures
import test_bili_planner as planner_fixtures
import test_bili_projects as project_fixtures
import test_bili_memory_policy as memory_fixtures

main, core, SCOPE = fixtures.main, fixtures.core, fixtures.SCOPE


class LifeJourneyTests(unittest.IsolatedAsyncioTestCase):
    save_config = planner_fixtures.PlannerIntegrationTests.save_config
    asyncTearDown = planner_fixtures.PlannerIntegrationTests.asyncTearDown
    seed = project_fixtures.ProjectIntegrationTests.seed
    enable = project_fixtures.ProjectIntegrationTests.enable

    async def asyncSetUp(self):
        await planner_fixtures.PlannerIntegrationTests.asyncSetUp(self)
        self.config.update(life_projects_enabled=True, workshop_enabled=True, life_memory_enabled=True,
                           self_expression_enabled=True, life_rhythm_enabled=True, project_max_stages=2,
                           collection_management=False, public_interactions=False, engagement_actions=False)
        self.save_config()
        # Keep the real agent/build hook/runner budget adapter. Substitute only
        # AstrBot build dependencies and a deterministic provider/tool script.
        self.plugin.agent = types.MethodType(main.BiliActivity.agent, self.plugin)
        self.plugin.context = types.SimpleNamespace(get_config=lambda **kw: {})
        self.provider_calls, self.runners, self.requests = [], [], []
        self.script = None
        self.script_assertion = None
        original_call = self.backend.json_call.side_effect

        async def readonly_backend(name, args):
            if name not in {"bili_phone", "bili_read"}:
                raise AssertionError("Non-read backend call: " + name)
            return await original_call(name, args)

        self.backend.json_call.side_effect = readonly_backend
        test = self

        class Provider:
            provider_config = {"id": "offline-script"}

            async def text_chat(self, **kwargs):
                test.provider_calls.append(kwargs)
                return types.SimpleNamespace(completion_text="离线脚本返回")

        class Runner:
            state = "done"
            streaming = False
            request_context_manager_config = None

            def __init__(self, event, req):
                self.event, self.req = event, req
                self.provider, self.fallback_providers = Provider(), []
                self.stopped = False

            def request_stop(self):
                self.stopped = True

            async def step_until_done(self, max_steps):
                await self.provider.text_chat(phase="choose_tools")
                try:
                    await test.script(self.event, self.req)
                except AssertionError as exc:
                    test.script_assertion = exc
                    raise
                yield None
                # The real runner asks its provider again after tool results.
                # This is the handoff that isolated tool-only tests omit.
                await self.provider.text_chat(phase="after_tools")
                yield None

            def get_final_llm_resp(self):
                return types.SimpleNamespace(completion_text="离线脚本：本轮结束，不发送消息。")

        async def build(**kwargs):
            req = kwargs["req"]
            req.system_prompt = "原有人格与情绪背景"
            names = main.ACTIVITY_TOOLS | main.LIFE_TOOLS | main.WORKSHOP_TOOLS | {main.PROJECT_TOOL, main.EXPRESSION_TOOL}
            req.func_tool = fixtures.ToolSet([fixtures.FunctionTool(name=name, active=True) for name in sorted(names)])
            runner = Runner(kwargs["event"], req)
            self.requests.append(req)
            self.runners.append(runner)

            async def reset():
                self.assertTrue(req.system_prompt.startswith("原有人格与情绪背景"))

            return types.SimpleNamespace(agent_runner=runner, reset_coro=reset())

        async def hook(event, name, *args):
            if args:
                await self.plugin.guard_phone(event, args[0])

        attrs = {
            "astrbot.core.astr_main_agent": {"MainAgentBuildConfig": lambda **kw: types.SimpleNamespace(**kw),
                "_get_session_conv": AsyncMock(return_value=types.SimpleNamespace(history="[]")), "build_main_agent": build},
            "astrbot.core.pipeline.context_utils": {"call_event_hook": hook},
            "astrbot.core.star.star_handler": {"EventType": types.SimpleNamespace(OnWaitingLLMRequestEvent="waiting", OnLLMRequestEvent="request")},
            "astrbot.core.agent.runners.base": {"AgentState": types.SimpleNamespace(ERROR="error")},
        }
        modules = {name: types.ModuleType(name) for name in attrs}
        for name, values in attrs.items():
            vars(modules[name]).update(values)
        for patcher in (patch.dict(sys.modules, modules), patch.object(main, "ProviderRequest", lambda **kw: types.SimpleNamespace(**kw)),
                        patch.object(main, "MCPClient", side_effect=AssertionError("real transport forbidden"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.delivery.send.side_effect = AssertionError("QQ sends forbidden in this journey")
        self.clock = datetime.fromisoformat("2026-11-03T10:00:00+08:00").timestamp()
        clock_patch = patch.object(main.time, "time", side_effect=lambda: self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    async def run_script(self, script):
        self.script = script
        self.script_assertion = None
        await self.plugin.run_activity("offline-acceptance")
        if self.script_assertion:
            raise self.script_assertion
        return self.plugin.life_planner().recent(SCOPE)[0]

    async def test_final_review_closes_native_round_without_extra_provider_or_failed_run(self):
        project, goal, source, work = self.seed()

        async def close(event, req):
            self.assertEqual(set(req.func_tool.names()), {"df_project", "df_workshop", "df_recall", "df_life_status"})
            result = json.loads(await self.plugin.project(event, "review", project_id=project["id"],
                reflection="已整理这阶段的观察", limitations="仍需独立来源", next_question=""))
            self.assertEqual(result["status"], "completed")

        result = await self.run_script(close)
        self.assertEqual(result["run_status"], "completed")
        self.assertEqual(len(self.provider_calls), 1)
        self.assertTrue(self.runners[-1].stopped)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM project_reviews").fetchone()[0], 1)
        self.assertEqual(self.calls, [])
        self.delivery.send.assert_not_called()
        self.assertEqual(self.plugin.project_review_runs, {})

    async def test_three_dates_keep_sources_versions_memory_and_room_in_sync(self):
        from deploy.memory_observatory.activity_reader import read_activity
        from deploy.bili_activity.life_observation import observe

        memory = memory_fixtures.MemoryIntegrationTests.mock_lm(self)
        self.plugin.context.get_config = lambda **kw: {}
        self.plugin.flush_memory = types.MethodType(main.BiliActivity.flush_memory, self.plugin)
        journey = {"goals": [], "works": [], "sources": []}

        async def research(event, req):
            self.assertIn("df_project", req.func_tool.names())
            self.assertIn("[自身近况依据]", req.system_prompt)
            goal = self.plugin.active_goal(event)
            if not goal:
                goal = json.loads(await self.plugin.goal(event, "create", title="比较合作建造方式",
                    why="想用不同资料回答一个小问题", next_step="先读一份资料"))
                project = json.loads(await self.plugin.project(event, "create", title="合作建造观察册", why="逐步比较"))
                journey["project_id"] = project["id"]
            journey["goals"].append(goal["id"])
            await self.plugin.read(event, "info", {"bvid": "BVexample", "cid": 1})
            source = next(e for e in self.plugin.store.life.episodes(SCOPE, goal_id=goal["id"]) if e["kind"] == "read")
            work = json.loads(await self.plugin.workshop(event, "create", title="阶段观察"))
            body = f"## 资料观察\n离线资料中的建造演示。[E:{source['id']}]\n## 我的想法\n比较过程，暂不推论。\n## 待核实\n需要独立资料。"
            work = json.loads(await self.plugin.workshop(event, "revise", work_id=work["id"], body=body,
                source_ids=json.dumps([source["id"]]), expected_revision=0))
            journey["sources"].append(source["id"])
            journey["works"].append(work["id"])
            completed = json.loads(await self.plugin.goal(event, "update", goal_id=goal["id"], status="completed"))
            self.assertEqual(completed["status"], "completed")

        first = await self.run_script(research)
        self.assertEqual((first["action"], first["run_status"]), ("explore", "completed"))
        self.assertEqual(len(self.provider_calls), 2)
        self.assertEqual(memory.call.await_count, 1)
        self.assertEqual(self.plugin.activity_budget().snapshot(SCOPE, self.config)["model_calls"], 2)

        # Same day: do not review before the 24-hour delay. Occupied phone also
        # prevents unrelated exploration; the readiness gate exits before choice.
        self.clock += 3600
        self.assertEqual(main.Projects(self.plugin.store).due(SCOPE, self.config), [])
        async with self.plugin.gate.lease("foreground"):
            self.assertFalse(self.plugin.life_ready(self.config))
            await self.run_script(research)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(len(self.provider_calls), 2)
        self.assertEqual(memory.call.await_count, 1)
        self.clock = datetime.fromisoformat("2026-11-03T23:50:00+08:00").timestamp()
        resting = await self.run_script(research)
        self.assertEqual(resting["action"], "rest")
        self.assertEqual(len(self.provider_calls), 2)

        # Reopen the persisted temporary database, as controller startup does.
        self.plugin.store.close()
        self.plugin.store = core.Store(self.plugin.folder)
        self.plugin.budget = main.Budget(self.plugin.store)
        self.clock = datetime.fromisoformat("2026-11-04T11:00:00+08:00").timestamp()
        backend_before = len(self.calls)

        async def reflect_and_prepare(event, req):
            self.assertEqual(set(req.func_tool.names()), {"df_project", "df_workshop", "df_recall", "df_life_status"})
            await self.plugin.project(event, "review", project_id=journey["project_id"], stage=1,
                reflection="第一阶段记录了演示", limitations="单一资料不能当结论", next_question="再比较一个案例")
            next_stage = json.loads(await self.plugin.project(event, "advance", project_id=journey["project_id"], stage=1))["stages"][-1]
            replay = json.loads(await self.plugin.project(event, "advance", project_id=journey["project_id"], stage=1))["stages"][-1]
            self.assertEqual(next_stage["goal_id"], replay["goal_id"])
            self.assertEqual(next_stage["goal_status"], "ready")
            journey["next_goal"] = next_stage["goal_id"]

        async with self.plugin.gate.lease("foreground"):
            review = await self.run_script(reflect_and_prepare)
        self.assertEqual((review["action"], review["run_status"]), ("project_review", "completed"))
        self.assertEqual(len(self.calls), backend_before)
        self.assertEqual(memory.call.await_count, 1)
        self.assertEqual(len(self.provider_calls), 4)

        self.clock += 600
        second = await self.run_script(research)
        self.assertEqual((second["goal_id"], second["run_status"]), (journey["next_goal"], "completed"))
        self.assertEqual(len(set(journey["goals"])), 2)
        self.assertEqual(memory.call.await_count, 2)
        self.assertEqual(self.plugin.activity_budget().snapshot(SCOPE, self.config)["model_calls"], 4)

        self.clock = datetime.fromisoformat("2026-11-05T12:00:00+08:00").timestamp()
        # A larger new setting must not extend this project's frozen two stages.
        self.config["project_max_stages"] = 6
        self.save_config()
        backend_before = len(self.calls)

        async def final_review(event, req):
            value = json.loads(await self.plugin.project(event, "review", project_id=journey["project_id"], stage=2,
                reflection="已完成两阶段比较", limitations="离线素材不能证明真实内容", next_question="以后另开主题"))
            self.assertEqual(value["status"], "completed")
            self.assertTrue(value["round_complete"])

        last = await self.run_script(final_review)
        self.assertEqual(last["run_status"], "completed")
        self.assertEqual(len(self.provider_calls), 7)
        self.assertEqual(len(self.calls), backend_before)
        self.assertEqual(self.plugin.activity_budget().snapshot(SCOPE, self.config)["model_calls"], 1)
        project = main.Projects(self.plugin.store).read(SCOPE, journey["project_id"], self.config)
        self.assertEqual((project["status"], project["max_stages"]), ("completed", 2))
        self.assertEqual([s["goal_id"] for s in project["stages"]], journey["goals"])
        self.assertEqual([s["work"]["id"] for s in project["stages"]], journey["works"])

        before = list(self.plugin.store.db.iterdump())
        room = read_activity(self.plugin.folder, now=self.clock)
        shelf = room["projects"]["items"][0]
        self.assertEqual((shelf["id"], shelf["status"]), (project["id"], "completed"))
        self.assertEqual([s["review_state"] for s in shelf["stages"]], ["recorded", "recorded"])
        self.assertEqual([s["review"]["revision"] for s in shelf["stages"]], [1, 1])
        self.assertEqual([s["review"]["reflection"] for s in shelf["stages"]], ["第一阶段记录了演示", "已完成两阶段比较"])
        expression = json.loads(await self.plugin.self_reflect(fixtures.FakeEvent()))
        self.assertEqual({g["goal_id"] for g in expression["completed_goals"]}, set(journey["goals"]))
        self.assertTrue(all(g["basis"] == "checked_work_record" for g in expression["completed_goals"]))
        self.assertEqual(expression["focus"], [])
        self.assertEqual(expression["interruptions"], [])
        report = observe(self.plugin.folder, project["id"], now=self.clock)
        self.assertEqual(report["state"], "recorded")
        self.assertEqual(report["recorded_run_dates"], ["2026-11-03", "2026-11-04", "2026-11-05"])
        self.assertEqual(len(report["runs"]), 4)
        self.assertEqual([r["action"] for r in report["runs"]], ["project_review", "research", "project_review", "explore"])
        self.assertEqual(sum(r["model_reservations"] for r in report["runs"]), 7)
        self.assertTrue(all(r["status"] == "completed" for r in report["runs"]))
        self.assertEqual(len(report["work_memory_queue"]), 2)
        self.assertTrue(all(r["status"] == "saved" for r in report["work_memory_queue"]))
        self.assertFalse(report["live_verified"])
        self.assertEqual(before, list(self.plugin.store.db.iterdump()))
        await self.plugin.flush_memory(fixtures.FakeEvent())
        self.assertEqual(memory.call.await_count, 2)
        rows = self.plugin.store.db.execute("SELECT id,status,body FROM memories").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["status"] == "saved" and not r["id"].startswith("run:") for r in rows))
        self.assertTrue(all("离线脚本：本轮结束" not in r["body"] for r in rows))
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM goals").fetchone()[0], 2)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM runs WHERE status!='completed'").fetchone()[0], 0)
        self.delivery.send.assert_not_called()
        self.assertTrue(all(not self.plugin.cfg()[key] for key in ("collection_management", "public_interactions", "engagement_actions")))

    async def test_external_completion_is_still_denied_at_provider_boundary(self):
        project, *_ = self.seed()

        async def external_change(event, req):
            main.Projects(self.plugin.store).review(SCOPE, project["id"], 1, "外部回看", "仍有局限", "", self.config)

        result = await self.run_script(external_change)
        self.assertEqual(result["run_status"], "failed")
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM events WHERE kind='project_review_run_finished'").fetchone()[0], 0)

    async def test_shutdown_after_saved_review_is_not_overridden_by_normal_finish(self):
        project, *_ = self.seed()

        async def close_and_disable(event, req):
            await self.plugin.project(event, "review", project_id=project["id"], reflection="已回看", limitations="待核实")
            self.config["enabled"] = False
            self.save_config()

        result = await self.run_script(close_and_disable)
        self.assertEqual(result["run_status"], "failed")
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(main.Projects(self.plugin.store)._project(SCOPE, project["id"])["status"], "completed")
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM events WHERE kind='project_review_run_finished'").fetchone()[0], 0)

    async def test_final_review_blocks_extra_tools_before_io_even_with_one_call_budget(self):
        project, *_ = self.seed()
        self.config["max_model_calls_per_run"] = 1
        self.save_config()

        async def close_then_read(event, req):
            await self.plugin.project(event, "review", project_id=project["id"], reflection="已回看", limitations="待核实")
            await self.plugin.read(event, "info", {"bvid": "BVexample"})
            self.fail("final review must not allow another tool to run")

        result = await self.run_script(close_then_read)
        self.assertEqual(result["run_status"], "completed")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(self.plugin.activity_budget().snapshot(SCOPE, self.config)["model_calls"], 1)

    async def test_unrelated_runner_error_after_review_is_not_reported_completed(self):
        project, *_ = self.seed()

        async def close_then_fail(event, req):
            await self.plugin.project(event, "review", project_id=project["id"], reflection="已回看", limitations="待核实")
            raise RuntimeError("fake runner failure")

        result = await self.run_script(close_then_fail)
        self.assertEqual(result["run_status"], "failed")
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM events WHERE kind='project_review_run_finished'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

"""Offline episode lifecycle and real controller/outbox with a fake LM tool."""
import asyncio
import importlib
import json
import sys
import tempfile
import time
import types
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import test_bili_activity as fixtures
import test_bili_life_integration as integration
import test_bili_life_ops as ops_tests
import test_bili_planner as planner_tests

main, core = fixtures.main, fixtures.core
SCOPE, OTHER = fixtures.SCOPE, fixtures.OTHER_SCOPE
policy = importlib.import_module("bili_activity_test.memory_policy")
DAY = 86400


def episode(store, *, age=0, scope=SCOPE, kind="read", goal_id=None, **kw):
    result = store.life.record_episode(scope, kind=kind, source_id=uuid.uuid4().hex,
        evidence=kw.pop("evidence", {"operation": "comments", "result_excerpt": "RAW-DETAIL"}),
        interpretation=kw.pop("interpretation", {"thought": "当时的主观感想"}), goal_id=goal_id, **kw)
    store.db.execute("UPDATE episodes SET occurred_at=? WHERE id=?", (time.time()-age*DAY, result["id"]))
    return store.life._episode(store.db.execute("SELECT * FROM episodes WHERE id=?", (result["id"],)).fetchone())


def interest(store, bvid, *, scope=SCOPE, topic="海洋", polarity=1, observed=True, age=0, source=None):
    source = source or episode(store, scope=scope, kind="video", age=age,
        evidence={"bvid": bvid, "cid": 1, "observations": ["实际读取摘录"] if observed else []})
    result = episode(store, scope=scope, kind="interest", age=age,
        evidence={"episode_id": source["id"], "bvid": bvid, "cid": 1},
        interpretation={"interest": {"topic": topic, "polarity": polarity}, "thought": "同一个兴趣"})
    return result, source


class MemoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Store(self.temp.name)
        self.policy = policy.MemoryPolicy(self.store)
        self.cfg = main.DEFAULTS | {"life_enabled": True, "enabled": True, "life_memory_enabled": True}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def recall(self, **kw):
        return self.policy.recall(SCOPE, self.cfg, **kw)

    def test_detail_gist_dormancy_and_source_without_deletion(self):
        row = episode(self.store)
        before = tuple(self.store.db.execute("SELECT * FROM episodes").fetchone())
        at = row["occurred_at"]
        self.assertEqual(self.recall(now=at+6*DAY)[0]["evidence"]["result_excerpt"], "RAW-DETAIL")
        gist = self.recall(now=at+7*DAY)[0]
        self.assertEqual(gist["recall_phase"], "gist")
        self.assertNotIn("RAW-DETAIL", core.canonical(gist))
        self.assertEqual(self.recall(now=at+60*DAY), [])
        self.assertEqual(self.recall(query="RAW-DETAIL", now=at+60*DAY)[0]["recall_phase"], "dormant")
        source = self.recall(episode_id=row["id"], now=at+60*DAY)[0]
        self.assertEqual(source["evidence"]["result_excerpt"], "RAW-DETAIL")
        self.assertEqual(tuple(self.store.db.execute("SELECT * FROM episodes").fetchone()), before)
        for table in ("memories", "events"):
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)

    def test_explicit_preference_and_active_goal_are_protected(self):
        pref = self.store.life.record_user_preference(SCOPE, "12345", "m1", "我喜欢蓝色")
        goal = self.store.life.create_goal(SCOPE, "课题", "原因", "查来源", "g1")
        source = episode(self.store, goal_id=goal["id"], age=100)
        self.store.db.execute("UPDATE episodes SET occurred_at=? WHERE id=?", (time.time()-100*DAY, pref["id"]))
        rows = {row["id"]: row for row in self.recall()}
        self.assertEqual(rows[pref["id"]]["recall_phase"], "protected")
        self.assertEqual(rows[source["id"]]["recall_phase"], "protected")
        self.store.life.update_goal(SCOPE, goal["id"], status="cancelled")
        self.assertNotIn(source["id"], [r["id"] for r in self.recall()])

    def test_checked_artifacts_remain_available(self):
        for kind in ("work_checked", "method_result"):
            episode(self.store, kind=kind, age=300)
        self.assertEqual([v["recall_phase"] for v in self.recall()], ["protected", "protected"])

    def test_off_switch_restores_original_query_exactly(self):
        episode(self.store, age=100)
        for change in ({"life_memory_enabled": False}, {"life_enabled": False}):
            self.assertEqual(self.policy.recall(SCOPE, self.cfg | change), self.store.life.episodes(SCOPE))
            self.assertIsNone(self.policy.queue(SCOPE, self.cfg | change))

    def test_corrected_source_is_auditable_but_not_default_recalled(self):
        old = episode(self.store)
        new = episode(self.store, supersedes_id=old["id"])
        self.assertEqual([v["id"] for v in self.recall()], [new["id"]])
        self.assertEqual(self.recall(episode_id=old["id"])[0]["superseded_by"], new["id"])
        self.assertEqual(self.policy.recall(OTHER, self.cfg, episode_id=old["id"]), [])

    def test_unknown_or_future_timestamps_do_not_look_fresh(self):
        row = episode(self.store)
        self.store.db.execute("UPDATE episodes SET occurred_at=?", (time.time()+DAY,))
        self.assertEqual(self.recall(), [])
        self.assertEqual(self.recall(query="RAW-DETAIL")[0]["recall_phase"], "unknown")
        self.assertEqual(self.recall(episode_id=row["id"])[0]["recall_phase"], "source")

    def test_invalid_config_and_clock_rejected(self):
        for change in ({"life_memory_enabled": "false"}, {"life_memory_detail_days": True},
                       {"life_memory_detail_days": 0}, {"life_memory_dormant_days": 366},
                       {"life_memory_detail_days": 60}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                policy.validate_config(self.cfg | change)
        for now in (-1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.recall(now=now)

    def test_only_independent_observed_videos_consolidate(self):
        for _ in range(3):
            interest(self.store, "BVsame")
        interest(self.store, "BVempty", observed=False)
        episode(self.store, kind="run_end", interpretation={"summary": "我非常喜欢海洋"})
        self.assertEqual(self.policy.candidates(SCOPE), [])
        interest(self.store, "BVnew")
        candidate = self.policy.candidates(SCOPE)[0]
        body = json.loads(candidate["body"])
        self.assertEqual({v["bvid"] for v in body["sources"]}, {"BVsame", "BVnew"})
        self.assertIn("不代表主人偏好", body["meaning"])
        self.assertEqual(len(self.policy.candidates(SCOPE)), 1)

    def test_same_video_different_parts_do_not_count_as_independent(self):
        interest(self.store, "BVsame")
        source = episode(self.store, kind="video", evidence={"bvid": "BVsame", "cid": 2, "subtitles": ["摘录"]})
        episode(self.store, kind="interest", evidence={"episode_id": source["id"], "bvid": "BVsame", "cid": 2},
                interpretation={"interest": {"topic": "海洋", "polarity": 1}})
        self.assertEqual(self.policy.candidates(SCOPE), [])

    def test_mixed_stance_and_cross_scope_sources_are_not_promoted(self):
        interest(self.store, "BV1")
        interest(self.store, "BV2", polarity=-1)
        self.assertEqual(self.policy.candidates(SCOPE), [])
        _, source = interest(self.store, "BV3", scope=OTHER)
        interest(self.store, "BV3", source=source)
        self.assertEqual(self.policy.candidates(SCOPE), [])
        self.assertEqual(self.policy.candidates(OTHER), [])

    def test_corrected_evidence_invalidates_pending_snapshot(self):
        _, source = interest(self.store, "BV1")
        interest(self.store, "BV2")
        key = self.policy.queue(SCOPE, self.cfg)
        row = self.store.db.execute("SELECT * FROM memories WHERE id=?", (key,)).fetchone()
        self.assertTrue(self.policy.valid_pending(SCOPE, row))
        episode(self.store, kind="video", supersedes_id=source["id"])
        self.assertFalse(self.policy.valid_pending(SCOPE, row))

    def test_snapshot_milestones_spacing_and_restart_no_replay(self):
        for i in range(8):
            interest(self.store, "BV" + str(i))
        self.assertEqual(len(self.policy.candidates(SCOPE)), 3)
        first = self.policy.queue(SCOPE, self.cfg)
        self.assertIsNone(self.policy.queue(SCOPE, self.cfg))
        self.store.db.execute("UPDATE memories SET status='sending'")
        self.store.close()
        self.store = core.Store(self.temp.name)
        self.policy = policy.MemoryPolicy(self.store)
        self.assertEqual(self.store.db.execute("SELECT status FROM memories WHERE id=?", (first,)).fetchone()[0], "uncertain")
        later = time.time()+DAY+1
        with patch.object(policy.time, "time", return_value=later):
            second = self.policy.queue(SCOPE, self.cfg)
        self.assertIsNotNone(second)
        self.assertNotEqual(second, first)
        self.assertEqual(self.store.db.execute("SELECT status FROM memories WHERE id=?", (first,)).fetchone()[0], "uncertain")

    def test_scope_keys_and_readonly_status(self):
        for scope in (SCOPE, OTHER):
            for bvid in ("BV1", "BV2"):
                interest(self.store, bvid, scope=scope)
        first = self.policy.queue(SCOPE, self.cfg)
        second = self.policy.queue(OTHER, self.cfg)
        self.assertNotEqual(first, second)
        changes = self.store.db.total_changes
        status = self.policy.status(SCOPE, self.cfg)
        self.assertEqual([r["id"] for r in status["consolidations"]], [first])
        self.assertNotIn("body", core.canonical(status))
        self.assertEqual(self.store.db.total_changes, changes)


class MemoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = integration.LifeIntegrationTests.asyncSetUp
    asyncTearDown = integration.LifeIntegrationTests.asyncTearDown
    save_config = integration.LifeIntegrationTests.save_config

    def enable(self):
        self.config["life_memory_enabled"] = True
        self.save_config()

    def mock_lm(self):
        tool = types.SimpleNamespace(active=True, call=AsyncMock(return_value='{"memorized":true,"id":"fake-memory"}'))
        manager = types.SimpleNamespace(get_full_tool_set=lambda: types.SimpleNamespace(get_tool=lambda name: tool))
        self.plugin.context = types.SimpleNamespace(get_llm_tool_manager=lambda: manager)
        module = types.ModuleType("astrbot.core.astr_agent_context")
        module.AgentContextWrapper = module.AstrAgentContext = types.SimpleNamespace
        patcher = patch.dict(sys.modules, {module.__name__: module})
        patcher.start()
        self.addCleanup(patcher.stop)
        return tool

    async def flush(self, event=None):
        await main.BiliActivity.flush_memory(self.plugin, event or fixtures.FakeEvent())

    def sources(self):
        first = interest(self.plugin.store, "BV1")
        interest(self.plugin.store, "BV2")
        return first

    async def test_old_config_defaults_and_original_recall(self):
        for key in policy.DEFAULTS:
            self.config.pop(key)
        self.save_config()
        self.assertFalse(self.plugin.cfg()["life_memory_enabled"])
        episode(self.plugin.store, age=100)
        self.assertEqual(json.loads(await self.plugin.recall(fixtures.FakeEvent()))["episodes"], self.plugin.store.life.episodes(SCOPE))

    async def test_recall_tool_and_foreground_context_apply_fading(self):
        self.enable()
        old = episode(self.plugin.store, age=10)
        request = types.SimpleNamespace(system_prompt="", func_tool=fixtures.ToolSet(), contexts=[])
        self.plugin.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", ("old", SCOPE, 1, 2, "completed", "OLD-RAW-SUMMARY"))
        await self.plugin.guard_phone(fixtures.FakeEvent(), request)
        self.assertIn('"recall_phase":"gist"', request.system_prompt)
        self.assertNotIn("RAW-DETAIL", request.system_prompt)
        self.assertNotIn("OLD-RAW-SUMMARY", request.system_prompt)
        recalled = await self.plugin.recall(fixtures.FakeEvent(), episode_id=old["id"])
        self.assertIn("RAW-DETAIL", recalled)
        with self.assertRaises(PermissionError):
            await self.plugin.recall(fixtures.FakeEvent(scope=OTHER), episode_id=old["id"])

    async def test_lm_bridge_uses_genuine_event_and_confirmation_no_resend(self):
        self.enable()
        self.sources()
        tool = self.mock_lm()
        event = fixtures.FakeEvent()
        await self.flush(event)
        tool.call.assert_awaited_once()
        call = tool.call.await_args
        self.assertIs(call.args[0].context.event, event)
        self.assertTrue(call.kwargs["reason"].startswith(policy.PREFIX))
        self.assertEqual(len(json.loads(call.kwargs["memory"])["sources"]), 2)
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM memories").fetchone()[0], "saved")
        receipt = json.loads(self.plugin.store.db.execute("SELECT details FROM events WHERE kind='memory_consolidation_result'").fetchone()[0])
        self.assertEqual(receipt["livingmemory_id"], "fake-memory")
        self.assertNotIn("body", receipt)
        await self.flush(event)
        tool.call.assert_awaited_once()

    async def test_bridge_inactive_wrong_owner_and_disabled_do_not_write(self):
        self.enable()
        self.sources()
        tool = self.mock_lm()
        tool.active = False
        await self.flush()
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
        tool.active = True
        for event in (fixtures.FakeEvent(scope=OTHER), fixtures.FakeEvent(user="98765")):
            await self.flush(event)
        tool.call.assert_not_called()
        policy.MemoryPolicy(self.plugin.store).queue(SCOPE, self.config)
        self.config["life_memory_enabled"] = False
        self.save_config()
        self.plugin.store.queue_memory(SCOPE, "原有显式偏好", "legacy")
        await self.flush()
        self.assertEqual(tool.call.await_count, 1)
        self.assertEqual(tool.call.await_args.kwargs["memory"], "原有显式偏好")
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM memories WHERE id LIKE ?", (policy.PREFIX+"%",)).fetchone()[0], "pending")

    async def test_cancellation_is_uncertain_and_concurrent_flush_does_not_duplicate(self):
        self.enable()
        self.sources()
        tool = self.mock_lm()
        started, wait = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            started.set()
            await wait.wait()

        tool.call.side_effect = delayed
        first = asyncio.create_task(self.flush())
        await started.wait()
        await self.flush()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await self.flush()
        self.assertEqual(tool.call.await_count, 1)
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM memories").fetchone()[0], "uncertain")

    async def test_tool_negative_response_is_failed_never_saved_or_retried(self):
        self.enable()
        self.sources()
        tool = self.mock_lm()
        tool.call.return_value = '{"memorized":false,"error":"internal_error"}'
        await self.flush()
        await self.flush()
        tool.call.assert_awaited_once()
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM memories").fetchone()[0], "failed")

    async def test_pending_invalidated_before_dispatch(self):
        self.enable()
        _, source = self.sources()
        tool = self.mock_lm()
        policy.MemoryPolicy(self.plugin.store).queue(SCOPE, self.config)
        episode(self.plugin.store, kind="video", supersedes_id=source["id"])
        await self.flush()
        tool.call.assert_not_called()
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM memories").fetchone()[0], "stale")

    async def test_disable_during_prior_write_prevents_snapshot_dispatch(self):
        self.enable()
        self.sources()
        tool = self.mock_lm()
        self.plugin.store.queue_memory(SCOPE, "已有的显式偏好", "legacy")

        async def disable(*args, **kwargs):
            self.config["life_memory_enabled"] = False
            self.save_config()
            return '{"memorized":true}'

        tool.call.side_effect = disable
        await self.flush()
        tool.call.assert_awaited_once()
        self.assertEqual(tool.call.await_args.kwargs["memory"], "已有的显式偏好")
        self.assertEqual(self.plugin.store.db.execute("SELECT status FROM memories WHERE id LIKE ?", (policy.PREFIX+"%",)).fetchone()[0], "pending")

    async def test_video_notes_stay_local_and_disable_restores_export(self):
        self.enable()
        value = {"bvid": "BV1", "cid": 1, "observations": [{"kind": "frame", "text": "真实截取"}], "agent_thought": "只是一次观察"}
        self.plugin.record_video(fixtures.FakeEvent(), value)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM episodes").fetchone()[0], 1)
        self.config["life_memory_enabled"] = False
        self.save_config()
        self.plugin.record_video(fixtures.FakeEvent(event_id="second"), value)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM memories").fetchone()[0], 1)

    async def test_reply_failure_details_never_enter_result_event(self):
        self.enable()
        self.sources()
        tool = self.mock_lm()
        tool.call.side_effect = RuntimeError("RAW SECRET RESPONSE https://example.invalid")
        with self.assertRaises(RuntimeError):
            await self.flush()
        receipt = self.plugin.store.db.execute("SELECT details FROM events WHERE kind='memory_consolidation_result'").fetchone()[0]
        self.assertNotIn("SECRET", receipt)
        self.assertNotIn("https", receipt)

    async def test_single_interest_is_local_while_explicit_preference_still_exports(self):
        self.enable()
        source = episode(self.plugin.store, kind="video", evidence={"bvid": "BV1", "cid": 1, "observations": ["mock"]})
        await self.plugin.interest(fixtures.FakeEvent(), source["id"], "海洋")
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
        await self.plugin.incoming(fixtures.FakeEvent(text="我喜欢蓝色", event_id="explicit"))
        self.assertIn("蓝色", self.plugin.store.db.execute("SELECT body FROM memories").fetchone()[0])

    async def test_status_is_scoped_readonly(self):
        self.enable()
        # Warm existing planner/contact ledgers separately, then inspect memory reads.
        await self.plugin.life_status(fixtures.FakeEvent())
        before = self.plugin.store.db.total_changes
        value = json.loads(await self.plugin.life_status(fixtures.FakeEvent()))
        self.assertTrue(value["memory_policy"]["enabled"])
        self.assertEqual(self.plugin.store.db.total_changes, before)


class MemoryRunTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = planner_tests.PlannerIntegrationTests.asyncSetUp
    asyncTearDown = planner_tests.PlannerIntegrationTests.asyncTearDown
    save_config = planner_tests.PlannerIntegrationTests.save_config

    async def test_background_prompt_fades_and_run_summary_is_not_durable_evidence(self):
        self.config["life_memory_enabled"] = True
        self.save_config()
        episode(self.plugin.store, age=10)
        self.plugin.store.video(SCOPE, {"bvid": "BVold", "cid": 1, "agent_thought": "OLD-RAW-VIDEO"})
        await self.plugin.run_activity("periodic")
        prompt = self.plugin.agent.await_args.args[1]
        self.assertIn('"recall_phase":"gist"', prompt)
        self.assertEqual(prompt.count('"recall_phase":"gist"'), 1)
        self.assertIn("见下方持续活动资料中的episodes", prompt)
        self.assertNotIn("RAW-DETAIL", prompt)
        self.assertNotIn("OLD-RAW-VIDEO", prompt)
        self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM memories").fetchone()[0], 0)
        self.assertTrue(self.plugin.store.life.episodes(SCOPE))


class MemoryOpsTests(unittest.TestCase):
    setUp = ops_tests.LifeOpsTests.setUp
    tearDown = ops_tests.LifeOpsTests.tearDown
    config = ops_tests.LifeOpsTests.config
    run_ops = ops_tests.LifeOpsTests.run_ops

    def test_defaults_match_and_enable_requires_life_then_rollback(self):
        for key, value in policy.DEFAULTS.items():
            self.assertEqual(ops_tests.ops.DEFAULTS[key], value)
        path = self.config(one_shot_verified=True)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.run_ops("switch", "life_memory_enabled", "on")
        self.assertEqual(path.read_bytes(), before)
        self.run_ops("switch", "life_enabled", "on")
        self.run_ops("switch", "life_memory_enabled", "on")
        self.assertTrue(json.loads(path.read_text())["life_memory_enabled"])
        self.run_ops("switch", "life_memory_enabled", "off")
        self.assertFalse(json.loads(path.read_text())["life_memory_enabled"])

    def test_invalid_windows_rejected_without_config_change(self):
        path = self.config(life_enabled=True, one_shot_verified=True)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.run_ops("configure", "--life-memory-detail-days", "60")
        self.assertEqual(path.read_bytes(), before)
        path = self.config(life_enabled=True, one_shot_verified=True, life_memory_detail_days=60)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.run_ops("switch", "life_memory_enabled", "on")
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

"""Offline self-expression provenance, boundaries and native request hook."""
import importlib
import json
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_bili_activity as fixtures
import test_bili_life_integration as life_tests
import test_bili_life_ops as ops_tests
import test_bili_memory_policy as memory_tests
import test_bili_planner as planner_tests

main, core = fixtures.main, fixtures.core
SCOPE, OTHER = fixtures.SCOPE, fixtures.OTHER_SCOPE
expression = importlib.import_module("bili_activity_test.self_expression")
Workshop = importlib.import_module("bili_activity_test.workshop").Workshop
episode, interest = memory_tests.episode, memory_tests.interest
DAY = 86400


class ExpressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = patch.object(expression, "_clock", side_effect=lambda now: 1800000000 if now is None else memory_tests.policy._clock(now))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.time = patch.object(memory_tests.time, "time", return_value=1800000000)
        self.time.start()
        self.addCleanup(self.time.stop)
        self.store = core.Store(self.temp.name)
        self.view = expression.SelfExpression(self.store)
        self.cfg = main.DEFAULTS | {"life_enabled": True, "self_expression_enabled": True}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def snapshot(self, **kw):
        return self.view.snapshot(SCOPE, self.cfg, **kw)

    def goal(self, key="goal", scope=SCOPE):
        return self.store.life.create_goal(scope, "合作建造", "想查清限制", "接着核对资料", key)

    def test_empty_history_is_unknown_and_creates_nothing(self):
        before = list(self.store.db.iterdump())
        value = self.snapshot()
        for key in ("focus", "completed_goals", "interests", "interruptions"):
            self.assertEqual(value[key], [])
        self.assertIn("未知", value["meaning"])
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_disabled_and_old_config_have_no_new_context(self):
        for cfg in (main.DEFAULTS, self.cfg | {"self_expression_enabled": False}, self.cfg | {"life_enabled": False}):
            with patch.object(self.view.memory, "observed_interests", side_effect=AssertionError("must not scan")):
                self.assertEqual(self.view.context(SCOPE, cfg), "")
                self.assertEqual(self.view.snapshot(SCOPE, cfg), {"enabled": False})

    def test_single_observation_cannot_be_a_personality_or_change(self):
        interest(self.store, "BVold1", age=20, polarity=-1)
        interest(self.store, "BVold2", age=21, polarity=-1)
        interest(self.store, "BVnew", age=1)
        value = self.snapshot()["interests"][0]
        self.assertEqual(value["state"], "tentative")
        self.assertIsNone(value["recent_direction"])
        self.assertEqual(value["previous_direction"], -1)

    def test_independent_evidence_in_both_windows_supports_change(self):
        for bvid, age, polarity in (("BV1", 20, -1), ("BV2", 21, -1), ("BV3", 1, 1), ("BV4", 2, 1)):
            interest(self.store, bvid, age=age, polarity=polarity)
        row = self.snapshot()["interests"][0]
        self.assertEqual(row["state"], "changed_between_windows")
        self.assertEqual((row["previous_direction"], row["recent_direction"]), (-1, 1))
        self.assertEqual((row["previous_origin_count"], row["recent_origin_count"]), (2, 2))
        self.assertTrue(all(v["source_episode_id"] for v in row["recent_sources"] + row["previous_sources"]))

    def test_repeated_direction_is_not_relabelled_as_change(self):
        for i, age in enumerate((20, 21, 1, 2)):
            interest(self.store, "BV"+str(i), age=age)
        self.assertEqual(self.snapshot()["interests"][0]["state"], "consistent_between_windows")

    def test_repeated_video_and_other_parts_cannot_manufacture_change(self):
        interest(self.store, "BV1", age=20, polarity=-1)
        interest(self.store, "BV2", age=21, polarity=-1)
        for _ in range(4):
            interest(self.store, "BV1", age=1)
        source = episode(self.store, kind="video", age=1, evidence={"bvid": "BV1", "cid": 2, "subtitles": ["摘录"]})
        episode(self.store, kind="interest", age=1, evidence={"episode_id": source["id"], "bvid": "BV1", "cid": 2},
                interpretation={"interest": {"topic": "海洋", "polarity": 1}})
        row = self.snapshot()["interests"][0]
        self.assertEqual((row["state"], row["recent_origin_count"], row["previous_origin_count"]), ("tentative", 1, 1))

    def test_conflicting_current_evidence_remains_mixed(self):
        interest(self.store, "BV1", polarity=1)
        interest(self.store, "BV2", polarity=-1)
        row = self.snapshot()["interests"][0]
        self.assertEqual(row["state"], "mixed")
        self.assertIsNone(row["recent_direction"])

    def test_no_recent_evidence_does_not_mean_interest_lost(self):
        interest(self.store, "BV1", age=20)
        interest(self.store, "BV2", age=21)
        self.assertEqual(self.snapshot()["interests"], [])

    def test_invalid_source_foreign_user_and_summaries_are_not_self_evidence(self):
        self.store.life.record_user_preference(SCOPE, "12345", "m1", "我喜欢海洋")
        interest(self.store, "BVempty", observed=False)
        _, other = interest(self.store, "BVother", scope=OTHER)
        interest(self.store, "BVother", source=other)
        episode(self.store, kind="run_end", interpretation={"summary": "我完成了所有事情，还变得喜欢海洋"})
        self.assertEqual(self.snapshot()["interests"], [])
        self.assertEqual(self.snapshot()["completed_goals"], [])

    def test_corrected_and_future_sources_do_not_support_interests(self):
        _, source = interest(self.store, "BV1")
        episode(self.store, kind="video", supersedes_id=source["id"])
        interest(self.store, "BVfuture", age=-1)
        self.assertEqual(self.snapshot()["interests"], [])

    def test_period_boundary_uses_actual_timestamps_and_shanghai_labels(self):
        for i, age in enumerate((14, 1, 14.001, 28, 28.001)):
            interest(self.store, "BV"+str(i), age=age)
        value = self.snapshot()
        row = value["interests"][0]
        self.assertEqual((row["recent_origin_count"], row["previous_origin_count"]), (2, 2))
        self.assertTrue(value["as_of"].endswith("+08:00"))

    def test_focus_is_intent_and_reading_does_not_claim_goal(self):
        goal = self.goal()
        before = list(self.store.db.iterdump())
        value = self.snapshot()
        self.assertEqual(value["focus"][0]["status"], "ready")
        self.assertTrue(value["focus"][0]["intent_only"])
        self.assertEqual(value["completed_goals"], [])
        self.assertEqual(self.store.life.get_goal(SCOPE, goal["id"])["status"], "ready")
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_goal_completion_requires_current_observed_source(self):
        goal = self.goal()
        source = episode(self.store, goal_id=goal["id"])
        self.store.life.update_goal(SCOPE, goal["id"], status="completed")
        row = self.snapshot()["completed_goals"][0]
        self.assertEqual(row["basis"], "goal_record_with_observations")
        self.assertEqual(row["source_ids"], [source["id"]])
        episode(self.store, goal_id=goal["id"], supersedes_id=source["id"], evidence={"operation": "info", "result_excerpt": '{"ok":false}'})
        self.assertEqual(self.snapshot()["completed_goals"][0]["basis"], "needs_recheck")

    def checked_work(self):
        goal = self.goal()
        source = episode(self.store, goal_id=goal["id"])
        workshop = Workshop(self.store)
        work = workshop.create(SCOPE, goal["id"], "小笔记", "w1")
        body = f"## 资料观察\n来源描述。[E:{source['id']}]\n## 我的想法\n先记下想法。\n## 待核实\n还需要别的来源。"
        work = workshop.revise(SCOPE, work["id"], body, [source["id"]], 0, "r1")
        check = episode(self.store, kind="work_checked", goal_id=goal["id"], evidence={"work_id": work["id"],
            "revision": 1, "body_hash": work["body_hash"], "source_ids": work["source_ids"], "checks_passed": True})
        self.store.life.update_goal(SCOPE, goal["id"], status="completed")
        return source, work, check

    def test_work_check_is_readonly_and_requires_current_revision(self):
        source, work, check = self.checked_work()
        before = list(self.store.db.iterdump())
        row = self.snapshot()["completed_goals"][0]
        self.assertEqual((row["basis"], row["check_episode_id"]), ("checked_work_record", check["id"]))
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.store.db.execute("UPDATE creative_works SET revision=2 WHERE id=?", (work["id"],))
        self.assertEqual(self.snapshot()["completed_goals"][0]["basis"], "needs_recheck")

    def test_work_source_correction_and_invalid_receipt_block_completion_claim(self):
        source, work, check = self.checked_work()
        self.store.db.execute("UPDATE episodes SET evidence_json=? WHERE id=?", (json.dumps({"checks_passed": False}), check["id"]))
        self.assertEqual(self.snapshot()["completed_goals"][0]["basis"], "needs_recheck")
        episode(self.store, supersedes_id=source["id"])
        self.assertEqual(self.snapshot()["completed_goals"][0]["source_ids"], [])

    def test_stops_report_known_boundaries_not_model_summary_or_emotions(self):
        for i, status in enumerate(expression.STOP_REASONS):
            self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)",
                (str(i), SCOPE, 1799999000+i, 1799999100+i, status, "RAW SECRET: 主人讨厌我，所以我生气了"))
        value = self.snapshot()
        self.assertEqual(len(value["interruptions"]), 3)
        self.assertNotIn("RAW SECRET", core.canonical(value))
        self.assertNotIn("emotion_score", value)
        self.assertEqual(value["completed_goals"], [])
        for row in value["interruptions"]:
            self.assertEqual(row["known_reason"], expression.STOP_REASONS[row["status"]])

    def test_complete_run_not_goal_completion_and_foreign_future_runs_ignored(self):
        for name, scope, ended, status in (("done", SCOPE, 1799999990, "completed"),
                 ("foreign", OTHER, 1799999990, "failed"), ("future", SCOPE, 1800000100, "timeout")):
            self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (name, scope, ended-1, ended, status, "secret"))
        self.assertEqual(self.snapshot()["completed_goals"], [])
        self.assertEqual(self.snapshot()["interruptions"], [])

    def test_scope_isolation_for_goals_and_no_outside_window_completion(self):
        self.goal(scope=OTHER)
        self.assertEqual(self.snapshot()["focus"], [])
        goal = self.goal()
        episode(self.store, goal_id=goal["id"])
        self.store.life.update_goal(SCOPE, goal["id"], status="completed")
        self.store.db.execute("UPDATE goals SET updated_at=? WHERE id=?", (1800000000-15*DAY, goal["id"]))
        self.assertEqual(self.snapshot()["completed_goals"], [])

    def test_repeated_review_is_stable_and_not_a_new_memory(self):
        interest(self.store, "BV1")
        interest(self.store, "BV2")
        before = list(self.store.db.iterdump())
        first = self.snapshot()
        for _ in range(5):
            self.assertEqual(self.snapshot(), first)
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_bounded_scan_reports_incomplete_history(self):
        for _ in range(501):
            episode(self.store)
        for i in range(5):
            self.goal(str(i))
        value = self.snapshot()
        self.assertTrue(value["coverage"]["episode_history_truncated"])
        self.assertEqual(len(value["focus"]), 3)

    def test_invalid_config_and_clock_are_rejected(self):
        for change in ({"self_expression_enabled": "false"}, {"self_expression_window_days": True},
                       {"self_expression_window_days": 6}, {"self_expression_window_days": 91}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.view.snapshot(SCOPE, self.cfg | change)
        for now in (-1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.snapshot(now=now)

    def test_rhythm_phase_and_load_are_exposed_readonly_in_snapshot_and_context(self):
        from datetime import datetime
        self.assertEqual(self.snapshot()["rhythm"], {"enabled": False, "configured": False, "phase": "disabled"})
        cfg = self.cfg | {"life_decision_enabled": True, "life_rhythm_enabled": True}
        wind_down_at = datetime.fromisoformat("2026-11-01T22:30:00+08:00").timestamp()
        self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)",
                              ("r1", SCOPE, wind_down_at - 900, wind_down_at - 60, "completed", "PRIVATE SUMMARY"))
        before = list(self.store.db.iterdump())
        snap = self.view.snapshot(SCOPE, cfg, now=wind_down_at)
        self.assertEqual(snap["rhythm"]["phase"], "wind_down")
        self.assertTrue(snap["rhythm"]["writing_only"])
        self.assertEqual(snap["rhythm"]["recent_activity_minutes"], 14.0)
        memo = self.view.context(SCOPE, cfg, now=wind_down_at)
        self.assertIn("睡前整理时段", memo)
        self.assertIn("14.0/20 分钟", memo)
        self.assertNotIn("PRIVATE SUMMARY", memo)
        self.assertEqual(list(self.store.db.iterdump()), before)

        day_at = datetime.fromisoformat("2026-11-02T14:00:00+08:00").timestamp()
        self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)",
                              ("r2", SCOPE, day_at - 1500, day_at - 300, "completed", "PRIVATE SUMMARY"))
        rest_snap = self.view.snapshot(SCOPE, cfg, now=day_at)
        self.assertEqual(rest_snap["rhythm"]["phase"], "rest_after_activity")
        self.assertFalse(rest_snap["rhythm"]["allow_activity"])
        self.assertIn("活动后留白休息", self.view.context(SCOPE, cfg, now=day_at))

    def test_projects_stage_and_due_states_are_exposed_readonly_without_creating_tables(self):
        Projects = importlib.import_module("bili_activity_test.projects").Projects
        cfg = self.cfg | {"life_projects_enabled": True, "workshop_enabled": True, "life_decision_enabled": True}
        before_tables = {r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(self.view.snapshot(SCOPE, cfg)["projects"], [])
        self.assertEqual({r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}, before_tables)

        goal = self.goal("proj-g1")
        projects = Projects(self.store)
        proj = projects.create(SCOPE, goal["id"], "海洋建筑考据", "分阶段核对不同资料", cfg)
        other_goal = self.goal("other-g", scope=OTHER)
        projects.create(OTHER, other_goal["id"], "外部会话项目", "不应出现", cfg)
        source = episode(self.store, goal_id=goal["id"])
        workshop = Workshop(self.store)
        work = workshop.create(SCOPE, goal["id"], "第一阶段笔记", "pw1")
        body = f"## 资料观察\n阶段观察。[E:{source['id']}]\n## 我的想法\n阶段想法。\n## 待核实\n阶段待核实。"
        work = workshop.revise(SCOPE, work["id"], body, [source["id"]], 0, "pr1")
        episode(self.store, kind="work_checked", goal_id=goal["id"], evidence={
            "work_id": work["id"], "revision": 1, "body_hash": work["body_hash"],
            "source_ids": work["source_ids"], "checks_passed": True})
        self.store.life.update_goal(SCOPE, goal["id"], status="completed")

        # Before 24h review delay: not due yet.
        early = self.view.snapshot(SCOPE, cfg, now=1800000000 + 3600)
        self.assertEqual(len(early["projects"]), 1)
        self.assertEqual(early["projects"][0]["project_id"], proj["id"])
        self.assertIsNone(early["projects"][0]["due_task"])

        # After 24h delay: review is due.
        due_time = 1800000000 + 25 * 3600
        due_snap = self.view.snapshot(SCOPE, cfg, now=due_time)
        self.assertEqual(due_snap["projects"][0]["due_task"], "review")
        self.assertIn("阶段已完成，到点待回看", self.view.context(SCOPE, cfg, now=due_time))

        # Record review with next_question: advance is due.
        with patch.object(importlib.import_module("bili_activity_test.projects").time, "time", return_value=due_time):
            projects.review(SCOPE, proj["id"], 1, "阶段一收获", "单一来源局限", "查第二份独立案例", cfg)
        adv_snap = self.view.snapshot(SCOPE, cfg, now=due_time + 60)
        self.assertEqual(adv_snap["projects"][0]["due_task"], "advance")
        self.assertEqual(adv_snap["projects"][0]["current_stage"]["next_question"], "查第二份独立案例")
        self.assertIn("已回看，待推进下一阶段：查第二份独立案例", self.view.context(SCOPE, cfg, now=due_time + 60))

        # Supersede source with failed read: portfolio invalidated and flagged for recheck.
        episode(self.store, goal_id=goal["id"], supersedes_id=source["id"],
                evidence={"operation": "info", "result_excerpt": '{"ok":false}'})
        invalid_snap = self.view.snapshot(SCOPE, cfg, now=due_time + 120)
        self.assertFalse(invalid_snap["projects"][0]["current_stage"]["portfolio_valid"])
        self.assertIsNone(invalid_snap["projects"][0]["due_task"])
        self.assertIn("来源或作品结构待重新核对", self.view.context(SCOPE, cfg, now=due_time + 120))

    def test_foreground_preemption_links_activity_and_goal_with_whitelist_and_recency_window(self):
        goal = self.goal("preempt-g")
        started, ended = 1799999800, 1799999860
        self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)",
                              ("run-preempt", SCOPE, started, ended, "cancelled", "UNTRUSTED SUMMARY"))
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                              (started, SCOPE, "trigger", core.canonical({"run_id": "run-preempt", "activity": "browsing"})))
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                              (started + 1, SCOPE, "goal_selected", core.canonical({"run_id": "run-preempt", "goal_id": goal["id"]})))
        # Untrusted or foreign-scope preemption reasons must be ignored.
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                              (ended, OTHER, "foreground_preempt", core.canonical({"reason": "主人使用手机"})))
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                              (ended, SCOPE, "foreground_preempt", core.canonical({"reason": "RAW SECRET: 非白名单原因"})))
        snap_untrusted = self.snapshot()
        self.assertIsNone(snap_untrusted["recent_preemption"])
        self.assertNotIn("preempt_reason", snap_untrusted["interruptions"][0])
        self.assertNotIn("RAW SECRET", self.view.context(SCOPE, self.cfg))

        # Whitelisted foreground preemption within the run window links cleanly.
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                              (ended, SCOPE, "foreground_preempt", core.canonical({"reason": "主人请求截图"})))
        snap = self.snapshot()
        top = snap["interruptions"][0]
        self.assertEqual(top["known_reason"], expression.STOP_REASONS["cancelled"])
        self.assertEqual(top["activity"], "browsing")
        self.assertEqual((top["goal_id"], top["goal_title"]), (goal["id"], "合作建造"))
        self.assertEqual(top["preempt_reason"], "主人请求截图")
        self.assertIsNotNone(snap["recent_preemption"])
        self.assertEqual(snap["recent_preemption"]["ago_seconds"], 140)
        memo = self.view.context(SCOPE, self.cfg)
        self.assertIn("刚刚停下的手头事：因主人要看截图而停下了当时的后台活动；当时正在逛B站看资料（目标《合作建造》），进度已保留。", memo)

        # Outside the 15-minute recency window, recent_preemption clears.
        later = self.view.snapshot(SCOPE, self.cfg, now=ended + 901)
        self.assertIsNone(later["recent_preemption"])
        self.assertEqual(later["interruptions"][0]["preempt_reason"], "主人请求截图")

        # If a newer run finishes after the preemption, recent_preemption also clears.
        self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)",
                              ("run-after", SCOPE, ended + 10, ended + 40, "completed", "done"))
        self.assertIsNone(self.snapshot()["recent_preemption"])

    def test_natural_context_memo_omits_internal_hashes_and_ids_while_keeping_boundaries(self):
        source, work, check = self.checked_work()
        for bvid, age, polarity in (("BV1", 20, -1), ("BV2", 21, -1), ("BV3", 1, 1), ("BV4", 2, 1)):
            interest(self.store, bvid, age=age, polarity=polarity)
        memo = self.view.context(SCOPE, self.cfg)
        self.assertIn(expression.MARKER, memo)
        self.assertIn("只读回顾", memo)
        self.assertIn("已核对关联作品第 1 版与当前来源", memo)
        self.assertIn("自身兴趣观察「海洋」：此前窗口（2 个来源）偏不太感冒，近期窗口（2 个来源）转为偏感兴趣", memo)
        for internal in (work["body_hash"], work["id"], check["id"], source["id"], "episode_scan_limit", "checked_work_record"):
            self.assertNotIn(internal, memo)

    def test_held_thoughts_expose_blocked_shares_and_clear_when_delivered_or_invalid(self):
        source, work, _ = self.checked_work()
        goal = self.store.life.goals(SCOPE, include_terminal=True)[0]
        now = 1800000000
        # Foreign scope and untrusted reasons are ignored.
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)", (
            now - 120, OTHER, "share_held", core.canonical({
                "kind": "thought", "delivery_id": "t-other", "fingerprint": "fp-other",
                "excerpt": "外部会话念头", "reason": "现在是免打扰时间"})))
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)", (
            now - 110, SCOPE, "share_held", core.canonical({
                "kind": "thought", "delivery_id": "t-bad", "fingerprint": "fp-bad",
                "excerpt": "不应出现", "reason": "RAW SECRET: 非白名单拒绝"})))
        self.assertEqual(self.snapshot()["held_thoughts"], [])

        # Valid held thought and held work excerpt are surfaced read-only.
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)", (
            now - 90, SCOPE, "share_held", core.canonical({
                "kind": "thought", "delivery_id": "thought:r1:fp1", "fingerprint": "fp1",
                "excerpt": "看到一段双人机关配合特别巧妙", "reason": "主人刚在聊天，先留出空闲",
                "goal_id": goal["id"]})))
        self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)", (
            now - 60, SCOPE, "share_held", core.canonical({
                "kind": "work", "delivery_id": f"work:{work['id']}:1", "fingerprint": work["body_hash"],
                "excerpt": "整理好啦 — 《小笔记》第1版摘录", "reason": "daily_shares exhausted",
                "goal_id": goal["id"], "work_id": work["id"], "revision": 1})))
        before = list(self.store.db.iterdump())
        snap = self.snapshot()
        self.assertEqual(len(snap["held_thoughts"]), 2)
        self.assertEqual(snap["held_thoughts"][0]["kind"], "work")
        self.assertEqual(snap["held_thoughts"][1]["kind"], "thought")
        self.assertEqual(snap["held_thoughts"][1]["goal_title"], "合作建造")
        memo = self.view.context(SCOPE, self.cfg)
        self.assertIn("攒着没发出去的作品摘录（关联目标《合作建造》）", memo)
        self.assertIn("当时今日主动分享次数已满，留着没发，当次主动分享未发送QQ）：整理好啦 — 《小笔记》第1版摘录", memo)
        self.assertIn("攒着没发出去的想法（关联目标《合作建造》）", memo)
        self.assertIn("当时主人刚在聊天，留着没打扰，当次主动分享未发送QQ）：看到一段双人机关配合特别巧妙", memo)
        self.assertEqual(list(self.store.db.iterdump()), before)

        # Once the thought is delivered, it is no longer held.
        self.store.delivery(SCOPE, "thought:r2:fp1", "thought", "fp1", {}, True)
        self.store.delivered("thought:r2:fp1", {"message_id": 201})
        after_send = self.snapshot()["held_thoughts"]
        self.assertEqual(len(after_send), 1)
        self.assertEqual(after_send[0]["kind"], "work")

        # If the work's source is superseded by a failed read, the held work also clears.
        episode(self.store, goal_id=goal["id"], supersedes_id=source["id"],
                evidence={"operation": "info", "result_excerpt": '{"ok":false}'})
        self.assertEqual(self.snapshot()["held_thoughts"], [])

    def test_curiosity_threads_surface_project_followup_and_checked_read_work_questions(self):
        source, work, _ = self.checked_work()
        snap = self.snapshot()
        self.assertEqual(len(snap["curiosity_threads"]), 1)
        thread = snap["curiosity_threads"][0]
        self.assertEqual(thread["kind"], "work_open_question")
        self.assertEqual(thread["question"], "还需要别的来源。")
        self.assertEqual(thread["reflection"], "先记下想法。")
        self.assertEqual(thread["source_ids"], [source["id"]])
        memo = self.view.context(SCOPE, self.cfg)
        self.assertIn("留着想继续弄清的问题（来自已完成目标《合作建造》作品待核实，有 1 条可用来源）：还需要别的来源。（当时想法：先记下想法。）", memo)

        # If a new goal already uses that question as its title, do not repeat it as a pending thread.
        self.store.life.create_goal(SCOPE, "还需要别的来源。", "已经立项", "去查资料", "followup-goal")
        self.assertEqual(self.snapshot()["curiosity_threads"], [])

    def test_douyin_interests_preemptions_and_hypothesis_threads_in_self_expression(self):
        # 1. Two independent Douyin video observations support repeated_recent interest.
        for idx in (1, 2):
            dy_ep = episode(
                self.store, kind="douyin_video", age=idx,
                evidence={"author": f"@up{idx}", "caption": f"手工木作第{idx}期", "audio_transcript": "榫卯结构讲解"},
            )
            episode(
                self.store, kind="interest", age=idx,
                evidence={"episode_id": dy_ep["id"], "platform": "douyin", "author": f"@up{idx}", "caption": f"手工木作第{idx}期"},
                interpretation={"interest": {"topic": "传统木作", "polarity": 1}},
            )
        # 2. Hypothesis card from Douyin observation surfaces in curiosity_threads.
        gate = main.KnowledgeGate(self.store)
        hyp = gate.evaluate_candidate(
            SCOPE, self.cfg | {"learning_enabled": True},
            topic="燕尾榫承重极限",
            claim="干燥硬木燕尾榫无需胶水即可承受百斤拉力",
            rationale="抖音木工视频单源演示，想再去B站找力学测试核实",
            source_episode_ids=[dy_ep["id"]],
            category="creator_craft",
            proposed_tier="verified_knowledge",
        )
        self.assertEqual(hyp["epistemic_status"], "hypothesis")
        # 3. Foreground preemption from Douyin browsing request links cleanly.
        started, ended = 1799999900, 1799999950
        self.store.db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?)",
            ("run-dy-preempt", SCOPE, started, ended, "cancelled", "summary"),
        )
        self.store.db.execute(
            "INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
            (ended, SCOPE, "foreground_preempt", core.canonical({"reason": "主人请求刷抖音"})),
        )
        snap = self.snapshot()
        self.assertEqual(snap["interests"][0]["topic"], "传统木作")
        self.assertEqual(snap["interests"][0]["state"], "repeated_recent")
        self.assertEqual(snap["interests"][0]["recent_origin_count"], 2)
        self.assertEqual(snap["recent_preemption"]["reason"], "主人请求刷抖音")
        self.assertEqual(snap["curiosity_threads"][0]["kind"], "hypothesis_verification")
        self.assertEqual(snap["curiosity_threads"][0]["title"], "燕尾榫承重极限")
        memo = self.view.context(SCOPE, self.cfg)
        self.assertIn("因主人请求刷抖音而停下了当时的后台活动", memo)
        self.assertIn("来自互联网见闻待核实假设《燕尾榫承重极限》", memo)
        self.assertIn("自身兴趣观察「传统木作」", memo)


class ExpressionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = life_tests.LifeIntegrationTests.asyncSetUp
    asyncTearDown = life_tests.LifeIntegrationTests.asyncTearDown
    save_config = life_tests.LifeIntegrationTests.save_config

    def enable(self):
        self.config["self_expression_enabled"] = True
        self.save_config()

    @staticmethod
    def request():
        return types.SimpleNamespace(system_prompt="原有人格\nEmotionAI 原有上下文", contexts=[],
            func_tool=fixtures.ToolSet([fixtures.FunctionTool(name=expression.TOOL, active=True)]))

    async def test_old_config_default_off_and_optional_tool_removed(self):
        for key in expression.DEFAULTS:
            self.config.pop(key)
        self.save_config()
        self.assertFalse(self.plugin.cfg()["self_expression_enabled"])
        req = self.request()
        await self.plugin.guard_phone(fixtures.FakeEvent(), req)
        self.assertNotIn(expression.TOOL, req.func_tool.names())
        self.assertNotIn(expression.MARKER, req.system_prompt)
        with self.assertRaises(PermissionError):
            await self.plugin.self_reflect(fixtures.FakeEvent())

    async def test_hook_preserves_persona_and_emotion_and_adds_only_scoped_data(self):
        self.enable()
        req = self.request()
        await self.plugin.guard_phone(fixtures.FakeEvent(), req)
        self.assertTrue(req.system_prompt.startswith("原有人格\nEmotionAI 原有上下文"))
        self.assertEqual(req.system_prompt.count(expression.MARKER), 1)
        self.assertIn("只读回顾", req.system_prompt)
        self.assertIn(expression.TOOL, req.func_tool.names())
        for event in (fixtures.FakeEvent(scope=OTHER), fixtures.FakeEvent(user="98765")):
            req = self.request()
            await self.plugin.guard_phone(event, req)
            self.assertNotIn(expression.MARKER, req.system_prompt)
            self.assertNotIn(expression.TOOL, req.func_tool.names())

    async def test_tool_owner_checks_foreground_and_background(self):
        self.enable()
        for event in (fixtures.FakeEvent(scope=OTHER), fixtures.FakeEvent(user="98765"),
                      fixtures.FakeEvent(user="98765", run_id="run")):
            with self.assertRaises(PermissionError):
                await self.plugin.self_reflect(event)
        self.assertEqual(self.calls, [])
        self.delivery.send.assert_not_called()

    async def test_review_reads_without_memory_calls_sends_or_permission_changes(self):
        self.enable()
        before_cfg = self.plugin.control.read_bytes()
        before = list(self.plugin.store.db.iterdump())
        value = json.loads(await self.plugin.self_reflect(fixtures.FakeEvent()))
        self.assertTrue(value["enabled"])
        self.assertEqual(list(self.plugin.store.db.iterdump()), before)
        self.assertEqual(self.plugin.control.read_bytes(), before_cfg)
        self.assertEqual(self.calls, [])
        self.plugin.flush_memory.assert_not_called()
        self.delivery.send.assert_not_called()

    async def test_public_reply_watcher_does_not_receive_private_self_review(self):
        self.enable()
        self.config.update(reply_watch_enabled=True, public_interactions=True)
        self.save_config()
        self.plugin.reply_runs = {"reply": {}}
        event = fixtures.FakeEvent(run_id="reply")
        req = self.request()
        await self.plugin.guard_phone(event, req)
        self.assertNotIn(expression.MARKER, req.system_prompt)
        self.assertNotIn(expression.TOOL, req.func_tool.names())
        with self.assertRaises(PermissionError):
            await self.plugin.self_reflect(event)

    async def test_disable_restores_prompt_and_tool_boundaries(self):
        self.enable()
        await self.plugin.self_reflect(fixtures.FakeEvent())
        self.config["self_expression_enabled"] = False
        self.save_config()
        req = self.request()
        await self.plugin.guard_phone(fixtures.FakeEvent(), req)
        self.assertNotIn(expression.MARKER, req.system_prompt)
        with self.assertRaises(PermissionError):
            await self.plugin.self_reflect(fixtures.FakeEvent())

    async def test_background_pause_and_dnd_still_reject_tool(self):
        self.enable()
        self.plugin.life_runs.add("run")
        event = fixtures.FakeEvent(run_id="run")
        with patch.object(main, "quiet_now", return_value=True), self.assertRaises(PermissionError):
            await self.plugin.self_reflect(event)
        self.plugin.store.setting("life_paused:"+SCOPE, True)
        with self.assertRaises(PermissionError):
            await self.plugin.self_reflect(event)

    async def test_life_status_exposes_view_without_writes(self):
        self.enable()
        before = list(self.plugin.store.db.iterdump())
        value = json.loads(await self.plugin.life_status(fixtures.FakeEvent()))
        self.assertTrue(value["self_expression"]["enabled"])
        self.assertEqual(list(self.plugin.store.db.iterdump()), before)

    async def test_native_agent_hook_supplies_context_before_runner_reset(self):
        self.enable()
        observed = {}
        tool = fixtures.FunctionTool(name=expression.TOOL, active=True)
        self.plugin.context = types.SimpleNamespace(get_config=lambda **kw: {})

        class Runner:
            state = "done"

            async def step_until_done(self, steps):
                yield None

            def get_final_llm_resp(self):
                return types.SimpleNamespace(completion_text="离线回顾")

        async def build(**kwargs):
            req = kwargs["req"]
            req.system_prompt = "原人格和情绪"
            req.func_tool = fixtures.ToolSet([tool])

            async def reset():
                observed["prompt"], observed["tools"] = req.system_prompt, req.func_tool.names()

            return types.SimpleNamespace(agent_runner=Runner(), reset_coro=reset())

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
        with patch.dict(sys.modules, modules), patch.object(main, "ProviderRequest", lambda **kw: types.SimpleNamespace(**kw)):
            result = await self.plugin.agent(fixtures.FakeEvent(), "近况", {expression.TOOL})
        self.assertEqual(result, "离线回顾")
        self.assertTrue(observed["prompt"].startswith("原人格和情绪"))
        self.assertIn(expression.MARKER, observed["prompt"])
        self.assertEqual(observed["tools"], [expression.TOOL])

    async def test_blocked_autonomous_share_records_held_thought_for_next_chat(self):
        self.enable()
        self.config["daily_shares"] = 0
        self.save_config()
        self.plugin.life_runs.add("hold-run")
        self.plugin.activity_budget().start_run(SCOPE, "hold-run", 600, self.config)
        event = fixtures.FakeEvent(run_id="hold-run")
        goal = self.plugin.store.life.create_goal(SCOPE, "合作建造", "想查清限制", "接着核对资料", "hold-goal")
        self.plugin.store.life.claim_goal(SCOPE, goal["id"], "hold-run")
        self.plugin.run_goals["hold-run"] = goal["id"]
        with self.assertRaises(main.BudgetExceeded):
            await self.plugin.share_thought(event, "刚才看到个很有意思的合作机关想跟你说")
        self.delivery.send.assert_not_called()
        snap = json.loads(await self.plugin.self_reflect(fixtures.FakeEvent()))
        self.assertEqual(len(snap["held_thoughts"]), 1)
        self.assertEqual(snap["held_thoughts"][0]["excerpt"], "刚才看到个很有意思的合作机关想跟你说")
        req = self.request()
        await self.plugin.guard_phone(fixtures.FakeEvent(), req)
        self.assertIn("攒着没发出去的想法", req.system_prompt)
        self.assertIn("刚才看到个很有意思的合作机关想跟你说", req.system_prompt)


class ExpressionRunTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = planner_tests.PlannerIntegrationTests.asyncSetUp
    asyncTearDown = planner_tests.PlannerIntegrationTests.asyncTearDown
    save_config = planner_tests.PlannerIntegrationTests.save_config

    async def test_disabled_tool_not_required_by_background_and_enabled_tool_is_available(self):
        await self.plugin.run_activity("once")
        self.assertNotIn(expression.TOOL, self.plugin.agent.await_args.args[2])
        self.config["self_expression_enabled"] = True
        self.config["life_decision_enabled"] = False
        self.save_config()
        await self.plugin.run_activity("once")
        self.assertIn(expression.TOOL, self.plugin.agent.await_args.args[2])


class ExpressionOpsTests(unittest.TestCase):
    setUp = ops_tests.LifeOpsTests.setUp
    tearDown = ops_tests.LifeOpsTests.tearDown
    config = ops_tests.LifeOpsTests.config
    run_ops = ops_tests.LifeOpsTests.run_ops

    def test_enable_rollback_defaults_and_existing_permissions_preserved(self):
        for key, value in expression.DEFAULTS.items():
            self.assertEqual(main.DEFAULTS[key], value)
            self.assertEqual(ops_tests.ops.DEFAULTS[key], value)
        path = self.config(custom="keep")
        with self.assertRaises(ValueError):
            self.run_ops("switch", "self_expression_enabled", "on")
        path = self.config(life_enabled=True, custom="keep")
        self.run_ops("switch", "self_expression_enabled", "on")
        cfg = json.loads(path.read_text())
        self.assertEqual(cfg["custom"], "keep")
        self.assertTrue(cfg["self_expression_enabled"])
        for key in ("enabled", "periodic_enabled", "public_interactions", "engagement_actions", "collection_management"):
            self.assertFalse(cfg[key])
        self.run_ops("switch", "self_expression_enabled", "off")
        self.assertFalse(json.loads(path.read_text())["self_expression_enabled"])

    def test_window_config_and_switch_validation_preserve_invalid_file(self):
        path = self.config(life_enabled=True)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.run_ops("configure", "--self-expression-window-days", "6")
        self.assertEqual(path.read_bytes(), before)
        self.run_ops("configure", "--self-expression-window-days", "30")
        self.assertEqual(json.loads(path.read_text())["self_expression_window_days"], 30)
        path = self.config(life_enabled=True, self_expression_window_days=True)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.run_ops("switch", "self_expression_enabled", "on")
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

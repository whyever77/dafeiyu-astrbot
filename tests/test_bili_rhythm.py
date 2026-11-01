"""Offline rhythm policy and controller integration. No live source or account."""
import importlib
import json
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import test_bili_activity as fixtures
import test_bili_planner as planner_fixtures
import test_bili_life_ops as ops_fixtures

main, core = fixtures.main, fixtures.core
SCOPE, OTHER = fixtures.SCOPE, fixtures.OTHER_SCOPE
rhythm = importlib.import_module("bili_activity_test.rhythm")
Workshop = planner_fixtures.Workshop


def at(clock, day="2026-11-01"):
    return datetime.fromisoformat(day + "T" + clock + "+08:00").timestamp()


def add_run(store, start, end, *, scope=SCOPE, status="completed", name="past"):
    store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (name, scope, start, end, status, "PRIVATE SUMMARY"))


class RhythmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Store(self.temp.name)
        self.cfg = main.DEFAULTS | {"life_enabled": True, "life_decision_enabled": True, "life_rhythm_enabled": True}
        self.planner = main.LifePlanner(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def state(self, clock="14:00", **cfg):
        return rhythm.snapshot(self.store.db, SCOPE, self.cfg | cfg, now=at(clock))

    def goal(self, key="g", status=None):
        g = self.store.life.create_goal(SCOPE, "观察海洋", "想弄清楚", "查第二份资料", key)
        return self.store.life.update_goal(SCOPE, g["id"], status=status) if status else g

    def test_old_config_and_missing_prerequisites_disable_without_reading_db(self):
        for cfg in ({}, {"life_rhythm_enabled": True}, self.cfg | {"life_decision_enabled": False}):
            self.assertFalse(rhythm.snapshot(None, SCOPE, cfg)["enabled"])
        old = {k: v for k, v in self.cfg.items() if k not in rhythm.DEFAULTS}
        a = self.planner.choose(SCOPE, old, now=at("23:30"))
        self.assertEqual(a["action"], "explore")
        self.assertNotIn("rhythm", a)

    def test_shanghai_sleep_wake_and_wind_down_boundaries(self):
        for clock, expected in (("07:59", "sleep"), ("08:00", "morning"), ("10:00", "day"),
                                ("21:59", "day"), ("22:00", "wind_down"), ("23:00", "sleep"), ("00:00", "sleep")):
            with self.subTest(clock=clock):
                self.assertEqual(self.state(clock)["phase"], expected)
        self.assertEqual(self.state("23:00")["next_eligible_at"], at("08:00", "2026-11-02"))
        self.assertEqual(self.state("07:59")["next_eligible_at"], at("08:00"))

    def test_configured_daytime_sleep_and_no_wind_down(self):
        cfg = {"life_sleep_hour": 8, "life_wake_hour": 16, "life_wind_down_minutes": 0}
        self.assertEqual(self.state("09:00", **cfg)["phase"], "sleep")
        self.assertEqual(self.state("23:00", **cfg)["phase"], "day")

    def test_recent_time_merges_overlaps_and_counts_failed_work(self):
        add_run(self.store, at("13:30"), at("13:45"))
        add_run(self.store, at("13:40"), at("13:50"), status="failed", name="overlap")
        state = self.state()
        self.assertEqual(state["recent_activity_minutes"], 20)
        self.assertFalse(state["allow_activity"])
        self.assertEqual(state["phase"], "rest_after_activity")
        self.assertEqual(state["source_run_count"], 2)

    def test_scope_future_unfinished_and_invalid_records_are_not_activity(self):
        add_run(self.store, at("13:00"), at("13:40"), scope=OTHER, name="other")
        add_run(self.store, at("13:00"), None, status="running", name="running")
        add_run(self.store, at("13:00"), at("15:00"), name="future")
        add_run(self.store, at("13:40"), at("13:30"), name="invalid")
        add_run(self.store, at("09:00"), at("09:30"), name="old")
        self.assertEqual(self.state()["recent_activity_minutes"], 0)
        self.assertTrue(self.state()["allow_activity"])

    def test_window_clips_old_intervals_and_recovers_without_a_reset(self):
        add_run(self.store, at("11:30"), at("12:30"))
        self.assertEqual(self.state()["recent_activity_minutes"], 30)
        self.assertFalse(self.state()["allow_activity"])
        self.assertTrue(self.state("14:11")["allow_activity"])
        self.assertAlmostEqual(self.state()["next_eligible_at"], at("14:11"), delta=1)

    def test_midnight_does_not_reset_recent_activity(self):
        add_run(self.store, at("23:30"), at("23:50"))
        cfg = self.cfg | {"life_sleep_hour": 6, "life_wake_hour": 14}
        state = rhythm.snapshot(self.store.db, SCOPE, cfg, now=at("00:10", "2026-11-02"))
        self.assertEqual(state["recent_activity_minutes"], 20)
        self.assertFalse(state["allow_activity"])

    def test_snapshot_is_readonly_and_survives_restart(self):
        add_run(self.store, at("13:30"), at("13:50"))
        before = self.store.db.total_changes
        state = self.state()
        self.assertEqual(self.store.db.total_changes, before)
        self.assertNotIn("PRIVATE SUMMARY", json.dumps(state))
        self.store.close()
        self.store = core.Store(self.temp.name)
        self.assertEqual(self.state(), state)

    def test_sleep_selects_rest_without_changing_goal_or_memory(self):
        goal = self.goal()
        before = self.store.db.total_changes
        choice = self.planner.choose(SCOPE, self.cfg, now=at("23:00"))
        self.assertEqual(choice["action"], "rest")
        self.assertIn("休息时段", choice["reason"])
        self.assertEqual(self.store.db.total_changes, before)
        self.assertEqual(self.store.life.get_goal(SCOPE, goal["id"])["status"], "ready")

    def test_wind_down_uses_existing_work_only_and_keeps_approval_exclusions(self):
        goal = self.goal()
        cfg = self.cfg | {"workshop_enabled": True}
        self.assertEqual(self.planner.choose(SCOPE, cfg, now=at("22:00"))["action"], "rest")
        work = Workshop(self.store).create(SCOPE, goal["id"], "海洋笔记", "work")
        choice = self.planner.choose(SCOPE, cfg, now=at("22:00"), phone_busy=True)
        self.assertEqual((choice["action"], choice["work_id"]), ("writing", work["id"]))
        self.store.life.update_goal(SCOPE, goal["id"], status="blocked")
        self.assertEqual(self.planner.choose(SCOPE, cfg, now=at("22:00"))["action"], "rest")

    def test_morning_prefers_continuation_and_day_still_allows_exploration(self):
        goal = self.goal()
        choice = self.planner.choose(SCOPE, self.cfg, now=at("08:30"))
        self.assertEqual(choice["goal_id"], goal["id"])
        self.assertIn({"reason": "rhythm_continue_in_morning", "points": 6}, choice["factors"])
        self.store.life.update_goal(SCOPE, goal["id"], status="blocked")
        self.assertEqual(self.planner.choose(SCOPE, self.cfg, now=at("14:00"))["action"], "explore")

    def test_less_than_one_minute_headroom_rests(self):
        add_run(self.store, at("13:00"), at("13:19:30"))
        self.assertFalse(self.state()["allow_activity"])
        self.assertEqual(self.state("22:59:40")["phase"], "sleep")

    def test_invalid_config_and_clock_are_rejected(self):
        for change in ({"life_rhythm_enabled": "false"}, {"life_sleep_hour": True}, {"life_wake_hour": 24},
                       {"life_wake_hour": 23}, {"life_activity_window_minutes": 30, "life_activity_limit_minutes": 31},
                       {"life_sleep_hour": 9, "life_wake_hour": 8, "life_wind_down_minutes": 60}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                rhythm.validate_config(self.cfg | change)
        for value in (float("nan"), float("inf"), -1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rhythm.snapshot(self.store.db, SCOPE, self.cfg, now=value)


class RhythmIntegrationTests(unittest.IsolatedAsyncioTestCase):
    save_config = planner_fixtures.PlannerIntegrationTests.save_config
    asyncTearDown = planner_fixtures.PlannerIntegrationTests.asyncTearDown
    goal = planner_fixtures.PlannerIntegrationTests.goal

    async def asyncSetUp(self):
        await planner_fixtures.PlannerIntegrationTests.asyncSetUp(self)
        self.config["life_rhythm_enabled"] = True
        self.save_config()

    async def test_night_rest_has_zero_agent_device_send_budget_and_memory(self):
        self.goal()
        with patch.object(main.time, "time", return_value=at("23:30")):
            await self.plugin.run_activity("periodic")
        self.plugin.agent.assert_not_called()
        self.plugin.make_event.assert_not_called()
        self.plugin.flush_memory.assert_not_called()
        self.delivery.send.assert_not_called()
        self.assertEqual(self.calls, [])
        for table in ("runs", "episodes", "memories", "activity_budget_runs"):
            self.assertEqual(self.plugin.store.db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
        choice = self.plugin.life_planner().recent(SCOPE)[0]
        self.assertEqual((choice["action"], choice["rhythm"]["phase"]), ("rest", "sleep"))

    async def test_writing_duration_stops_at_sleep_boundary(self):
        self.config["workshop_enabled"] = True
        self.save_config()
        goal = self.goal()
        Workshop(self.plugin.store).create(SCOPE, goal["id"], "笔记", "work")
        budget = self.plugin.activity_budget()
        with patch.object(main.time, "time", return_value=at("22:58")), patch.object(budget, "start_run", wraps=budget.start_run) as start:
            await self.plugin.run_activity("periodic")
        self.assertEqual(start.call_args.args[2], 120)
        self.plugin.agent.assert_awaited_once()
        self.assertEqual(self.calls, [])

    async def test_slow_event_preparation_does_not_start_work_after_sleep_boundary(self):
        self.config["workshop_enabled"] = True
        self.save_config()
        goal = self.goal()
        Workshop(self.plugin.store).create(SCOPE, goal["id"], "笔记", "work")
        clock = [at("22:59")]

        async def prepare(run_id):
            clock[0] = at("23:01")
            return fixtures.FakeEvent(run_id=run_id)

        self.plugin.make_event.side_effect = prepare
        with patch.object(main.time, "time", side_effect=lambda: clock[0]):
            await self.plugin.run_activity("periodic")
        self.plugin.agent.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plugin.life_planner().recent(SCOPE)[0]["run_status"], "budget_exhausted")
        self.assertEqual(self.plugin.store.life.get_goal(SCOPE, goal["id"])["status"], "paused")

    async def test_recorded_load_caps_next_run_and_disable_restores_legacy_duration(self):
        self.goal()
        add_run(self.plugin.store, at("13:00"), at("13:18"))
        budget = self.plugin.activity_budget()
        with patch.object(main.time, "time", return_value=at("14:00")), patch.object(budget, "start_run", wraps=budget.start_run) as start:
            await self.plugin.run_activity("periodic")
        self.assertEqual(start.call_args.args[2], 120)
        self.config["life_rhythm_enabled"] = False
        self.save_config()
        with patch.object(main.time, "time", return_value=at("18:00")), patch.object(budget, "start_run", wraps=budget.start_run) as start:
            await self.plugin.run_activity("periodic")
        self.assertEqual(start.call_args.args[2], self.config["max_minutes"] * 60)

    async def test_existing_dnd_pause_and_owner_scope_still_apply(self):
        with patch.object(main, "quiet_now", return_value=True):
            await self.plugin.run_activity("periodic")
        self.assertEqual(self.plugin.life_planner().recent(SCOPE), [])
        self.plugin.store.setting("life_paused:" + SCOPE, True)
        with patch.object(main.time, "time", return_value=at("14:00")):
            await self.plugin.run_activity("periodic")
        self.plugin.agent.assert_not_called()
        with self.assertRaises(PermissionError):
            await self.plugin.life_status(fixtures.FakeEvent(scope=OTHER))

    async def test_status_is_readonly_even_during_sleep(self):
        before = self.plugin.store.db.total_changes
        with patch.object(main.time, "time", return_value=at("23:30")):
            state = json.loads(await self.plugin.life_status(fixtures.FakeEvent()))
        self.assertEqual(state["rhythm"]["phase"], "sleep")
        self.assertEqual(self.plugin.store.db.total_changes, before)
        self.plugin.agent.assert_not_called()


class RhythmOpsTests(unittest.TestCase):
    setUp = ops_fixtures.LifeOpsTests.setUp
    tearDown = ops_fixtures.LifeOpsTests.tearDown
    run_ops = ops_fixtures.LifeOpsTests.run_ops
    config = ops_fixtures.LifeOpsTests.config

    def test_defaults_match_and_activation_does_not_expand_permissions(self):
        self.assertEqual({k: main.DEFAULTS[k] for k in rhythm.DEFAULTS}, rhythm.DEFAULTS)
        self.assertEqual({k: ops_fixtures.ops.DEFAULTS[k] for k in rhythm.DEFAULTS}, rhythm.DEFAULTS)
        path = self.config(custom="keep")
        with self.assertRaises(ValueError):
            self.run_ops("switch", "life_rhythm_enabled", "on")
        self.config(life_enabled=True, life_decision_enabled=True, one_shot_verified=True, custom="keep")
        self.run_ops("switch", "life_rhythm_enabled", "on")
        cfg = json.loads(path.read_text())
        self.assertTrue(cfg["life_rhythm_enabled"])
        self.assertEqual(cfg["custom"], "keep")
        for key in ("enabled", "periodic_enabled", "public_interactions", "engagement_actions", "collection_management"):
            self.assertFalse(cfg[key])
        self.run_ops("switch", "life_rhythm_enabled", "off")
        self.assertFalse(json.loads(path.read_text())["life_rhythm_enabled"])

    def test_switch_refuses_invalid_rhythm_values(self):
        path = self.config(life_enabled=True, life_decision_enabled=True, one_shot_verified=True,
                           life_sleep_hour=8, life_wake_hour=8)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.run_ops("switch", "life_rhythm_enabled", "on")
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_pair_does_not_write_config_and_valid_changes_preserve_metadata(self):
        path = self.config(custom="keep")
        before = path.read_bytes()
        for args in (("--life-wake-hour", "23"), ("--life-sleep-hour", "24"),
                     ("--life-activity-window-minutes", "30", "--life-activity-limit-minutes", "31")):
            with self.assertRaises(ValueError):
                self.run_ops("configure", *args)
            self.assertEqual(path.read_bytes(), before)
        self.run_ops("configure", "--life-wake-hour", "9", "--life-activity-limit-minutes", "15")
        cfg = json.loads(path.read_text())
        self.assertEqual((cfg["life_wake_hour"], cfg["life_activity_limit_minutes"], cfg["custom"]), (9, 15, "keep"))


if __name__ == "__main__":
    unittest.main()

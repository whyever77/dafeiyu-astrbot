"""Offline observation export with real producer schemas in temporary storage."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from deploy.bili_activity import life_observation as observation
import test_memory_projects as producer

SCOPE = producer.SCOPE


class ObservationTests(unittest.TestCase):
    setUp = producer.ProjectShelfTests.setUp
    tearDown = producer.ProjectShelfTests.tearDown
    write_config = producer.ProjectShelfTests.write_config
    seed = producer.ProjectShelfTests.seed
    work = producer.ProjectShelfTests.work

    def observe(self, project_id):
        return observation.observe(self.root, project_id, now=self.now + 5)

    def run_record(self, project, goal, *, key=1, action="research", status="completed", started=None, ended=None):
        run_id = f"{key:032x}"
        start = self.now - 60 if started is None else started
        end = self.now if ended is None else ended
        self.store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", (run_id, SCOPE, start, end, status, "PRIVATE-RESPONSE"))
        self.store.event(SCOPE, "life_decision", {"run_id": run_id, "action": action, "goal_id": goal["id"],
            "project_id": project["id"] if action == "project_review" else None,
            "project_stage": 1, "reason": "PRIVATE-REASON"})
        return run_id

    def test_missing_paths_and_invalid_configuration_never_initialize(self):
        absent = self.root / "absent"
        self.assertEqual(observation.observe(absent, "a"*32)["state"], "missing_control")
        self.assertFalse(absent.exists())
        for body in ('{PRIVATE-BROKEN-CONFIG', '[]', '{"owner_scope":"qq:GroupMessage:12345"}',
                     json.dumps({"owner_scope": SCOPE, "owner_id": "99999"}), " "*65537):
            with self.subTest(body=body[:40]):
                (self.root / "control.json").write_text(body, encoding="utf-8")
                self.assertEqual(self.observe("a"*32)["state"], "invalid_control")

    def test_legacy_missing_and_future_schema_are_not_silent_success(self):
        self.assertEqual(self.observe("a"*32)["state"], "legacy")
        project, *_ = self.seed()
        self.store.db.execute("UPDATE project_meta SET value='99'")
        self.assertEqual(self.observe(project["id"])["state"], "unsupported")
        self.store.db.execute("DROP TABLE project_reviews")
        self.assertEqual(self.observe(project["id"])["state"], "unsupported")
        separate = self.root / "empty"
        separate.mkdir()
        (separate / "control.json").write_text(json.dumps(self.cfg), encoding="utf-8")
        self.assertEqual(observation.observe(separate, "a"*32)["state"], "missing_records")
        self.assertEqual([p.name for p in separate.iterdir()], ["control.json"])

    def test_metadata_only_export_and_same_scope_work_memory(self):
        project, goal, source, work = self.seed()
        run_id = self.run_record(project, goal, action="project_review")
        checked = self.store.life.record_episode(SCOPE, kind="work_checked", source_id="check", goal_id=goal["id"], run_id=run_id,
                                                evidence={"checks_passed": True, "work_id": work["id"]})
        self.store.queue_memory(SCOPE, "SECRET-MEMORY-BODY", "episode:"+checked["id"])
        self.store.db.execute("UPDATE memories SET status='saved'")
        value = self.observe(project["id"])
        self.assertEqual(value["state"], "recorded")
        self.assertEqual(value["stages"][0]["work"]["id"], work["id"])
        self.assertEqual(value["stages"][0]["review"]["revision"], 1)
        self.assertEqual(value["runs"][0]["id"], run_id)
        self.assertEqual(value["runs"][0]["action"], "project_review")
        self.assertEqual(value["work_memory_queue"][0]["status"], "saved")
        self.assertFalse(value["live_verified"])
        self.assertEqual(set(value["external_checks"].values()), {"UNVERIFIED"})
        self.assertEqual(value["source_validation"], "not_rechecked")
        encoded = json.dumps(value) + observation.markdown(value)
        for hidden in (SCOPE, "TOKEN-MUST-NOT-LEAVE", "PRIVATE-RESPONSE", "PRIVATE-REASON",
                       "SECRET-MEMORY-BODY", source["id"], work["body_hash"], str(self.root), "实际模拟内容"):
            self.assertNotIn(hidden, encoded)
        self.store.db.execute("UPDATE memories SET scope=?", (producer.OTHER,))
        self.assertEqual(self.observe(project["id"])["work_memory_queue"], [])

    def test_foreign_project_is_indistinguishable_from_absent(self):
        foreign, *_ = self.seed(scope=producer.OTHER)
        self.assertEqual(self.observe(foreign["id"]), self.observe("f"*32))
        self.assertEqual(self.observe(foreign["id"])["state"], "not_found")

    def test_exploration_before_goal_creation_uses_later_episode_link(self):
        project, goal, *_ = self.seed()
        run_id = self.run_record(project, goal, action="explore")
        self.store.db.execute("UPDATE events SET details=? WHERE kind='life_decision'",
            (json.dumps({"run_id": run_id, "action": "explore", "goal_id": None}),))
        self.store.life.record_episode(SCOPE, kind="read", source_id="after-choice", goal_id=goal["id"],
                                      run_id=run_id, evidence={"operation": "info"})
        report = self.observe(project["id"])
        self.assertEqual(report["runs"][0]["action"], "explore")

    def test_readonly_snapshot_does_not_recover_running_or_retry_memory(self):
        project, goal, *_ = self.seed()
        self.run_record(project, goal, status="running")
        self.store.db.execute("UPDATE runs SET ended=NULL")
        checked = self.store.life.record_episode(SCOPE, kind="work_checked", source_id="check", goal_id=goal["id"], evidence={"checks_passed": True})
        self.store.queue_memory(SCOPE, "unknown outcome", "episode:"+checked["id"])
        self.store.db.execute("UPDATE memories SET status='sending'")
        self.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        files = [self.root / "control.json", self.root / "activity.sqlite3"]
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        before = list(self.store.db.iterdump())
        # Poison cookie location: attempting to open it as a file would fail.
        (self.root / "cookies.json").mkdir()
        connect = sqlite3.connect
        connections = []

        def readonly_connection(database, **kwargs):
            self.assertTrue(database.endswith("?mode=ro"))
            db = connect(database, **kwargs)
            connections.append(db)
            def authorize(action, arg1, arg2, *_):
                self.assertNotIn(action, (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                                          sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE))
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            return db

        with patch.object(observation.sqlite3, "connect", side_effect=readonly_connection):
            value = self.observe(project["id"])
            self.assertEqual(self.observe(project["id"]), value)
        self.assertEqual(len(connections), 2)
        self.assertEqual(value["runs"][0]["status"], "running")
        self.assertEqual(value["work_memory_queue"][0]["status"], "sending")
        self.assertEqual(before, list(self.store.db.iterdump()))
        self.assertEqual(hashes, [hashlib.sha256(p.read_bytes()).hexdigest() for p in files])

    def test_optional_missing_tables_are_visible_gaps(self):
        project, *_ = self.seed()
        for table in ("events", "runs", "memories", "episodes", "creative_works"):
            self.store.db.execute("DROP TABLE " + table)
        value = self.observe(project["id"])
        self.assertEqual(value["state"], "recorded")
        self.assertIn("memories", value["coverage"]["missing_tables"])
        self.assertEqual(value["runs"], [])
        self.assertIsNone(value["stages"][0]["work"])
        self.assertEqual(value["work_memory_queue"], [])
        self.assertIn("未读到对应记录", observation.markdown(value))

    def test_unreadable_database_and_columns_are_sanitized(self):
        project, *_ = self.seed()
        self.store.db.execute("ALTER TABLE project_reviews RENAME COLUMN revision TO private_column")
        self.assertEqual(self.observe(project["id"])["state"], "unavailable")
        separate = self.root / "broken"
        separate.mkdir()
        (separate / "control.json").write_text(json.dumps(self.cfg), encoding="utf-8")
        (separate / "activity.sqlite3").write_text("PRIVATE-DB-CONTENT")
        result = observation.observe(separate, project["id"])
        self.assertEqual(result["state"], "unavailable")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_invalid_ids_are_refused_before_any_file_read(self):
        for bad in ("../control.json", "' OR 1=1 --", "A"*32, "", None):
            with self.subTest(bad=bad), patch.object(Path, "open", side_effect=AssertionError("must not read")), self.assertRaises(ValueError):
                observation.observe(self.root, bad)

    def test_malformed_decisions_are_skipped_without_body_export(self):
        project, goal, *_ = self.seed()
        self.run_record(project, goal)
        for raw in ('{PRIVATE', '[]', json.dumps({"private": "X"*5000}),
                    json.dumps({"goal_id": [], "action": {"private": "value"}})):
            self.store.db.execute("INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)", (self.now, SCOPE, "life_decision", raw))
        value = self.observe(project["id"])
        self.assertEqual(len(value["runs"]), 1)
        self.assertEqual(value["coverage"]["unreadable_decisions"], 3)
        self.assertNotIn("PRIVATE", json.dumps(value))

    def test_bounds_report_truncation_instead_of_claiming_complete_history(self):
        project, goal, *_ = self.seed()
        for key in range(1, 56):
            self.run_record(project, goal, key=key)
        for _ in range(501):
            self.store.event(SCOPE, "life_decision", {"run_id": f"{1:032x}", "project_id": project["id"], "action": "project_review"})
        self.assertTrue(self.observe(project["id"])["coverage"]["events_truncated"])
        self.store.db.execute("DELETE FROM events WHERE seq>55")
        value = self.observe(project["id"])
        self.assertEqual(len(value["runs"]), 50)
        self.assertTrue(value["coverage"]["runs_truncated"])

    def test_invalid_times_never_count_as_observed_dates(self):
        project, goal, *_ = self.seed()
        self.run_record(project, goal, key=1, started=self.now+600, ended=self.now+900)
        self.run_record(project, goal, key=2, started=self.now-10, ended=self.now-60)
        value = self.observe(project["id"])
        self.assertEqual(value["recorded_run_dates"], [])
        self.assertTrue(all(r["timestamps_valid"] is False for r in value["runs"]))

    def test_versions_and_unknown_queue_remain_records_not_acceptance(self):
        project, goal, source, work = self.seed()
        self.store.db.execute("UPDATE creative_works SET revision=2")
        self.store.db.execute("UPDATE project_reviews SET revision=1")
        for key, state in enumerate(("failed", "uncertain", "pending")):
            episode = self.store.life.record_episode(SCOPE, kind="work_checked", source_id=str(key), goal_id=goal["id"], evidence={"checks_passed": True})
            self.store.queue_memory(SCOPE, "private", "episode:"+episode["id"])
            self.store.db.execute("UPDATE memories SET status=? WHERE id=?", (state, "episode:"+episode["id"]))
        value = self.observe(project["id"])
        self.assertEqual(value["stages"][0]["work"]["revision"], 2)
        self.assertEqual(value["stages"][0]["review"]["revision"], 1)
        self.assertEqual({r["status"] for r in value["work_memory_queue"]}, {"failed", "uncertain", "pending"})
        self.assertFalse(value["live_verified"])

    def test_cli_requires_explicit_path_and_project_and_never_claims_pass(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            observation.main([])
        self.assertEqual(exc.exception.code, 2)
        project, *_ = self.seed()
        for fmt in ("markdown", "json"):
            output = io.StringIO()
            with redirect_stdout(output):
                code = observation.main(["--data-dir", str(self.root), "--project-id", project["id"], "--format", fmt])
            self.assertEqual(code, 0)  # Read succeeded; not a live acceptance result.
            self.assertIn("UNVERIFIED", output.getvalue())
            self.assertNotIn("PASS", output.getvalue())
        with redirect_stdout(io.StringIO()):
            code = observation.main(["--data-dir", str(self.root), "--project-id", "f"*32])
        self.assertEqual(code, 2)

    def test_numeric_metadata_cannot_leak_arbitrary_database_text(self):
        project, *_ = self.seed()
        self.store.db.execute("UPDATE life_projects SET max_stages='PRIVATE-URL'")
        self.store.db.execute("UPDATE creative_works SET revision='PRIVATE-URL'")
        self.store.db.execute("UPDATE project_reviews SET revision='PRIVATE-URL'")
        report = self.observe(project["id"])
        self.assertIsNone(report["project"]["max_stages"])
        self.assertIsNone(report["stages"][0]["work"]["revision"])
        self.assertIsNone(report["stages"][0]["review"]["revision"])
        self.assertNotIn("PRIVATE-URL", json.dumps(report) + observation.markdown(report))

    def test_reserved_stage_and_missing_goal_do_not_claim_completion(self):
        project, goal, *_ = self.seed()
        self.store.db.execute("INSERT INTO project_stages VALUES(?,?,?,?,?,?,?,?,?)",
            (project["id"], SCOPE, 2, None, "pending", "pending", "pending", "pending", self.now))
        report = self.observe(project["id"])
        self.assertEqual(report["stages"][1]["goal_status"], "pending_creation")
        self.assertIsNone(report["stages"][1]["work"])
        self.store.db.execute("UPDATE goals SET scope=? WHERE id=?", (producer.OTHER, goal["id"]))
        self.assertEqual(self.observe(project["id"])["stages"][0]["goal_status"], "unknown")


if __name__ == "__main__":
    unittest.main()

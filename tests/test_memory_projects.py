"""Read-only room project projection against the real producer's temporary schema."""
import hashlib
import importlib
import json
import unittest

import test_memory_room as room

projects = importlib.import_module(room.PACKAGE + ".projects")
Workshop = importlib.import_module(room.PACKAGE + ".workshop").Workshop
SCOPE = room.SCOPE
OTHER = "qq:FriendMessage:98765"


class ProjectShelfTests(unittest.TestCase):
    setUp = room.ReaderTests.setUp
    tearDown = room.ReaderTests.tearDown
    write_config = room.ReaderTests.write_config
    read = room.ReaderTests.read

    def seed(self, key="first", scope=SCOPE):
        self.cfg.update(projects.DEFAULTS | {"life_projects_enabled": True, "workshop_enabled": True})
        self.write_config()
        goal = self.store.life.create_goal(scope, "跨日主题"+key, "想慢慢查清", "查实际来源", key)
        project = projects.Projects(self.store).create(scope, goal["id"], "项目"+key, "记录自己的疑问", self.cfg)
        source, work = self.work(goal, key)
        self.store.db.execute("UPDATE goals SET updated_at=? WHERE id=?", (self.now-90000, goal["id"]))
        projects.Projects(self.store).review(scope, project["id"], 1, "收获", "局限", "新问题", self.cfg)
        return project, goal, source, work

    def work(self, goal, key):
        source = self.store.life.record_episode(goal["scope"], kind="read", source_id=key, goal_id=goal["id"],
            evidence={"operation": "info", "result_excerpt": '{"description":"实际模拟内容"}'})
        workshop = Workshop(self.store)
        work = workshop.create(goal["scope"], goal["id"], "阶段作品"+key, key)
        body = f"## 资料观察\n模拟观察。[E:{source['id']}]\n## 我的想法\n比较资料。\n## 待核实\n需要独立来源。"
        work = workshop.revise(goal["scope"], work["id"], body, [source["id"]], 0, key)
        self.store.life.update_goal(goal["scope"], goal["id"], status="completed")
        return source, work

    def head(self):
        return self.read()["projects"]["items"][0]["stages"][0]

    def test_old_store_is_empty_and_never_migrated(self):
        before = list(self.store.db.iterdump())
        result = self.read()["projects"]
        self.assertEqual(result, {"state":"legacy", "enabled":False,"items":[],"total":0,"truncated":False})
        self.assertEqual(before, list(self.store.db.iterdump()))

    def test_real_review_projection_is_bounded_readonly_and_no_private_metadata(self):
        project, goal, source, work = self.seed()
        self.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        files = [self.root / "control.json", self.root / "activity.sqlite3"]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        data = self.read()
        stage = data["projects"]["items"][0]["stages"][0]
        self.assertEqual(stage["work"]["revision"], work["revision"])
        self.assertIn("模拟观察", stage["work"]["excerpt"])
        self.assertTrue(stage["work"]["sources_available"])
        self.assertEqual(stage["review_state"], "recorded")
        self.assertEqual(stage["review"]["reflection"], "收获")
        for private in (SCOPE, "TOKEN-MUST-NOT-LEAVE", work["body_hash"], str(self.root), "sources_json", "goal_source"):
            self.assertNotIn(private, json.dumps(data, ensure_ascii=False))
        self.assertEqual(data, self.read())
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in files])

    def test_foreign_projects_and_corrupt_cross_scope_goal_do_not_leak(self):
        own, goal, source, work = self.seed()
        other, foreign, _, _ = self.seed("FOREIGN-SECRET", OTHER)
        self.assertNotIn("FOREIGN-SECRET", json.dumps(self.read()))
        self.store.db.execute("UPDATE project_stages SET goal_id=? WHERE project_id=?", (foreign["id"], own["id"]))
        stage = self.head()
        self.assertEqual(stage["goal_status"], "unavailable")
        self.assertIsNone(stage["work"])
        self.assertIsNone(stage["review"])
        self.assertNotIn("FOREIGN-SECRET", json.dumps(self.read()))

    def test_cross_work_review_is_hidden(self):
        project, goal, source, work = self.seed()
        self.store.db.execute("UPDATE project_reviews SET work_id='foreign',reflection='PRIVATE-WRONG-WORK'")
        self.assertEqual(self.head()["review_state"], "unavailable")
        self.assertIsNone(self.head()["review"])
        self.assertNotIn("PRIVATE-WRONG-WORK", json.dumps(self.read()))

    def test_source_correction_marks_historical_review_for_recheck(self):
        project, goal, source, work = self.seed()
        self.store.life.record_episode(SCOPE, kind="read", source_id="correction", goal_id=goal["id"],
            supersedes_id=source["id"], evidence={"operation":"info", "result_excerpt":"new actual data"})
        stage = self.head()
        self.assertFalse(stage["work"]["sources_available"])
        self.assertEqual(stage["review_state"], "needs_recheck")
        self.assertEqual(stage["review"]["reflection"], "收获")

    def test_missing_or_foreign_source_is_not_available(self):
        project, goal, source, work = self.seed()
        self.store.db.execute("UPDATE episodes SET scope=? WHERE id=?", (OTHER, source["id"]))
        self.assertFalse(self.head()["work"]["sources_available"])
        self.assertEqual(self.head()["review_state"], "needs_recheck")

    def test_revision_or_hash_mismatch_does_not_claim_current_review(self):
        self.seed()
        self.store.db.execute("UPDATE project_reviews SET body_hash='old-hash'")
        self.assertEqual(self.head()["review_state"], "needs_recheck")
        self.store.db.execute("UPDATE project_reviews SET revision=999")
        self.assertEqual(self.head()["review"]["revision"], 999)
        self.assertEqual(self.head()["work"]["revision"], 1)

    def test_malformed_sources_are_not_success_and_omitted(self):
        self.seed()
        for value in ('{"secret":"DO-NOT-EXPOSE"}', '["invalid-id"]', 'null'):
            with self.subTest(value=value):
                self.store.db.execute("UPDATE creative_revisions SET source_ids_json=?", (value,))
                stage = self.head()
                self.assertFalse(stage["work"]["sources_available"])
                self.assertIsNone(stage["work"]["source_count"])
                self.assertEqual(stage["review_state"], "needs_recheck")
                self.assertNotIn("DO-NOT-EXPOSE", json.dumps(self.read()))

    def test_unsupported_partial_and_wrong_columns_keep_existing_room(self):
        self.seed()
        self.store.db.execute("UPDATE project_meta SET value='99'")
        self.assertEqual(self.read()["projects"]["state"], "unsupported")
        self.assertTrue(self.read()["works"])
        self.store.db.execute("UPDATE project_meta SET value='1'")
        self.store.db.execute("ALTER TABLE project_reviews RENAME COLUMN reflection TO unknown_column")
        self.assertEqual(self.read()["projects"]["state"], "unsupported")
        self.store.db.execute("DROP TABLE project_reviews")
        self.assertEqual(self.read()["projects"]["state"], "unsupported")

    def test_disabled_projects_keep_history_without_resuming(self):
        self.seed()
        self.cfg["life_projects_enabled"] = False
        self.write_config()
        before = list(self.store.db.iterdump())
        result = self.read()["projects"]
        self.assertFalse(result["enabled"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(before, list(self.store.db.iterdump()))

    def test_reservation_is_pending_not_completed_and_no_work_is_invented(self):
        project, goal, source, work = self.seed()
        self.store.db.execute("INSERT INTO project_stages VALUES(?,?,?,?,?,?,?,?,?)",
            (project["id"], SCOPE, 2, None, "pending-key", "下一问题", "原因", "下一步", self.now))
        stage = self.read()["projects"]["items"][0]["stages"][1]
        self.assertEqual(stage["goal_status"], "pending_creation")
        self.assertIsNone(stage["work"])
        self.assertIsNone(stage["review"])

    def test_review_decision_maps_to_shelf_without_inferring_completion(self):
        self.seed()
        room.ReaderTests.decision(self, action="project_review", status="completed")
        result = self.read()
        self.assertEqual(result["zone"], "shelf")
        self.assertEqual(result["decisions"][0]["action"], "project_review")
        self.assertEqual(result["projects"]["items"][0]["status"], "active")

    def test_snapshot_caps_text_projects_stages_and_ssh_payload(self):
        for i in range(5):
            project, goal, source, work = self.seed(str(i))
            for j in range(2, 7):
                key = f"{i}-{j}"
                g = self.store.life.create_goal(SCOPE, key, "why", "next", key)
                self.work(g, key)
                self.store.db.execute("INSERT INTO project_stages VALUES(?,?,?,?,?,?,?,?,?)",
                    (project["id"], SCOPE, j, g["id"], key, "章"*400, "why", "next", self.now))
            self.store.db.execute("UPDATE life_projects SET status='completed',title=?,why=? WHERE id=?", ("题"*500,"因"*2000,project["id"]))
        self.store.db.execute("UPDATE creative_revisions SET body=?", ("文"*8000,))
        self.store.db.execute("UPDATE project_reviews SET reflection=?,limitations=?", ("想"*1200,"疑"*1000))
        result = self.read()
        shelf = result["projects"]
        self.assertEqual(len(shelf["items"]), 4)
        self.assertEqual(shelf["total"], 5)
        self.assertTrue(shelf["truncated"])
        stage = shelf["items"][0]["stages"][0]
        self.assertEqual(len(stage["work"]["excerpt"]), 600)
        self.assertTrue(stage["work"]["excerpt_truncated"])
        self.assertEqual(len(stage["review"]["reflection"]), 360)
        self.assertTrue(stage["review"]["truncated"])
        self.assertLess(len(json.dumps({"ok":True,"snapshot":result},ensure_ascii=False).encode()),256*1024)


if __name__ == "__main__":
    unittest.main()

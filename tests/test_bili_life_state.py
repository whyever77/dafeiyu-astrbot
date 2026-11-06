"""Durable goal/evidence regressions, independent of QQ, models and Bilibili."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


PLUGIN = Path(__file__).resolve().parents[1] / "plugin/astrbot_plugin_bili_activity"
package = types.ModuleType("bili_life_state_test")
package.__path__ = [str(PLUGIN)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__ + ".core", PLUGIN / "core.py")
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)
SCOPE, OTHER = "qq:FriendMessage:12345", "qq:GroupMessage:12345"


class LifeStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.store = core.Store(self.folder)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @property
    def life(self):
        return self.store.life

    def goal(self, source="first", scope=SCOPE):
        return self.life.create_goal(scope, "研究建造游戏", "想了解协作机制", "找实机演示", source)

    def episode(self, source="video-1", goal_id=None, run_id=None, **kwargs):
        return self.life.record_episode(
            SCOPE, kind="video", source_id=source,
            evidence={"bvid": "BV1", "cid": 100, "range": [0, 20]},
            interpretation={"thought": "合作搭房子有趣", "interest": {"topic": "建造游戏", "polarity": 1}},
            goal_id=goal_id, run_id=run_id, **kwargs,
        )

    def reopen(self):
        self.store.close()
        self.store = core.Store(self.folder)

    def test_fresh_schema_is_versioned_and_reopen_does_not_repeat_backup(self):
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertIsNone(self.store.schema_backup)
        goal = self.goal()
        self.reopen()
        self.assertEqual(self.life.get_goal(SCOPE, goal["id"])["why"], goal["why"])
        self.assertFalse((self.folder / "schema_backups").exists())

    def test_v0_snapshot_contains_committed_wal_before_recovery_changes(self):
        self.store.close()
        path = self.folder / "activity.sqlite3"
        with closing(sqlite3.connect(path, isolation_level=None)) as writer:
            writer.executescript("DROP TABLE episodes; DROP TABLE goals; PRAGMA user_version=0;")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO settings VALUES(?,?)", ("last_once", '"already-fired"'))
            writer.execute("INSERT INTO runs VALUES(?,?,?,?,?,?)", ("old-run", SCOPE, 1, None, "running", None))
            self.assertGreater(Path(str(path) + "-wal").stat().st_size, 0)
            control = self.folder / "control.json"
            control.write_text('{"enabled":false,"keep":"my value"}')
            self.store = core.Store(self.folder)
            backup_path = self.store.schema_backup
            self.assertIsNotNone(backup_path)
            with closing(sqlite3.connect(backup_path)) as backup:
                self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(backup.execute("SELECT value FROM settings WHERE key='last_once'").fetchone()[0], '"already-fired"')
                self.assertEqual(backup.execute("SELECT status FROM runs WHERE id='old-run'").fetchone()[0], "running")
                self.assertIsNone(backup.execute("SELECT name FROM sqlite_master WHERE name='goals'").fetchone())
            self.assertEqual(self.store.db.execute("SELECT status FROM runs WHERE id='old-run'").fetchone()[0], "interrupted")
            self.assertEqual(control.read_text(), '{"enabled":false,"keep":"my value"}')
        self.reopen()
        self.assertEqual(len(list((self.folder / "schema_backups").glob("*.sqlite3"))), 1)

    def test_future_schema_rejected_before_mutation_and_connection_closed(self):
        self.store.close()
        path = self.folder / "future"
        path.mkdir()
        connection = sqlite3.connect(path / "activity.sqlite3", isolation_level=None)
        connection.execute("PRAGMA user_version=99")
        with patch.object(core.sqlite3, "connect", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "newer"):
                core.Store(path)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with closing(sqlite3.connect(path / "activity.sqlite3")) as reader:
            self.assertEqual(reader.execute("PRAGMA user_version").fetchone()[0], 99)
            self.assertEqual(reader.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0], 0)
        self.assertFalse((path / "schema_backups").exists())

    def test_restart_pauses_running_goal_retaining_reason_and_checkpoint(self):
        goal = self.goal()
        self.life.claim_goal(SCOPE, goal["id"], "run-1")
        episode = self.episode(goal_id=goal["id"], run_id="run-1")
        checkpoint = {"bvid": "BV1", "cid": 100, "analyzed_range": [0, 20], "episode_id": episode["id"]}
        self.life.update_goal(SCOPE, goal["id"], run_id="run-1", checkpoint=checkpoint, next_step="继续核对合作模式")
        self.reopen()
        recovered = self.life.get_goal(SCOPE, goal["id"])
        self.assertEqual((recovered["status"], recovered["run_id"]), ("paused", None))
        self.assertEqual(recovered["checkpoint"], checkpoint)
        self.assertEqual(recovered["why"], "想了解协作机制")
        self.assertEqual(recovered["next_step"], "继续核对合作模式")
        self.assertEqual(self.life.claim_goal(SCOPE, goal["id"], "run-2")["run_id"], "run-2")

    def test_scope_and_run_ownership_are_enforced(self):
        one, two = self.goal(), self.goal("second")
        self.assertIsNone(self.life.get_goal(OTHER, one["id"]))
        self.assertEqual(self.life.goals(OTHER), [])
        self.life.claim_goal(SCOPE, one["id"], "r1")
        for action in (
            lambda: self.life.claim_goal(OTHER, one["id"], "r1"),
            lambda: self.life.claim_goal(SCOPE, two["id"], "r2"),
            lambda: self.life.update_goal(SCOPE, one["id"], next_step="other run", run_id="r2"),
            lambda: self.life.update_goal(SCOPE, one["id"], next_step="no lease"),
            lambda: self.episode(goal_id=one["id"], run_id="r2"),
        ):
            with self.assertRaises(ValueError):
                action()
        self.life.pause_run(OTHER, "r1", "unrelated")
        self.assertEqual(self.life.get_goal(SCOPE, one["id"])["status"], "running")
        self.life.pause_run(SCOPE, "r1", "foreground needs phone")
        self.assertEqual(self.life.claim_goal(SCOPE, two["id"], "r2")["status"], "running")

    def test_waiting_and_blocked_goals_are_not_automatically_resumed(self):
        goal = self.goal()
        self.life.claim_goal(SCOPE, goal["id"], "r1")
        self.life.update_goal(SCOPE, goal["id"], status="waiting_approval", run_id="r1")
        self.reopen()
        with self.assertRaises(ValueError):
            self.life.claim_goal(SCOPE, goal["id"], "r2")
        self.life.update_goal(SCOPE, goal["id"], status="blocked")
        with self.assertRaises(ValueError):
            self.life.claim_goal(SCOPE, goal["id"], "r2")
        self.life.update_goal(SCOPE, goal["id"], status="paused")
        self.assertEqual(self.life.claim_goal(SCOPE, goal["id"], "r2")["status"], "running")

    def test_completed_goal_requires_evidence_and_is_immutable(self):
        goal = self.goal()
        self.life.claim_goal(SCOPE, goal["id"], "r1")
        with self.assertRaises(ValueError):
            self.life.update_goal(SCOPE, goal["id"], status="completed", run_id="r1")
        self.life.record_episode(SCOPE, kind="run_end", source_id="failed-run", evidence={"status": "failed"},
                                 goal_id=goal["id"], run_id="r1", subject_kind="system")
        with self.assertRaises(ValueError):
            self.life.update_goal(SCOPE, goal["id"], status="completed", run_id="r1")
        self.life.record_episode(SCOPE, kind="video", source_id="observed", evidence={
            "bvid": "BVexample", "cid": "1", "subtitles": [{"from": 0, "to": 5, "content": "真实读取"}]},
            goal_id=goal["id"], run_id="r1")
        self.life.update_goal(SCOPE, goal["id"], status="completed", run_id="r1", next_step="")
        self.assertEqual(self.life.goals(SCOPE), [])
        self.assertEqual(len(self.life.goals(SCOPE, include_terminal=True)), 1)
        with self.assertRaises(ValueError):
            self.life.update_goal(SCOPE, goal["id"], why="rewrite history")
        with self.assertRaises(ValueError):
            self.life.claim_goal(SCOPE, goal["id"], "r2")

    def test_paused_goal_completion_still_requires_observed_evidence(self):
        goal = self.goal()
        self.life.update_goal(SCOPE, goal["id"], status="paused")
        with self.assertRaises(ValueError):
            self.life.update_goal(SCOPE, goal["id"], status="completed")
        self.life.record_episode(SCOPE, kind="video", source_id="paused-observed", evidence={
            "bvid": "BVexample", "cid": "1", "observations": [{"at": 2, "text": "真实画面"}]},
            goal_id=goal["id"])
        completed = self.life.update_goal(SCOPE, goal["id"], status="completed")
        self.assertEqual(completed["status"], "completed")

    def test_failed_read_evidence_cannot_complete_goal_but_truncated_success_can(self):
        failures = (
            {"operation": "home", "result_excerpt": '{"ok":false,"error":"TimeoutError"}'},
            {"operation": "home", "result_excerpt": '{"items":[1]}', "ok": False},
        )
        for index, evidence in enumerate(failures):
            with self.subTest(evidence=evidence):
                goal = self.goal(f"failed-read-{index}")
                self.life.record_episode(SCOPE, kind="read", source_id=f"failed-read-{index}",
                                         evidence=evidence, goal_id=goal["id"])
                with self.assertRaises(ValueError):
                    self.life.update_goal(SCOPE, goal["id"], status="completed")

        goal = self.goal("truncated-read")
        self.life.record_episode(SCOPE, kind="read", source_id="truncated-read", goal_id=goal["id"],
                                 evidence={"operation": "comments", "result_excerpt": '{"items":[{"id":1}'})
        self.assertEqual(self.life.update_goal(SCOPE, goal["id"], status="completed")["status"], "completed")

    def test_duplicate_goal_source_is_idempotent_and_open_goals_are_bounded(self):
        first = self.goal()
        duplicate = self.life.create_goal(SCOPE, "changed", "changed", "changed", "first")
        self.assertEqual(duplicate, first)
        for index in range(7):
            self.goal(str(index))
        with self.assertRaises(ValueError):
            self.goal("over-limit")
        self.life.update_goal(SCOPE, first["id"], status="cancelled")
        for index in range(10):
            done = self.goal(f"done-{index}")
            self.life.update_goal(SCOPE, done["id"], status="cancelled")
        self.goal("replacement")
        listed = self.life.goals(SCOPE, limit=8, include_terminal=True)
        self.assertEqual(len(listed), 8)
        self.assertTrue(all(g["status"] not in {"completed", "cancelled"} for g in listed))

    def test_old_episodes_survive_latest_video_cache_replacements(self):
        first = self.episode()
        for index in range(20):
            self.life.record_episode(SCOPE, kind="video", source_id=str(index), evidence={"title": "other"})
            self.store.video(SCOPE, {"bvid": "BV1", "cid": 100, "position": index})
        found = self.life.episodes(SCOPE, query="建造")
        self.assertEqual([row["id"] for row in found], [first["id"]])
        self.assertEqual(self.life.episodes(OTHER), [])
        self.assertEqual(len(self.life.episodes(SCOPE, limit=999)), 21)
        self.assertEqual(self.life.episodes(SCOPE, query="%"), [])

    def test_multi_term_episode_query_matches_any_term_without_wildcards(self):
        first = self.episode()
        second = self.life.record_episode(
            SCOPE, kind="video", source_id="video-2",
            evidence={"bvid": "BV2", "cid": 200, "range": [0, 20], "title": "势赵云伪装"},
            interpretation={"thought": "三国杀对局没看懂"})
        for query in ("建造，势赵云", "建造势赵云", "建造三国杀"):
            with self.subTest(query=query):
                found = self.life.episodes(SCOPE, query=query)
                self.assertEqual({row["id"] for row in found}, {first["id"], second["id"]})
        self.assertEqual(self.life.episodes(SCOPE, query="不存在的词"), [])
        self.assertEqual(self.life.episodes(SCOPE, query="%"), [])

    def test_episode_dedup_preserves_original_evidence_and_correction_is_append_only(self):
        original = self.episode()
        repeated = self.life.record_episode(SCOPE, kind="video", source_id="video-1", evidence={"false": "rewrite"})
        self.assertEqual(repeated, original)
        correction = self.life.record_episode(
            SCOPE, kind="correction", source_id="correction-1", supersedes_id=original["id"],
            evidence={"bvid": "BV1", "cid": 100, "range": [0, 20]},
            interpretation={"thought": "其实这是单人建造", "interest": {"topic": "建造游戏", "polarity": -1}},
        )
        self.assertEqual([row["id"] for row in self.life.episodes(SCOPE)], [correction["id"]])
        saved = self.store.db.execute("SELECT evidence_json FROM episodes WHERE id=?", (original["id"],)).fetchone()[0]
        self.assertEqual(json.loads(saved), original["evidence"])
        with self.assertRaises(ValueError):
            self.episode("another-correction", supersedes_id=original["id"])
        with self.assertRaises(ValueError):
            self.life.record_episode(OTHER, kind="correction", source_id="wrong-scope", supersedes_id=correction["id"], evidence={"text": "wrong"})

    def test_repeated_video_does_not_inflate_interest_evidence(self):
        self.episode("watch-1")
        self.episode("watch-2")
        values = self.life.interests(SCOPE)
        self.assertEqual(values[0]["evidence_count"], 1)
        self.assertEqual(values[0]["strength"], "tentative")
        self.life.record_episode(SCOPE, kind="video", source_id="independent", evidence={"bvid": "BV2", "cid": 200},
                                 interpretation={"interest": {"topic": "建造游戏", "polarity": 1}})
        self.assertEqual(self.life.interests(SCOPE)[0]["evidence_count"], 2)
        self.assertEqual(self.life.interests(SCOPE)[0]["strength"], "supported")
        self.assertEqual(self.life.interests(OTHER), [])
        self.assertEqual(self.life.interests(SCOPE, "user"), [])

    def test_only_authenticated_explicit_messages_become_user_preference(self):
        for message in ("可以", "可以收藏", "我喜欢建造游戏吗", "他说我喜欢游戏", "我喜欢的话再买", "引用：我喜欢建造游戏"):
            self.assertIsNone(self.life.record_user_preference(SCOPE, "12345", message, message))
        explicit = self.life.record_user_preference(SCOPE, "12345", "qq1", "我喜欢建造游戏。")
        self.assertEqual(explicit["subject_kind"], "user")
        self.assertEqual(explicit["evidence"]["message_id"], "qq1")
        self.assertEqual(self.life.record_user_preference(SCOPE, "12345", "qq1", "我喜欢建造游戏。")["id"], explicit["id"])
        self.assertEqual(self.life.interests(SCOPE, "user")[0]["topic"], "建造游戏")
        self.assertEqual(self.life.interests(SCOPE, "agent"), [])
        with self.assertRaises(ValueError):
            self.life.record_user_preference(SCOPE, "999", "bad", "我喜欢建造游戏")
        with self.assertRaises(ValueError):
            self.life.record_user_preference(OTHER, "12345", "bad", "我喜欢建造游戏")
        with self.assertRaises(ValueError):
            self.life.record_episode(SCOPE, kind="preference", source_id="model-claim", subject_kind="user", evidence={"text": "主人喜欢"})

    def test_nonpreference_chat_and_missing_receipts_do_not_break_event_handling(self):
        for event_id, message in ((None, "我喜欢游戏"), ("", "我喜欢游戏"), (True, "我喜欢游戏"),
                                  ("normal", ""), ("normal", "正文" * 2000)):
            self.assertIsNone(self.life.record_user_preference(SCOPE, "12345", event_id, message))
        self.assertEqual(self.life.episodes(SCOPE), [])

    def test_invalid_or_unbounded_evidence_is_rejected(self):
        for evidence in ({}, {"range": [20, 10]}, {"range": [0, float("nan")]},
                         {"range": [False, 20]}, {"range": None}, {"text": "x" * 25000}):
            with self.subTest(evidence=str(evidence)[:50]), self.assertRaises(ValueError):
                self.life.record_episode(SCOPE, kind="video", source_id="bad", evidence=evidence)
        self.assertEqual(self.life.episodes(SCOPE), [])

    def test_latest_explicit_user_stance_wins_without_erasing_history(self):
        life_module = sys.modules[package.__name__ + ".life"]
        with patch.object(life_module.time, "time", return_value=100):
            self.life.record_user_preference(SCOPE, "12345", "old", "我喜欢建造游戏")
        with patch.object(life_module.time, "time", return_value=200):
            self.life.record_user_preference(SCOPE, "12345", "new", "我不喜欢建造游戏")
            preference = self.life.interests(SCOPE, "user")[0]
        self.assertEqual(preference["score"], -1)
        self.assertEqual(preference["strength"], "explicit")
        self.assertEqual(preference["evidence_count"], 1)
        self.assertEqual(len(self.life.episodes(SCOPE)), 2)

    def test_known_disabled_write_releases_proposal_slot_without_replaying_consent(self):
        old, _ = self.store.propose(SCOPE, "12345", "follow", {"uid": 1})
        self.store.offered(old["id"], {"message_id": 1})
        self.store.decide(SCOPE, "12345", "可以", "yes-1")
        self.store.finish_proposal(old["id"], {"verified": False, "executed": False, "reason": "switch_disabled"})
        status = self.store.db.execute("SELECT status FROM proposals WHERE id=?", (old["id"],)).fetchone()[0]
        self.assertEqual(status, "blocked")
        new, fresh = self.store.propose(SCOPE, "12345", "follow", {"uid": 2})
        self.assertTrue(fresh)
        self.store.offered(new["id"], {"message_id": 2})
        self.assertIsNone(self.store.decide(SCOPE, "12345", "可以", "yes-1"))

    def test_missing_execution_proof_remains_uncertain(self):
        old, _ = self.store.propose(SCOPE, "12345", "follow", {"uid": 1})
        self.store.offered(old["id"], {"message_id": 1})
        self.store.decide(SCOPE, "12345", "可以", "yes")
        self.store.finish_proposal(old["id"], {"verified": False, "reason": "switch_disabled"})
        self.assertEqual(self.store.db.execute("SELECT status FROM proposals").fetchone()[0], "uncertain")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.propose(SCOPE, "12345", "follow", {"uid": 2})


if __name__ == "__main__":
    unittest.main(verbosity=2)

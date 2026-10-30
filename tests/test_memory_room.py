"""Room snapshots use real producer schemas and temporary state, never real accounts."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock

from deploy.memory_observatory.activity import SSHActivitySource, demo_room
from deploy.memory_observatory.activity_reader import ActivityError, read_activity
from deploy.memory_observatory.server import ObservatoryServer
import test_memory_observatory as existing

PACKAGE = "room_fixture_plugin"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / "plugin/astrbot_plugin_bili_activity")]
sys.modules[PACKAGE] = package
core = importlib.import_module(PACKAGE + ".core")
contacts = importlib.import_module(PACKAGE + ".contact")
SCOPE = "qq:FriendMessage:12345"


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = {"owner_scope": SCOPE, "owner_id": "12345", "enabled": True,
                    "life_enabled": True, "life_decision_enabled": True, "max_minutes": 10,
                    "secret": "TOKEN-MUST-NOT-LEAVE"}
        self.write_config()
        self.store = core.Store(self.root)
        self.now = time.time()
        self.store.db.execute("INSERT INTO goals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "goal", SCOPE, "self", "研究海里的光", "出于好奇", "source-1", "paused", "核对第二份资料", "{}", "[]", None, self.now-60, self.now-30, None))

    def write_config(self):
        (self.root / "control.json").write_text(json.dumps(self.cfg), encoding="utf-8")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def decision(self, action="research", status="running", *, scope=SCOPE, started=None, at=None):
        started = self.now-10 if started is None else started
        self.store.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?)", ("run-"+scope, scope, started, None if status=="running" else self.now, status, "RAW-RESPONSE-MUST-NOT-LEAVE"))
        self.store.event(scope, "life_decision" if action!="rest" else "life_rest", {
            "action": action, "run_id": "run-"+scope if action!="rest" else "no-run", "reason": "继续昨天的问题", "goal_id":"goal", "topic":"海洋", "secret":"TOKEN-MUST-NOT-LEAVE"})
        self.store.db.execute("UPDATE events SET time=? WHERE seq=last_insert_rowid()", (self.now-10 if at is None else at,))

    def read(self):
        return read_activity(self.root, now=self.now)

    def test_readonly_preserves_running_state_and_source_files(self):
        self.decision()
        self.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        paths = [self.root / n for n in ("control.json", "activity.sqlite3")]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        result = self.read()
        self.assertEqual(result["state"], "recorded_running")
        self.assertEqual(self.store.db.execute("SELECT status FROM runs").fetchone()[0], "running")
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])
        self.assertEqual(self.read(), result)

    def test_wal_commits_are_visible_without_store_recovery(self):
        self.decision("writing")
        self.assertEqual(self.read()["decisions"][0]["action"], "writing")
        self.assertEqual(self.store.db.execute("SELECT status FROM runs").fetchone()[0], "running")

    def test_stale_running_is_not_presented_as_live(self):
        self.decision(started=self.now-4000)
        self.assertEqual(self.read()["state"], "unconfirmed")

    def test_completed_run_does_not_complete_goal(self):
        self.decision(status="completed")
        result = self.read()
        self.assertEqual(result["state"], "ended")
        self.assertEqual(result["goals"][0]["status"], "paused")

    def test_rest_without_run_and_unstarted_plan_are_distinct(self):
        self.decision(action="rest")
        self.assertEqual(self.read()["state"], "rest")
        self.store.db.execute("DELETE FROM events")
        self.decision()
        self.store.db.execute("DELETE FROM runs")
        self.assertEqual(self.read()["state"], "planned")

    def test_switches_and_pause_override_old_activity(self):
        self.decision()
        self.cfg["enabled"] = False
        self.write_config()
        self.assertEqual(self.read()["state"], "disabled")
        self.cfg["enabled"] = True
        self.write_config()
        self.store.setting("life_paused:"+SCOPE, True)
        self.assertEqual(self.read()["state"], "paused")

    def test_other_scope_and_sensitive_metadata_never_leave_reader(self):
        self.decision()
        self.decision(scope="qq:GroupMessage:999")
        self.store.db.execute("UPDATE events SET details=? WHERE scope<>?", (json.dumps({"action":"writing","reason":"FOREIGN-PRIVATE","run_id":"other"}),SCOPE))
        result = json.dumps(self.read())
        for forbidden in ("FOREIGN-PRIVATE", "TOKEN-MUST-NOT-LEAVE", "RAW-RESPONSE-MUST-NOT-LEAVE", SCOPE, "cookies", str(self.root)):
            self.assertNotIn(forbidden, result)
        self.assertEqual(len(self.read()["decisions"]), 1)

    def test_optional_old_tables_are_not_created_and_records_are_bounded(self):
        for _ in range(17):
            self.decision()
        self.assertEqual(len(self.read()["decisions"]), 12)
        self.assertEqual(self.read()["contacts"], [])
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='contact_attempts'").fetchone())

    def test_contact_projection_has_no_body_hash_or_message_receipt(self):
        contacts.ContactLedger(self.store)
        self.store.db.execute("INSERT INTO contact_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            "secret-key",SCOPE,"life","thought","secret-fingerprint","2026-10-30",self.now-10,None,None,"uncertain","private-message-id"))
        result = self.read()
        self.assertEqual(result["contacts"][0]["status"], "uncertain")
        self.assertNotIn("private-message-id",json.dumps(result))
        self.assertNotIn("secret-fingerprint",json.dumps(result))

    def test_real_work_schema_is_scoped_and_pending_proposals_expose_no_payload(self):
        importlib.import_module(PACKAGE + ".workshop").Workshop(self.store)
        for scope, ident, title in ((SCOPE,"own","自己的作品"),("qq:FriendMessage:9","other","FOREIGN-WORK")):
            self.store.db.execute("INSERT INTO creative_works VALUES(?,?,?,?,?,?,?,?,?,?)", (
                ident,scope,"goal",title,"write","draft",2,ident,self.now-60,self.now))
        self.store.db.execute("INSERT INTO proposals(id,scope,actor,action,args,fingerprint,status,created,expires) VALUES(?,?,?,?,?,?,?,?,?)", (
            "proposal",SCOPE,"12345","comment",'{"message":"PRIVATE-PAYLOAD"}',"fingerprint","uncertain",self.now,self.now+60))
        result=self.read()
        self.assertEqual(result["works"][0]["title"],"自己的作品")
        self.assertEqual(result["works"][0]["revision"],2)
        self.assertEqual(result["pending"],1)
        self.assertNotIn("FOREIGN-WORK",json.dumps(result))
        self.assertNotIn("PRIVATE-PAYLOAD",json.dumps(result))

    def test_bad_config_missing_db_and_future_schema_fail_closed(self):
        self.cfg["owner_scope"] = "qq:GroupMessage:12345"
        self.write_config()
        with self.assertRaises(ActivityError): self.read()
        self.cfg["owner_scope"] = SCOPE
        self.write_config()
        self.store.db.execute("PRAGMA user_version=99")
        with self.assertRaises(ActivityError): self.read()
        with self.assertRaises(ActivityError): read_activity(self.root / "absent")
        self.assertFalse((self.root / "absent").exists())

    def test_invalid_or_future_decision_does_not_claim_current_activity(self):
        self.decision(at=self.now+9999)
        self.assertEqual(self.read()["state"], "idle")
        self.store.db.execute("UPDATE events SET details='broken private payload',time=?",(self.now,))
        with self.assertRaisesRegex(ActivityError, "无法读取活动账本") as error: self.read()
        self.assertNotIn("private",str(error.exception))

    def test_ssh_uses_fixed_stdin_reader_and_sanitizes_failures(self):
        run = Mock(return_value=types.SimpleNamespace(returncode=0,stdout=json.dumps({"ok":True,"snapshot":demo_room()})))
        source = SSHActivitySource(host="example.test",key=self.root/"fake-key",run=run)
        self.assertEqual(source.load()["schema"],"dafeiyu-room/v1")
        args,kwargs=run.call_args
        self.assertIn("StrictHostKeyChecking=yes",args[0])
        self.assertIn("mode=ro",kwargs["input"])
        self.assertNotIn("Store(",kwargs["input"])
        run.return_value=types.SimpleNamespace(returncode=1,stdout="TOKEN secret URL response")
        with self.assertRaises(ActivityError) as error: source.load()
        self.assertNotIn("TOKEN",str(error.exception))


class RoomHTTPTests(unittest.TestCase):
    request = existing.LocalServerTests.request

    def setUp(self):
        self.source = Mock()
        self.data = demo_room() | {"mode":"activity","connection":"ready","as_of":time.time()}
        self.source.load.return_value = copy.deepcopy(self.data)
        self.server = ObservatoryServer(("127.0.0.1",0),activity_source=self.source)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def test_assets_and_cached_views_do_not_refresh_or_change_memory(self):
        before=self.request("/api/snapshot")[2]
        for path in ("/room","/room.js","/room.css","/room-scene.js","/cozy.css",
                     "/character-credits","/character-license","/api/room"):
            status,headers,body=self.request(path)
            self.assertEqual(status,200)
            self.assertEqual(headers["Cache-Control"],"no-store")
        self.assertEqual(self.source.load.call_count,1)
        self.assertEqual(before,self.request("/api/snapshot")[2])

    def test_character_assets_are_explicit_and_do_not_expose_other_files(self):
        for name in ("idle", "walk", "desk", "shelf", "window", "rest"):
            status, headers, body = self.request(f"/character/{name}.png")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "image/png")
            self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack(">II", body[16:24]), (1786, 894), name)
        for path in ("/character/../server.py", "/character/cookies.json", "/character/unknown.png"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.source.load.call_count, 1)

    def test_refresh_failure_marks_old_snapshot_stale_and_recovery_is_explicit(self):
        self.source.load.side_effect=ActivityError("private failure")
        self.assertEqual(self.request("/api/room/refresh",{})[0],503)
        stale=json.loads(self.request("/api/room")[2])
        self.assertEqual(stale["connection"],"stale")
        self.assertEqual(stale["goals"],self.data["goals"])
        self.source.load.side_effect=None
        self.assertEqual(json.loads(self.request("/api/room/refresh",{})[2])["connection"],"ready")

    def test_source_failure_never_falls_back_to_fiction(self):
        with self.server.lock:
            self.server.activity_snapshot=None
            self.server.activity_state="unavailable"
        result=json.loads(self.request("/api/room")[2])
        self.assertEqual(result["mode"],"unavailable")
        self.assertEqual(result["goals"],[])

    def test_initial_activity_failure_keeps_memory_available_without_demo_room(self):
        broken=Mock()
        broken.load.side_effect=ActivityError("private diagnostic")
        with ObservatoryServer(("127.0.0.1",0),activity_source=broken) as server:
            self.assertTrue(server.view()["memories"])
            self.assertEqual(server.room_view()["mode"],"unavailable")
            self.assertEqual(server.room_view()["connection"],"unavailable")

    def test_missing_life_source_with_real_or_imported_memory_is_not_demo(self):
        with self.server.lock:
            self.server.activity_snapshot=None;self.server.activity_source=None;self.server.activity_state="unconfigured";self.server.demo=False
        self.assertEqual(json.loads(self.request("/api/room")[2])["mode"],"unavailable")
        self.server.demo=True
        self.assertEqual(json.loads(self.request("/api/room")[2])["mode"],"demo")

    def test_http_cannot_change_source_scope_or_invoke_bot(self):
        for payload in ({"path":"cookies.json"},{"scope":"other"},{"action":"start"}):
            self.assertEqual(self.request("/api/room/refresh",payload)[0],400)
        self.assertEqual(self.request("/api/room/execute",{})[0],404)
        self.assertEqual(self.request("/api/room",headers={"Origin":"https://other.test"})[0],403)
        self.assertEqual(self.source.load.call_count,1)

    def test_refresh_single_flight(self):
        entered,release=threading.Event(),threading.Event()
        def load():
            entered.set();release.wait(3);return copy.deepcopy(self.data)
        self.source.load.side_effect=load
        result=[]
        thread=threading.Thread(target=lambda:result.append(self.request("/api/room/refresh",{})[0]))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request("/api/room/refresh",{})[0],409)
        finally: release.set();thread.join()
        self.assertEqual(result,[200])

    def test_snapshot_age_marks_stale_without_upstream_io(self):
        self.server.activity_loaded_at=time.monotonic()-181
        stale=json.loads(self.request("/api/room")[2])
        self.assertEqual(stale["connection"],"stale")
        self.assertGreaterEqual(stale["age_seconds"],180)
        self.assertEqual(self.source.load.call_count,1)

    def test_remote_clock_skew_does_not_mark_fresh_snapshot_stale(self):
        self.server.activity_snapshot["as_of"]=time.time()-3600
        view=json.loads(self.request("/api/room")[2])
        self.assertEqual(view["connection"],"ready")
        self.assertLess(view["age_seconds"],180)
        self.assertEqual(self.source.load.call_count,1)


if __name__=="__main__": unittest.main()

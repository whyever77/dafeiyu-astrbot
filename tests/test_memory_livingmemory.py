"""Synthetic 2.7 schema fixtures only. No real memory/SSH credentials loaded."""

from contextlib import closing
import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deploy.memory_observatory.livingmemory import SSHMemorySource
from deploy.memory_observatory.livingmemory_reader import SourceError, SUPPORTED_VERSION, read_snapshot, scope_key
from deploy.memory_observatory.model import normalize, project
from deploy.memory_observatory.server import ObservatoryServer

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
TS = NOW.timestamp()


def make_db(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE documents(id INTEGER PRIMARY KEY, text TEXT, metadata TEXT);
        CREATE TABLE memory_atoms(id INTEGER PRIMARY KEY,parent_memory_id INTEGER,atom_type TEXT,content TEXT,
          importance REAL,confidence REAL,created_at REAL,last_accessed_at REAL,last_reinforced_at REAL,
          expires_at REAL,status TEXT,reinforcement_count INTEGER,session_id TEXT,persona_id TEXT,
          decay_type TEXT,ttl_days REAL);
        CREATE TABLE graph_nodes(id INTEGER PRIMARY KEY,node_value TEXT);
        CREATE TABLE graph_edges(id INTEGER PRIMARY KEY,source_node_id INTEGER,target_node_id INTEGER,
          relation_type TEXT,source_memory_id INTEGER,status TEXT);
    """)
    for ident, session, persona, status in ((1, 's1', 'p1', 'active'), (2, 's1', 'p2', 'active'),
                                           (3, 's1', 'p1', 'archived'), (4, None, 'p1', 'active'),
                                           (5, 's1', None, 'active')):
        meta = dict(session_id=session, persona_id=persona, status=status, create_time=TS - 86400,
                    last_access_time=TS - 100, access_count=7, importance=.8, topics=['音乐'],
                    canonical_summary='保留来源摘要', api_key='must-never-leave', source_messages=['private transcript'])
        db.execute('INSERT INTO documents VALUES(?,?,?)', (ident, f'Synthetic document {ident}', json.dumps(meta)))
    rows = [(11, 1, 'planned', 'active', 's1', 'p1', None, TS - 86400),
            (12, 1, 'factual', 'active', 's2', 'p1', None, TS - 86400),
            (13, 1, 'factual', 'expired', 's1', 'p1', None, TS - 86400),
            (14, 1, 'factual', 'forgotten', 's1', 'p1', None, TS - 86400),
            (15, 1, 'factual', 'active', 's1', 'p1', TS - 10, TS - 86400),
            (16, 3, 'factual', 'active', 's1', 'p1', None, TS - 86400),
            (17, 1, 'factual', 'active', 's1', 'p1', None, TS + 1000),
            (18, 1, 'unknown', 'active', 's1', 'p1', 0, TS - 86400)]
    for ident, parent, kind, status, session, persona, expires, created in rows:
        db.execute('INSERT INTO memory_atoms VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (ident, parent, kind, 'Synthetic atom', .6, .7, created, TS - 100, TS - 100,
                    expires, status, 3, session, persona, 'exponential', 14))
    db.executemany('INSERT INTO graph_nodes VALUES(?,?)', [(1,'Person A'),(2,'Music'),(3,'Person B')])
    db.executemany('INSERT INTO graph_edges VALUES(?,?,?,?,?,?)',
                   [(1,1,2,'prefers',1,'active'),(2,3,2,'dislikes',2,'active'),(3,1,3,'deleted',1,'deleted')])
    db.commit()
    db.close()


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'fixture.db'
        make_db(self.path)

    def read(self):
        return read_snapshot(self.path, SUPPORTED_VERSION, now=NOW)

    def test_active_documents_and_valid_atoms_only(self):
        result = self.read()
        self.assertEqual({m['id'] for m in result['memories']}, {'lm-doc-1','lm-doc-2','lm-doc-5','lm-atom-11','lm-atom-18'})
        counts = result['source_info']['counts']
        self.assertEqual(counts['inactive_documents'], 1)
        self.assertEqual(counts['inactive_atoms'], 3)
        self.assertEqual(counts['unscoped'], 1)
        self.assertEqual(counts['orphan_or_scope_mismatch_atoms'], 2)
        self.assertEqual(counts['invalid_records'], 1)

    def test_session_and_persona_are_both_isolation_keys(self):
        snapshot = normalize(self.read())
        scope = scope_key('s1','p1')
        view = project(snapshot, scope=scope, query='Synthetic')
        ids = {m['id'] for m in view['memories']}
        self.assertEqual(ids, {'lm-doc-1','lm-atom-11','lm-atom-18'})
        self.assertTrue(all(t['id'] in ids for t in view['trace']))
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in view['edges']))
        self.assertNotEqual(scope_key('s1', None), scope_key('s1', 'p1'))
        self.assertNotEqual(scope_key('s1', ''), scope_key('s1', None))

    def test_missing_persona_is_quarantined_but_explicit_null_stays_separate(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            meta = json.loads(db.execute('SELECT metadata FROM documents WHERE id=1').fetchone()[0])
            del meta['persona_id']
            db.execute('UPDATE documents SET metadata=? WHERE id=1',(json.dumps(meta),))
        ids = {m['id'] for m in self.read()['memories']}
        self.assertNotIn('lm-doc-1',ids)
        self.assertIn('lm-doc-5',ids)

    def test_plans_unknown_and_document_types_are_not_invented_experiences(self):
        records = {m['id']: m for m in self.read()['memories']}
        self.assertEqual(records['lm-doc-1']['kind'], 'document')
        self.assertEqual(records['lm-atom-11']['kind'], 'plan')
        self.assertEqual(records['lm-atom-18']['kind'], 'unclassified')

    def test_native_ttl_is_evidence_not_simulation_half_life(self):
        record = next(m for m in normalize(self.read())['memories'] if m['id']=='lm-atom-11')
        self.assertEqual(record['upstream']['ttl_days'],14)
        self.assertNotEqual(record['half_life_days'],14)
        self.assertTrue(record['stability_assumed'])
        zero = next(m for m in self.read()['memories'] if m['id']=='lm-atom-18')
        self.assertNotIn('expires_at', zero['upstream'])

    def test_source_relations_keep_parent_attribution_and_skip_deleted_edges(self):
        result = self.read()
        doc = next(m for m in result['memories'] if m['id']=='lm-doc-1')
        self.assertEqual([r['relation'] for r in doc['upstream']['relations']], ['prefers'])
        self.assertEqual(result['edges'][0]['source'],'lm-doc-1')
        self.assertFalse(result['edges'][0]['inferred'])
        self.assertEqual(result['source_info']['graph_edges_total'],3)

    def test_metadata_allowlist_drops_secrets_and_transcripts(self):
        result = json.dumps(self.read())
        self.assertNotIn('must-never-leave',result)
        self.assertNotIn('private transcript',result)
        self.assertNotIn('api_key',result)

    def test_repeated_reads_do_not_mutate_db_or_access_counts(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        for _ in range(3):
            self.read()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(json.loads(db.execute('SELECT metadata FROM documents WHERE id=1').fetchone()[0])['access_count'],7)
            self.assertEqual(db.execute('SELECT reinforcement_count FROM memory_atoms WHERE id=11').fetchone()[0],3)

    def test_sqlite_readonly_mode_and_authorizer_reject_mutations(self):
        original_connect = sqlite3.connect
        blocked = []

        class ProbedConnection(sqlite3.Connection):
            def set_authorizer(connection, callback):
                super().set_authorizer(callback)
                for sql in ("ATTACH DATABASE ':memory:' AS unexpected", "PRAGMA writable_schema=ON"):
                    try:
                        super(ProbedConnection, connection).execute(sql)
                    except sqlite3.DatabaseError:
                        blocked.append('authorizer')
                    else:
                        raise AssertionError('source authorizer allowed a mutation')

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs, factory=ProbedConnection)
            try:
                connection.execute("UPDATE documents SET text='must not write'")
            except sqlite3.OperationalError:
                blocked.append('readonly')
                # sqlite3 starts an implicit transaction before rejecting the
                # UPDATE. End the probe's transaction before the real reader.
                connection.rollback()
            else:
                connection.close()
                raise AssertionError('source connection allowed a write before authorizer')
            return connection

        with patch('deploy.memory_observatory.livingmemory_reader.sqlite3.connect', side_effect=connect):
            self.read()
        self.assertEqual(blocked, ['readonly', 'authorizer', 'authorizer'])

    def test_live_wal_records_are_visible_without_immutable_shortcut(self):
        with closing(sqlite3.connect(self.path)) as writer, writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("UPDATE documents SET text='committed WAL' WHERE id=1")
            writer.commit()
            record = next(m for m in self.read()['memories'] if m['id']=='lm-doc-1')
            self.assertEqual(record['text'],'committed WAL')

    def test_unknown_version_schema_and_missing_file_fail_closed(self):
        with self.assertRaises(SourceError):
            read_snapshot(self.path,'9.0.0',now=NOW)
        missing = self.path.with_name('missing.db')
        with self.assertRaises(FileNotFoundError):
            read_snapshot(missing,SUPPORTED_VERSION,now=NOW)
        self.assertFalse(missing.exists())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP TABLE memory_atoms')
        with self.assertRaises(sqlite3.DatabaseError):
            self.read()

    def test_long_content_reports_truncation(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE documents SET text=? WHERE id=1',('x'*4100,))
        record = next(m for m in self.read()['memories'] if m['id']=='lm-doc-1')
        self.assertEqual(len(record['text']),4000)
        self.assertTrue(record['upstream']['truncated'])

    def test_empty_trusted_source_is_valid_and_never_becomes_demo(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM documents')
        snapshot = normalize(self.read(),allow_empty=True)
        self.assertEqual(project(snapshot,scope=None)['stats']['total'],0)


class SSHSourceTests(unittest.TestCase):
    def test_command_has_strict_identity_fixed_reader_and_no_remote_file_write(self):
        runner=Mock(return_value=SimpleNamespace(returncode=1,stdout='',stderr='secret remote diagnostic'))
        source=SSHMemorySource(host='fake@host',key='synthetic-key',run=runner)
        with self.assertRaisesRegex(SourceError,'SSH') as caught:
            source.load()
        self.assertNotIn('secret',str(caught.exception))
        argv=runner.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=yes',argv)
        self.assertIn('BatchMode=yes',argv)
        self.assertEqual(argv[-1],'python3 -')
        self.assertIn('?mode=ro',runner.call_args.kwargs['input'])
        self.assertEqual(runner.call_args.kwargs['timeout'],40)

    def test_rejects_option_or_command_injection_in_host(self):
        for host in ('-oProxyCommand=bad','host;bad','host bad','$(bad)','host\nuser'):
            with self.subTest(host=host),self.assertRaises(SourceError):
                SSHMemorySource(host=host)

    def test_transport_error_is_redacted(self):
        runner=Mock(side_effect=RuntimeError('secret /path host user body'))
        with self.assertRaises(SourceError) as caught:
            SSHMemorySource(host='fake@host',run=runner).load()
        self.assertNotIn('secret',str(caught.exception))


class FakeSource:
    def __init__(self):
        self.fail=False
        self.calls=0
        self.entered=None
        self.release=None

    def load(self):
        self.calls+=1
        if self.entered:
            self.entered.set()
            self.release.wait(5)
        if self.fail:
            raise SourceError('fake unavailable')
        raw={'schema':'memory-observatory/v1','as_of':NOW.isoformat(),'memories':[
            {'id':'doc','scope':'test','title':'Synthetic live','text':f'version {self.calls}',
             'created_at':NOW.isoformat(),'kind':'document'}]}
        return normalize(raw),{'enabled':True,'state':'ready','version':SUPPORTED_VERSION,'synced_at':NOW.isoformat()}


class LiveServerTests(unittest.TestCase):
    def setUp(self):
        self.source=FakeSource()
        self.server=ObservatoryServer(('127.0.0.1',0),source=self.source)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)

    def request(self,path,body=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=8)
        conn.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),
                     {'Content-Type':'application/json','X-Observatory':'local'})
        response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result

    def test_reads_and_projections_never_refresh_or_write_upstream(self):
        for _ in range(3):
            code,body=self.request('/api/snapshot')
            self.assertEqual(code,200);self.assertFalse(body['demo'])
            self.request('/api/project',{'scope':'test','query':'Synthetic','days':90})
        self.assertEqual(self.source.calls,1)

    def test_live_mode_refuses_import_and_reset_without_losing_source(self):
        for route in ('/api/reset','/api/import'):
            self.assertEqual(self.request(route,{})[0],409)
        self.assertFalse(self.request('/api/snapshot')[1]['demo'])

    def test_failed_refresh_preserves_previous_snapshot_and_marks_stale(self):
        before=self.request('/api/snapshot')[1]['memories']
        self.source.fail=True
        self.assertEqual(self.request('/api/livingmemory/refresh',{})[0],503)
        after=self.request('/api/snapshot')[1]
        self.assertEqual(after['memories'],before)
        self.assertEqual(after['connection']['state'],'stale')
        self.source.fail=False
        code,body=self.request('/api/livingmemory/refresh',{})
        self.assertEqual(code,200);self.assertEqual(body['connection']['state'],'ready')
        self.assertNotEqual(body['memories'],before)

    def test_concurrent_refresh_is_single_flight(self):
        self.source.entered=threading.Event();self.source.release=threading.Event()
        result=[]
        worker=threading.Thread(target=lambda:result.append(self.request('/api/livingmemory/refresh',{})))
        worker.start()
        try:
            self.assertTrue(self.source.entered.wait(3))
            self.assertEqual(self.request('/api/livingmemory/refresh',{})[0],409)
        finally:
            self.source.release.set();worker.join(timeout=5)
        self.assertEqual(result[0][0],200)
        self.assertEqual(self.source.calls,2)

    def test_initial_failure_does_not_start_a_demo_server(self):
        source=FakeSource();source.fail=True
        with self.assertRaises(SourceError):
            ObservatoryServer(('127.0.0.1',0),source=source)


if __name__=='__main__':
    unittest.main()

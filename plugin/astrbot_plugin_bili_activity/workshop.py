"""Scoped, versioned text works backed by actual activity evidence.

Checks establish source availability and document structure, not semantic truth.
This module performs no model calls, device operations or external publication.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from contextlib import closing, nullcontext
from pathlib import Path

from .budget import BudgetExceeded
from .life import observed_read_evidence


VERSION = 1
ID = re.compile(r"[0-9a-f]{32}\Z")
CITATION = re.compile(r"\[E:([0-9a-f]{32})\]")
SECTIONS = ("资料观察", "我的想法", "待核实")


def _text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    return value.strip()


def _positive(value, name, minimum=1, maximum=50):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Workshop:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.schema_backup = None
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creative_meta'").fetchone()
        if exists:
            row = self.db.execute("SELECT value FROM creative_meta WHERE key='version'").fetchone()
            if not row or row[0] != str(VERSION):
                raise RuntimeError("unsupported creative workshop database schema")
            return
        if self.db.in_transaction:
            raise RuntimeError("initialize creative workshop outside a transaction")
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('creative_works','creative_revisions')").fetchone():
            raise RuntimeError("incomplete creative workshop database schema")
        directory = Path(store.folder) / "schema_backups"
        directory.mkdir(exist_ok=True)
        self.schema_backup = directory / f"creative-v0-{time.time_ns()}-{uuid.uuid4().hex[:8]}.sqlite3"
        with closing(sqlite3.connect(self.schema_backup)) as backup:
            self.db.backup(backup)
        try:
            self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE creative_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO creative_meta VALUES('version','1');
            CREATE TABLE creative_works(
              id TEXT PRIMARY KEY,scope TEXT NOT NULL,goal_id TEXT NOT NULL,title TEXT NOT NULL,
              phase TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL,
              create_key TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
              UNIQUE(scope,goal_id),UNIQUE(scope,create_key));
            CREATE TABLE creative_revisions(
              work_id TEXT NOT NULL,scope TEXT NOT NULL,revision INTEGER NOT NULL,
              body TEXT NOT NULL,source_ids_json TEXT NOT NULL,validation_json TEXT NOT NULL,
              body_hash TEXT NOT NULL,request_key TEXT NOT NULL,created_at REAL NOT NULL,
              PRIMARY KEY(work_id,revision),UNIQUE(work_id,request_key));
            CREATE INDEX creative_scope_phase ON creative_works(scope,phase,updated_at);
            COMMIT;
            """)
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _goal(self, scope, goal_id, run_id=None):
        goal = self.store.life.get_goal(scope, goal_id)
        if not goal:
            raise ValueError("goal not found in this session")
        if goal["status"] not in {"ready", "paused", "running"}:
            raise ValueError("goal is not available for writing")
        if goal["status"] == "running" and (not run_id or goal["run_id"] != run_id):
            raise ValueError("work goal belongs to another active run")
        if run_id and (goal["status"] != "running" or goal["run_id"] != run_id):
            raise ValueError("work goal run lease has ended")
        return goal

    def _row(self, scope, work_id):
        if not isinstance(work_id, str) or not ID.fullmatch(work_id):
            return None
        return self.db.execute("SELECT * FROM creative_works WHERE scope=? AND id=?", (scope, work_id)).fetchone()

    def _require(self, scope, work_id):
        row = self._row(scope, work_id)
        if row is None:
            raise ValueError("work not found in this session")
        return dict(row)

    def create(self, scope, goal_id, title, request_key, run_id=None):
        scope, title = _text(scope, "scope", 250), _text(title, "title", 160)
        request_key = _text(request_key, "request_key", 250)
        with self.store.transaction():
            self._goal(scope, goal_id, run_id)
            old = self.db.execute("SELECT * FROM creative_works WHERE scope=? AND create_key=?", (scope, request_key)).fetchone()
            if old:
                if old["goal_id"] != goal_id or old["title"] != title:
                    raise ValueError("create request key reused with different content")
                return self.get(scope, old["id"])
            old = self.db.execute("SELECT * FROM creative_works WHERE scope=? AND goal_id=?", (scope, goal_id)).fetchone()
            if old:
                return self.get(scope, old["id"])
            if self.db.execute("SELECT count(*) FROM creative_works WHERE scope=?", (scope,)).fetchone()[0] >= 200:
                raise ValueError("work limit reached")
            work_id, now = uuid.uuid4().hex, time.time()
            self.db.execute("INSERT INTO creative_works VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (work_id, scope, goal_id, title, "write", "draft", 0, request_key, now, now))
        return self.get(scope, work_id)

    def list(self, scope, limit=12):
        limit = _positive(limit, "limit", maximum=50)
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM creative_works WHERE scope=? ORDER BY updated_at DESC,id LIMIT ?", (scope, limit))]

    def pending(self, scope, limit=8):
        limit = _positive(limit, "limit", maximum=50)
        return [dict(row) for row in self.db.execute(
            "SELECT w.* FROM creative_works w JOIN goals g ON g.id=w.goal_id AND g.scope=w.scope "
            "WHERE w.scope=? AND w.phase='write' AND g.status IN ('ready','paused') "
            "ORDER BY w.updated_at ASC,w.id LIMIT ?", (scope, limit))]

    def get(self, scope, work_id, revision=None):
        row = self._row(scope, work_id)
        if row is None:
            return None
        work = dict(row)
        selected = work["revision"] if revision is None else _positive(revision, "revision")
        work.update(head_revision=work["revision"], revision=selected)
        if selected == 0:
            return {**work, "body": "", "source_ids": [], "validation": None,
                    "body_hash": None, "revision_created_at": None}
        row = self.db.execute("SELECT * FROM creative_revisions WHERE scope=? AND work_id=? AND revision=?",
                              (scope, work_id, selected)).fetchone()
        if row is None:
            return None
        return {**work, "body": row["body"], "source_ids": json.loads(row["source_ids_json"]),
                "validation": json.loads(row["validation_json"]), "body_hash": row["body_hash"],
                "revision_created_at": row["created_at"]}

    def _sources(self, scope, source_ids):
        if (not isinstance(source_ids, list) or len(source_ids) > 20
                or any(not isinstance(item, str) or not ID.fullmatch(item) for item in source_ids)
                or len(set(source_ids)) != len(source_ids)):
            raise ValueError("source_ids must contain at most 20 unique episode IDs")
        result = []
        for episode_id in source_ids:
            row = self.db.execute(
                "SELECT * FROM episodes e WHERE e.scope=? AND e.id=? "
                "AND NOT EXISTS(SELECT 1 FROM episodes next WHERE next.supersedes_id=e.id)",
                (scope, episode_id)).fetchone()
            if row is None or row["kind"] not in {"video", "video_note", "read", "douyin_video", "phone_observation"}:
                raise ValueError("source is unavailable, superseded or not observed content in this session")
            proof = json.loads(row["evidence_json"])
            if row["kind"] in {"read", "douyin_video", "phone_observation"}:
                valid = observed_read_evidence(proof)
            else:
                valid = bool(proof.get("observations") or proof.get("subtitles"))
            if not valid:
                raise ValueError("source contains no observed content")
            source = {"id": episode_id, "kind": row["kind"], "occurred_at": row["occurred_at"],
                      "source_id": row["source_id"], "excerpt": row["evidence_json"][:6000]}
            for key in ("bvid", "cid", "range", "covered_range", "analyzed_range", "operation"):
                if key in proof:
                    source[key] = proof[key]
            result.append(source)
        return result

    def _validate(self, scope, body, source_ids, revision):
        errors, sources = [], []
        try:
            sources = self._sources(scope, source_ids)
        except ValueError as exc:
            errors.append(str(exc))
        citations = set(CITATION.findall(body))
        if not source_ids:
            errors.append("at least one observed source is required")
        if citations != set(source_ids):
            errors.append("inline [E:episode_id] citations must match source_ids exactly")
        if body.count("[E:") != len(CITATION.findall(body)):
            errors.append("malformed evidence citation")
        headings = list(re.finditer(r"(?m)^##[ \t]+([^\r\n]+)[ \t]*$", body))
        sections = {}
        for index, match in enumerate(headings):
            title = match.group(1).strip()
            if title in SECTIONS:
                if title in sections:
                    errors.append(f"duplicate section: {title}")
                end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
                sections[title] = body[match.end():end].strip()
        for section in SECTIONS:
            if not sections.get(section):
                errors.append(f"nonempty ## {section} section is required")
        if sections.get("资料观察") and not CITATION.search(sections["资料观察"]):
            errors.append("资料观察 needs an inline evidence citation")
        return {"passed": not errors, "errors": errors, "source_ids": source_ids,
                "sources": sources, "body_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "revision": revision, "checked_at": time.time(),
                "meaning": "structure and source availability only; not proof of factual truth"}

    def revise(self, scope, work_id, body, source_ids, expected_revision, request_key,
               run_id=None, max_chars=12000, max_revisions=3):
        max_chars = _positive(max_chars, "max_chars", minimum=500, maximum=20000)
        max_revisions = _positive(max_revisions, "max_revisions", maximum=10)
        body = _text(body, "body", max_chars)
        if len(body.encode("utf-8")) > 60000:
            raise ValueError("body exceeds UTF-8 size limit")
        expected_revision = _positive(expected_revision, "expected_revision", minimum=0)
        request_key = _text(request_key, "request_key", 250)
        with self.store.transaction():
            work = self._require(scope, work_id)
            self._goal(scope, work["goal_id"], run_id)
            # Reject foreign/removed sources even when the body is a draft.
            self._sources(scope, source_ids)
            sources_json = _json(sorted(source_ids))
            old = self.db.execute("SELECT * FROM creative_revisions WHERE work_id=? AND scope=? AND request_key=?",
                                  (work_id, scope, request_key)).fetchone()
            if old:
                if old["body"] != body or old["source_ids_json"] != sources_json:
                    raise ValueError("revision request key reused with different content")
                return {**self.get(scope, work_id, old["revision"]), "created_revision": False}
            if expected_revision != work["revision"]:
                raise ValueError("work revision changed; read latest version before revising")
            head = self.get(scope, work_id)
            if head["body"] == body and sorted(head["source_ids"]) == sorted(source_ids):
                return {**head, "created_revision": False}
            if work["revision"] >= 50:
                raise ValueError("revision limit reached")
            budget_key = f"workshop_revisions:{scope}:{run_id}"
            used = self.store.get_setting(budget_key, 0) if run_id else 0
            if run_id and used >= max_revisions:
                raise BudgetExceeded("workshop revision budget exhausted for this run")
            revision, now = work["revision"] + 1, time.time()
            validation = self._validate(scope, body, sorted(source_ids), revision)
            self.db.execute("INSERT INTO creative_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                            (work_id, scope, revision, body, sources_json, _json(validation),
                             validation["body_hash"], request_key, now))
            self.db.execute("UPDATE creative_works SET revision=?,status=?,phase='write',updated_at=? WHERE scope=? AND id=?",
                            (revision, "ready" if validation["passed"] else "draft", now, scope, work_id))
            if run_id:
                self.store.setting(budget_key, used + 1)
        return {**self.get(scope, work_id), "created_revision": True}

    def check(self, scope, work_id, revision=None):
        transaction = nullcontext() if self.db.in_transaction else self.store.transaction()
        with transaction:
            work = self.get(scope, work_id, revision)
            if work is None:
                raise ValueError("work or revision not found in this session")
            validation = self._validate(scope, work["body"], work["source_ids"], work["revision"])
            self.db.execute("UPDATE creative_revisions SET validation_json=? WHERE scope=? AND work_id=? AND revision=?",
                            (_json(validation), scope, work_id, work["revision"]))
            if work["revision"] == work["head_revision"]:
                self.db.execute("UPDATE creative_works SET status=? WHERE scope=? AND id=?",
                                ("ready" if validation["passed"] else "draft", scope, work_id))
        return validation

    def set_phase(self, scope, work_id, phase, run_id=None):
        if phase not in {"research", "write"}:
            raise ValueError("phase must be research or write")
        with self.store.transaction():
            work = self._require(scope, work_id)
            self._goal(scope, work["goal_id"], run_id)
            self.db.execute("UPDATE creative_works SET phase=?,updated_at=? WHERE scope=? AND id=?",
                            (phase, time.time(), scope, work_id))
        return self.get(scope, work_id)

    @staticmethod
    def _directory(parent_fd, name):
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)

    def export(self, scope, work_id, revision=None):
        work = self.get(scope, work_id, revision)
        if work is None:
            raise ValueError("work or revision not found in this session")
        checked = self.check(scope, work_id, revision)
        if not checked["passed"]:
            raise ValueError("work must pass fresh source and structure checks before export")
        lines = [f"# {work['title']}", "", work["body"], "", "---", "", "## 来源记录", "",
                 "以下仅核验资料来源和笔记结构，不代表所有推断已被事实验证。", ""]
        for source in checked["sources"]:
            lines.extend([f"- [E:{source['id']}] {source['kind']} / 时间戳 {source['occurred_at']}",
                          "", "```json", source["excerpt"].replace("```", "` ` `"), "```", ""])
        content = ("\n".join(lines) + "\n").encode("utf-8")
        filename = f"r{work['revision']}.md"
        # Open each directory without following symlinks and rename within the
        # held directory FD. Neither work IDs nor user text select a file path.
        root_fd = os.open(self.store.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        works_fd = work_fd = None
        temporary = f".export-{uuid.uuid4().hex}.tmp"
        try:
            works_fd = self._directory(root_fd, "works")
            work_fd = self._directory(works_fd, work_id)
            try:
                target = os.stat(filename, dir_fd=work_fd, follow_symlinks=False)
                if not stat.S_ISREG(target.st_mode):
                    raise ValueError("export destination must be a regular file")
            except FileNotFoundError:
                pass
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=work_fd)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, filename, src_dir_fd=work_fd, dst_dir_fd=work_fd)
            os.fsync(work_fd)
        finally:
            if work_fd is not None:
                try:
                    os.unlink(temporary, dir_fd=work_fd)
                except FileNotFoundError:
                    pass
                os.close(work_fd)
            if works_fd is not None:
                os.close(works_fd)
            os.close(root_fd)
        return {"work_id": work_id, "revision": work["revision"],
                "path": str(Path(self.store.folder).resolve() / "works" / work_id / filename),
                "sha256": hashlib.sha256(content).hexdigest()}

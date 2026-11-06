"""Persistent, scoped goals and evidence. No model, network or device operations."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import closing


SCHEMA_VERSION = 1
ACTIVE = {"ready", "running", "paused", "waiting_approval", "blocked"}
TERMINAL = {"completed", "cancelled"}
TRANSITIONS = {
    "ready": {"paused", "blocked", "completed", "cancelled"},
    "running": {"paused", "waiting_approval", "blocked", "completed", "cancelled"},
    "paused": {"ready", "blocked", "completed", "cancelled"},
    "waiting_approval": {"ready", "paused", "blocked", "cancelled"},
    "blocked": {"ready", "paused", "cancelled"},
    "completed": set(), "cancelled": set(),
}


def _text(value, name, maximum=1000, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f"invalid {name}")
    return value.strip()


CJK = re.compile(r"[㐀-鿿]")
CHAT_TOPIC_PATTERNS = (
    re.compile(r"^(?:我)?最近(?:在玩|在看|在查|在研究|对|挺喜欢|迷上了)([^。！？!?\n，,]{2,60}?)(?:感兴趣|挺有意思|很有意思)?[。！!～~]?$"),
    re.compile(r"^(?:你可以|有空可以|下次可以|推荐你|给你安利)(?:去)?(?:看看|了解下|了解一下|查查|搜搜)([^。！？!?\n，,]{2,60})[。！!～~]?$"),
    re.compile(r"^你知道([^。！？!?\n，,]{2,60}?)(?:吗|么)[？?]?$"),
)
_CHAT_TOPIC_STOPWORDS = (
    "吗", "么", "如果", "的话", "可以", "暂停", "继续", "恢复",
    "自主活动", "截图", "画面", "不喜欢", "讨厌", "不要", "别",
)


def extract_chat_topic(text):
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned or len(cleaned) > 200:
        return None
    for pattern in CHAT_TOPIC_PATTERNS:
        match = pattern.fullmatch(cleaned)
        if match:
            topic = match.group(1).strip()
            if topic and not any(word in topic for word in _CHAT_TOPIC_STOPWORDS):
                return topic
    return None


def query_terms(query, maximum=8):
    """Split a natural query into bounded terms without losing LIKE safety.

    Chinese text often arrives without spaces (for example
    "比格自助三国杀"), so add two-character windows from longer CJK terms.
    """
    terms = []

    def add(term):
        if term and term not in terms and len(terms) < maximum:
            terms.append(term)

    for term in re.split(r"[\W_]+", query, flags=re.UNICODE):
        term = term.strip()
        add(term)
        if CJK.search(term) and len(term) >= 4:
            for index in range(len(term) - 1):
                add(term[index:index + 2])
    return terms


def _json(value, name, maximum=24000):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid {name}") from exc
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError(f"{name} too large")
    _validate_ranges(value)
    return encoded


def observed_read_evidence(proof):
    """Reject explicit failed reads while tolerating truncated legacy excerpts."""
    if not isinstance(proof, dict) or proof.get("ok") is False or not proof.get("operation"):
        return False
    excerpt = proof.get("result_excerpt")
    if excerpt in (None, "", "null", "{}", "[]", {}, []):
        return False
    parsed = excerpt
    if isinstance(excerpt, str):
        try:
            parsed = json.loads(excerpt)
        except (json.JSONDecodeError, TypeError):
            # Stored excerpts may be intentionally truncated; absence of valid JSON
            # is not evidence that an otherwise non-empty read failed.
            parsed = None
    return not (isinstance(parsed, dict) and parsed.get("ok") is False)


_WORK_HEADING_RE = re.compile(r"(?m)^##[ \t]+([^\r\n]+)[ \t]*$")
_WORK_CITATION_RE = re.compile(r"\[E:[0-9a-f]{32}\]")


def parse_work_sections(body):
    if not isinstance(body, str) or not body:
        return {}
    headings = list(_WORK_HEADING_RE.finditer(body))
    sections = {}
    for index, match in enumerate(headings):
        title = match.group(1).strip()
        if title in {"资料观察", "我的想法", "待核实"} and title not in sections:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            raw = _WORK_CITATION_RE.sub("", body[match.end():end]).strip()
            cleaned = " ".join(line.lstrip("-* \t") for line in raw.splitlines() if line.strip())
            if cleaned:
                sections[title] = cleaned[:160]
    return sections


def _validate_ranges(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"range", "covered_range", "analyzed_range"}:
                if (not isinstance(item, (list, tuple)) or len(item) != 2
                        or any(isinstance(n, bool) or not isinstance(n, (int, float))
                               or not math.isfinite(n) or n < 0 for n in item)
                        or item[1] < item[0]):
                    raise ValueError("invalid evidence time range")
            _validate_ranges(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_ranges(item)


def prepare_database(db, folder, existed):
    """Check versions and snapshot v0 before any legacy or new schema mutation."""
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise RuntimeError("activity database is newer than this plugin; do not downgrade its ledger")
    if version == 0 and existed:
        directory = Path(folder) / "schema_backups"
        directory.mkdir(exist_ok=True)
        destination = directory / f"activity-v0-{time.time_ns()}.sqlite3"
        with closing(sqlite3.connect(destination)) as backup:
            db.backup(backup)
        return destination
    return None


class LifeState:
    def __init__(self, store):
        self.store, self.db = store, store.db
        version = int(self.db.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError("unsupported activity database schema")
        if version == 0:
            try:
                self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS goals (
                  id TEXT PRIMARY KEY, scope TEXT NOT NULL, owner_kind TEXT NOT NULL,
                  title TEXT NOT NULL, why TEXT NOT NULL, source_id TEXT NOT NULL,
                  status TEXT NOT NULL, next_step TEXT NOT NULL,
                  checkpoint_json TEXT NOT NULL, evidence_ids TEXT NOT NULL,
                  run_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                  due_at REAL, UNIQUE(scope,source_id));
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_goal ON goals(scope)
                  WHERE status='running';
                CREATE INDEX IF NOT EXISTS goals_scope_state ON goals(scope,status,updated_at);
                CREATE TABLE IF NOT EXISTS episodes (
                  id TEXT PRIMARY KEY, scope TEXT NOT NULL, run_id TEXT, goal_id TEXT,
                  occurred_at REAL NOT NULL, kind TEXT NOT NULL,
                  subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
                  source_id TEXT NOT NULL, evidence_json TEXT NOT NULL,
                  interpretation_json TEXT NOT NULL, supersedes_id TEXT,
                  UNIQUE(scope,kind,source_id));
                CREATE INDEX IF NOT EXISTS episodes_scope_time ON episodes(scope,occurred_at);
                CREATE INDEX IF NOT EXISTS episodes_goal ON episodes(scope,goal_id,occurred_at);
                CREATE UNIQUE INDEX IF NOT EXISTS one_episode_revision ON episodes(supersedes_id)
                  WHERE supersedes_id IS NOT NULL;
                PRAGMA user_version=1;
                COMMIT;
                """)
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        # Resume only after a new observation. Never replay the prior run's tools.
        self.db.execute("UPDATE goals SET status='paused',run_id=NULL,updated_at=? WHERE status='running'", (time.time(),))

    @staticmethod
    def _goal(row):
        if row is None:
            return None
        result = dict(row)
        result["checkpoint"] = json.loads(result.pop("checkpoint_json"))
        result["evidence_ids"] = json.loads(result["evidence_ids"])
        return result

    @staticmethod
    def _episode(row):
        if row is None:
            return None
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        result["interpretation"] = json.loads(result.pop("interpretation_json"))
        return result

    def create_goal(self, scope, title, why, next_step, source_id, owner_kind="agent"):
        scope = _text(scope, "scope", 250)
        source_id = _text(source_id, "source_id", 250)
        if owner_kind != "agent":
            raise ValueError("autonomous goals belong to the agent")
        title, why, next_step = (_text(title, "title", 160), _text(why, "why", 1000),
                                _text(next_step, "next_step", 1000))
        with self.store.transaction():
            old = self.db.execute("SELECT * FROM goals WHERE scope=? AND source_id=?", (scope, source_id)).fetchone()
            if old:
                return self._goal(old)
            count = self.db.execute("SELECT count(*) FROM goals WHERE scope=? AND status NOT IN ('completed','cancelled')", (scope,)).fetchone()[0]
            if count >= 8:
                raise ValueError("finish or cancel an existing goal before creating another")
            gid, now = uuid.uuid4().hex, time.time()
            self.db.execute("INSERT INTO goals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (gid, scope, owner_kind, title, why, source_id, "ready", next_step,
                             "{}", "[]", None, now, now, None))
        return self.get_goal(scope, gid)

    def get_goal(self, scope, goal_id):
        return self._goal(self.db.execute("SELECT * FROM goals WHERE scope=? AND id=?", (scope, goal_id)).fetchone())

    def goals(self, scope, limit=8, include_terminal=False):
        limit = max(1, min(int(limit), 50))
        where = "" if include_terminal else " AND status NOT IN ('completed','cancelled')"
        order = ("CASE WHEN status IN ('completed','cancelled') THEN 1 ELSE 0 END,updated_at DESC,id"
                 if include_terminal else "updated_at DESC,id")
        return [self._goal(row) for row in self.db.execute(
            f"SELECT * FROM goals WHERE scope=?{where} ORDER BY {order} LIMIT ?", (scope, limit))]

    def claim_goal(self, scope, goal_id, run_id):
        run_id = _text(run_id, "run_id", 250)
        with self.store.transaction():
            goal = self.get_goal(scope, goal_id)
            if not goal:
                raise ValueError("goal not found in this session")
            if goal["status"] == "running" and goal["run_id"] == run_id:
                return goal
            if goal["status"] not in {"ready", "paused"}:
                raise ValueError("goal is not ready to resume")
            running = self.db.execute("SELECT id FROM goals WHERE scope=? AND status='running'", (scope,)).fetchone()
            if running:
                raise ValueError("another goal is already running")
            self.db.execute("UPDATE goals SET status='running',run_id=?,updated_at=? WHERE scope=? AND id=?",
                            (run_id, time.time(), scope, goal_id))
        return self.get_goal(scope, goal_id)

    def update_goal(self, scope, goal_id, *, next_step=None, checkpoint=None, status=None, why=None, run_id=None):
        if next_step is not None:
            next_step = _text(next_step, "next_step", 1000, empty=status in TERMINAL)
        if why is not None:
            why = _text(why, "why", 1000)
        checkpoint_json = None if checkpoint is None else _json(checkpoint, "checkpoint")
        with self.store.transaction():
            goal = self.get_goal(scope, goal_id)
            if not goal:
                raise ValueError("goal not found in this session")
            if goal["status"] == "running" and (not run_id or goal["run_id"] != run_id):
                raise ValueError("goal belongs to another active run")
            if run_id and goal["status"] != "running":
                raise ValueError("goal run lease has ended")
            if goal["status"] in TERMINAL:
                if any(v is not None for v in (next_step, checkpoint, why)) or (status and status != goal["status"]):
                    raise ValueError("a finished goal is immutable")
                return goal
            new_status = status or goal["status"]
            if new_status != goal["status"] and new_status not in TRANSITIONS[goal["status"]]:
                raise ValueError("invalid goal state transition")
            if new_status == "completed":
                linked_work = None
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creative_works'").fetchone():
                    linked_work = self.db.execute("SELECT id FROM creative_works WHERE scope=? AND goal_id=?", (scope, goal_id)).fetchone()
                if linked_work:
                    # A research read cannot stand in for a finished linked work.
                    # Check the current head, even when its feature switch is off.
                    from .workshop import Workshop
                    if not Workshop(self.store).check(scope, linked_work["id"])["passed"]:
                        raise ValueError("a completed writing goal needs its latest work to pass fresh checks")
                progress = self.db.execute(
                    "SELECT kind,evidence_json FROM episodes WHERE scope=? AND goal_id=? AND kind IN ('video','video_note','read')",
                    (scope, goal_id)).fetchall()
                observed = bool(linked_work)
                for row in progress:
                    proof = json.loads(row["evidence_json"])
                    if ((row["kind"] in {"video", "video_note"} and (proof.get("observations") or proof.get("subtitles")))
                            or (row["kind"] == "read" and observed_read_evidence(proof))):
                        observed = True
                        break
                if not observed:
                    raise ValueError("a completed goal needs observed content, not a lifecycle summary")
            self.db.execute("UPDATE goals SET next_step=?,checkpoint_json=?,status=?,why=?,run_id=?,updated_at=? WHERE scope=? AND id=?",
                            (goal["next_step"] if next_step is None else next_step,
                             json.dumps(goal["checkpoint"], ensure_ascii=False) if checkpoint_json is None else checkpoint_json,
                             new_status, goal["why"] if why is None else why,
                             goal["run_id"] if new_status == "running" else None, time.time(), scope, goal_id))
        return self.get_goal(scope, goal_id)

    def pause_run(self, scope, run_id, reason):
        reason = _text(reason, "reason", 1000)
        self.db.execute("UPDATE goals SET status='paused',run_id=NULL,updated_at=? WHERE scope=? AND run_id=? AND status='running'",
                        (time.time(), scope, run_id))
        self.store.event(scope, "goal_run_paused", {"run_id": run_id, "reason": reason})

    def record_episode(self, scope, *, kind, source_id, evidence, interpretation=None,
                       run_id=None, goal_id=None, subject_kind="agent", subject_id="", supersedes_id=None):
        if subject_kind not in {"agent", "system"}:
            raise ValueError("user preference requires an authenticated QQ message")
        return self._record_episode(scope, kind=kind, source_id=source_id, evidence=evidence,
                                    interpretation=interpretation, run_id=run_id, goal_id=goal_id,
                                    subject_kind=subject_kind, subject_id=subject_id, supersedes_id=supersedes_id)

    def _record_episode(self, scope, *, kind, source_id, evidence, interpretation=None,
                        run_id=None, goal_id=None, subject_kind="agent", subject_id="", supersedes_id=None):
        scope = _text(scope, "scope", 250)
        kind = _text(kind, "kind", 64)
        source_id = _text(source_id, "source_id", 250)
        subject_id = _text(subject_id, "subject_id", 250, empty=True)
        evidence_json = _json(evidence, "evidence")
        interpretation_json = _json(interpretation or {}, "interpretation", 8000)
        if not evidence:
            raise ValueError("an episode needs source evidence")
        with self.store.transaction():
            old = self.db.execute("SELECT * FROM episodes WHERE scope=? AND kind=? AND source_id=?", (scope, kind, source_id)).fetchone()
            if old:
                return self._episode(old)
            if goal_id:
                goal = self.get_goal(scope, goal_id)
                if not goal:
                    raise ValueError("episode goal belongs to another session or is missing")
                if goal["status"] == "running" and goal["run_id"] != run_id:
                    raise ValueError("episode does not own the goal run")
            if supersedes_id:
                previous = self.db.execute("SELECT * FROM episodes WHERE scope=? AND id=?", (scope, supersedes_id)).fetchone()
                if (not previous or previous["subject_kind"] != subject_kind or previous["subject_id"] != subject_id):
                    raise ValueError("correction must preserve source session and subject")
                if self.db.execute("SELECT 1 FROM episodes WHERE supersedes_id=?", (supersedes_id,)).fetchone():
                    raise ValueError("correct the latest revision instead")
            eid, now = uuid.uuid4().hex, time.time()
            self.db.execute("INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (eid, scope, run_id, goal_id, now, kind, subject_kind, subject_id,
                             source_id, evidence_json, interpretation_json, supersedes_id))
            if goal_id:
                ids = goal["evidence_ids"]
                self.db.execute("UPDATE goals SET evidence_ids=?,updated_at=? WHERE scope=? AND id=?",
                                (json.dumps((ids + [eid])[-32:]), now, scope, goal_id))
        return self._episode(self.db.execute("SELECT * FROM episodes WHERE id=?", (eid,)).fetchone())

    def record_user_preference(self, scope, actor_id, event_id, text):
        """Trusted event-handler adapter only; never expose this as an Agent tool."""
        actor_id = str(actor_id)
        if not re.fullmatch(r"[^:]+:FriendMessage:[0-9]+", scope) or scope.rsplit(":", 1)[-1] != actor_id:
            raise ValueError("preference message must belong to the configured private session")
        # Ordinary chat may be empty, long or lack a transport message ID.
        # It must continue normally; only bounded, attributable statements
        # qualify as evidence for this narrow extractor.
        if (event_id is None or isinstance(event_id, bool)
                or not str(event_id).strip() or len(str(event_id)) > 250
                or not isinstance(text, str) or not text.strip() or len(text) > 2000):
            return None
        event_id = str(event_id).strip()
        text = text.strip()
        match = re.fullmatch(r"我(不喜欢|更喜欢|喜欢|讨厌)([^。！？!?\n]{1,100})[。！!～~]?", text)
        if not match:
            return None
        topic = match.group(2).strip()
        if not topic or any(word in topic for word in ("吗", "么", "如果", "的话", "可以")):
            return None
        polarity = -1 if match.group(1) in {"不喜欢", "讨厌"} else 1
        return self._record_episode(scope, kind="explicit_preference", source_id="qq:" + event_id,
                                    subject_kind="user", subject_id=actor_id,
                                    evidence={"message_id": event_id, "actor_id": actor_id, "text": text},
                                    interpretation={"interest": {"topic": topic, "polarity": polarity}})

    def record_chat_topic(self, scope, actor_id, event_id, text):
        """Record an owner-mentioned chat topic cue in events without creating an episode or agent interest."""
        actor_id = str(actor_id)
        if not re.fullmatch(r"[^:]+:FriendMessage:[0-9]+", scope) or scope.rsplit(":", 1)[-1] != actor_id:
            raise ValueError("chat topic message must belong to the configured private session")
        if (event_id is None or isinstance(event_id, bool)
                or not str(event_id).strip() or len(str(event_id)) > 250
                or not isinstance(text, str) or not text.strip() or len(text) > 2000):
            return None
        event_id = str(event_id).strip()
        topic = extract_chat_topic(text)
        if not topic:
            return None
        for row in self.db.execute(
            "SELECT details FROM events WHERE scope=? AND kind='owner_chat_topic' ORDER BY seq DESC LIMIT 12",
            (scope,),
        ):
            try:
                old = json.loads(row["details"])
            except (ValueError, TypeError):
                continue
            if isinstance(old, dict) and (old.get("message_id") == event_id or old.get("topic") == topic):
                return old
        details = {
            "message_id": event_id,
            "actor_id": actor_id,
            "topic": topic,
            "text": text.strip()[:200],
            "subject_kind": "user",
        }
        self.store.event(scope, "owner_chat_topic", details)
        return details

    def episodes(self, scope, query="", goal_id=None, limit=8):
        query = _text(query, "query", 200, empty=True)
        limit = max(1, min(int(limit), 50))
        sql = "SELECT e.* FROM episodes e WHERE e.scope=? AND NOT EXISTS (SELECT 1 FROM episodes n WHERE n.supersedes_id=e.id)"
        args = [scope]
        if goal_id:
            sql += " AND e.goal_id=?"
            args.append(goal_id)
        if query:
            # Match any term, across evidence or interpretation. Each term is
            # escaped so user text cannot accidentally match every memory.
            terms = query_terms(query) or [query]
            clauses = []
            for term in terms:
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                clauses.append("(e.evidence_json LIKE ? ESCAPE '\\' OR e.interpretation_json LIKE ? ESCAPE '\\')")
                args += ["%" + escaped + "%"] * 2
            sql += " AND (" + " OR ".join(clauses) + ")"
        sql += " ORDER BY e.occurred_at DESC,e.id LIMIT ?"
        args.append(limit)
        return [self._episode(row) for row in self.db.execute(sql, args)]

    def interests(self, scope, subject_kind="agent", limit=8):
        if subject_kind not in {"agent", "user"}:
            raise ValueError("invalid interest owner")
        limit = max(1, min(int(limit), 30))
        rows = self.db.execute("SELECT e.* FROM episodes e WHERE e.scope=? AND e.subject_kind=? AND NOT EXISTS (SELECT 1 FROM episodes n WHERE n.supersedes_id=e.id) ORDER BY e.occurred_at DESC LIMIT 500", (scope, subject_kind))
        topics, seen = {}, set()
        now = time.time()
        for raw in rows:
            row = self._episode(raw)
            interest = row["interpretation"].get("interest")
            if not isinstance(interest, dict):
                continue
            topic, polarity = interest.get("topic"), interest.get("polarity")
            if not isinstance(topic, str) or not topic.strip() or len(topic) > 100 or isinstance(polarity, bool) or polarity not in {-1, 1}:
                continue
            # Rephrasing or re-summarizing one video/message is not fresh evidence.
            origin = row["evidence"].get("message_id") or (str(row["evidence"].get("bvid")) + ":" + str(row["evidence"].get("cid")) if row["evidence"].get("bvid") else row["source_id"])
            # An explicit user's latest statement supersedes their previous
            # stance; repeated statements must not be treated as a vote count.
            dedup = (topic.strip(), "latest-user-statement" if subject_kind == "user" else str(origin))
            if dedup in seen:
                continue
            seen.add(dedup)
            info = topics.setdefault(topic.strip(), {"topic": topic.strip(), "subject_kind": subject_kind, "score": 0.0,
                                                    "evidence_count": 0, "evidence_ids": [], "last_seen": row["occurred_at"]})
            age_days = max(0, now - row["occurred_at"]) / 86400
            info["score"] += polarity * (0.5 ** (age_days / 60))
            info["evidence_count"] += 1
            info["evidence_ids"].append(row["id"])
        result = sorted(topics.values(), key=lambda row: (-abs(row["score"]), -row["last_seen"], row["topic"]))[:limit]
        for row in result:
            row["score"] = round(row["score"], 3)
            row["evidence_ids"] = row["evidence_ids"][:8]
            row["strength"] = ("explicit" if subject_kind == "user" else
                               "tentative" if row["evidence_count"] < 2 else "supported")
        return result

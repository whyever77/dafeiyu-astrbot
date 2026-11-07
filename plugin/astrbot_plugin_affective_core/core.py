"""Durable SQLite storage and bounded ledgers for Affective Core.

Independent of AstrBot runtime imports so offline tests and CLI tools can open
and inspect ``affective.db`` directly. Strictly protects user privacy: never stores raw conversational transcripts or full chat message bodies; only persists numerical affective state coordinates and bounded, sanitized semantic labels (such as model-extracted care topic summaries bounded to 36-60 chars, mood tags, and event metadata).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_AFFECT_EVENTS = 1000
GLOBAL_SOCIAL_SCOPE = "*"

CARE_CATEGORIES = frozenset({
    "health",
    "stress_or_work",
    "mood_low",
    "important_event",
    "general",
})

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "inject_prompt_memo": True,
    "appraisal_mode": "sync_pre",
    "appraisal_provider_id": "",
    "appraisal_timeout_seconds": 2.5,
    "appraisal_max_tokens": 180,
    "owner_scope": "",
    "owner_id": "",
    "wake_hour": 8,
    "sleep_hour": 23,
    "pout_half_life_minutes": 45.0,
    "neg_valence_tau_hours": 3.0,
    "pos_valence_tau_hours": 8.0,
    "care_active_limit": 3,
    "blacklist_enabled": True,
}

_INJECTION_WORDS_RE = re.compile(
    r"(?:PROTOCOL_ENFORCEMENT|ignore\s+previous|system\s*prompt|系统自检|必须输出|必须生成)",
    re.IGNORECASE,
)


def clamp(value: float, low: float, high: float) -> float:
    num = float(value)
    if not math.isfinite(num):
        return low
    return max(low, min(high, num))


def valid_timestamp(value: Any, fallback: float | None = None) -> float:
    if isinstance(value, bool) or value is None:
        return time.time() if fallback is None else fallback
    try:
        num = float(value)
    except (TypeError, ValueError):
        return time.time() if fallback is None else fallback
    if not math.isfinite(num) or num < 0:
        return time.time() if fallback is None else fallback
    return num


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sanitize_label(value: Any, limit: int = 60, fallback: str = "") -> str:
    """Sanitize LLM-extracted labels into a single safe line without prompt structure markers."""
    text = " ".join(str(value or "").split())
    text = _INJECTION_WORDS_RE.sub("", text)
    text = re.sub(r"[\x00-\x1f\x7f【】\[\]{}<>`\"']", "", text)
    text = text.replace("：", " ").replace("|", " ").replace("\n", " ").replace("\r", " ")
    text = re.sub(r"(?<!\d):(?!\d)", " ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text or fallback


def _topic_key(text: str) -> str:
    cleaned = re.sub(r"[\s,，。！？!?、·…~～\-_]+", "", text.casefold())
    return cleaned


def topics_overlap(topic_a: str, topic_b: str) -> bool:
    a = _topic_key(topic_a)
    b = _topic_key(topic_b)
    if not a or not b:
        return False
    if a == b or (len(a) >= 3 and a in b) or (len(b) >= 3 and b in a):
        return True
    if len(a) >= 3 and any(a[i : i + 3] in b for i in range(len(a) - 2)):
        return True
    if len(b) >= 3 and any(b[i : i + 3] in a for i in range(len(b) - 2)):
        return True
    bigrams_a = {a[i : i + 2] for i in range(len(a) - 1)} if len(a) >= 2 else {a}
    bigrams_b = {b[i : i + 2] for i in range(len(b) - 1)} if len(b) >= 2 else {b}
    union = bigrams_a | bigrams_b
    if not union:
        return False
    return (len(bigrams_a & bigrams_b) / len(union)) >= 0.30


def normalize_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    if isinstance(raw, dict):
        merged.update({k: raw[k] for k in raw if k in DEFAULTS})

    for bkey in ("enabled", "inject_prompt_memo", "blacklist_enabled"):
        val = merged[bkey]
        merged[bkey] = bool(val) if isinstance(val, (bool, int)) else DEFAULTS[bkey]

    mode = str(merged.get("appraisal_mode", "sync_pre")).strip().lower()
    if mode not in {"sync_pre", "async_post", "math_only"}:
        mode = "sync_pre"
    merged["appraisal_mode"] = mode

    merged["appraisal_provider_id"] = str(merged.get("appraisal_provider_id") or "").strip()
    merged["owner_scope"] = str(merged.get("owner_scope") or "").strip()
    merged["owner_id"] = str(merged.get("owner_id") or "").strip()

    for fkey, low, high in (
        ("appraisal_timeout_seconds", 0.5, 10.0),
        ("pout_half_life_minutes", 5.0, 360.0),
        ("neg_valence_tau_hours", 0.5, 24.0),
        ("pos_valence_tau_hours", 1.0, 48.0),
    ):
        try:
            merged[fkey] = clamp(float(merged[fkey]), low, high)
        except (TypeError, ValueError):
            merged[fkey] = DEFAULTS[fkey]

    for ikey, low, high in (
        ("appraisal_max_tokens", 64, 512),
        ("wake_hour", 0, 23),
        ("sleep_hour", 0, 23),
        ("care_active_limit", 1, 5),
    ):
        try:
            if isinstance(merged[ikey], bool):
                raise ValueError("bool is not int")
            merged[ikey] = max(low, min(high, int(merged[ikey])))
        except (TypeError, ValueError):
            merged[ikey] = DEFAULTS[ikey]

    if merged["wake_hour"] == merged["sleep_hour"]:
        merged["wake_hour"] = DEFAULTS["wake_hour"]
        merged["sleep_hour"] = DEFAULTS["sleep_hour"]

    return merged


class AffectiveStore:
    """SQLite-backed state store for owner affective dynamics, social states, and care threads."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db_path = self.folder / "affective.db"
        self._tx_depth = 0
        self._lock = threading.RLock()
        self.db = sqlite3.connect(
            self.db_path,
            isolation_level=None,
            check_same_thread=False,
        )
        self.db.row_factory = sqlite3.Row
        try:
            self._initialize()
        except BaseException:
            with self._lock:
                self.db.close()
            raise

    def close(self) -> None:
        with self._lock:
            self.db.close()

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._tx_depth > 0:
                self._tx_depth += 1
                try:
                    yield
                finally:
                    self._tx_depth -= 1
                return
            self.db.execute("BEGIN IMMEDIATE")
            self._tx_depth = 1
            try:
                yield
            except BaseException:
                self._tx_depth = 0
                self.db.execute("ROLLBACK")
                raise
            else:
                self._tx_depth = 0
                self.db.execute("COMMIT")

    def _initialize(self) -> None:
        with self._lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS affect_states (
                    scope TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    valence REAL NOT NULL,
                    arousal REAL NOT NULL,
                    longing REAL NOT NULL,
                    pout REAL NOT NULL,
                    share_urge REAL NOT NULL,
                    warmth_bond REAL NOT NULL,
                    dominant_tag TEXT NOT NULL,
                    softened_mode TEXT NOT NULL,
                    last_settled_at REAL NOT NULL,
                    last_interaction_at REAL NOT NULL,
                    last_sleep_reset_day TEXT NOT NULL,
                    consecutive_rejects INTEGER NOT NULL DEFAULT 0,
                    consecutive_neg_stimuli INTEGER NOT NULL DEFAULT 0,
                    interaction_count INTEGER NOT NULL DEFAULT 0,
                    extra_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS social_states (
                    scope TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    familiarity REAL NOT NULL,
                    vibe REAL NOT NULL,
                    is_blacklisted INTEGER NOT NULL DEFAULT 0,
                    interaction_count INTEGER NOT NULL DEFAULT 0,
                    last_interaction_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, user_id)
                );

                CREATE TABLE IF NOT EXISTS care_threads (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    category TEXT NOT NULL,
                    salience REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    last_prompted_at REAL NOT NULL DEFAULT 0,
                    prompt_count INTEGER NOT NULL DEFAULT 0,
                    resolved_at REAL,
                    resolution_note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_care_scope_status
                    ON care_threads(scope, status, expires_at);

                CREATE TABLE IF NOT EXISTS affect_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time REAL NOT NULL,
                    scope TEXT NOT NULL,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_affect_events_scope_time
                    ON affect_events(scope, time DESC);
                """
            )
            if self.get_meta("schema_version") is None:
                self.set_meta("schema_version", SCHEMA_VERSION)

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self.db.execute("SELECT value_json FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except (TypeError, ValueError):
            return default

    def set_meta(self, key: str, value: Any, *, now: float | None = None) -> None:
        stamp = valid_timestamp(now)
        with self._lock:
            self.db.execute(
            "INSERT INTO meta(key, value_json, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (key, canonical(value), stamp),
        )

    @staticmethod
    def _decode_owner_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            extra = json.loads(data.pop("extra_json", "{}") or "{}")
        except (TypeError, ValueError):
            extra = {}
        data["extra"] = extra if isinstance(extra, dict) else {}
        return data

    def get_owner_state(self, scope: str) -> dict[str, Any] | None:
        if not scope:
            return None
        with self._lock:
            row = self.db.execute("SELECT * FROM affect_states WHERE scope=?", (scope,)).fetchone()
        return self._decode_owner_row(row) if row else None

    def list_owner_states(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM affect_states ORDER BY last_settled_at DESC").fetchall()
        return [self._decode_owner_row(r) for r in rows]

    def save_owner_state(self, state: dict[str, Any]) -> dict[str, Any]:
        clean = {
            "scope": str(state["scope"]),
            "owner_id": str(state.get("owner_id") or ""),
            "valence": clamp(state.get("valence", 0.15), -1.0, 1.0),
            "arousal": clamp(state.get("arousal", 0.50), 0.0, 1.0),
            "longing": clamp(state.get("longing", 0.10), 0.0, 1.0),
            "pout": clamp(state.get("pout", 0.0), 0.0, 1.0),
            "share_urge": clamp(state.get("share_urge", 0.10), 0.0, 1.0),
            "warmth_bond": clamp(state.get("warmth_bond", 320.0), 0.0, 1000.0),
            "dominant_tag": str(state.get("dominant_tag") or "calm")[:40],
            "softened_mode": str(state.get("softened_mode") or "")[:40],
            "last_settled_at": valid_timestamp(state.get("last_settled_at")),
            "last_interaction_at": valid_timestamp(state.get("last_interaction_at"), 0.0),
            "last_sleep_reset_day": str(state.get("last_sleep_reset_day") or "")[:16],
            "consecutive_rejects": max(0, min(50, int(state.get("consecutive_rejects") or 0))),
            "consecutive_neg_stimuli": max(0, min(50, int(state.get("consecutive_neg_stimuli") or 0))),
            "interaction_count": max(0, int(state.get("interaction_count") or 0)),
            "extra": state.get("extra") if isinstance(state.get("extra"), dict) else {},
        }
        extra_json = canonical(clean["extra"])
        with self._lock:
            self.db.execute(
            """
            INSERT INTO affect_states(
                scope, owner_id, valence, arousal, longing, pout, share_urge, warmth_bond,
                dominant_tag, softened_mode, last_settled_at, last_interaction_at,
                last_sleep_reset_day, consecutive_rejects, consecutive_neg_stimuli,
                interaction_count, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                owner_id=excluded.owner_id,
                valence=excluded.valence,
                arousal=excluded.arousal,
                longing=excluded.longing,
                pout=excluded.pout,
                share_urge=excluded.share_urge,
                warmth_bond=excluded.warmth_bond,
                dominant_tag=excluded.dominant_tag,
                softened_mode=excluded.softened_mode,
                last_settled_at=excluded.last_settled_at,
                last_interaction_at=excluded.last_interaction_at,
                last_sleep_reset_day=excluded.last_sleep_reset_day,
                consecutive_rejects=excluded.consecutive_rejects,
                consecutive_neg_stimuli=excluded.consecutive_neg_stimuli,
                interaction_count=excluded.interaction_count,
                extra_json=excluded.extra_json
            """,
            (
                clean["scope"],
                clean["owner_id"],
                clean["valence"],
                clean["arousal"],
                clean["longing"],
                clean["pout"],
                clean["share_urge"],
                clean["warmth_bond"],
                clean["dominant_tag"],
                clean["softened_mode"],
                clean["last_settled_at"],
                clean["last_interaction_at"],
                clean["last_sleep_reset_day"],
                clean["consecutive_rejects"],
                clean["consecutive_neg_stimuli"],
                clean["interaction_count"],
                extra_json,
            ),
        )
        return clean

    def get_social_state(self, scope: str, user_id: str) -> dict[str, Any]:
        uid = str(user_id or "")
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM social_states WHERE scope=? AND user_id=?",
                (scope, uid),
            ).fetchone()
            if row:
                return dict(row)
            global_row = self.db.execute(
                "SELECT * FROM social_states WHERE scope=? AND user_id=?",
                (GLOBAL_SOCIAL_SCOPE, uid),
            ).fetchone()
            if global_row:
                seeded = dict(global_row)
                seeded["scope"] = scope
                return seeded
        return {
            "scope": scope,
            "user_id": uid,
            "familiarity": 0.0,
            "vibe": 0.0,
            "is_blacklisted": 0,
            "interaction_count": 0,
            "last_interaction_at": 0.0,
            "updated_at": 0.0,
        }

    def is_user_blacklisted(self, scope: str, user_id: str) -> bool:
        uid = str(user_id or "")
        with self._lock:
            row = self.db.execute(
                "SELECT 1 FROM social_states WHERE user_id=? AND scope IN (?, ?) AND is_blacklisted=1 LIMIT 1",
                (uid, scope, GLOBAL_SOCIAL_SCOPE),
            ).fetchone()
        return bool(row)

    def save_social_state(self, state: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        stamp = valid_timestamp(now, state.get("updated_at"))
        clean = {
            "scope": str(state.get("scope") or GLOBAL_SOCIAL_SCOPE),
            "user_id": str(state.get("user_id") or ""),
            "familiarity": clamp(state.get("familiarity", 0.0), 0.0, 100.0),
            "vibe": clamp(state.get("vibe", 0.0), -1.0, 1.0),
            "is_blacklisted": 1 if state.get("is_blacklisted") else 0,
            "interaction_count": max(0, int(state.get("interaction_count") or 0)),
            "last_interaction_at": valid_timestamp(state.get("last_interaction_at"), 0.0),
            "updated_at": stamp,
        }
        with self._lock:
            self.db.execute(
                """
                INSERT INTO social_states(
                    scope, user_id, familiarity, vibe, is_blacklisted,
                    interaction_count, last_interaction_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, user_id) DO UPDATE SET
                    familiarity=excluded.familiarity,
                    vibe=excluded.vibe,
                    is_blacklisted=excluded.is_blacklisted,
                    interaction_count=excluded.interaction_count,
                    last_interaction_at=excluded.last_interaction_at,
                    updated_at=excluded.updated_at
                """,
                (
                    clean["scope"],
                    clean["user_id"],
                    clean["familiarity"],
                    clean["vibe"],
                    clean["is_blacklisted"],
                    clean["interaction_count"],
                    clean["last_interaction_at"],
                    clean["updated_at"],
                ),
            )
        return clean

    def expire_care_threads(self, scope: str, *, now: float | None = None) -> int:
        stamp = valid_timestamp(now)
        with self._lock:
            cur = self.db.execute(
                "UPDATE care_threads SET status='expired', updated_at=? "
                "WHERE scope=? AND status='active' AND expires_at<=?",
                (stamp, scope, stamp),
            )
            return cur.rowcount

    def active_care_threads(
        self,
        scope: str,
        *,
        limit: int = 3,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        if not scope:
            return []
        stamp = valid_timestamp(now)
        with self._lock:
            self.expire_care_threads(scope, now=stamp)
            rows = self.db.execute(
                "SELECT * FROM care_threads WHERE scope=? AND status='active' AND expires_at>? "
                "ORDER BY salience DESC, updated_at DESC LIMIT ?",
                (scope, stamp, max(1, min(10, int(limit)))),
            ).fetchall()
            return [dict(r) for r in rows]

    def upsert_care_thread(
        self,
        scope: str,
        topic: str,
        *,
        category: str = "general",
        salience: float = 0.65,
        ttl_hours: float = 36.0,
        limit: int = 3,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        clean_topic = sanitize_label(topic, limit=60)
        if len(clean_topic) < 2 or not scope:
            return None
        cat = category if category in CARE_CATEGORIES else "general"
        stamp = valid_timestamp(now)
        bounded_ttl = clamp(ttl_hours, 6.0, 96.0)
        expires_at = stamp + bounded_ttl * 3600.0
        bounded_salience = clamp(salience, 0.1, 1.0)
        max_active = max(1, min(5, int(limit)))

        with self.transaction():
            self.expire_care_threads(scope, now=stamp)
            active = self.db.execute(
                "SELECT * FROM care_threads WHERE scope=? AND status='active' ORDER BY updated_at DESC",
                (scope,),
            ).fetchall()
            for existing in active:
                if topics_overlap(existing["topic"], clean_topic):
                    new_salience = max(float(existing["salience"]), bounded_salience)
                    new_expires = max(float(existing["expires_at"]), expires_at)
                    self.db.execute(
                        "UPDATE care_threads SET topic=?, category=?, salience=?, updated_at=?, expires_at=? WHERE id=?",
                        (clean_topic, cat, new_salience, stamp, new_expires, existing["id"]),
                    )
                    updated = self.db.execute(
                        "SELECT * FROM care_threads WHERE id=?",
                        (existing["id"],),
                    ).fetchone()
                    return dict(updated)

            if len(active) >= max_active:
                ordered = sorted(
                    active,
                    key=lambda r: (float(r["salience"]), float(r["updated_at"])),
                )
                for victim in ordered[: len(active) - max_active + 1]:
                    self.db.execute(
                        "UPDATE care_threads SET status='expired', updated_at=?, resolution_note='evicted_by_limit' WHERE id=?",
                        (stamp, victim["id"]),
                    )

            cid = "care_" + uuid.uuid4().hex[:10]
            self.db.execute(
                """
                INSERT INTO care_threads(
                    id, scope, topic, category, salience, status,
                    created_at, updated_at, expires_at, last_prompted_at, prompt_count,
                    resolved_at, resolution_note
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, 0.0, 0, NULL, '')
                """,
                (cid, scope, clean_topic, cat, bounded_salience, stamp, stamp, expires_at),
            )
            created = self.db.execute("SELECT * FROM care_threads WHERE id=?", (cid,)).fetchone()
            return dict(created)

    def resolve_care_threads(
        self,
        scope: str,
        thread_ids: list[str],
        *,
        note: str = "resolved_in_chat",
        now: float | None = None,
    ) -> list[str]:
        if not scope or not thread_ids:
            return []
        stamp = valid_timestamp(now)
        safe_note = sanitize_label(note, limit=40, fallback="resolved")
        resolved: list[str] = []
        with self.transaction():
            for raw_id in thread_ids:
                cid = str(raw_id or "").strip()
                if not cid:
                    continue
                cur = self.db.execute(
                    "UPDATE care_threads SET status='resolved', updated_at=?, resolved_at=?, resolution_note=? "
                    "WHERE id=? AND scope=? AND status='active'",
                    (stamp, stamp, safe_note, cid, scope),
                )
                if cur.rowcount > 0:
                    resolved.append(cid)
        return resolved

    def mark_care_prompted(self, scope: str, thread_id: str, *, now: float | None = None) -> None:
        stamp = valid_timestamp(now)
        with self._lock:
            self.db.execute(
                "UPDATE care_threads SET last_prompted_at=?, prompt_count=prompt_count+1, updated_at=? "
                "WHERE id=? AND scope=? AND status='active'",
                (stamp, stamp, thread_id, scope),
            )

    def record_event(
        self,
        scope: str,
        source: str,
        kind: str,
        details: dict[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> None:
        stamp = valid_timestamp(now)
        payload = details if isinstance(details, dict) else {}
        with self._lock:
            self.db.execute(
                "INSERT INTO affect_events(time, scope, source, kind, details_json) VALUES(?, ?, ?, ?, ?)",
                (stamp, str(scope or ""), str(source)[:40], str(kind)[:60], canonical(payload)),
            )
            max_id_row = self.db.execute("SELECT MAX(id) AS m FROM affect_events").fetchone()
            if max_id_row and max_id_row["m"] and int(max_id_row["m"]) > MAX_AFFECT_EVENTS + 100:
                cutoff_id = int(max_id_row["m"]) - MAX_AFFECT_EVENTS
                self.db.execute("DELETE FROM affect_events WHERE id<=?", (cutoff_id,))

    def recent_events(self, scope: str | None = None, *, limit: int = 5) -> list[dict[str, Any]]:
        lim = max(1, min(50, int(limit)))
        with self._lock:
            if scope:
                rows = self.db.execute(
                    "SELECT * FROM affect_events WHERE scope=? ORDER BY id DESC LIMIT ?",
                    (scope, lim),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM affect_events ORDER BY id DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json", "{}") or "{}")
            except (TypeError, ValueError):
                item["details"] = {}
            items.append(item)
        return items

"""Additive interaction policy around the existing proposal and Executor ledgers."""
from __future__ import annotations

import json
import random
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from .core import ENGAGEMENT, PUBLIC, WRITES, canonical, digest


DEFAULTS = {
    "engagement_actions": False, "interaction_mode": "approval", "auto_public": False,
    "max_actions_per_run": 3, "max_actions_per_day": 20,
    "max_comments_per_day": 3, "max_replies_per_day": 5, "max_danmaku_per_day": 1,
    "max_likes_per_day": 10, "max_coins_per_day": 1,
    "action_interval_ms": [1500, 4000], "risk_cooldown_minutes": 60,
}
ACTION_LIMITS = {"comment": "max_comments_per_day", "reply": "max_replies_per_day",
                 "danmaku": "max_danmaku_per_day", "like": "max_likes_per_day", "coin": "max_coins_per_day"}
RISK_CODES = {12014, -412, -352}
VISIBILITY = {"public", "not_public_or_folded", "unknown"}


def validate_config(cfg):
    for key in ("engagement_actions", "auto_public"):
        if type(cfg[key]) is not bool:
            raise ValueError(f"{key} 必须是布尔开关")
    if cfg["interaction_mode"] not in {"approval", "auto_low_risk", "auto_public"}:
        raise ValueError("无效 interaction_mode")
    for key in (*ACTION_LIMITS.values(), "max_actions_per_run", "max_actions_per_day", "risk_cooldown_minutes"):
        if type(cfg[key]) is not int or not 0 <= cfg[key] <= 10000:
            raise ValueError(f"{key} 必须为 0..10000 的整数")
    if cfg["risk_cooldown_minutes"] < 1:
        raise ValueError("risk_cooldown_minutes 必须至少 1 分钟")
    interval = cfg["action_interval_ms"]
    if (not isinstance(interval, list) or len(interval) != 2
            or any(type(v) is not int or not 0 <= v <= 60000 for v in interval)
            or interval[0] > interval[1]):
        raise ValueError("action_interval_ms 必须为递增的毫秒范围")


def enabled(action, cfg):
    return bool(cfg.get("public_interactions") if action in PUBLIC else
                cfg.get("engagement_actions") if action in ENGAGEMENT else cfg.get("collection_management"))


def day_at(now):
    return datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()


def dedupe_key(action, args):
    if action in {"comment", "danmaku"}:
        return digest([action, args.get("bvid")])
    if action == "reply":
        return digest([action, args.get("bvid"), str(args.get("root_rpid")), str(args.get("parent_rpid"))])
    if action == "follow":
        return digest([action, str(args.get("uid"))])
    return None


def safe_result(value):
    """Only typed ledger metadata can enter events/status, never remote bodies."""
    value = value if isinstance(value, dict) else {}
    result = {"verified": value.get("verified") is True,
              "visibility": value.get("visibility") if value.get("visibility") in VISIBILITY else "unknown",
              "risk_code": value.get("risk_code") if type(value.get("risk_code")) is int else None}
    for key in ("rpid", "dmid"):
        text = str(value.get(key) or "")
        result[key] = text if text.isascii() and text.isdigit() else ""
    result["result"] = "verified" if result["verified"] else "uncertain"
    if value.get("executed") is False and value.get("reason") in {"switch_disabled", "interaction_blocked"}:
        result.update(executed=False, reason=value["reason"], result="blocked")
    # reason/error strings from a transport are deliberately discarded.
    return result


class InteractionBlocked(RuntimeError):
    """A local pre-I/O refusal; its message contains no external data."""


class Interactions:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.schema_backup = None
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='interaction_meta'").fetchone()
        if exists:
            version = self.db.execute("SELECT value FROM interaction_meta WHERE key='version'").fetchone()
            if not version or version[0] != "1":
                raise RuntimeError("unsupported interaction schema")
            return
        if self.db.in_transaction:
            raise RuntimeError("initialize interactions outside a transaction")
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('interactions','daily_actions')").fetchone():
            raise RuntimeError("incomplete interaction schema")
        directory = store.folder / "schema_backups"
        directory.mkdir(exist_ok=True)
        self.schema_backup = directory / f"interactions-v0-{time.time_ns()}.sqlite3"
        with closing(sqlite3.connect(self.schema_backup)) as backup:
            self.db.backup(backup)
        try:
            self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE interaction_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO interaction_meta VALUES('version','1');
            CREATE TABLE interactions(
              proposal_id TEXT PRIMARY KEY, scope TEXT NOT NULL, run_id TEXT NOT NULL,
              action TEXT NOT NULL, fingerprint TEXT NOT NULL, dedupe_key TEXT,
              bvid TEXT NOT NULL, cid TEXT NOT NULL, uid TEXT NOT NULL,
              root_rpid TEXT NOT NULL, parent_rpid TEXT NOT NULL, rpid TEXT NOT NULL,
              created REAL NOT NULL, day TEXT NOT NULL, result TEXT NOT NULL,
              verified INTEGER NOT NULL, visibility TEXT NOT NULL, risk_code INTEGER,
              UNIQUE(scope,dedupe_key));
            CREATE INDEX interaction_recent ON interactions(scope,created);
            CREATE INDEX interaction_run ON interactions(scope,run_id);
            CREATE TABLE daily_actions(scope TEXT NOT NULL,day TEXT NOT NULL,action TEXT NOT NULL,
              count INTEGER NOT NULL CHECK(count>=0),PRIMARY KEY(scope,day,action));
            """)
            # Preserve pre-v0.5 successes/ambiguous attempts as business dedup evidence.
            for row in self.db.execute("SELECT * FROM proposals WHERE status IN ('verified','executing','uncertain') ORDER BY created").fetchall():
                args = json.loads(row["args"])
                self._insert(row["scope"], "legacy:" + row["id"], row["id"], row["action"], args, row["created"], ignore=True)
                value = safe_result(json.loads(row["result"]) if row["result"] else {})
                self.db.execute("UPDATE interactions SET result=?,verified=?,rpid=?,visibility=? WHERE proposal_id=?",
                    ("verified" if row["status"] == "verified" else "uncertain", row["status"] == "verified",
                     value["rpid"], value["visibility"], row["id"]))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _insert(self, scope, run_id, pid, action, args, now, *, ignore=False):
        # Multiple historical successes may share a new business key. Keep all
        # counts/history, while the earliest one owns the unique guard.
        key = dedupe_key(action, args)
        if ignore and key and self.db.execute("SELECT 1 FROM interactions WHERE scope=? AND dedupe_key=?", (scope, key)).fetchone():
            key = None
        day = day_at(now)
        self.db.execute("INSERT INTO interactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, scope, run_id, action, digest([action, args]), key,
             str(args.get("bvid", "")), str(args.get("cid", "")), str(args.get("uid", "")),
             str(args.get("root_rpid", "")), str(args.get("parent_rpid", "")), "", now, day,
             "uncertain", 0, "unknown", None))
        self.db.execute("INSERT INTO daily_actions VALUES(?,?,?,1) ON CONFLICT(scope,day,action) DO UPDATE SET count=count+1",
                        (scope, day, action))

    def guard(self, scope, run_id, now=None):
        now = time.time() if now is None else now
        if self.store.get_setting("interaction_login:" + scope) == "invalid":
            raise InteractionBlocked("B站登录失效，请主人重新扫码后查询互动状态确认登录。")
        if self.store.get_setting("interaction_stopped:" + scope + ":" + run_id, False):
            raise InteractionBlocked("本轮因风控已停止，不再发送下一条。")
        until = self.store.get_setting("interaction_cooldown:" + scope, 0)
        if now < until:
            raise InteractionBlocked("B站风控冷却中，本次没有执行。")

    def check(self, scope, run_id, action, args, cfg, now=None):
        now = time.time() if now is None else now
        if action not in WRITES:
            raise ValueError("unsupported interaction")
        self.guard(scope, run_id, now)
        key = dedupe_key(action, args)
        if key and self.db.execute("SELECT 1 FROM interactions WHERE scope=? AND dedupe_key=?", (scope, key)).fetchone():
            raise InteractionBlocked("相同视频/回复目标/关注对象已有动作记录；禁止重复评论、回复或自动重试。")
        day = day_at(now)
        total = self.db.execute("SELECT COALESCE(SUM(count),0) FROM daily_actions WHERE scope=? AND day=?", (scope, day)).fetchone()[0]
        if total >= cfg["max_actions_per_day"]:
            raise InteractionBlocked("今日 max_actions_per_day 额度已用完。")
        count = self.db.execute("SELECT COUNT(*) FROM interactions WHERE scope=? AND run_id=?", (scope, run_id)).fetchone()[0]
        if count >= cfg["max_actions_per_run"]:
            raise InteractionBlocked("本轮 max_actions_per_run 额度已用完。")
        limit_key = ACTION_LIMITS.get(action)
        if limit_key:
            row = self.db.execute("SELECT count FROM daily_actions WHERE scope=? AND day=? AND action=?", (scope, day, action)).fetchone()
            if (row[0] if row else 0) >= cfg[limit_key]:
                raise InteractionBlocked(f"今日 {limit_key} 额度已用完。")

    def reserve(self, scope, run_id, pid, action, args, cfg, now=None):
        now = time.time() if now is None else now
        with self.store.transaction():
            existing = self.db.execute("SELECT * FROM interactions WHERE proposal_id=?", (pid,)).fetchone()
            if existing:
                if existing["scope"] != scope or existing["fingerprint"] != digest([action, args]):
                    raise ValueError("proposal_id belongs to different arguments")
                raise InteractionBlocked("该操作已经预留或执行，结果未知时不自动重试。")
            self.check(scope, run_id, action, args, cfg, now)
            self._insert(scope, run_id, pid, action, args, now)

    def risk(self, scope, run_id, code, cfg, now=None):
        now = time.time() if now is None else now
        if type(code) is not int:
            return
        if code in RISK_CODES:
            key = "interaction_cooldown:" + scope
            self.store.setting(key, max(self.store.get_setting(key, 0), now + cfg["risk_cooldown_minutes"] * 60))
            self.store.setting("interaction_stopped:" + scope + ":" + run_id, True)
        elif code == -101:
            self.store.setting("interaction_login:" + scope, "invalid")
            self.store.setting("interaction_stopped:" + scope + ":" + run_id, True)

    def finish(self, scope, run_id, pid, action, args, value, cfg, now=None):
        now = time.time() if now is None else now
        value = safe_result(value)
        with self.store.transaction():
            self.db.execute("UPDATE interactions SET result=?,verified=?,visibility=?,risk_code=?,rpid=? WHERE proposal_id=? AND scope=?",
                (value["result"], value["verified"], value["visibility"], value["risk_code"], value["rpid"], pid, scope))
            self.risk(scope, run_id, value["risk_code"], cfg, now)
            self.store.setting("interaction_next:" + scope, now + random.uniform(*cfg["action_interval_ms"]) / 1000)
            self.store.event(scope, "interaction_result", {"proposal_id": pid, "action": action,
                "bvid": args.get("bvid", ""), "cid": args.get("cid", ""), **value})
        return value

    def status(self, scope, cfg, now=None):
        now = time.time() if now is None else now
        day = day_at(now)
        counts = {row["action"]: row["count"] for row in self.db.execute("SELECT * FROM daily_actions WHERE scope=? AND day=?", (scope, day))}
        return {"day": day, "mode": cfg["interaction_mode"], "counting": "attempts_including_unknown",
            "today": {action: {"count": counts.get(action, 0), "limit": cfg.get(ACTION_LIMITS.get(action), cfg["max_actions_per_day"])} for action in sorted(WRITES)},
            "total": {"count": sum(counts.values()), "limit": cfg["max_actions_per_day"]},
            "max_actions_per_run": cfg["max_actions_per_run"],
            "pending": [dict(row) for row in self.db.execute("SELECT id,action,status FROM proposals WHERE scope=? AND ((status IN ('draft','offered') AND expires>?) OR status IN ('executing','uncertain')) ORDER BY created", (scope, now))],
            "cooldown_remaining_seconds": max(0, self.store.get_setting("interaction_cooldown:" + scope, 0) - now),
            "login": self.store.get_setting("interaction_login:" + scope, "unknown"),
            "recent": [dict(row) for row in self.db.execute("SELECT proposal_id,action,bvid,cid,rpid,result,verified,visibility,risk_code,created FROM interactions WHERE scope=? ORDER BY created DESC,rowid DESC LIMIT 5", (scope,))]}

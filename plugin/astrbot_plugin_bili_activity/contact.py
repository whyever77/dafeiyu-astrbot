"""Shared optional-contact accounting; no sender, scheduler, or message bodies."""
from __future__ import annotations

from datetime import datetime
import re
import time
from zoneinfo import ZoneInfo

from .core import digest, quiet_now

DEFAULTS = {
    "contact_coordination_enabled": False,
    "contact_daily_limit": 3,
    "contact_cooldown_minutes": 90,
    "contact_owner_quiet_minutes": 5,
    "contact_unanswered_limit": 2,
}
LIMITS = {"contact_daily_limit": (0, 50), "contact_cooldown_minutes": (1, 1440),
          "contact_owner_quiet_minutes": (0, 1440), "contact_unanswered_limit": (1, 20)}
BRIDGE = "dafeiyu_contact_v1"


class ContactBlocked(PermissionError):
    pass


def validate_config(cfg):
    if not isinstance(cfg["contact_coordination_enabled"], bool):
        raise ValueError("contact_coordination_enabled 必须是布尔开关")
    for name, (low, high) in LIMITS.items():
        value = cfg[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} 必须是 {low}–{high} 的整数")


def content_hash(text):
    return digest(" ".join(str(text).split()))


def day(now):
    return datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date().isoformat()


class ContactLedger:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS contact_attempts (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, source TEXT NOT NULL,
            kind TEXT NOT NULL, fingerprint TEXT NOT NULL, day TEXT NOT NULL,
            created REAL NOT NULL, dispatched REAL, finished REAL,
            status TEXT NOT NULL, message_id TEXT);
        CREATE INDEX IF NOT EXISTS contact_scope_day ON contact_attempts(scope,day,status);
        CREATE TABLE IF NOT EXISTS contact_owner_events (
            scope TEXT NOT NULL, event_id TEXT NOT NULL, received REAL NOT NULL,
            PRIMARY KEY(scope,event_id));
        """)

    def owner_message(self, scope, event_id, now=None):
        if event_id:
            self.db.execute("INSERT OR IGNORE INTO contact_owner_events VALUES(?,?,?)",
                            (scope, str(event_id), time.time() if now is None else now))

    def owner_time(self, scope):
        return self.db.execute("SELECT coalesce(max(received),0) FROM contact_owner_events WHERE scope=?",
                               (scope,)).fetchone()[0]

    def _expire(self, now):
        # A crash before dispatch may release capacity. A possible send cannot.
        self.db.execute("UPDATE contact_attempts SET status='cancelled',finished=created+600 "
                        "WHERE status='reserved' AND created<?", (now - 600,))
        self.db.execute("UPDATE contact_attempts SET status='uncertain',finished=dispatched+600 "
                        "WHERE status='dispatching' AND dispatched<?", (now - 600,))

    def _guard(self, scope, cfg, now, exclude=""):
        validate_config(cfg)
        if not cfg["contact_coordination_enabled"]:
            raise ContactBlocked("主动联系协调已关闭，本次安排取消")
        if (scope != cfg["owner_scope"] or not re.fullmatch(r"[^:]+:FriendMessage:\d+", scope)
                or scope.rsplit(":", 1)[-1] != str(cfg["owner_id"])):
            raise ContactBlocked("主动联系仅用于已配置的主人私聊")
        if cfg["dnd_ranges"] is None or quiet_now(cfg["dnd_ranges"], datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai"))):
            raise ContactBlocked("现在是免打扰时间")
        last_owner = self.owner_time(scope)
        if last_owner and now - last_owner < cfg["contact_owner_quiet_minutes"] * 60:
            raise ContactBlocked("主人刚在聊天，先留出空闲")
        rows = self.db.execute("SELECT * FROM contact_attempts WHERE scope=? AND id<>? "
            "AND status<>'cancelled' AND (day=? OR coalesce(finished,dispatched,created)>?)",
            (scope, exclude, day(now), now - 86400)).fetchall()
        today = [r for r in rows if r["day"] == day(now)]
        if len(today) >= cfg["contact_daily_limit"]:
            raise ContactBlocked("今天的主动联系额度已用完")
        latest = max((r["finished"] or r["dispatched"] or r["created"] for r in rows), default=0)
        if latest and now - latest < cfg["contact_cooldown_minutes"] * 60:
            raise ContactBlocked("刚主动联系过，先等一会儿")
        if sum((r["dispatched"] or r["created"]) > last_owner for r in today) >= cfg["contact_unanswered_limit"]:
            raise ContactBlocked("这几次还没有收到主人回复，今天先安静些")

    def check(self, scope, cfg, now=None):
        now = time.time() if now is None else now
        with self.store.transaction():
            self._expire(now)
            self._guard(scope, cfg, now)

    def reserve(self, scope, source, key, kind, fingerprint, cfg, now=None):
        now = time.time() if now is None else now
        if source not in {"life", "proactive_chat"} or kind not in {"thought", "screenshot", "work", "proposal", "chat"}:
            raise ValueError("unknown contact source or kind")
        if not isinstance(key, str) or not 1 <= len(key) <= 300 or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise ValueError("invalid contact key or fingerprint")
        ticket = digest([scope, source, key])
        with self.store.transaction():
            self._expire(now)
            old = self.db.execute("SELECT * FROM contact_attempts WHERE id=?", (ticket,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint or old["kind"] != kind:
                    raise ValueError("同一联系 ID 的内容不能改变")
                raise ContactBlocked("这次联系已处理或正在处理，不会重复发送")
            self._guard(scope, cfg, now)
            repeated = self.db.execute("SELECT 1 FROM contact_attempts WHERE scope=? AND fingerprint=? "
                "AND status<>'cancelled' AND (created>? OR status IN ('uncertain','dispatching'))",
                (scope, fingerprint, now - 86400)).fetchone()
            if repeated:
                raise ContactBlocked("相同内容已联系过或结果待核对，不会重发")
            self.db.execute("INSERT INTO contact_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ticket, scope, source, kind, fingerprint, day(now), now, None, None, "reserved", None))
        return ticket

    def dispatch(self, ticket, cfg, now=None):
        now = time.time() if now is None else now
        with self.store.transaction():
            row = self.db.execute("SELECT * FROM contact_attempts WHERE id=?", (ticket,)).fetchone()
            if not row or row["status"] not in {"reserved", "dispatching"} or now - row["created"] >= 600:
                raise ContactBlocked("联系安排已结束或过期")
            self._guard(row["scope"], cfg, now, ticket)
            self.db.execute("UPDATE contact_attempts SET status='dispatching',day=?,dispatched=coalesce(dispatched,?) WHERE id=?",
                            (day(now), now, ticket))

    def finish(self, ticket, outcome, message_id=None, now=None):
        if outcome not in {"sent", "reported", "uncertain", "cancelled", "rejected"}:
            raise ValueError("invalid contact outcome")
        now = time.time() if now is None else now
        with self.store.transaction():
            row = self.db.execute("SELECT * FROM contact_attempts WHERE id=?", (ticket,)).fetchone()
            if not row or row["status"] not in {"reserved", "dispatching"}:
                return
            if row["dispatched"] is None:
                outcome = "cancelled"
            elif outcome == "cancelled":
                outcome = "uncertain"
            mid = str(message_id) if message_id is not None else None
            if mid is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", mid):
                mid = None
            if outcome == "sent" and not mid:
                outcome = "uncertain"
            self.db.execute("UPDATE contact_attempts SET status=?,message_id=?,finished=? WHERE id=?",
                            (outcome, mid, now, ticket))

    def status(self, scope, cfg, now=None):
        now = time.time() if now is None else now
        rows = [dict(r) for r in self.db.execute("SELECT source,kind,day,created,dispatched,finished,status,message_id "
            "FROM contact_attempts WHERE scope=? ORDER BY created DESC,rowid DESC LIMIT 20", (scope,))]
        used = self.db.execute("SELECT count(*) FROM contact_attempts WHERE scope=? AND day=? AND status<>'cancelled'",
                               (scope, day(now))).fetchone()[0]
        return {"enabled": cfg["contact_coordination_enabled"], "day": day(now), "used": used,
                "limit": cfg["contact_daily_limit"], "last_owner_message": self.owner_time(scope), "recent": rows[:8],
                "meaning": "sent有消息ID；reported仅是社区发送器报告；uncertain不自动重发。"}


class ContactAPI:
    """Small in-process bridge; the community patch does not import this plugin."""
    version = 1

    def __init__(self, plugin):
        self.plugin, self.active = plugin, True

    def ledger(self):
        if not self.active:
            raise ContactBlocked("联系协调器正在重载")
        return self.plugin.contact_ledger()

    def check(self, scope):
        self.ledger().check(scope, self.plugin.cfg())

    def reserve(self, scope, key, text):
        return self.ledger().reserve(scope, "proactive_chat", key, "chat", content_hash(text), self.plugin.cfg())

    def dispatch(self, ticket):
        self.ledger().dispatch(ticket, self.plugin.cfg())

    def finish(self, ticket, outcome):
        self.ledger().finish(ticket, outcome)

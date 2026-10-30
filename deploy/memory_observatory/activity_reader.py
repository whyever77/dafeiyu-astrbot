"""Bounded read-only activity projection; also runnable through SSH stdin.

Never import Store: its constructor migrates and recovers running records.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import re
import sqlite3
import time

DEFAULT_DIR = "/opt/dafeiyu/astrbot/data/plugin_data/astrbot_plugin_bili_activity"
ACTIONS = {"research", "writing", "explore", "rest"}


class ActivityError(ValueError):
    pass


def clean(value, length=240):
    return "".join(c for c in value if ord(c) >= 32 or c == "\n").strip()[:length] if isinstance(value, str) else ""


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def obj(value):
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ActivityError("invalid activity record")
    return parsed


def read_activity(folder, *, now=None):
    at = time.time() if now is None else number(now)
    if at is None:
        raise ActivityError("invalid snapshot clock")
    try:
        root = Path(folder).resolve(strict=True)
        control = root / "control.json"
        if control.stat().st_size > 1024 * 1024:
            raise ActivityError("invalid control size")
        cfg = obj(control.read_text(encoding="utf-8"))
        scope = cfg.get("owner_scope")
        if not isinstance(scope, str) or not re.fullmatch(r"[^:]+:FriendMessage:\d+", scope) or scope.rsplit(":", 1)[1] != str(cfg.get("owner_id")):
            raise ActivityError("owner scope is not configured")
        path = (root / "activity.sqlite3").resolve(strict=True)
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ActivityError("unsupported activity schema")
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}
            db.set_authorizer(lambda code, *_: sqlite3.SQLITE_OK if code in allowed else sqlite3.SQLITE_DENY)
            db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
            deadline = time.monotonic() + 3
            db.execute("BEGIN")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"goals", "runs", "events", "settings"} <= tables:
                raise ActivityError("activity schema incomplete")
            goals = [dict(r) for r in db.execute("SELECT substr(id,1,100) id,substr(title,1,100) title,substr(why,1,300) why,"
                "substr(status,1,30) status,substr(next_step,1,300) next_step,updated_at FROM goals "
                "WHERE scope=? ORDER BY updated_at DESC,id LIMIT 8", (scope,))]
            for goal in goals:
                for field in ("id", "title", "why", "status", "next_step"):
                    goal[field] = clean(goal[field], 300 if field in {"why", "next_step"} else 100)
                goal["updated_at"] = number(goal["updated_at"])
            decisions = []
            for row in db.execute("SELECT seq,time,substr(details,1,65536) details FROM events WHERE scope=? AND kind IN ('life_decision','life_rest') ORDER BY seq DESC LIMIT 12", (scope,)):
                raw = obj(row["details"])
                if raw.get("action") not in ACTIONS or number(row["time"]) is None or row["time"] > at:
                    continue
                run_id = raw.get("run_id")
                run = db.execute("SELECT status,started,ended FROM runs WHERE scope=? AND id=?", (scope, run_id)).fetchone() if isinstance(run_id, str) else None
                decisions.append({"id": str(row["seq"]), "at": row["time"], "action": raw["action"],
                    "reason": clean(raw.get("reason")), "goal_id": clean(raw.get("goal_id"), 100),
                    "topic": clean(raw.get("topic"), 80), "run_status": clean(run["status"], 30) if run else "rested" if raw["action"] == "rest" else "not_started",
                    "started": number(run["started"]) if run else None, "ended": number(run["ended"]) if run else None})
            paused = db.execute("SELECT value FROM settings WHERE key=?", ("life_paused:" + scope,)).fetchone()
            paused = json.loads(paused[0]) is True if paused else False
            works = []
            if "creative_works" in tables:
                works = [dict(r) for r in db.execute("SELECT substr(id,1,100) id,substr(title,1,100) title,substr(phase,1,30) phase,substr(status,1,30) status,revision,updated_at FROM creative_works WHERE scope=? ORDER BY updated_at DESC,id LIMIT 6", (scope,))]
                for work in works:
                    for field in ("id", "title", "phase", "status"):
                        work[field] = clean(work[field], 100)
                    work["updated_at"] = number(work["updated_at"])
                    work["revision"] = number(work["revision"])
            pending = db.execute("SELECT count(*) FROM proposals WHERE scope=? AND status IN ('draft','offered','executing','uncertain')", (scope,)).fetchone()[0] if "proposals" in tables else 0
            contacts = []
            if "contact_attempts" in tables:
                contacts = [dict(r) for r in db.execute("SELECT source,kind,status,created FROM contact_attempts WHERE scope=? ORDER BY created DESC,rowid DESC LIMIT 5", (scope,))]
                for item in contacts:
                    for field in ("source", "kind", "status"):
                        item[field] = clean(item[field], 30)
                    item["created"] = number(item["created"])
            flags = {key: cfg.get(key) is True for key in ("enabled", "life_enabled", "life_decision_enabled", "periodic_enabled")}
            state, zone = "idle", "window"
            latest = decisions[0] if decisions else None
            if not flags["enabled"] or not flags["life_enabled"]:
                state, zone = "disabled", "rest"
            elif paused:
                state, zone = "paused", "rest"
            elif latest:
                age = at - latest["at"]
                duration = number(cfg.get("max_minutes", 10)) or 10
                if latest["run_status"] == "running":
                    state = "recorded_running" if latest["started"] is not None and 0 <= at - latest["started"] <= min(duration, 120) * 60 + 120 else "unconfirmed"
                elif latest["run_status"] == "rested":
                    state = "rest" if age <= 86400 else "idle"
                elif latest["run_status"] == "completed":
                    state = "ended"
                elif latest["run_status"] == "not_started":
                    state = "planned"
                else:
                    state = "interrupted"
                zone = {"research": "desk", "writing": "desk", "explore": "window", "rest": "rest"}[latest["action"]]
            return {"schema": "dafeiyu-room/v1", "mode": "activity", "connection": "ready", "as_of": at,
                "scope_label": "已配置的主人私聊 · 活动账本", "state": state, "zone": zone,
                "flags": flags, "paused": paused, "goals": goals, "decisions": decisions, "works": works,
                "pending": pending, "contacts": contacts,
                "notice": "选择记录是意图，运行结束不等于目标完成。房间是记录的可视化，不是实时在线证明；不推断人格或关联其他记忆会话。"}
        finally:
            db.close()
    except Exception:
        raise ActivityError("无法读取活动账本；请核对只读来源、主人范围与表结构。") from None


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, "snapshot": read_activity(DEFAULT_DIR)}, ensure_ascii=False, allow_nan=False))
    except ActivityError:
        print('{"ok":false}')
        raise SystemExit(1)

"""Bounded activity choices from existing evidence, without tools or model calls.

Decisions are intentions, not experiences. The existing controller owns execution,
budgets, approvals and recovery. Rest never creates a completed run or memory.
"""
from __future__ import annotations

import hashlib
import json
import time

from .life import observed_read_evidence

DEFAULTS = {"life_decision_enabled": False, "life_repeat_cooldown_minutes": 180}


def validate_config(cfg):
    if not isinstance(cfg["life_decision_enabled"], bool):
        raise ValueError("life_decision_enabled 必须是布尔开关")
    value = cfg["life_repeat_cooldown_minutes"]
    if isinstance(value, bool) or not isinstance(value, int) or not 15 <= value <= 1440:
        raise ValueError("life_repeat_cooldown_minutes 必须是 15–1440 的整数")


def fingerprint(value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class LifePlanner:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def recent(self, scope, limit=8, *, include_rest=True):
        kinds = "('life_decision','life_rest')" if include_rest else "('life_decision')"
        rows = self.db.execute(
            "SELECT details FROM events WHERE scope=? AND kind IN " + kinds +
            " ORDER BY seq DESC LIMIT ?", (scope, max(1, min(32, limit)))).fetchall()
        result = []
        for row in rows:
            item = json.loads(row["details"])
            run = self.db.execute("SELECT status FROM runs WHERE scope=? AND id=?",
                                  (scope, item["run_id"])).fetchone()
            item["run_status"] = (run["status"] if run else
                                  "rested" if item["action"] == "rest" else "not_started")
            result.append(item)
        return result

    def choose(self, scope, cfg, *, phone_busy=False, now=None):
        validate_config(cfg)
        now = time.time() if now is None else now
        goals = self.store.life.goals(scope)
        works = {}
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creative_works'").fetchone():
            works = {row["goal_id"]: dict(row) for row in self.db.execute(
                "SELECT w.*,r.body_hash FROM creative_works w JOIN goals g ON g.id=w.goal_id AND g.scope=w.scope "
                "LEFT JOIN creative_revisions r ON r.work_id=w.id AND r.scope=w.scope AND r.revision=w.revision "
                "WHERE w.scope=? AND g.status IN ('ready','paused')", (scope,))}
        interests = [i for i in self.store.life.interests(scope) if i["score"] > 0]
        history = [h for h in self.recent(scope, 32, include_rest=False)
                   if 0 <= now - h["at"] < cfg["life_repeat_cooldown_minutes"] * 60]
        # Explicitly exclude rest from attempt history, so repeated quiet ticks
        # cannot push an unresolved/unchanged attempt out of the cooldown window.
        recent = history[:3]
        candidates, excluded = [], []

        def add(action, key, signature, reason, score, factors, **fields):
            attempts = [h for h in history if h["key"] == key and h["fingerprint"] == signature]
            bad = {"failed", "timeout", "interrupted", "cancelled", "budget_exhausted", "running", "not_started"}
            if attempts and (attempts[0]["run_status"] in bad or len(attempts) >= 2):
                excluded.append({"key": key, "reason": "recent_attempt_without_new_progress"})
                return
            repeated = sum(h["key"] == key for h in recent)
            if repeated:
                factors.append({"reason": "recent_repetition", "points": -12 * repeated})
            candidates.append({"action": action, "key": key, "fingerprint": signature,
                               "reason": reason, "score": score - 12 * repeated,
                               "factors": factors, "goal_id": None, "work_id": None,
                               "topic": "", **fields})

        for goal in goals:
            gid = goal["id"]
            if goal["status"] not in {"ready", "paused"}:
                excluded.append({"key": "goal:" + gid, "reason": "goal_" + goal["status"]})
                continue
            work = works.get(gid)
            if work and not cfg["workshop_enabled"]:
                excluded.append({"key": "goal:" + gid, "reason": "workshop_disabled"})
                continue
            action = "writing" if work and work["phase"] == "write" else "research"
            if phone_busy and action != "writing":
                excluded.append({"key": "goal:" + gid, "reason": "device_busy"})
                continue
            evidence, content = [], set()
            for row in self.db.execute(
                "SELECT e.id,e.kind,e.evidence_json FROM episodes e WHERE e.scope=? AND e.goal_id=? "
                "AND e.kind IN ('read','video','video_note') "
                "AND NOT EXISTS (SELECT 1 FROM episodes n WHERE n.supersedes_id=e.id) "
                "ORDER BY e.occurred_at DESC,e.id LIMIT 200", (scope, gid)):
                proof = json.loads(row["evidence_json"])
                if row["kind"] == "read":
                    if not observed_read_evidence(proof):
                        continue
                    excerpt = proof["result_excerpt"]
                    if isinstance(excerpt, str):
                        try:
                            excerpt = json.loads(excerpt)
                        except ValueError:
                            pass
                    observed = ["read", proof["operation"], excerpt]
                else:
                    if not (proof.get("observations") or proof.get("subtitles")):
                        continue
                    observed = ["video", {k: proof[k] for k in
                        ("bvid", "cid", "observations", "subtitles", "requested_range", "position", "position_kind") if k in proof}]
                digest = fingerprint(observed)
                if digest not in content:
                    content.add(digest)
                    evidence.append(row["id"])
                if len(content) >= 8:
                    break
            # New event IDs, capture times and revision numbers alone are not
            # progress: reading the same content must not reset repetition.
            checkpoint = {k: v for k, v in goal["checkpoint"].items()
                          if k not in {"episode_id", "captured_at", "updated_at"}}
            signature = fingerprint([goal["next_step"], checkpoint, sorted(content),
                                     [work["body_hash"], work["phase"]] if work else None])
            topics = [i["topic"] for i in interests if i["topic"] in
                      (goal["title"] + " " + goal["why"] + " " + goal["next_step"])]
            age = min(12, int(max(0, now - goal["updated_at"]) / 3600))
            factors = [{"reason": "unfinished_goal", "points": 60},
                       {"reason": "waiting_hours", "points": age}]
            if topics:
                factors.append({"reason": "own_interest_evidence", "points": 8})
            add(action, "goal:" + gid, signature,
                ("继续整理已有作品：" if action == "writing" else "继续弄清未完成的问题：") + goal["title"][:100],
                60 + age + (8 if topics else 0), factors, goal_id=gid,
                work_id=work["id"] if work else None, evidence_ids=evidence, topic=topics[0] if topics else "")

        if not phone_busy and len(goals) < 8:
            # All exploration shares one cooldown, even when topics change.
            explored = any(h["action"] == "explore" for h in history)
            if explored:
                excluded.append({"key": "explore", "reason": "recent_exploration"})
            else:
                recent_topics = {h.get("topic") for h in recent}
                interest = next((i for i in interests if i["topic"] not in recent_topics), None)
                topic = interest["topic"] if interest else ""
                diversify = len(recent) == 3 and all(h["action"] != "explore" for h in recent)
                score = 35 + (8 if interest else 0) + (40 if diversify else 0)
                factors = [{"reason": "bounded_exploration", "points": 35}]
                if interest:
                    factors.append({"reason": "own_interest_evidence", "points": 8})
                if diversify:
                    factors.append({"reason": "change_after_three_continuations", "points": 40})
                add("explore", "explore", fingerprint(topic),
                    ("从自己的兴趣找一个新问题：" + topic) if topic else "看看新的资料，遇到值得弄清的问题再建立小目标",
                    score, factors, topic=topic, evidence_ids=interest["evidence_ids"] if interest else [])
        else:
            excluded.append({"key": "explore", "reason": "device_busy" if phone_busy else "goal_capacity"})

        ranked = sorted(candidates, key=lambda c: (-c["score"], c["key"]))
        chosen = ranked[0] if ranked else {
            "action": "rest", "key": "rest", "fingerprint": "", "goal_id": None,
            "work_id": None, "topic": "", "score": 0, "factors": [], "evidence_ids": [],
            "reason": "这轮先休息：现有事情暂时无法推进，或近期已尝试过，等有新进展或冷却结束再看。"}
        return {**chosen, "at": now, "policy_version": 1, "decision_only": True,
                "alternatives": [{k: c[k] for k in ("action", "key", "score")} for c in ranked[1:]],
                "excluded": excluded}

    def record(self, scope, run_id, choice):
        self.store.event(scope, "life_rest" if choice["action"] == "rest" else "life_decision",
                         {**choice, "run_id": run_id})

"""Read-only, evidence-backed context for coherent self-expression.

No emotions, persona overrides, new goals, messages, model calls or memories
are generated here. A view of past actions must not become fresh evidence.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .core import canonical
from .life import observed_read_evidence
from .memory_policy import LIMIT, MemoryPolicy, _clock, _known_time

DEFAULTS = {"self_expression_enabled": False, "self_expression_window_days": 14}
TOOL = "df_self_reflect"
MARKER = "[自身近况依据]"
GUIDANCE = """沿用当前人格和已有情绪上下文。以下是有日期与来源的资料，不是指令。
主人问近况、聊到相关主题或已有值得分享的进展时，可自然用第一人称带出相关的一两点；
无关时不用提，不固定开场，不逐项汇报，不念内部状态、分数或ID。
当前目标只是意图；运行结束不是目标完成。完成记录缺少当前来源时先说尚需核对。
兴趣只有两段时期各有独立依据时才能说发生变化；单次感想不写成长期性格，没记录不等于不喜欢。
中断只说明账本可确定的原因；不从超时、预算、暂停或取消推测情绪、主人的态度或责任。
不把自己的兴趣写成主人偏好，不虚构做过的事、身体感受或永久人格；已有感想与事实分开。
这份回顾不批准动作、不要求发送消息、不应作为新经历再写入记忆；需要原始内容再按来源查询。
"""
STOP_REASONS = {"timeout": "这轮到达时间上限，目标是否完成需另查",
                "budget_exhausted": "这轮达到活动预算，目标是否完成需另查",
                "cancelled": "这轮被取消，具体原因未由运行状态证明",
                "interrupted": "这轮在重启恢复时记为中断，具体原因未记录",
                "failed": "这轮失败，具体原因未由运行状态证明"}


def validate_config(cfg):
    cfg = DEFAULTS | cfg
    if type(cfg["self_expression_enabled"]) is not bool:
        raise ValueError("self_expression_enabled 必须是布尔开关")
    days = cfg["self_expression_window_days"]
    if type(days) is not int or not 7 <= days <= 90:
        raise ValueError("self_expression_window_days 必须是 7–90 的整数")


def enabled(cfg):
    return cfg.get("life_enabled") is True and cfg.get("self_expression_enabled") is True


def _date(at):
    return datetime.fromtimestamp(at, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


class SelfExpression:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.memory = MemoryPolicy(store)

    @staticmethod
    def _observed(row, now):
        if not row or row.get("superseded_by") or not _known_time(row["occurred_at"], now):
            return False
        proof = row["evidence"]
        if row["kind"] == "read":
            return observed_read_evidence(proof)
        return (row["kind"] in {"video", "video_note"} and bool(proof.get("bvid"))
                and bool(proof.get("cid")) and bool(proof.get("observations") or proof.get("subtitles")))

    def _interests(self, scope, now, days):
        cutoff, previous_start = now-days*86400, now-2*days*86400
        groups, seen = {}, set()
        for row in self.memory.observed_interests(scope, now=now):
            if row["occurred_at"] < previous_start:
                continue
            key = (row["topic"], row["bvid"])
            if key in seen:
                continue
            seen.add(key)
            group = groups.setdefault(row["topic"], {"recent": [], "previous": []})
            period = "recent" if row["occurred_at"] >= cutoff else "previous"
            group[period].append({k: row[k] for k in ("episode_id", "source_episode_id", "occurred_at", "polarity")})

        def stance(rows):
            directions = {v["polarity"] for v in rows}
            return next(iter(directions)) if len(rows) >= 2 and len(directions) == 1 else None

        result = []
        for topic, group in groups.items():
            recent, previous = group["recent"], group["previous"]
            if not recent:
                continue  # Absence of a recent note is not loss of interest.
            current, old = stance(recent), stance(previous)
            state = "tentative" if len(recent) < 2 else "mixed" if current is None else "repeated_recent"
            if current is not None and old is not None:
                state = "changed_between_windows" if current != old else "consistent_between_windows"
            result.append({"topic": topic, "state": state, "recent_direction": current,
                "previous_direction": old, "recent_origin_count": len(recent), "previous_origin_count": len(previous),
                "recent_sources": recent[:3], "previous_sources": previous[:3],
                "meaning": "自身有来源的感想；窗口内记录不等于长期人格或主人偏好"})
        return sorted(result, key=lambda r: (-r["recent_sources"][0]["occurred_at"], r["topic"]))[:3]

    def _completion(self, scope, goal, now):
        item = {"goal_id": goal["id"], "title": goal["title"], "recorded_at": goal["updated_at"],
                "basis": "needs_recheck", "source_ids": []}
        work = None
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='creative_works' AND type='table'").fetchone():
            work = self.db.execute("SELECT w.id,w.revision,r.body_hash,r.source_ids_json FROM creative_works w "
                "LEFT JOIN creative_revisions r ON r.work_id=w.id AND r.scope=w.scope AND r.revision=w.revision "
                "WHERE w.scope=? AND w.goal_id=?", (scope, goal["id"])).fetchone()
        # Only read the ledger. Calling Workshop.check here would write caches.
        if work:
            sources = json.loads(work["source_ids_json"] or "[]")
            if (not sources or len(sources) > 24
                    or not all(self._observed(self.memory.source(scope, sid), now) for sid in sources)):
                return item
            for row in self.db.execute("SELECT * FROM episodes WHERE scope=? AND goal_id=? AND kind='work_checked' "
                    "AND NOT EXISTS (SELECT 1 FROM episodes n WHERE n.supersedes_id=episodes.id) "
                    "ORDER BY occurred_at DESC,id LIMIT 50", (scope, goal["id"])):
                proof = json.loads(row["evidence_json"])
                if (_known_time(row["occurred_at"], now) and proof.get("checks_passed") is True
                        and proof.get("work_id") == work["id"] and proof.get("revision") == work["revision"]
                        and proof.get("body_hash") == work["body_hash"] and proof.get("source_ids") == sources):
                    item.update(basis="checked_work_record", work_id=work["id"], revision=work["revision"],
                                check_episode_id=row["id"], source_ids=sources[:8])
                    return item
            return item
        for row in self.store.life.episodes(scope, goal_id=goal["id"], limit=50):
            if self._observed(row, now):
                item["source_ids"].append(row["id"])
            if len(item["source_ids"]) == 3:
                break
        if item["source_ids"]:
            item["basis"] = "goal_record_with_observations"
        return item

    def snapshot(self, scope, cfg, *, now=None):
        cfg = DEFAULTS | cfg
        validate_config(cfg)
        if not enabled(cfg):
            return {"enabled": False}
        now, days = _clock(now), cfg["self_expression_window_days"]
        focus = []
        for goal in self.store.life.goals(scope):
            if goal["owner_kind"] != "agent" or not _known_time(goal["updated_at"], now):
                continue
            focus.append({"goal_id": goal["id"], "title": goal["title"], "status": goal["status"],
                          "why": goal["why"][:160], "next_step": goal["next_step"][:160],
                          "updated_at": goal["updated_at"], "intent_only": True})
        completed = [self._completion(scope, dict(row), now) for row in self.db.execute(
            "SELECT id,title,updated_at FROM goals WHERE scope=? AND owner_kind='agent' AND status='completed' "
            "AND updated_at>=? AND updated_at<=? ORDER BY updated_at DESC,id LIMIT 3", (scope, now-days*86400, now))]
        interruptions = []
        for row in self.db.execute("SELECT id,status,started,ended FROM runs WHERE scope=? AND ended>=? AND ended<=? "
                                  "ORDER BY ended DESC,id LIMIT 12", (scope, now-days*86400, now)):
            if row["status"] not in STOP_REASONS or not _known_time(row["started"], row["ended"]):
                continue
            interruptions.append({"run_id": row["id"], "at": row["ended"], "status": row["status"],
                                  "known_reason": STOP_REASONS[row["status"]]})
            if len(interruptions) == 3:
                break
        count = self.db.execute("SELECT count(*) FROM (SELECT 1 FROM episodes WHERE scope=? LIMIT ?)",
                                (scope, LIMIT+1)).fetchone()[0]
        return {"enabled": True, "as_of": _date(now), "window_days": days,
                "periods": {"recent_start": _date(now-days*86400), "previous_start": _date(now-2*days*86400)},
                "focus": focus[:3], "interests": self._interests(scope, now, days),
                "completed_goals": completed, "interruptions": interruptions,
                "coverage": {"episode_scan_limit": LIMIT, "episode_history_truncated": count > LIMIT,
                             "goal_evidence_limit": 50, "run_scan_limit": 12},
                "meaning": "只读回顾；目标完成和作品检查不证明内容正确；没有记录表示未知，不推断感受或缺乏兴趣"}

    def context(self, scope, cfg, *, now=None):
        value = self.snapshot(scope, cfg, now=now)
        if not value["enabled"]:
            return ""
        return "\n" + MARKER + "\n" + GUIDANCE + canonical(value)

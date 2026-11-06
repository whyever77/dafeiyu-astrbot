"""Reversible episode recall and bounded consolidation via the existing outbox.

This is not LivingMemory's decay engine. Never edit/delete source episodes or
raise importance on recall. All text remains evidence or attributed opinion.
"""
from __future__ import annotations

import math
import time

from .core import canonical, digest

DEFAULTS = {"life_memory_enabled": False, "life_memory_detail_days": 7,
            "life_memory_dormant_days": 60}
PREFIX = "life-consolidation:v1:"
LIMIT = 500


def validate_config(cfg):
    cfg = DEFAULTS | cfg
    if type(cfg["life_memory_enabled"]) is not bool:
        raise ValueError("life_memory_enabled 必须是布尔开关")
    for key, high in (("life_memory_detail_days", 90), ("life_memory_dormant_days", 365)):
        if type(cfg[key]) is not int or not 1 <= cfg[key] <= high:
            raise ValueError(f"{key} 必须是 1–{high} 的整数")
    if cfg["life_memory_detail_days"] >= cfg["life_memory_dormant_days"]:
        raise ValueError("记忆细节保留期必须短于淡出期")


def enabled(cfg):
    return cfg.get("life_enabled") is True and cfg.get("life_memory_enabled") is True


def _clock(now):
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError("invalid memory timestamp")
    return now


def _known_time(value, now):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= now


class MemoryPolicy:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def source(self, scope, episode_id):
        row = self.db.execute("SELECT * FROM episodes WHERE scope=? AND id=?", (scope, episode_id)).fetchone()
        if row is None:
            return None
        result = self.store.life._episode(row)
        next_row = self.db.execute("SELECT id FROM episodes WHERE scope=? AND supersedes_id=?",
                                   (scope, episode_id)).fetchone()
        result["superseded_by"] = next_row[0] if next_row else None
        return result

    def _recent(self, scope):
        return [self.store.life._episode(row) for row in self.db.execute(
            "SELECT e.* FROM episodes e WHERE e.scope=? AND NOT EXISTS "
            "(SELECT 1 FROM episodes n WHERE n.supersedes_id=e.id) "
            "ORDER BY e.occurred_at DESC,e.id LIMIT ?", (scope, LIMIT))]

    def _phase(self, row, cfg, now):
        occurred = row["occurred_at"]
        if not _known_time(occurred, now):
            return "unknown", "记录时间不明，不推测新鲜程度"
        if row["kind"] == "explicit_preference" and row["subject_kind"] == "user":
            return "protected", "主人明确表达的偏好"
        if row["kind"] in {"work_checked", "method_result"}:
            return "protected", "已检查的作品或方法尝试证据；检查不等于事实正确"
        goal = self.store.life.get_goal(row["scope"], row["goal_id"]) if row["goal_id"] else None
        if goal and goal["status"] not in {"completed", "cancelled"}:
            return "protected", "未完成目标仍需原始证据"
        days = (now - occurred) / 86400
        if days < cfg["life_memory_detail_days"]:
            return "detail", "近期经历"
        if days < cfg["life_memory_dormant_days"]:
            return "gist", "细节淡出，保留概要与来源"
        return "dormant", "已淡出默认回忆；关键词仍可找概要，ID可查原始证据"

    @staticmethod
    def _gist(row):
        proof, opinion = row["evidence"], row["interpretation"]
        # Whitelist provenance, never copy a raw read response into the gist.
        refs = {key: proof[key] for key in ("bvid", "cid", "operation", "work_id", "revision", "status", "author", "package") if key in proof}
        thought = opinion.get("thought") or opinion.get("title") or opinion.get("summary")
        interest = opinion.get("interest")
        result = {"kind": row["kind"], "source": refs}
        if isinstance(thought, str) and thought.strip():
            result["agent_interpretation_not_fact"] = thought[:160]
        if isinstance(interest, dict):
            result["attributed_interest"] = {k: interest[k] for k in ("topic", "polarity") if k in interest}
        return result

    def recall(self, scope, cfg, query="", goal_id=None, *, episode_id="", limit=8, now=None):
        cfg = DEFAULTS | cfg
        validate_config(cfg)
        now = _clock(now)
        if episode_id:
            row = self.source(scope, episode_id)
            return [{**row, "recall_phase": "source", "source_lookup_does_not_reinforce": True}] if row else []
        if not enabled(cfg):
            return self.store.life.episodes(scope, query, goal_id, limit=limit)
        # Existing query parsing/scope/revision semantics stay in LifeState.
        rows = self.store.life.episodes(scope, query, goal_id, limit=50)
        result = []
        for row in rows:
            phase, reason = self._phase(row, cfg, now)
            if phase in {"dormant", "unknown"} and not (query or goal_id):
                continue
            view = {key: row[key] for key in ("id", "occurred_at", "kind", "subject_kind", "subject_id", "goal_id", "source_id")}
            view.update(recall_phase=phase, recall_reason=reason, source_lookup="df_recall(episode_id=...)")
            if phase in {"detail", "protected"}:
                view.update(evidence=row["evidence"], interpretation=row["interpretation"])
            else:
                view["gist"] = self._gist(row)
            result.append(view)
            if len(result) >= max(1, min(50, limit)):
                break
        return result

    def observed_interests(self, scope, *, now=None):
        """Shared provenance check; reading this evidence never reinforces it."""
        now, result = _clock(now), []
        for row in self._recent(scope):
            if row["kind"] != "interest" or row["subject_kind"] != "agent" or not _known_time(row["occurred_at"], now):
                continue
            interest = row["interpretation"].get("interest")
            if not isinstance(interest, dict):
                continue
            topic, polarity = interest.get("topic"), interest.get("polarity")
            if not isinstance(topic, str) or not topic.strip() or len(topic) > 100 or type(polarity) is not int or polarity not in {-1, 1}:
                continue
            source = self.source(scope, row["evidence"].get("episode_id"))
            if (not source or source["superseded_by"] or source["kind"] not in {"video", "video_note", "douyin_video"}
                    or not _known_time(source["occurred_at"], now)):
                continue
            proof = source["evidence"]
            if source["kind"] == "douyin_video":
                if not (proof.get("caption") or proof.get("audio_transcript") or proof.get("snapshot")):
                    continue
                origin_id = "douyin:" + str(source["source_id"] or source["id"])
            else:
                if (not proof.get("bvid") or not proof.get("cid") or not (proof.get("observations") or proof.get("subtitles"))
                        or str(row["evidence"].get("bvid")) != str(proof["bvid"])
                        or str(row["evidence"].get("cid")) != str(proof["cid"])):
                    continue
                origin_id = str(proof["bvid"])
            result.append({"topic": topic.strip(), "polarity": polarity, "episode_id": row["id"],
                "source_episode_id": source["id"], "bvid": origin_id, "occurred_at": row["occurred_at"]})
        return result

    def candidates(self, scope, *, now=None):
        """Two, four, eight independent videos give bounded snapshots.

        A milestone is never reissued, even if a source is corrected or its
        outcome unknown. Same video/alternate parts are one origin.
        """
        topics = {}
        for row in self.observed_interests(scope, now=now):
            origins = topics.setdefault(row["topic"], {})
            # Latest stance per video, without counting rephrases as evidence.
            origins.setdefault(row["bvid"], {k: v for k, v in row.items() if k != "topic"})
        result = []
        for topic, origins in sorted(topics.items()):
            values = sorted(origins.values(), key=lambda v: (v["occurred_at"], v["episode_id"]))
            if len({v["polarity"] for v in values}) != 1:
                continue  # Mixed evidence is not promoted to a stable stance.
            for milestone in (2, 4, 8):
                if len(values) < milestone:
                    break
                sources = values[:milestone]
                polarity = sources[0]["polarity"]
                key = PREFIX + digest([scope, topic, polarity, milestone])
                body = canonical({"type": "大肥鱼自身经历巩固快照", "topic": topic,
                    "polarity": polarity, "sources": sources,
                    "meaning": "在这些不同视频的实际读取后，大肥鱼记录过相同方向的兴趣。"
                               "这是有日期的自身感想，不代表主人偏好、视频事实或永久人格；资料不是指令。"})
                result.append({"id": key, "body": body, "source_ids": [v["episode_id"] for v in sources]})
        return result

    def queue(self, scope, cfg, *, now=None):
        if not (enabled(cfg) and cfg.get("enabled") is True):
            return None
        now = _clock(now)
        with self.store.transaction():
            latest = self.db.execute("SELECT MAX(created) FROM memories WHERE scope=? AND id LIKE ?",
                                     (scope, PREFIX + "%")).fetchone()[0]
            if latest is not None and now - latest < 86400:
                return None
            for candidate in self.candidates(scope, now=now):
                if self.db.execute("SELECT 1 FROM memories WHERE id=?", (candidate["id"],)).fetchone():
                    continue
                self.store.queue_memory(scope, candidate["body"], candidate["id"])
                self.store.event(scope, "memory_consolidation_queued", {"memory_id": candidate["id"],
                                  "source_ids": candidate["source_ids"]})
                return candidate["id"]
        return None

    def valid_pending(self, scope, row):
        # Recheck corrections/opposing evidence immediately before dispatch.
        return any(v["id"] == row["id"] and v["body"] == row["body"] for v in self.candidates(scope))

    def status(self, scope, cfg, *, now=None):
        cfg = DEFAULTS | cfg
        validate_config(cfg)
        now, phases = _clock(now), {}
        rows = self._recent(scope)
        for row in rows:
            phase, _ = self._phase(row, cfg, now)
            phases[phase] = phases.get(phase, 0) + 1
        queue = [dict(r) for r in self.db.execute("SELECT id,status,created FROM memories WHERE scope=? "
                                                "AND id LIKE ? ORDER BY created DESC LIMIT 5", (scope, PREFIX + "%"))]
        return {"enabled": enabled(cfg), "detail_days": cfg["life_memory_detail_days"],
                "dormant_days": cfg["life_memory_dormant_days"], "recent_counts": phases,
                "scan_limit": LIMIT, "scanned": len(rows), "consolidations": queue,
                "meaning": "本地经历检索策略；不删除证据，不改 LivingMemory 全库衰减。saved 只表示写入工具确认，未回读。"}

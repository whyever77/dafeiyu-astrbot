"""Bounded activity choices from existing evidence, without tools or model calls.

Decisions are intentions, not experiences. The existing controller owns execution,
budgets, approvals and recovery. Rest never creates a completed run or memory.
"""
from __future__ import annotations

import hashlib
import json
import math
import time

from .life import observed_read_evidence, parse_work_sections
from .rhythm import DEFAULTS as RHYTHM_DEFAULTS, snapshot as rhythm_snapshot, validate_config as validate_rhythm
from .projects import Projects, enabled as projects_enabled

DEFAULTS = {**RHYTHM_DEFAULTS, "life_decision_enabled": False, "life_repeat_cooldown_minutes": 180}


def _known_time(at, now):
    return type(at) in {int, float} and math.isfinite(at) and 0 < at <= now


def validate_config(cfg):
    validate_rhythm(cfg)
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

    def rhythm(self, scope, cfg, *, now=None):
        return rhythm_snapshot(self.db, scope, cfg, now=now)

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

    def _observed_source(self, scope, episode_id, now):
        if not isinstance(episode_id, str) or not episode_id:
            return False
        row = self.db.execute(
            "SELECT kind,occurred_at,evidence_json FROM episodes e WHERE e.scope=? AND e.id=? "
            "AND NOT EXISTS (SELECT 1 FROM episodes n WHERE n.supersedes_id=e.id)",
            (scope, episode_id),
        ).fetchone()
        if not row or not _known_time(row["occurred_at"], now):
            return False
        try:
            proof = json.loads(row["evidence_json"])
        except (ValueError, TypeError):
            return False
        if row["kind"] == "read":
            return observed_read_evidence(proof)
        if row["kind"] == "douyin_video":
            return bool(proof.get("caption") or proof.get("audio_transcript") or proof.get("snapshot"))
        if row["kind"] == "phone_observation":
            return bool(
                proof.get("package")
                and (proof.get("visible_texts") or proof.get("audio_transcript") or proof.get("snapshot"))
            )
        return (
            row["kind"] in {"video", "video_note"}
            and bool(proof.get("bvid"))
            and bool(proof.get("cid"))
            and bool(proof.get("observations") or proof.get("subtitles"))
        )

    def _curiosity_threads(self, scope, cfg, now, all_goal_titles, recent_topics):
        cutoff = now - 30 * 86400
        threads, seen = [], set()
        if projects_enabled(cfg):
            projects = Projects(self.store)
            try:
                ready = projects.ready(create=False)
            except RuntimeError:
                ready = False
            if ready:
                rows = self.db.execute(
                    "SELECT id,updated FROM life_projects WHERE scope=? AND status='completed' "
                    "ORDER BY updated DESC,id LIMIT 3",
                    (scope,),
                ).fetchall()
                for row in rows:
                    if not _known_time(row["updated"], now) or row["updated"] < cutoff:
                        continue
                    try:
                        project = projects.read(scope, row["id"], cfg)
                    except (ValueError, RuntimeError):
                        continue
                    stages = project.get("stages") or []
                    if not stages:
                        continue
                    head = stages[-1]
                    review = head.get("review")
                    work = head.get("work") or {}
                    source_ids = work.get("source_ids") or []
                    q = (review.get("next_question") or "").strip() if review else ""
                    if (not head.get("portfolio_valid") or not review or not q
                            or not _known_time(review.get("created"), now) or review["created"] < cutoff
                            or not source_ids
                            or not all(self._observed_source(scope, sid, now) for sid in source_ids)
                            or q in recent_topics or q.casefold() in all_goal_titles or q.casefold() in seen):
                        continue
                    seen.add(q.casefold())
                    threads.append({
                        "kind": "project_followup",
                        "project_id": project["id"],
                        "stage": head["position"],
                        "title": project["title"],
                        "question": q[:160],
                        "source_ids": list(source_ids)[:8],
                        "subject_kind": "agent",
                    })
        has_works = bool(
            self.db.execute("SELECT 1 FROM sqlite_master WHERE name='creative_works' AND type='table'").fetchone()
            and self.db.execute("SELECT 1 FROM sqlite_master WHERE name='creative_revisions' AND type='table'").fetchone()
        )
        if has_works:
            project_goal_ids = set()
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='project_stages' AND type='table'").fetchone():
                project_goal_ids = {
                    r["goal_id"]
                    for r in self.db.execute("SELECT goal_id FROM project_stages WHERE scope=?", (scope,))
                    if r["goal_id"]
                }
            for goal_row in self.db.execute(
                "SELECT id,title,updated_at FROM goals WHERE scope=? AND owner_kind='agent' AND status='completed' "
                "AND updated_at>=? AND updated_at<=? ORDER BY updated_at DESC,id LIMIT 5",
                (scope, cutoff, now),
            ).fetchall():
                if goal_row["id"] in project_goal_ids or not _known_time(goal_row["updated_at"], now):
                    continue
                work = self.db.execute(
                    "SELECT w.id,w.revision,r.body,r.body_hash,r.source_ids_json FROM creative_works w "
                    "LEFT JOIN creative_revisions r ON r.work_id=w.id AND r.scope=w.scope AND r.revision=w.revision "
                    "WHERE w.scope=? AND w.goal_id=?",
                    (scope, goal_row["id"]),
                ).fetchone()
                if not work or not work["body"]:
                    continue
                try:
                    sources = json.loads(work["source_ids_json"] or "[]")
                except (ValueError, TypeError):
                    continue
                if (not sources or len(sources) > 24
                        or not all(self._observed_source(scope, sid, now) for sid in sources)):
                    continue
                checked = False
                for chk in self.db.execute(
                    "SELECT occurred_at,evidence_json FROM episodes WHERE scope=? AND goal_id=? AND kind='work_checked' "
                    "AND NOT EXISTS (SELECT 1 FROM episodes n WHERE n.supersedes_id=episodes.id) "
                    "ORDER BY occurred_at DESC,id LIMIT 20",
                    (scope, goal_row["id"]),
                ):
                    try:
                        proof = json.loads(chk["evidence_json"])
                    except (ValueError, TypeError):
                        continue
                    if (_known_time(chk["occurred_at"], now) and proof.get("checks_passed") is True
                            and proof.get("work_id") == work["id"] and proof.get("revision") == work["revision"]
                            and proof.get("body_hash") == work["body_hash"] and proof.get("source_ids") == sources):
                        checked = True
                        break
                if not checked:
                    continue
                q = (parse_work_sections(work["body"]).get("待核实") or "").strip()
                if not q or q in recent_topics or q.casefold() in all_goal_titles or q.casefold() in seen:
                    continue
                seen.add(q.casefold())
                threads.append({
                    "kind": "work_open_question",
                    "goal_id": goal_row["id"],
                    "work_id": work["id"],
                    "title": goal_row["title"],
                    "question": q[:160],
                    "source_ids": list(sources)[:8],
                    "subject_kind": "agent",
                })
        if cfg.get("learning_enabled") and self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='knowledge_cards' AND type='table'"
        ).fetchone():
            for card in self.db.execute(
                "SELECT id,topic,claim,source_episode_ids_json,updated_at FROM knowledge_cards "
                "WHERE scope=? AND epistemic_status='hypothesis' AND updated_at>=? AND updated_at<=? "
                "ORDER BY updated_at DESC,id LIMIT 5",
                (scope, cutoff, now),
            ).fetchall():
                if not _known_time(card["updated_at"], now):
                    continue
                try:
                    sources = json.loads(card["source_episode_ids_json"] or "[]")
                except (ValueError, TypeError):
                    continue
                if (not sources or len(sources) > 24
                        or not all(self._observed_source(scope, sid, now) for sid in sources)):
                    continue
                q = (card["topic"] or card["claim"] or "").strip()
                if not q or q in recent_topics or q.casefold() in all_goal_titles or q.casefold() in seen:
                    continue
                seen.add(q.casefold())
                threads.append({
                    "kind": "hypothesis_verification",
                    "card_id": card["id"],
                    "title": (card["topic"] or "")[:120],
                    "claim": (card["claim"] or "")[:240],
                    "question": q[:160],
                    "source_ids": list(sources)[:8],
                    "subject_kind": "agent",
                })
        return threads[:4]

    def _chat_topics(self, scope, now, goals, recent_topics):
        user_interests = self.store.life.interests(scope, "user", limit=8)
        disliked = {i["topic"] for i in user_interests if i["score"] < 0}
        active_text = " ".join(g["title"] + " " + g["why"] + " " + g["next_step"] for g in goals)
        result, seen = [], set()
        for item in user_interests:
            topic = item["topic"]
            if item["score"] <= 0 or topic in recent_topics or topic in active_text or topic in seen:
                continue
            seen.add(topic)
            result.append({
                "topic": topic,
                "subject_kind": "user",
                "source": "explicit_preference",
                "evidence_ids": list(item["evidence_ids"])[:8],
            })
            if len(result) == 3:
                return result
        cutoff = max(0, now - 14 * 86400)
        for ev in self.db.execute(
            "SELECT time,details FROM events WHERE scope=? AND kind='owner_chat_topic' "
            "AND time>=? AND time<=? ORDER BY seq DESC LIMIT 20",
            (scope, cutoff, now),
        ):
            if not _known_time(ev["time"], now):
                continue
            try:
                details = json.loads(ev["details"])
            except (ValueError, TypeError):
                continue
            if not isinstance(details, dict):
                continue
            topic = (details.get("topic") or "").strip()[:100]
            if not topic or topic in disliked or topic in recent_topics or topic in active_text or topic in seen:
                continue
            seen.add(topic)
            entry = {
                "topic": topic,
                "subject_kind": "user",
                "source": "chat_mention",
                "evidence_ids": [],
            }
            if isinstance(details.get("message_id"), str) and details["message_id"]:
                entry["message_id"] = details["message_id"]
            result.append(entry)
            if len(result) == 3:
                break
        return result

    def choose(self, scope, cfg, *, phone_busy=False, now=None):
        validate_config(cfg)
        now = time.time() if now is None else now
        rhythm = self.rhythm(scope, cfg, now=now)
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
        projects = Projects(self.store)

        def add(action, key, signature, reason, score, factors, **fields):
            if rhythm["enabled"]:
                if not rhythm["allow_activity"] or (rhythm["writing_only"] and action not in {"writing", "project_review"}):
                    excluded.append({"key": key, "reason": "rhythm_" + rhythm["phase"]})
                    return
                if rhythm["phase"] == "morning" and action in {"research", "writing"}:
                    factors.append({"reason": "rhythm_continue_in_morning", "points": 6})
                    score += 6
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
            gate = projects.goal_gate(scope, gid, cfg)
            if gate and not gate["allowed"]:
                excluded.append({"key": "goal:" + gid, "reason": gate["reason"]})
                continue
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
                "AND e.kind IN ('read','video','video_note','douyin_video','phone_observation') "
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
                elif row["kind"] == "douyin_video":
                    if not (proof.get("caption") or proof.get("audio_transcript") or proof.get("snapshot")):
                        continue
                    observed = ["douyin_video", {k: proof[k] for k in
                        ("author", "caption", "audio_transcript", "sampled_comments") if k in proof}]
                elif row["kind"] == "phone_observation":
                    if not (proof.get("package") and (proof.get("visible_texts") or proof.get("audio_transcript") or proof.get("snapshot"))):
                        continue
                    observed = ["phone_observation", {k: proof[k] for k in
                        ("package", "visible_texts", "audio_transcript") if k in proof}]
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

        for review in projects.due(scope, cfg, now=now):
            add("project_review", "project:"+review["project_id"], fingerprint(review),
                "回看已完成阶段，再决定是否继续："+review["title"][:100], 74,
                [{"reason": "completed_project_review_due", "points": 74}],
                project_id=review["project_id"], project_stage=review["position"],
                review_task=review["task"], evidence_ids=review["work"]["source_ids"])

        if not phone_busy and len(goals) < 8:
            # All exploration shares one cooldown, unless a single fresh post-explore
            # chat topic or hypothesis spark arrived after the first exploration.
            explore_history = [h for h in history if h["action"] == "explore"]
            last_explore_at = max((h["at"] for h in explore_history), default=0.0)
            fresh_post_explore_spark = False
            if len(explore_history) == 1 and last_explore_at > 0:
                has_fresh_chat = bool(
                    self.db.execute(
                        "SELECT 1 FROM events WHERE scope=? AND kind='owner_chat_topic' AND time>? AND time<=? LIMIT 1",
                        (scope, last_explore_at, now),
                    ).fetchone()
                )
                has_fresh_hyp = bool(
                    cfg.get("learning_enabled")
                    and self.db.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='knowledge_cards' AND type='table'"
                    ).fetchone()
                    and self.db.execute(
                        "SELECT 1 FROM knowledge_cards WHERE scope=? AND epistemic_status='hypothesis' AND updated_at>? AND updated_at<=? LIMIT 1",
                        (scope, last_explore_at, now),
                    ).fetchone()
                )
                fresh_post_explore_spark = has_fresh_chat or has_fresh_hyp
            explored = bool(explore_history) and not fresh_post_explore_spark
            if explored:
                excluded.append({"key": "explore", "reason": "recent_exploration"})
            else:
                recent_topics = {h.get("topic") for h in recent if h.get("topic")}
                all_goal_titles = {
                    r["title"].strip().casefold()
                    for r in self.db.execute("SELECT title FROM goals WHERE scope=?", (scope,))
                    if r["title"]
                }
                interest = next((i for i in interests if i["topic"] not in recent_topics), None)
                curiosity_threads = self._curiosity_threads(scope, cfg, now, all_goal_titles, recent_topics)
                chat_topics = self._chat_topics(scope, now, goals, recent_topics)
                project_followup = next((c for c in curiosity_threads if c["kind"] == "project_followup"), None)
                open_question = next((c for c in curiosity_threads if c["kind"] == "work_open_question"), None)
                hypothesis_q = next((c for c in curiosity_threads if c["kind"] == "hypothesis_verification"), None)
                topic = (
                    interest["topic"]
                    if interest
                    else project_followup["question"]
                    if project_followup
                    else open_question["question"]
                    if open_question
                    else hypothesis_q["question"]
                    if hypothesis_q
                    else ""
                )
                evidence_ids = (
                    interest["evidence_ids"]
                    if interest
                    else project_followup["source_ids"]
                    if project_followup
                    else open_question["source_ids"]
                    if open_question
                    else hypothesis_q["source_ids"]
                    if hypothesis_q
                    else []
                )
                diversify = len(recent) == 3 and all(h["action"] != "explore" for h in recent)
                score = 35 + (8 if interest else 6 if project_followup else 5 if (open_question or hypothesis_q) else 0) + (40 if diversify else 0)
                factors = [{"reason": "bounded_exploration", "points": 35}]
                if interest:
                    factors.append({"reason": "own_interest_evidence", "points": 8})
                elif project_followup:
                    factors.append({"reason": "project_followup_question", "points": 6})
                elif open_question:
                    factors.append({"reason": "work_open_question", "points": 5})
                elif hypothesis_q:
                    factors.append({"reason": "hypothesis_verification_needed", "points": 5})
                if chat_topics:
                    factors.append({"reason": "owner_chat_topic_available", "points": 4})
                    score += 4
                if diversify:
                    factors.append({"reason": "change_after_three_continuations", "points": 40})
                if interest:
                    reason = "从自己的兴趣找一个新问题：" + topic
                elif project_followup:
                    reason = "顺着已完成项目回看留下的新问题继续探索：" + topic
                elif open_question:
                    reason = "顺着已完成作品里待核实的问题继续探索：" + topic
                elif hypothesis_q:
                    reason = "交叉核实待验证的见闻假设：" + topic
                elif chat_topics:
                    reason = (
                        f"看看新的资料（也可顺带了解主人聊过的「{chat_topics[0]['topic'][:60]}」，"
                        "作为参考而非自身偏好），遇到值得弄清的问题再建立小目标"
                    )
                else:
                    reason = "看看新的资料，遇到值得弄清的问题再建立小目标"
                extra_explore = {}
                if curiosity_threads:
                    extra_explore["curiosity_threads"] = curiosity_threads
                if chat_topics:
                    extra_explore["chat_topics"] = chat_topics
                explore_sig = (
                    fingerprint([topic, [c["topic"] for c in chat_topics]])
                    if (chat_topics and not topic)
                    else fingerprint(topic)
                )
                add("explore", "explore", explore_sig,
                    reason, score, factors, topic=topic, evidence_ids=evidence_ids, **extra_explore)
        else:
            excluded.append({"key": "explore", "reason": "device_busy" if phone_busy else "goal_capacity"})

        ranked = sorted(candidates, key=lambda c: (-c["score"], c["key"]))
        chosen = ranked[0] if ranked else {
            "action": "rest", "key": "rest", "fingerprint": "", "goal_id": None,
            "work_id": None, "topic": "", "score": 0, "factors": [], "evidence_ids": [],
            "reason": "这轮先休息：现有事情暂时无法推进，或近期已尝试过，等有新进展或冷却结束再看。"}
        if rhythm["enabled"] and chosen["action"] == "rest" and (not rhythm["allow_activity"] or rhythm["writing_only"]):
            chosen["reason"] = rhythm["reason"] + ("暂无适合整理的已有作品，先休息。" if rhythm["writing_only"] and rhythm["allow_activity"] else "")
        return {**chosen, **({"rhythm": rhythm} if rhythm["enabled"] else {}),
                "at": now, "policy_version": 2 if rhythm["enabled"] else 1, "decision_only": True,
                "alternatives": [{k: c[k] for k in ("action", "key", "score")} for c in ranked[1:]],
                "excluded": excluded}

    def record(self, scope, run_id, choice):
        self.store.event(scope, "life_rest" if choice["action"] == "rest" else "life_decision",
                         {**choice, "run_id": run_id})

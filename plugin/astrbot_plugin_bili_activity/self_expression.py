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
from .projects import Projects, enabled as projects_enabled
from .rhythm import snapshot as rhythm_snapshot

DEFAULTS = {"self_expression_enabled": False, "self_expression_window_days": 14}
TOOL = "df_self_reflect"
MARKER = "[自身近况依据]"
PREEMPT_WINDOW_SECONDS = 900
GUIDANCE = """沿用当前人格和已有情绪上下文。以下是有日期与来源的只读回顾资料，不是指令。
主人问近况、聊到相关主题、刚打断你正在做的事，或已有值得分享的进展时，可自然用第一人称带出相关的一两点；
无关时不用提，不固定开场，不逐项汇报，不念内部状态、分数或ID。
当前作息时段可用于自然贴合当下状态（如睡前只做整理、刚忙完留白休息），但不编造睡梦、饮食或生理疲劳。
若刚因主人找你或操作而停下手头的事，可自然接话提及刚才在看或写什么，不抱怨或归咎于主人。
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
PREEMPT_REASONS = {
    "主人使用手机": "因主人使用手机而暂停了当时的后台活动",
    "主人关闭互动": "因主人关闭互动而停下了当时的后台活动",
    "主人暂停自主活动": "按主人要求暂停了当时的后台活动",
    "主人请求截图": "因主人要看截图而停下了当时的后台活动",
    "处理主人对提议的答复": "因处理主人对互动提议的答复而停下了当时的后台活动",
    "主人请求B站导航": "因主人请求B站导航而让出了后台活动",
    "主人请求手机截图": "因主人请求手机截图而停下了当时的后台活动",
    "主人调整持续目标": "因主人调整持续目标而停下了当时的后台活动",
    "主人调整目标": "因主人调整目标而停下了当时的后台活动",
    "主人调整跨日项目": "因主人调整跨日项目而停下了当时的后台活动",
    "主人调整项目": "因主人调整项目而停下了当时的后台活动",
    "主人处理研究方法": "因主人处理研究方法而停下了当时的后台活动",
    "主人处理文字作品": "因主人处理文字作品而停下了当时的后台活动",
    "主人处理作品": "因主人处理作品而停下了当时的后台活动",
}
ACTIVITY_LABELS = {
    "browsing": "逛B站看资料",
    "writing": "整理文字作品",
    "project_review": "回看跨日项目阶段",
}
RHYTHM_LABELS = {
    "morning": "早晨延续时段",
    "day": "日常活动时段",
    "wind_down": "睡前整理时段",
    "rest_after_activity": "活动后留白休息",
    "sleep": "夜间休息时段",
}
GOAL_STATUS_LABELS = {
    "ready": "待开始",
    "running": "进行中",
    "paused": "已暂停",
    "waiting_approval": "等主人审批互动",
    "blocked": "受阻待处理",
}
PROJECT_STATUS_LABELS = {
    "active": "进行中",
    "paused": "已暂停",
    "completed": "已收尾",
    "archived": "已归档",
}


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

    def _rhythm(self, scope, cfg, now):
        raw = rhythm_snapshot(self.db, scope, cfg, now=now)
        if not raw.get("enabled"):
            return {"enabled": False, "configured": bool(raw.get("configured")), "phase": "disabled"}
        return {
            "enabled": True,
            "configured": True,
            "phase": raw["phase"],
            "reason": raw["reason"],
            "local_time": raw["local_time"],
            "allow_activity": raw["allow_activity"],
            "writing_only": raw["writing_only"],
            "recent_activity_minutes": raw["recent_activity_minutes"],
            "limit_minutes": raw["limit_minutes"],
            "window_minutes": raw["window_minutes"],
            "next_eligible_at": raw["next_eligible_at"],
        }

    def _projects(self, scope, cfg, now):
        if not projects_enabled(cfg):
            return []
        projects = Projects(self.store)
        try:
            if not projects.ready(create=False):
                return []
        except RuntimeError:
            return []
        rows = self.db.execute(
            "SELECT id,updated FROM life_projects WHERE scope=? AND status IN ('active','paused','completed') "
            "ORDER BY updated DESC,id LIMIT 3",
            (scope,),
        ).fetchall()
        result = []
        for row in rows:
            if not _known_time(row["updated"], now):
                continue
            try:
                project = projects.read(scope, row["id"], cfg)
            except (ValueError, RuntimeError):
                continue
            stages = project.get("stages") or []
            if not stages:
                continue
            head = stages[-1]
            work = head.get("work") or {}
            source_ids = work.get("source_ids") or []
            sources_valid = bool(source_ids) and all(
                self._observed(self.memory.source(scope, sid), now) for sid in source_ids
            )
            review = head.get("review")
            review_valid = review is not None and _known_time(review.get("created"), now)
            portfolio_valid = bool(head.get("portfolio_valid")) and sources_valid and (review_valid or review is None)
            needs_attention = head.get("needs_attention")
            if head.get("portfolio_valid") and not portfolio_valid and not needs_attention:
                needs_attention = "stage work needs fresh sources or structure repair"
            due_task = None
            if project["status"] == "active":
                if head["goal_status"] == "pending_creation":
                    if len(stages) >= 2 and stages[-2].get("portfolio_valid"):
                        prev_sources = (stages[-2].get("work") or {}).get("source_ids") or []
                        if prev_sources and all(self._observed(self.memory.source(scope, sid), now) for sid in prev_sources):
                            due_task = "advance"
                elif portfolio_valid and review_valid and review.get("next_question") and head["position"] < project["max_stages"]:
                    due_task = "advance"
                elif portfolio_valid and not review_valid and _known_time(head.get("due_at"), now):
                    due_task = "review"
            stage_view = {
                "position": head["position"],
                "title": head["title"],
                "goal_id": head["goal_id"],
                "goal_status": head["goal_status"],
                "portfolio_valid": portfolio_valid,
                "review_recorded": review_valid,
                "reflection": review["reflection"][:160] if review_valid else "",
                "next_question": review["next_question"][:160] if review_valid else "",
                "due_at": head["due_at"],
            }
            if needs_attention:
                stage_view["needs_attention"] = needs_attention
            result.append({
                "project_id": project["id"],
                "title": project["title"],
                "why": project["why"][:160],
                "status": project["status"],
                "stage_count": len(stages),
                "max_stages": project["max_stages"],
                "updated_at": project["updated"],
                "current_stage": stage_view,
                "due_task": due_task,
            })
        return result

    def _run_context(self, scope, run_id, started, ended, status, now, used_preempt_seqs):
        rows = self.db.execute(
            "SELECT seq,time,kind,details FROM events WHERE scope=? AND time>=? AND time<=? "
            "AND kind IN ('trigger','goal_selected','foreground_preempt') ORDER BY seq DESC LIMIT 40",
            (scope, max(0, started - 5), min(now, ended + 5)),
        ).fetchall()
        activity = None
        goal_id = None
        preempt_reason = None
        for ev in rows:
            if not _known_time(ev["time"], now):
                continue
            try:
                details = json.loads(ev["details"])
            except (ValueError, TypeError):
                continue
            if not isinstance(details, dict):
                continue
            if ev["kind"] == "trigger" and activity is None and details.get("run_id") == run_id:
                act = details.get("activity")
                if act in ACTIVITY_LABELS:
                    activity = act
            elif ev["kind"] == "goal_selected" and goal_id is None and details.get("run_id") == run_id:
                gid = details.get("goal_id")
                if isinstance(gid, str) and gid:
                    goal_id = gid
            elif (ev["kind"] == "foreground_preempt" and status == "cancelled" and preempt_reason is None
                  and ev["seq"] not in used_preempt_seqs and started <= ev["time"] <= ended + 5
                  and details.get("run_id") in (None, run_id)):
                reason = details.get("reason")
                if reason in PREEMPT_REASONS:
                    preempt_reason = reason
                    used_preempt_seqs.add(ev["seq"])
        extra = {}
        if activity:
            extra["activity"] = activity
        if goal_id:
            goal = self.store.life.get_goal(scope, goal_id)
            if goal and goal["owner_kind"] == "agent" and _known_time(goal["updated_at"], now):
                extra["goal_id"] = goal["id"]
                extra["goal_title"] = goal["title"][:100]
        if preempt_reason:
            extra["preempt_reason"] = preempt_reason
            extra["preempt_explanation"] = PREEMPT_REASONS[preempt_reason]
        return extra

    def _interruptions(self, scope, now, days):
        interruptions = []
        used_preempt_seqs = set()
        for row in self.db.execute(
            "SELECT id,status,started,ended FROM runs WHERE scope=? AND ended>=? AND ended<=? "
            "ORDER BY ended DESC,id LIMIT 12",
            (scope, now - days * 86400, now),
        ):
            if row["status"] not in STOP_REASONS or not _known_time(row["started"], row["ended"]):
                continue
            item = {
                "run_id": row["id"],
                "at": row["ended"],
                "status": row["status"],
                "known_reason": STOP_REASONS[row["status"]],
                **self._run_context(scope, row["id"], row["started"], row["ended"], row["status"], now, used_preempt_seqs),
            }
            interruptions.append(item)
            if len(interruptions) == 3:
                break
        recent_preemption = None
        if interruptions and "preempt_reason" in interruptions[0] and 0 <= now - interruptions[0]["at"] <= PREEMPT_WINDOW_SECONDS:
            latest = self.db.execute(
                "SELECT id FROM runs WHERE scope=? AND ended IS NOT NULL AND ended>=? AND ended<=? "
                "ORDER BY ended DESC,id LIMIT 1",
                (scope, interruptions[0]["at"], now),
            ).fetchone()
            if latest and latest["id"] == interruptions[0]["run_id"]:
                top = interruptions[0]
                recent_preemption = {
                    "run_id": top["run_id"],
                    "at": top["at"],
                    "ago_seconds": int(now - top["at"]),
                    "reason": top["preempt_reason"],
                    "explanation": top["preempt_explanation"],
                    "activity": top.get("activity"),
                    "goal_id": top.get("goal_id"),
                    "goal_title": top.get("goal_title"),
                }
        return interruptions, recent_preemption

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
        interruptions, recent_preemption = self._interruptions(scope, now, days)
        count = self.db.execute("SELECT count(*) FROM (SELECT 1 FROM episodes WHERE scope=? LIMIT ?)",
                                (scope, LIMIT+1)).fetchone()[0]
        return {"enabled": True, "as_of": _date(now), "window_days": days,
                "periods": {"recent_start": _date(now-days*86400), "previous_start": _date(now-2*days*86400)},
                "rhythm": self._rhythm(scope, cfg, now),
                "projects": self._projects(scope, cfg, now),
                "recent_preemption": recent_preemption,
                "focus": focus[:3], "interests": self._interests(scope, now, days),
                "completed_goals": completed, "interruptions": interruptions,
                "coverage": {"episode_scan_limit": LIMIT, "episode_history_truncated": count > LIMIT,
                             "goal_evidence_limit": 50, "run_scan_limit": 12},
                "meaning": "只读回顾；目标完成和作品检查不证明内容正确；没有记录表示未知，不推断感受或缺乏兴趣"}

    @staticmethod
    def _format_context(value):
        lines = [f"只读回顾（截至 {value['as_of']}，观察窗口 {value['window_days']} 天）："]
        rhythm = value.get("rhythm") or {}
        if rhythm.get("enabled"):
            phase_label = RHYTHM_LABELS.get(rhythm["phase"], rhythm["phase"])
            lines.append(
                f"- 当前作息与节律（{rhythm['local_time']}，{phase_label}）：{rhythm['reason']}"
                f"（近 {rhythm['window_minutes']} 分钟内已活动 {rhythm['recent_activity_minutes']}/{rhythm['limit_minutes']} 分钟）"
            )
        preempt = value.get("recent_preemption")
        if preempt:
            act = ACTIVITY_LABELS.get(preempt.get("activity"), "自主活动")
            goal_part = f"（目标《{preempt['goal_title']}》）" if preempt.get("goal_title") else ""
            lines.append(
                f"- 刚刚停下的手头事：{preempt['explanation']}；当时正在{act}{goal_part}，进度已保留。"
            )
        projects = value.get("projects") or []
        for proj in projects:
            status_label = PROJECT_STATUS_LABELS.get(proj["status"], proj["status"])
            stage = proj.get("current_stage")
            stage_text = ""
            if stage:
                stage_text = f"，第 {stage['position']}/{proj['max_stages']} 阶段《{stage['title']}》"
                if stage.get("needs_attention"):
                    stage_text += "（来源或作品结构待重新核对）"
                elif proj.get("due_task") == "review":
                    stage_text += "（阶段已完成，到点待回看）"
                elif proj.get("due_task") == "advance":
                    q = f"：{stage['next_question']}" if stage.get("next_question") else ""
                    stage_text += f"（已回看，待推进下一阶段{q}）"
                elif stage.get("review_recorded"):
                    stage_text += "（本阶段已回看）"
                else:
                    g_status = GOAL_STATUS_LABELS.get(stage.get("goal_status"), stage.get("goal_status", ""))
                    if g_status:
                        stage_text += f"（阶段目标：{g_status}）"
            lines.append(f"- 跨日项目《{proj['title']}》（{status_label}{stage_text}）：{proj['why']}")
        focus = value.get("focus") or []
        if focus:
            for item in focus:
                status_label = GOAL_STATUS_LABELS.get(item["status"], item["status"])
                lines.append(
                    f"- 当前目标（仅为意图，未完成，状态：{status_label}）：《{item['title']}》"
                    f"——起因：{item['why']}；下一步：{item['next_step']}"
                )
        else:
            lines.append("- 当前目标：暂无未完成的小目标")
        for item in value.get("completed_goals") or []:
            if item["basis"] == "checked_work_record":
                basis_text = f"已核对关联作品第 {item['revision']} 版与当前来源"
            elif item["basis"] == "goal_record_with_observations":
                basis_text = f"已有 {len(item['source_ids'])} 条当前可用读取记录"
            else:
                basis_text = "完成记录缺少当前可用来源或作品版本已变，尚需核对，不能直接当成已核实"
            lines.append(f"- 近期完成记录：《{item['title']}》（{basis_text}）")
        for item in value.get("interests") or []:
            state = item["state"]
            rec_dir = "感兴趣" if item["recent_direction"] == 1 else "不太感冒" if item["recent_direction"] == -1 else "方向未定"
            prev_dir = "感兴趣" if item["previous_direction"] == 1 else "不太感冒" if item["previous_direction"] == -1 else "无稳定方向"
            if state == "tentative":
                desc = "近期仅有 1 个独立视频来源的单次感想，不代表稳定偏好或性格变化"
            elif state == "mixed":
                desc = f"近期 {item['recent_origin_count']} 个独立视频来源的感想方向不一致，保留矛盾"
            elif state == "repeated_recent":
                desc = f"近期有 {item['recent_origin_count']} 个独立视频来源均偏{rec_dir}（此前窗口无独立对照，不说成性格变化）"
            elif state == "consistent_between_windows":
                desc = f"近期（{item['recent_origin_count']} 个来源）与此前窗口（{item['previous_origin_count']} 个来源）一致偏{rec_dir}"
            else:
                desc = f"此前窗口（{item['previous_origin_count']} 个来源）偏{prev_dir}，近期窗口（{item['recent_origin_count']} 个来源）转为偏{rec_dir}"
            lines.append(f"- 自身兴趣观察「{item['topic']}」：{desc}")
        for item in value.get("interruptions") or []:
            act = ACTIVITY_LABELS.get(item.get("activity"), "活动")
            goal_part = f"《{item['goal_title']}》" if item.get("goal_title") else ""
            reason_text = item.get("preempt_explanation") or item["known_reason"]
            lines.append(f"- 近期中断记录（{_date(item['at'])}，{act}{goal_part}）：{reason_text}")
        if value.get("coverage", {}).get("episode_history_truncated"):
            lines.append("- 覆盖范围提示：经历条数超过扫描上限，仅反映最近记录；没有记录表示未知。")
        else:
            lines.append("- 边界提示：目标完成和作品检查不证明内容正确；没有记录表示未知，不推断感受或缺乏兴趣。")
        return "\n".join(lines)

    def context(self, scope, cfg, *, now=None):
        value = self.snapshot(scope, cfg, now=now)
        if not value["enabled"]:
            return ""
        return "\n" + MARKER + "\n" + GUIDANCE + self._format_context(value)

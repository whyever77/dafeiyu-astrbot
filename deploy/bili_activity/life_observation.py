"""Read one project's observation ledger; never start, recover or accept a run.

Run from the repository with python -m deploy.bili_activity.life_observation.
Only an explicitly supplied data directory is read. No plugin/runtime imports.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

ID = re.compile(r"[0-9a-f]{32}")
SHANGHAI = ZoneInfo("Asia/Shanghai")
RUN_STATES = {"running", "completed", "cancelled", "timeout", "budget_exhausted", "failed"}
GOAL_STATES = {"ready", "running", "paused", "completed", "cancelled", "blocked", "waiting_approval"}
MEMORY_STATES = {"pending", "sending", "saved", "failed", "uncertain", "stale"}
ACTIONS = {"research", "writing", "explore", "project_review", "rest"}


def identifier(value):
    return value if isinstance(value, str) and ID.fullmatch(value) else None


def stamp(value, now):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= now:
        return None
    try:
        return datetime.fromtimestamp(value, SHANGHAI).isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return None


def known(value, options):
    return value if isinstance(value, str) and value in options else "unknown"


def integer(value, maximum=50):
    return value if type(value) is int and 0 <= value <= maximum else None


def _records(db, tables, scope, project_id, now):
    required = {"project_meta", "life_projects", "project_stages", "project_reviews", "goals"}
    if not tables & {"project_meta", "life_projects", "project_stages", "project_reviews"}:
        return {"state": "legacy"}
    if not required <= tables:
        return {"state": "unsupported"}
    version = db.execute("SELECT value FROM project_meta WHERE key='version'").fetchone()
    if not version or version[0] != "1":
        return {"state": "unsupported"}
    project = db.execute("SELECT id,status,max_stages,created,updated FROM life_projects WHERE scope=? AND id=?",
                         (scope, project_id)).fetchone()
    if not project:
        return {"state": "not_found"}  # Same response for an absent or foreign project.
    stages, memory_rows, run_ids = [], [], set()
    coverage = {"stage_limit": 6, "event_limit": 500, "run_limit": 50, "memory_limit_per_stage": 24,
                "episode_run_limit_per_stage": 50,
                "stages_truncated": False, "events_truncated": False, "runs_truncated": False,
                "memories_truncated": False, "episode_runs_truncated": False,
                "missing_tables": sorted({"events", "runs", "episodes", "memories", "creative_works",
                                           "creative_revisions", "creative_meta", "activity_budget_calls"} - tables)}
    stage_rows = db.execute("SELECT s.position,s.goal_id,s.created,g.status,g.updated_at,g.run_id "
        "FROM project_stages s LEFT JOIN goals g ON g.scope=s.scope AND g.id=s.goal_id "
        "WHERE s.scope=? AND s.project_id=? ORDER BY s.position LIMIT 7", (scope, project_id)).fetchall()
    coverage["stages_truncated"] = len(stage_rows) > 6
    goals = set()
    for row in stage_rows[:6]:
        goal_id = identifier(row["goal_id"])
        if goal_id:
            goals.add(goal_id)
        if identifier(row["run_id"]):
            run_ids.add(row["run_id"])
        stage = {"position": integer(row["position"], 6), "goal_id": goal_id,
                 "goal_status": known(row["status"], GOAL_STATES) if row["goal_id"] else "pending_creation",
                 "created_at": stamp(row["created"], now), "goal_updated_at": stamp(row["updated_at"], now),
                 "work": None, "review": None}
        if "creative_works" in tables:
            work = db.execute("SELECT id,revision,updated_at FROM creative_works WHERE scope=? AND goal_id=?",
                              (scope, goal_id)).fetchone()
            if work:
                stage["work"] = {"id": identifier(work["id"]), "revision": integer(work["revision"]),
                                 "updated_at": stamp(work["updated_at"], now)}
        review = db.execute("SELECT work_id,revision,created FROM project_reviews "
            "WHERE scope=? AND project_id=? AND position=?", (scope, project_id, row["position"])).fetchone()
        if review:
            stage["review"] = {"work_id": identifier(review["work_id"]), "revision": integer(review["revision"]),
                               "created_at": stamp(review["created"], now)}
        stages.append(stage)
        if "episodes" in tables:
            linked = db.execute("SELECT DISTINCT run_id FROM episodes WHERE scope=? AND goal_id=? "
                "AND run_id IS NOT NULL ORDER BY run_id LIMIT 51", (scope, goal_id)).fetchall()
            coverage["episode_runs_truncated"] |= len(linked) > 50
            run_ids.update(r[0] for r in linked[:50] if identifier(r[0]))
        if {"episodes", "memories"} <= tables:
            rows = db.execute("SELECT m.id,m.status,m.created FROM memories m JOIN episodes e "
                "ON m.id='episode:'||e.id AND m.scope=e.scope WHERE e.scope=? AND e.goal_id=? "
                "AND e.kind='work_checked' ORDER BY m.created DESC,m.id LIMIT 25", (scope, goal_id)).fetchall()
            coverage["memories_truncated"] |= len(rows) > 24
            memory_rows.extend({"episode_id": identifier(r["id"][8:]), "stage": stage["position"],
                "status": known(r["status"], MEMORY_STATES), "created_at": stamp(r["created"], now)} for r in rows[:24])
    decisions = {}
    if "events" in tables:
        events = db.execute("SELECT substr(details,1,4097) details FROM events WHERE scope=? "
            "AND kind='life_decision' ORDER BY seq DESC LIMIT 501", (scope,)).fetchall()
        coverage["events_truncated"] = len(events) > 500
        skipped_events = 0
        for event in events[:500]:
            try:
                data = json.loads(event[0]) if len(event[0]) <= 4096 else None
            except (TypeError, ValueError):
                data = None
            if not isinstance(data, dict):
                skipped_events += 1
                continue
            run_id = identifier(data.get("run_id"))
            # Exploration is selected before the model creates its first goal.
            # Its later goal episodes still identify the same recorded run.
            if data.get("project_id") != project_id and identifier(data.get("goal_id")) not in goals and run_id not in run_ids:
                continue
            if run_id:
                run_ids.add(run_id)
                decisions.setdefault(run_id, {"action": known(data.get("action"), ACTIONS),
                    "stage": data.get("project_stage") if type(data.get("project_stage")) is int and 1 <= data["project_stage"] <= 6 else None})
        coverage["unreadable_decisions"] = skipped_events
    runs = []
    if "runs" in tables and run_ids:
        # All IDs came from this scope and project; parameterize the set as well.
        placeholders = ",".join("?" for _ in run_ids)
        rows = db.execute("SELECT id,status,started,ended FROM runs WHERE scope=? AND id IN (" + placeholders +
            ") ORDER BY started DESC,id LIMIT 51", (scope, *sorted(run_ids))).fetchall()
        coverage["runs_truncated"] = len(rows) > 50
        for row in rows[:50]:
            start, end = stamp(row["started"], now), stamp(row["ended"], now)
            valid = bool(start and (row["ended"] is None or (end and row["ended"] >= row["started"])))
            calls = None
            if "activity_budget_calls" in tables:
                calls = db.execute("SELECT count(*) FROM activity_budget_calls WHERE scope=? AND run_id=? AND kind='model'",
                                   (scope, row["id"])).fetchone()[0]
            runs.append({"id": row["id"], "status": known(row["status"], RUN_STATES), "started_at": start,
                "ended_at": end, "timestamps_valid": valid, "model_reservations": calls,
                **decisions.get(row["id"], {"action": "unknown", "stage": None})})
    days = sorted({r["started_at"][:10] for r in runs if r["timestamps_valid"]})
    return {"state": "recorded", "project": {"id": project_id,
            "status": known(project["status"], {"active", "paused", "completed", "archived"}),
            "max_stages": integer(project["max_stages"], 6), "created_at": stamp(project["created"], now),
            "updated_at": stamp(project["updated"], now)}, "stages": stages, "runs": runs,
            "work_memory_queue": memory_rows, "recorded_run_dates": days, "coverage": coverage}


def observe(folder, project_id, *, now=None):
    if not identifier(project_id):
        raise ValueError("项目ID须为32位小写十六进制字符串")
    now = time.time() if now is None else now
    if stamp(now, now) is None:
        raise ValueError("无效观察时间")
    result = {"format_version": 1, "observed_at": stamp(now, now), "read_only": True,
              "live_verified": False, "source_validation": "not_rechecked",
              "external_checks": {key: "UNVERIFIED" for key in
                  ("natural_initiative", "livingmemory_recall", "qq_receipt", "actual_elapsed_days")}}
    folder = Path(folder)
    control = folder / "control.json"
    if not control.is_file():
        return result | {"state": "missing_control"}
    try:
        with control.open(encoding="utf-8") as stream:
            raw = stream.read(65537)
        cfg = json.loads(raw) if len(raw) <= 65536 else None
        if not isinstance(cfg, dict):
            raise ValueError
        scope = cfg.get("owner_scope")
        if not isinstance(scope, str) or not re.fullmatch(r"[^:]+:FriendMessage:\d+", scope):
            raise ValueError
        if scope.rsplit(":", 1)[-1] != str(cfg.get("owner_id", "")):
            raise ValueError
    except (OSError, ValueError, UnicodeError):
        return result | {"state": "invalid_control"}
    path = folder / "activity.sqlite3"
    if not path.is_file():
        return result | {"state": "missing_records"}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")  # One consistent snapshot; never instantiate Store.
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return result | _records(db, tables, scope, project_id, now)
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError):
        return result | {"state": "unavailable"}  # No raw exception, config, URL or response.


def markdown(report):
    lines = ["# 大肥鱼 · 项目生活观察", "", f"观察时间：{report['observed_at']}",
             f"读取状态：{report['state']}；只读账本，真实运行验收未完成。", "",
             "不包含账号、配置、正文或原始响应；记录中的 saved 只表示工具确认，不证明可召回。", ""]
    if report["state"] == "recorded":
        p = report["project"]
        lines += [f"项目：{p['id']} · {p['status']} · 上限 {p['max_stages']} 阶段", "",
                  "| 阶段 | 目标状态 | 当前作品版本 | 回看作品版本 | 回看时间 |", "| --- | --- | --- | --- | --- |"]
        for s in report["stages"]:
            work, review = s["work"] or {}, s["review"] or {}
            lines.append(f"| {s['position']} | {s['goal_status']} | {work.get('revision', '—')} | {review.get('revision', '—')} | {review.get('created_at') or '—'} |")
        lines += ["", "| 运行ID | 选择 | 结果 | 开始时间 | 模型额度记录 |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {r['id']} | {r['action']} | {r['status']} | {(r['started_at'] or '未知') + ('' if r['timestamps_valid'] else '（时间待核对）')} | {r['model_reservations'] if r['model_reservations'] is not None else '未知'} |" for r in report["runs"]]
        lines += ["", "账本中的运行日期：" + ("、".join(report["recorded_run_dates"]) or "无有效记录") + "。此日期列表不证明真实跨日观察。",
                  "", "作品记忆队列："]
        lines += [f"- 阶段 {m['stage']} · {m['episode_id']} · {m['status']}" for m in report["work_memory_queue"]]
        if not report["work_memory_queue"]:
            lines += ["- 未读到对应记录，不能推断为已保存。"]
        lines += ["", "记录覆盖范围（有截断或缺表时不能当作全量）：", "", "```json", json.dumps(report["coverage"], ensure_ascii=False, indent=2), "```"]
    lines += ["", "待现场核对：自然延续话题、LivingMemory实际召回、QQ实收、真实经过的天数，均为 UNVERIFIED。",
              "作品版本仅列历史记录，未重新核验来源、内容和事实。模型额度记录不等于调用一定成功。", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="显式指定隔离账本目录；不初始化或迁移")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    try:
        result = observe(args.data_dir, args.project_id)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else markdown(result))
    return 0 if result["state"] == "recorded" else 2


if __name__ == "__main__":
    raise SystemExit(main())

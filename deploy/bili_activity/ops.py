#!/usr/bin/env python3
"""Operate only the Bilibili activity plugin; never change AstrBot cron jobs."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit


DEFAULTS = {
    "engagement_actions": False, "interaction_mode": "approval", "auto_public": False,
    "allowed_uids": [], "allowed_keywords": [],
    "max_actions_per_run": 3, "max_actions_per_day": 20,
    "max_comments_per_day": 3, "max_replies_per_day": 5, "max_danmaku_per_day": 1,
    "max_likes_per_day": 10, "max_coins_per_day": 1,
    "action_interval_ms": [1500, 4000], "risk_cooldown_minutes": 60,
    "reply_watch_enabled": False, "reply_poll_minutes": 3,
    "reply_max_threads_per_tick": 15, "reply_min_len": 8, "reply_lookback_hours": 24,
    "reply_max_per_thread_per_day": 2,
    "comment_learning_enabled": False, "learn_min_score": 7, "learn_recent_notes": 5,
    "learn_prompt": "挑选有信息量、补充视频内容的评论；跳过纯情绪、复读和广告。",
    "enabled": False,
    "periodic_enabled": False,
    "public_interactions": False,
    "collection_management": False,
    "screenshot_enabled": True,
    "life_enabled": False,
    "life_decision_enabled": False,
    "life_repeat_cooldown_minutes": 180,
    "contact_coordination_enabled": False,
    "contact_daily_limit": 3, "contact_cooldown_minutes": 90,
    "contact_owner_quiet_minutes": 5, "contact_unanswered_limit": 2,
    "workshop_enabled": False,
    "methods_enabled": False,
    "workshop_revisions_per_run": 3,
    "workshop_max_characters": 12000,
    "daily_minutes": 30,
    "max_model_calls_per_run": 20,
    "daily_model_calls": 60,
    "daily_shares": 3,
    "screenshots_per_run": 1,
    "chat_cooldown_seconds": 300,
    "owner_scope": "",
    "owner_id": "",
    "dnd_ranges": None,
    "phone_url": "http://172.18.0.1:8009/Phone",
    "bili_url": "http://172.18.0.1:8011/mcp",
    "check_minutes": 120,
    "check_jitter_minutes": 60,
    "max_videos": 3,
    "max_minutes": 10,
    "once_at": None,
    "one_shot_verified": False,
    "device_id": "emulator-5554",
}
DEFAULT_DATA_DIR = "/opt/dafeiyu/astrbot/data/plugin_data/astrbot_plugin_bili_activity"
NUMERIC_LIMITS = {
    "max_actions_per_run": (0, 10000), "max_actions_per_day": (0, 10000),
    "max_comments_per_day": (0, 10000), "max_replies_per_day": (0, 10000),
    "max_danmaku_per_day": (0, 10000), "max_likes_per_day": (0, 10000),
    "max_coins_per_day": (0, 10000), "risk_cooldown_minutes": (1, 10000),
    "reply_poll_minutes": (1, 1440), "reply_max_threads_per_tick": (1, 15),
    "reply_min_len": (1, 1000), "reply_lookback_hours": (1, 720),
    "reply_max_per_thread_per_day": (0, 100), "learn_min_score": (0, 10), "learn_recent_notes": (1, 20),
    "check_minutes": (1, 1440), "check_jitter_minutes": (0, 1440),
    "max_videos": (1, 10), "max_minutes": (1, 30),
    "daily_minutes": (1, 240), "max_model_calls_per_run": (1, 64),
    "daily_model_calls": (1, 1000), "daily_shares": (0, 50),
    "screenshots_per_run": (0, 10), "chat_cooldown_seconds": (0, 3600),
    "workshop_revisions_per_run": (1, 10), "workshop_max_characters": (500, 20000),
    "life_repeat_cooldown_minutes": (15, 1440),
    "contact_daily_limit": (0, 50), "contact_cooldown_minutes": (1, 1440),
    "contact_owner_quiet_minutes": (0, 1440), "contact_unanswered_limit": (1, 20),
}


def load_control(folder: Path):
    path = folder / "control.json"
    if not path.exists():
        return dict(DEFAULTS)
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("control.json must contain an object")
    return {**DEFAULTS, **result}


def ready(config):
    scope = config["owner_scope"]
    if not isinstance(scope, str) or not re.fullmatch(r"[^:]+:FriendMessage:\d+", scope):
        raise ValueError("先设置实际主人的私聊 owner_scope；不能从模板推测接收人")
    if not config["owner_id"] or scope.rsplit(":", 1)[-1] != str(config["owner_id"]):
        raise ValueError("owner_id 必须和 owner_scope 的私聊用户一致")
    ranges = config.get("dnd_ranges")
    if ranges is None:
        raise ValueError("先核对现有免打扰配置并设置 dnd_ranges；确认无免打扰时才填 []")
    validate_dnd(ranges)
    for key in ("phone_url", "bili_url"):
        validate_url(config[key])


def validate_url(value):
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("MCP 地址必须是无凭证的 HTTP/HTTPS URL；不要把凭证放入配置或日志")


def validate_dnd(ranges):
    if not isinstance(ranges, list):
        raise ValueError("dnd_ranges 必须为 [[\"23:00\",\"08:00\"]] 形式的数组")
    for pair in ranges:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("每个免打扰时段必须包含开始和结束时间")
        for item in pair:
            if not isinstance(item, str) or len(item) != 5 or item[2] != ":":
                raise ValueError("免打扰时间必须为 HH:MM")
            hour, minute = map(int, item.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError("免打扰时间超出范围")


@contextmanager
def exclusive(folder):
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / ".ops.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("另一个 ops 命令正在修改配置；请稍后重试") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def save_control(folder, control):
    path = folder / "control.json"
    if path.exists():
        backup = folder / "config_backups"
        backup.mkdir(exist_ok=True)
        # Keep every user config before a modification; never print it in logs.
        (backup / f"control-{time.time_ns()}.json").write_bytes(path.read_bytes())
    temp = folder / f".control-{os.getpid()}.tmp"
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(control, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_rows(folder, query, params=(), *, optional_table=None):
    path = folder / "activity.sqlite3"
    if not path.exists():
        return []
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        # A pre-v0.2 database may not have these tables yet. Do not suppress
        # malformed columns, corrupt databases or other operational errors.
        if optional_table is not None and not con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (optional_table,)
        ).fetchone():
            return []
        return [dict(row) for row in con.execute(query, params)]
    finally:
        con.close()


def read_goals(folder, scope, limit=8, include_terminal=False):
    terminal = "" if include_terminal else " AND status NOT IN ('completed','cancelled')"
    return read_rows(folder,
                     "SELECT id,title,why,status,next_step,run_id,updated_at FROM goals WHERE scope=?"
                     + terminal + " ORDER BY updated_at DESC LIMIT ?",
                     (scope, limit), optional_table="goals")


def read_episodes(folder, scope, limit=8, goal_id=None):
    where = " AND goal_id=?" if goal_id else ""
    params = (scope, goal_id, limit) if goal_id else (scope, limit)
    return read_rows(folder,
                     "SELECT id,goal_id,run_id,occurred_at,kind,subject_kind,subject_id,source_id "
                     "FROM episodes WHERE scope=?" + where + " ORDER BY occurred_at DESC LIMIT ?",
                     params, optional_table="episodes")


def read_life_decisions(folder, scope, limit=8):
    rows = read_rows(folder, "SELECT details FROM events WHERE scope=? "
                    "AND kind IN ('life_decision','life_rest') ORDER BY seq DESC LIMIT ?",
                    (scope, max(1, min(32, limit))), optional_table="events")
    result = []
    for row in rows:
        item = json.loads(row["details"])
        run = read_rows(folder, "SELECT status FROM runs WHERE scope=? AND id=?",
                        (scope, item["run_id"]), optional_table="runs")
        item["run_status"] = run[0]["status"] if run else "rested" if item["action"] == "rest" else "not_started"
        result.append(item)
    return result


def read_contacts(folder, scope):
    return read_rows(folder, "SELECT source,kind,day,created,dispatched,finished,status,message_id "
                     "FROM contact_attempts WHERE scope=? ORDER BY created DESC,rowid DESC LIMIT 8",
                     (scope,), optional_table="contact_attempts")


def read_budget(folder, scope):
    """Read persisted accounting without migrating the database or reserving work."""
    return {
        "runs": read_rows(folder,
                          "SELECT run_id,started,reserved_until,finished_at FROM activity_budget_runs "
                          "WHERE scope=? ORDER BY started DESC LIMIT 5", (scope,),
                          optional_table="activity_budget_runs"),
        "time_days": read_rows(folder,
                               "SELECT day,SUM(seconds) AS reserved_or_used_seconds FROM activity_budget_time "
                               "WHERE scope=? GROUP BY day ORDER BY day DESC LIMIT 7", (scope,),
                               optional_table="activity_budget_time"),
        "call_days": read_rows(folder,
                               "SELECT day,SUM(CASE WHEN kind='model' THEN 1 ELSE 0 END) AS model_calls,"
                               "SUM(CASE WHEN kind='share' THEN 1 ELSE 0 END) AS shares,"
                               "SUM(CASE WHEN kind='share' AND share_kind='screenshot' THEN 1 ELSE 0 END) AS screenshots "
                               "FROM activity_budget_calls WHERE scope=? GROUP BY day ORDER BY day DESC LIMIT 7",
                               (scope,), optional_table="activity_budget_calls"),
    }


def read_works(folder, scope, limit=12):
    """List scoped works without creating tables or refreshing cached checks."""
    return read_rows(folder,
                     "SELECT id,goal_id,title,phase,status,revision,created_at,updated_at "
                     "FROM creative_works WHERE scope=? ORDER BY updated_at DESC,id LIMIT ?",
                     (scope, limit), optional_table="creative_works")


def read_work(folder, scope, work_id, revision=None):
    """Read a saved draft; validation is historical, not a new source check."""
    if not re.fullmatch(r"[0-9a-f]{32}", work_id):
        raise ValueError("作品 ID 格式无效")
    if revision is not None and revision < 1:
        raise ValueError("revision must be a positive integer")
    works = read_rows(folder,
                      "SELECT id,goal_id,title,phase,status,revision,created_at,updated_at "
                      "FROM creative_works WHERE scope=? AND id=?",
                      (scope, work_id), optional_table="creative_works")
    if not works:
        raise ValueError("当前会话没有此作品或版本")
    work = works[0]
    requested = work["revision"] if revision is None else revision
    rows = read_rows(folder,
                     "SELECT revision,body,source_ids_json,validation_json,body_hash,created_at "
                     "FROM creative_revisions WHERE scope=? AND work_id=? AND revision=?",
                     (scope, work_id, requested))
    if not rows:
        if requested != 0:
            raise ValueError("当前会话没有此作品或版本")
        version = {"revision": 0, "body": "", "source_ids": [], "validation": None,
                   "body_hash": None, "revision_created_at": None}
    else:
        version = rows[0]
        version["source_ids"] = json.loads(version.pop("source_ids_json"))
        version["validation"] = json.loads(version.pop("validation_json"))
        version["revision_created_at"] = version.pop("created_at")
    return {**work, **version, "head_revision": work["revision"], "validation_is_cached": True}


def read_methods(folder, scope, limit=8):
    """Read historical counts; runtime source validation is deliberately absent."""
    rows = read_rows(folder,
                     "SELECT m.id,m.title,m.status,m.revision,m.created_at,m.updated_at,"
                     "(SELECT count(*) FROM method_attempts a WHERE a.scope=m.scope AND a.method_id=m.id "
                     "AND a.revision=m.revision AND a.status='success') AS recorded_success_count "
                     "FROM research_methods m WHERE m.scope=? ORDER BY m.updated_at DESC,m.id LIMIT ?",
                     (scope, limit), optional_table="research_methods")
    return [{**row, "evidence_rechecked": False} for row in rows]


def read_method(folder, scope, method_id, revision=None):
    if not re.fullmatch(r"[0-9a-f]{32}", method_id):
        raise ValueError("方法 ID 格式无效")
    if revision is not None and not 1 <= revision <= 20:
        raise ValueError("revision must be between 1 and 20")
    methods = read_rows(folder,
                        "SELECT id,title,status,revision,created_at,updated_at FROM research_methods "
                        "WHERE scope=? AND id=?", (scope, method_id), optional_table="research_methods")
    if not methods:
        raise ValueError("当前会话没有此方法或版本")
    method = methods[0]
    selected = method["revision"] if revision is None else revision
    versions = read_rows(folder,
                         "SELECT revision,work_id,work_revision,work_hash,source_goal_id,title,"
                         "applies_to,steps_json,limitations,created_at FROM method_revisions "
                         "WHERE scope=? AND method_id=? AND revision=?", (scope, method_id, selected))
    if not versions:
        raise ValueError("当前会话没有此方法或版本")
    version = versions[0]
    version["steps"] = json.loads(version.pop("steps_json"))
    version["revision_created_at"] = version.pop("created_at")
    counts = read_rows(folder,
                       "SELECT count(*) AS count FROM method_attempts WHERE scope=? AND method_id=? "
                       "AND revision=? AND status='success'", (scope, method_id, selected))
    return {**method, **version, "head_revision": method["revision"],
            "recorded_success_count": counts[0]["count"], "evidence_rechecked": False}


def read_method_attempts(folder, scope, limit=8, goal_id=None):
    if goal_id is not None and not re.fullmatch(r"[0-9a-f]{32}", goal_id):
        raise ValueError("目标 ID 格式无效")
    where = " AND goal_id=?" if goal_id else ""
    params = (scope, goal_id, limit) if goal_id else (scope, limit)
    rows = read_rows(folder,
                     "SELECT id,method_id,revision,goal_id,run_id,started_at,status,"
                     "baseline_work_id,baseline_revision,baseline_hash,result_work_id,result_revision,"
                     "result_hash,summary,finished_at FROM method_attempts WHERE scope=?" + where
                     + " ORDER BY started_at DESC,id LIMIT ?", params, optional_table="method_attempts")
    return [{**row, "evidence_rechecked": False} for row in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(DEFAULT_DATA_DIR))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "start", "stop", "status", "enable-periodic", "disable-periodic"):
        sub.add_parser(name)
    cfg = sub.add_parser("configure")
    cfg.add_argument("--interaction-mode", choices=["approval", "auto_low_risk", "auto_public"])
    cfg.add_argument("--action-interval-ms", type=int, nargs=2)
    cfg.add_argument("--allowed-uids")
    cfg.add_argument("--allowed-keywords")
    cfg.add_argument("--learn-prompt")
    cfg.add_argument("--owner-scope")
    cfg.add_argument("--owner-id")
    cfg.add_argument("--dnd-ranges", help='JSON, e.g. [["23:00","08:00"]]; copy the actual current settings')
    cfg.add_argument("--phone-url")
    cfg.add_argument("--bili-url")
    cfg.add_argument("--device-id")
    for field in NUMERIC_LIMITS:
        cfg.add_argument("--" + field.replace("_", "-"), type=int)
    once = sub.add_parser("once")
    once.add_argument("--delay-seconds", type=int, default=180)
    switches = sub.add_parser("switch")
    switches.add_argument("name", choices=["public_interactions", "engagement_actions", "collection_management", "screenshot_enabled", "life_enabled", "life_decision_enabled", "contact_coordination_enabled", "workshop_enabled", "methods_enabled", "reply_watch_enabled", "comment_learning_enabled", "auto_public"])
    switches.add_argument("value", choices=["on", "off"])
    accepted = sub.add_parser("accept-one-shot")
    accepted.add_argument("--evidence", type=Path, required=True,
                          help="JSON with run_id, read_content_verified=true, screenshot_opened=true, qq_screenshot_message_id")
    logs = sub.add_parser("logs")
    logs.add_argument("--lines", type=int, default=50)
    goals = sub.add_parser("goals", help="只读查看当前主人私聊的持续目标")
    goals.add_argument("--limit", type=int, default=8)
    goals.add_argument("--all", action="store_true", help="同时显示已完成或已取消目标")
    decisions = sub.add_parser("life-decisions", help="只读查看生活选择与理由；选择不等于执行成功")
    decisions.add_argument("--limit", type=int, default=8)
    sub.add_parser("contacts", help="只读查看两种主动联系入口的最近记录")
    episodes = sub.add_parser("episodes", help="只读查看当前主人私聊的经历索引")
    episodes.add_argument("--limit", type=int, default=8)
    episodes.add_argument("--goal-id")
    works = sub.add_parser("works", help="只读查看当前主人私聊的文字作品")
    works.add_argument("--limit", type=int, default=12)
    work = sub.add_parser("work", help="只读查看作品正文与已保存版本；不重新核验来源")
    work.add_argument("work_id")
    work.add_argument("--revision", type=int)
    methods = sub.add_parser("methods", help="只读查看当前私聊的方法；历史成功次数未重新核验")
    methods.add_argument("--limit", type=int, default=8)
    method = sub.add_parser("method", help="只读查看方法版本、来源与步骤；不重新核验来源")
    method.add_argument("method_id")
    method.add_argument("--revision", type=int)
    attempts = sub.add_parser("method-attempts", help="只读查看方法尝试记录，不恢复或结束活动")
    attempts.add_argument("--limit", type=int, default=8)
    attempts.add_argument("--goal-id")
    args = parser.parse_args(argv)
    folder = args.data_dir.resolve()
    if args.command == "logs":
        for row in reversed(read_rows(folder, "SELECT * FROM events ORDER BY seq DESC LIMIT ?", (max(1, min(args.lines, 500)),))):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.command == "status":
        control = load_control(folder)
        print(json.dumps({"control": control,
                          "runs": read_rows(folder, "SELECT * FROM runs ORDER BY started DESC LIMIT 5"),
                          "proposals": read_rows(folder, "SELECT id,action,status,created,expires FROM proposals ORDER BY created DESC LIMIT 10"),
                          "deliveries": read_rows(folder, "SELECT id,kind,status,created,receipt FROM deliveries ORDER BY created DESC LIMIT 10"),
                          "goals": read_goals(folder, control["owner_scope"]),
                          "episodes": read_episodes(folder, control["owner_scope"]),
                          "life_decisions": read_life_decisions(folder, control["owner_scope"]),
                          "contacts": read_contacts(folder, control["owner_scope"]),
                          "budget": read_budget(folder, control["owner_scope"]),
                          "works": read_works(folder, control["owner_scope"]),
                          "methods": read_methods(folder, control["owner_scope"]),
                          "method_attempts": read_method_attempts(folder, control["owner_scope"])},
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "contacts":
        control = load_control(folder)
        print(json.dumps({"enabled": control["contact_coordination_enabled"],
                          "daily_limit": control["contact_daily_limit"],
                          "recent": read_contacts(folder, control["owner_scope"])}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "life-decisions":
        control = load_control(folder)
        print(json.dumps(read_life_decisions(folder, control["owner_scope"], args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.command == "method":
        control = load_control(folder)
        print(json.dumps(read_method(folder, control["owner_scope"], args.method_id, args.revision),
                         ensure_ascii=False, indent=2))
        return 0
    if args.command in {"methods", "method-attempts"}:
        control = load_control(folder)
        if not 1 <= args.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = (read_methods(folder, control["owner_scope"], args.limit) if args.command == "methods" else
                read_method_attempts(folder, control["owner_scope"], args.limit, args.goal_id))
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if args.command == "work":
        control = load_control(folder)
        print(json.dumps(read_work(folder, control["owner_scope"], args.work_id, args.revision),
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "works":
        control = load_control(folder)
        if not 1 <= args.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        print(json.dumps(read_works(folder, control["owner_scope"], args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.command in {"goals", "episodes"}:
        control = load_control(folder)
        if not 1 <= args.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = (read_goals(folder, control["owner_scope"], args.limit, args.all)
                if args.command == "goals" else
                read_episodes(folder, control["owner_scope"], args.limit, args.goal_id))
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    with exclusive(folder):
        control = load_control(folder)
        if args.command == "init" and (folder / "control.json").exists():
            print("配置已存在，保持原样。")
            return 0
        if args.command == "configure":
            for field in ("interaction_mode", "learn_prompt", "action_interval_ms", "allowed_uids", "allowed_keywords"):
                value = getattr(args, field)
                if value is None:
                    continue
                if field == "learn_prompt" and len(value) > 1000:
                    raise ValueError("learn_prompt 最多 1000 字")
                if field == "action_interval_ms" and not 0 <= value[0] <= value[1] <= 60000:
                    raise ValueError("action_interval_ms 必须为 0..60000 的递增范围")
                if field in {"allowed_uids", "allowed_keywords"}:
                    value = json.loads(value)
                    if not isinstance(value, list) or len(value) > 100:
                        raise ValueError(f"invalid {field}")
                    if field == "allowed_uids" and any(type(v) not in (str, int) or not str(v).isascii() or not str(v).isdigit() or int(v) <= 0 for v in value):
                        raise ValueError("allowed_uids 必须为 UID 数组")
                    if field == "allowed_keywords" and any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in value):
                        raise ValueError("allowed_keywords 必须为关键词数组")
                control[field] = value
                if field == "interaction_mode":
                    control["auto_public"] = value == "auto_public"
            for field in ("owner_scope", "owner_id", "phone_url", "bili_url", "device_id", *NUMERIC_LIMITS):
                value = getattr(args, field)
                if value is not None:
                    if field in NUMERIC_LIMITS:
                        low, high = NUMERIC_LIMITS[field]
                        if not low <= value <= high:
                            raise ValueError(f"{field} must be between {low} and {high}")
                    if field in {"phone_url", "bili_url"}:
                        validate_url(value)
                    if field == "device_id" and value != "emulator-5554":
                        raise ValueError("本次验收设备固定为 emulator-5554")
                    control[field] = value
            if args.dnd_ranges is not None:
                ranges = json.loads(args.dnd_ranges)
                validate_dnd(ranges)
                control["dnd_ranges"] = ranges
        elif args.command in {"start", "once", "enable-periodic"}:
            ready(control)
            control["enabled"] = True
            if args.command == "once":
                if args.delay_seconds < 60:
                    raise ValueError("首次联调至少留出 60 秒；默认 180 秒")
                control["once_at"] = time.time() + args.delay_seconds
            elif args.command == "enable-periodic":
                if control["one_shot_verified"] is not True:
                    raise ValueError("一次性活动尚未验收；先实际验收并保存 accept-one-shot 证据")
                control["periodic_enabled"] = True
        elif args.command == "stop":
            control.update(enabled=False, periodic_enabled=False, once_at=None)
        elif args.command == "disable-periodic":
            control["periodic_enabled"] = False
        elif args.command == "switch":
            if args.name == "contact_coordination_enabled" and args.value == "on":
                ready(control)
            if args.name == "life_decision_enabled" and args.value == "on":
                ready(control)
                if control["life_enabled"] is not True or control["one_shot_verified"] is not True:
                    raise ValueError("先完成一次性活动验收并开启 life_enabled，再开启生活决策")
            if args.name == "life_enabled" and args.value == "on":
                ready(control)
                if control["one_shot_verified"] is not True:
                    raise ValueError("先完成已有 B 站一次性活动验收，再开启持续目标功能")
            if args.name == "workshop_enabled" and args.value == "on":
                ready(control)
                if control["life_enabled"] is not True or control["one_shot_verified"] is not True:
                    raise ValueError("先完成一次性活动验收并开启 life_enabled，再开启文字创作")
            if args.name == "methods_enabled" and args.value == "on":
                ready(control)
                if any(control[key] is not True for key in ("life_enabled", "workshop_enabled", "one_shot_verified")):
                    raise ValueError("先完成一次性活动验收并开启 life_enabled、workshop_enabled，再开启方法复用")
            control[args.name] = args.value == "on"
        elif args.command == "accept-one-shot":
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            if not (evidence.get("run_id") and evidence.get("read_content_verified") is True
                    and evidence.get("screenshot_opened") is True and evidence.get("qq_screenshot_message_id")):
                raise ValueError("验收证据缺少实际运行 ID、内容核对或 QQ 图片打开确认/回执")
            rows = read_rows(folder, "SELECT id,status,scope,started,ended FROM runs WHERE id=?", (evidence["run_id"],))
            if not rows or rows[0]["status"] != "completed":
                raise ValueError("运行记录不存在或未完成，不能标记验收通过")
            run = rows[0]
            receipts = read_rows(folder, "SELECT receipt,meta FROM deliveries WHERE scope=? AND kind='screenshot' AND status='sent' AND created>=? AND created<=?",
                                 (run["scope"], run["started"], run["ended"]))
            receipt_ok = False
            for row in receipts:
                receipt = json.loads(row["receipt"] or "null") or {}
                meta = json.loads(row["meta"] or "null") or {}
                if (str(receipt.get("message_id")) == str(evidence["qq_screenshot_message_id"])
                        and receipt.get("verified") is True
                        and receipt.get("image_readback") is True
                        and meta.get("run_id") == run["id"]
                        and meta.get("share_mode") == "autonomous"):
                    receipt_ok = True
            if not receipt_ok:
                raise ValueError("该运行期间没有匹配的 QQ 图片回执和图片读回记录，不能标记验收通过")
            control["one_shot_verified"] = True
            control["one_shot_evidence"] = evidence
        save_control(folder, control)
    print(f"已更新 {folder / 'control.json'}；插件会读取最新设置。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

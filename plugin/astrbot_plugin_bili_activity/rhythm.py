"""Read-only scheduling policy, not a model of feelings or biological energy."""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULTS = {
    "life_rhythm_enabled": False,
    "life_sleep_hour": 23,
    "life_wake_hour": 8,
    "life_wind_down_minutes": 60,
    "life_activity_window_minutes": 120,
    "life_activity_limit_minutes": 20,
}
LIMITS = {"life_sleep_hour": (0, 23), "life_wake_hour": (0, 23),
          "life_wind_down_minutes": (0, 180),
          "life_activity_window_minutes": (30, 720),
          "life_activity_limit_minutes": (1, 120)}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def validate_config(cfg):
    cfg = DEFAULTS | cfg
    if type(cfg["life_rhythm_enabled"]) is not bool:
        raise ValueError("life_rhythm_enabled 必须是布尔开关")
    for key, (low, high) in LIMITS.items():
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError(f"{key} 必须为 {low}–{high} 的整数")
    awake = (cfg["life_sleep_hour"] - cfg["life_wake_hour"]) % 24 * 60
    if not awake or cfg["life_wind_down_minutes"] >= awake:
        raise ValueError("睡眠与起床时间须不同，睡前整理时段须短于清醒时段")
    if cfg["life_activity_limit_minutes"] > cfg["life_activity_window_minutes"]:
        raise ValueError("活动时长阈值不能大于观察窗口")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def snapshot(db, scope, cfg, *, now=None):
    """Only SELECT existing run boundaries; no recovery, counters or memory writes.

    Overlapping runs count once. Failed/cancelled runs still occupied time;
    unfinished, future and malformed intervals are not invented as completed work.
    """
    cfg = DEFAULTS | cfg
    validate_config(cfg)
    if not (cfg["life_rhythm_enabled"] and cfg.get("life_enabled")
            and cfg.get("life_decision_enabled")):
        return {"enabled": False, "configured": cfg["life_rhythm_enabled"], "phase": "disabled"}
    at = time.time() if now is None else now
    if not _number(at):
        raise ValueError("invalid rhythm timestamp")
    local = datetime.fromtimestamp(at, SHANGHAI)
    minute = local.hour * 60 + local.minute + local.second / 60
    awake = (cfg["life_sleep_hour"] - cfg["life_wake_hour"]) % 24 * 60
    since_wake = (minute - cfg["life_wake_hour"] * 60) % 1440
    sleeping = since_wake >= awake

    def next_hour(hour):
        value = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        return (value if value.timestamp() > at else value + timedelta(days=1)).timestamp()

    window = cfg["life_activity_window_minutes"] * 60
    limit = cfg["life_activity_limit_minutes"] * 60
    rows = db.execute("SELECT id,started,ended,status FROM runs WHERE scope=? AND ended>=? "
                      "AND ended<=? ORDER BY ended DESC,id LIMIT 257", (scope, at-window, at)).fetchall()
    intervals, ids, ignored = [], [], 0
    for row in rows:
        start, end = row["started"], row["ended"]
        if (not _number(start) or not _number(end) or not start < end <= at
                or row["status"] not in {"completed", "failed", "timeout", "cancelled", "interrupted", "budget_exhausted"}):
            ignored += 1
            continue
        intervals.append((max(start, at-window), end))
        ids.append(row["id"])
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])

    def used(t):
        return sum(max(0, end - max(start, t-window)) for start, end in merged)

    seconds = used(at)
    wind_down = not sleeping and since_wake >= awake - cfg["life_wind_down_minutes"]
    phase = "sleep" if sleeping else "wind_down" if wind_down else "morning" if since_wake < 120 else "day"
    reason = {"sleep": "到了休息时段，这轮先不安排活动。",
              "wind_down": "临近休息，先整理已有笔记，不开始新的资料探索。",
              "morning": "新一天先接着做已有的小目标。",
              "day": "这个时段可以继续研究，或跟着自己的兴趣找一个小问题。"}[phase]
    allowed = not sleeping
    next_eligible = next_hour(cfg["life_wake_hour"]) if sleeping else None
    active_until = next_hour(cfg["life_sleep_hour"])
    available = min(limit-seconds, active_until-at)
    if not sleeping and (len(rows) > 256 or available < 60):
        allowed = False
        phase = "rest_after_activity"
        reason = "近期活动已接近节律阈值，先留一点空白，稍后再继续。"
        low, high = at, at+window
        for _ in range(40):
            mid = (low+high)/2
            if used(mid) <= limit-60:
                high = mid
            else:
                low = mid
        next_eligible = math.ceil(high)
        if len(rows) > 256:
            reason, next_eligible = "近期活动记录过多，先休息，等待记录窗口收敛后再判断。", math.ceil(at+window)
        if available == active_until-at:
            phase, reason = "sleep", "快到休息时间了，这轮不再开始新活动。"
            next_eligible = next_hour(cfg["life_wake_hour"])
    return {"enabled": True, "configured": True, "phase": phase, "reason": reason,
            "at": at, "local_time": local.strftime("%Y-%m-%d %H:%M"),
            "allow_activity": allowed, "writing_only": wind_down,
            "active_until": active_until, "max_seconds": max(0, available) if allowed else 0,
            "next_eligible_at": next_eligible, "recent_activity_minutes": round(seconds/60, 2),
            "window_minutes": cfg["life_activity_window_minutes"],
            "limit_minutes": cfg["life_activity_limit_minutes"],
            "source_run_ids": ids[:8], "source_run_count": len(ids), "ignored_records": ignored,
            "meaning": "依据已结束运行的经过时间安排留白，不是体力或情绪测量；等待本身不生成经历。"}

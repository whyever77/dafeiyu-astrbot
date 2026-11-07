"""Three-layer affective dynamics, Shanghai circadian homeostasis, Softening Gate,
and deterministic multi-channel expression profiles.

All functions accept an explicit ``now`` epoch timestamp for deterministic offline testing.
"""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .core import DEFAULTS, clamp, sanitize_label, valid_timestamp

SHANGHAI = ZoneInfo("Asia/Shanghai")

_TAG_PREFIX_RE = re.compile(r"^\s*\[(?:主人|群友|主人的朋友)[^\]\r\n]{0,40}\]\s*")

_SERIOUS_TASK_RE = re.compile(
    r"(?:```|Traceback|Exception:|Error:|报错|修\s*bug|查一下|帮我(?:看|查|写|改|算|分析|排查)|"
    r"怎么(?:写|配|修|解决|实现)|部署|代码|脚本|接口|服务器|日志|数据库|编译|运行失败)",
    re.IGNORECASE,
)
_BUSY_OR_APOLOGY_RE = re.compile(
    r"(?:刚忙完|太忙了|一直在忙|刚下班|加班|开会|抱歉|不好意思|对不起|刚看到|久等了|忙死了|刚腾出手)"
)
_COAX_AFFECTION_RE = re.compile(
    r"(?:摸摸|抱抱|贴贴|亲亲|乖啦|真乖|好乖|最喜欢你|喜欢肥鱼|好肥鱼|别生气|不气不气|哄哄|想你了|超棒|真棒|夸夸)"
)
_DISTRESS_RE = re.compile(
    r"(?:头疼|头痛|发烧|感冒|生病|胃疼|难受|睡不着|失眠|心累|崩溃|焦虑|压力好大|好累啊|累趴|委屈|难过|烦死了)"
)
_TEASING_RE = re.compile(r"(?:笨鱼|傻鱼|胖鱼|小笨蛋|肥头鱼|略略略)")
_CARE_RESOLVED_RE = re.compile(
    r"(?:好多了|已经好了|退烧了|不疼了|没事了|没事啦|搞定了|交完了|弄完了|忙完了|顺利通过|睡了一觉好多了|缓过来了)"
)
_POSITIVE_SOCIAL_RE = re.compile(r"(?:哈哈|谢谢|厉害|好玩|可爱|早安|晚安|棒|赞|开心)")
_NEGATIVE_SOCIAL_RE = re.compile(r"(?:滚|闭嘴|傻逼|垃圾|蠢|烦人)")


def strip_sender_tag(text: str) -> str:
    return _TAG_PREFIX_RE.sub("", str(text or "")).strip()


def wake_cycle_day(at: float, wake_hour: int = 8) -> str:
    local = datetime.fromtimestamp(at, SHANGHAI)
    shifted = local - timedelta(hours=int(wake_hour) % 24)
    return shifted.date().isoformat()


def circadian_baseline(at: float, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    conf = DEFAULTS | (cfg or {})
    wake_hour = int(conf["wake_hour"]) % 24
    sleep_hour = int(conf["sleep_hour"]) % 24
    local = datetime.fromtimestamp(at, SHANGHAI)
    minute = local.hour * 60 + local.minute + local.second / 60.0
    awake_minutes = ((sleep_hour - wake_hour) % 24) * 60
    if awake_minutes == 0:
        awake_minutes = 15 * 60
    since_wake = (minute - wake_hour * 60) % 1440.0
    sleeping = since_wake >= awake_minutes

    if sleeping:
        return {"phase": "sleeping", "sleeping": True, "valence": 0.08, "arousal": 0.18}
    if awake_minutes > 90 and since_wake >= awake_minutes - 60:
        return {"phase": "wind_down", "sleeping": False, "valence": 0.12, "arousal": 0.32}
    if since_wake < min(180.0, awake_minutes):
        return {"phase": "morning", "sleeping": False, "valence": 0.16, "arousal": 0.56}
    if since_wake < min(660.0, awake_minutes):
        return {"phase": "daytime", "sleeping": False, "valence": 0.20, "arousal": 0.65}
    return {"phase": "evening", "sleeping": False, "valence": 0.18, "arousal": 0.50}


def awake_seconds_between(
    start_ts: float,
    end_ts: float,
    wake_hour: int = 8,
    sleep_hour: int = 23,
) -> float:
    """Exact awake seconds between ``start_ts`` and ``end_ts`` in Asia/Shanghai (capped to 7 days)."""
    if end_ts <= start_ts:
        return 0.0
    t0 = max(start_ts, end_ts - 7 * 86400.0)
    t1 = end_ts
    w_h = int(wake_hour) % 24
    s_h = int(sleep_hour) % 24
    if w_h == s_h:
        return t1 - t0

    start_date = datetime.fromtimestamp(t0, SHANGHAI).date() - timedelta(days=1)
    end_date = datetime.fromtimestamp(t1, SHANGHAI).date() + timedelta(days=1)
    total = 0.0
    day = start_date
    while day <= end_date:
        day_start = datetime.combine(day, dt_time(0, 0), tzinfo=SHANGHAI).timestamp()
        day_end = datetime.combine(day + timedelta(days=1), dt_time(0, 0), tzinfo=SHANGHAI).timestamp()
        wake_ts = datetime.combine(day, dt_time(w_h, 0), tzinfo=SHANGHAI).timestamp()
        sleep_ts = datetime.combine(day, dt_time(s_h, 0), tzinfo=SHANGHAI).timestamp()
        if w_h < s_h:
            intervals = ((wake_ts, sleep_ts),)
        else:
            intervals = ((day_start, sleep_ts), (wake_ts, day_end))
        for a, b in intervals:
            lo = max(t0, a)
            hi = min(t1, b)
            if hi > lo:
                total += hi - lo
        day += timedelta(days=1)
    return total


def compute_dominant_tag(
    state: dict[str, Any],
    *,
    sleeping: bool = False,
    care_due: bool = False,
) -> str:
    softened = state.get("softened_mode") or ""
    if softened == "serious_task":
        return "focused"
    if softened == "comforting":
        return "tender_care"
    if softened == "coaxed":
        return "coaxed_soft"

    pout = float(state.get("pout", 0.0))
    valence = float(state.get("valence", 0.0))
    arousal = float(state.get("arousal", 0.5))
    longing = float(state.get("longing", 0.0))
    share_urge = float(state.get("share_urge", 0.0))

    if care_due and pout < 0.35:
        return "tender_care"
    if pout >= 0.38:
        return "tsundere_pout"
    if sleeping or arousal < 0.28:
        return "sleepy"
    if valence >= 0.28 and (share_urge >= 0.42 or (arousal >= 0.68 and valence >= 0.38)):
        return "eager_share"
    if valence >= 0.38:
        return "joyful"
    if valence <= -0.25:
        return "low_mood"
    if longing >= 0.55:
        return "missing_you"
    return "calm"


def new_owner_state(
    scope: str,
    owner_id: str,
    cfg: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    stamp = valid_timestamp(now)
    conf = DEFAULTS | (cfg or {})
    circ = circadian_baseline(stamp, conf)
    base = {
        "scope": scope,
        "owner_id": str(owner_id or ""),
        "valence": circ["valence"],
        "arousal": circ["arousal"],
        "longing": 0.10,
        "pout": 0.0,
        "share_urge": 0.10,
        "warmth_bond": 320.0,
        "dominant_tag": "sleepy" if circ["sleeping"] else "calm",
        "softened_mode": "",
        "last_settled_at": stamp,
        "last_interaction_at": stamp,
        "last_sleep_reset_day": wake_cycle_day(stamp, conf["wake_hour"]),
        "consecutive_rejects": 0,
        "consecutive_neg_stimuli": 0,
        "interaction_count": 0,
        "extra": {},
    }
    base["dominant_tag"] = compute_dominant_tag(base, sleeping=circ["sleeping"])
    return base


def settle_owner_state(
    state: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Settle circadian homeostasis, overnight negative mood reset, and awake longing."""
    stamp = valid_timestamp(now)
    conf = DEFAULTS | (cfg or {})
    nxt = {**state, "extra": dict(state.get("extra") or {})}
    notes: list[str] = []

    prev_settled = valid_timestamp(nxt.get("last_settled_at"), stamp)
    dt = max(0.0, stamp - prev_settled)
    dh = dt / 3600.0
    circ = circadian_baseline(stamp, conf)
    cur_wake_day = wake_cycle_day(stamp, conf["wake_hour"])
    prev_wake_day = str(nxt.get("last_sleep_reset_day") or "")

    last_inter = valid_timestamp(nxt.get("last_interaction_at"), 0.0)
    quiet_gap = (stamp - last_inter) if last_inter > 0 else dt

    if prev_wake_day and cur_wake_day != prev_wake_day and quiet_gap >= 3 * 3600.0:
        if float(nxt["valence"]) < 0.0:
            nxt["valence"] = max(0.05, circ["valence"] * 0.5)
        nxt["pout"] = 0.0
        nxt["consecutive_rejects"] = 0
        nxt["consecutive_neg_stimuli"] = 0
        nxt["softened_mode"] = ""
        notes.append("overnight_sleep_reset")
    nxt["last_sleep_reset_day"] = cur_wake_day

    if dt > 0.0:
        v0 = float(nxt["valence"])
        v_circ = circ["valence"]
        tau_v = float(conf["neg_valence_tau_hours"] if v0 < v_circ else conf["pos_valence_tau_hours"])
        nxt["valence"] = clamp(v_circ + (v0 - v_circ) * math.exp(-dh / max(0.25, tau_v)), -1.0, 1.0)

        a0 = float(nxt["arousal"])
        a_circ = circ["arousal"]
        nxt["arousal"] = clamp(a_circ + (a0 - a_circ) * math.exp(-dh / 2.5), 0.0, 1.0)

        pout_hl = max(5.0, float(conf["pout_half_life_minutes"])) * 60.0
        p_next = float(nxt["pout"]) * (0.5 ** (dt / pout_hl))
        nxt["pout"] = 0.0 if p_next < 0.02 else clamp(p_next, 0.0, 1.0)

        u_next = float(nxt["share_urge"]) * (0.5 ** (dh / 4.0))
        nxt["share_urge"] = 0.0 if u_next < 0.02 else clamp(u_next, 0.0, 1.0)

        if dt >= 900.0:
            nxt["softened_mode"] = ""

        if last_inter > 0.0:
            threshold_ts = last_inter + 4.0 * 3600.0
            if stamp > threshold_ts:
                acc_from = max(prev_settled, threshold_ts)
                awake_sec = awake_seconds_between(
                    acc_from,
                    stamp,
                    conf["wake_hour"],
                    conf["sleep_hour"],
                )
                if awake_sec > 0.0:
                    l0 = float(nxt["longing"])
                    nxt["longing"] = clamp(
                        1.0 - (1.0 - l0) * math.exp(-(awake_sec / 3600.0) / 10.0),
                        0.0,
                        1.0,
                    )

    cooldowns = nxt["extra"].get("cooldown_topics")
    if isinstance(cooldowns, dict):
        nxt["extra"]["cooldown_topics"] = {
            k: v for k, v in cooldowns.items() if isinstance(v, (int, float)) and v > stamp
        }

    nxt["last_settled_at"] = stamp
    nxt["dominant_tag"] = compute_dominant_tag(nxt, sleeping=circ["sleeping"])
    return nxt, notes


def local_fast_screen(
    user_text: str,
    active_care_threads: list[dict[str, Any]] | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Zero-latency deterministic cue detector for Softening Gate and timeout fallback."""
    stamp = valid_timestamp(now)
    clean = strip_sender_tag(user_text)
    intent = "casual"
    user_emotion = "calm"
    distress = 0.0
    delta = {"valence": 0.02, "arousal": 0.02, "pout": 0.0, "warmth": 0.5}
    care_open = None
    care_resolved_ids: list[str] = []

    if _SERIOUS_TASK_RE.search(clean):
        intent = "serious_task"
        user_emotion = "focused"
        delta = {"valence": 0.04, "arousal": 0.08, "pout": -0.40, "warmth": 0.5}
    elif _DISTRESS_RE.search(clean):
        intent = "venting"
        user_emotion = "tired"
        distress = 0.65
        delta = {"valence": -0.05, "arousal": -0.08, "pout": -0.80, "warmth": 1.5}
        snippet = sanitize_label(clean, limit=36)
        if snippet:
            cat = "health" if re.search(r"(?:头疼|头痛|发烧|感冒|生病|胃疼)", clean) else "stress_or_work"
            care_open = {"topic": snippet, "category": cat, "ttl_hours": 36}
    elif _COAX_AFFECTION_RE.search(clean):
        intent = "coaxing_affection"
        user_emotion = "playful"
        delta = {"valence": 0.25, "arousal": 0.10, "pout": -0.60, "warmth": 2.0}
    elif _BUSY_OR_APOLOGY_RE.search(clean):
        intent = "apology_or_busy_explain"
        user_emotion = "busy"
        delta = {"valence": 0.15, "arousal": 0.04, "pout": -0.50, "warmth": 1.5}
    elif _TEASING_RE.search(clean):
        intent = "teasing"
        user_emotion = "playful"
        delta = {"valence": 0.02, "arousal": 0.12, "pout": 0.28, "warmth": 0.5}

    if active_care_threads and _CARE_RESOLVED_RE.search(clean):
        for thread in active_care_threads:
            last_prompted = float(thread.get("last_prompted_at") or 0.0)
            topic = str(thread.get("topic") or "")
            topic_hit = any(
                tok in clean
                for tok in (topic[:4], "头疼", "感冒", "发烧", "项目", "加班", "考试", "答辩")
                if len(tok) >= 2 and tok in topic
            )
            if (last_prompted > 0 and stamp - last_prompted <= 2700.0) or topic_hit or len(active_care_threads) == 1:
                care_resolved_ids.append(str(thread["id"]))

    return {
        "source": "local_fast_screen",
        "user_emotion": user_emotion,
        "user_distress": distress,
        "intent": intent,
        "delta": delta,
        "care_open": care_open,
        "care_resolved_ids": care_resolved_ids,
    }


def merge_appraisal_with_fast_screen(
    llm_result: dict[str, Any] | None,
    fast_result: dict[str, Any],
) -> dict[str, Any]:
    """Combine LLM appraisal with deterministic Softening Gate safety cues."""
    if not llm_result:
        return fast_result
    merged = dict(llm_result)
    fast_intent = fast_result.get("intent")
    # Never miss an obvious code/debugging request or coaxing/apology/distress cue.
    if fast_intent == "serious_task" and merged.get("intent") != "serious_task":
        merged["intent"] = "serious_task"
    elif merged.get("intent") == "casual" and fast_intent in {
        "serious_task",
        "apology_or_busy_explain",
        "coaxing_affection",
    }:
        merged["intent"] = fast_intent
    merged["user_distress"] = max(
        float(merged.get("user_distress") or 0.0),
        float(fast_result.get("user_distress") or 0.0),
    )
    resolved = list(dict.fromkeys([
        *(merged.get("care_resolved_ids") or []),
        *(fast_result.get("care_resolved_ids") or []),
    ]))
    merged["care_resolved_ids"] = resolved
    # A deterministic distress cue must not be lost when the LLM returns null.
    if not merged.get("care_open") and fast_result.get("care_open"):
        merged["care_open"] = fast_result["care_open"]
    return merged


def apply_owner_turn(
    state: dict[str, Any],
    appraisal: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    *,
    now: float | None = None,
    care_due: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply pre-reply empathy appraisal and Softening Gate to a settled owner state."""
    stamp = valid_timestamp(now)
    conf = DEFAULTS | (cfg or {})
    circ = circadian_baseline(stamp, conf)
    nxt = {**state, "extra": dict(state.get("extra") or {})}

    intent = str(appraisal.get("intent") or "casual")
    distress = clamp(float(appraisal.get("user_distress") or 0.0), 0.0, 1.0)
    raw_delta = appraisal.get("delta") if isinstance(appraisal.get("delta"), dict) else {}

    dv = clamp(float(raw_delta.get("valence") or 0.0), -0.45, 0.45)
    da = clamp(float(raw_delta.get("arousal") or 0.0), -0.35, 0.35)
    dp = clamp(float(raw_delta.get("pout") or 0.0), -0.80, 0.40)
    dw = clamp(float(raw_delta.get("warmth") or 0.5), -2.0, 5.0)

    longing_before = float(nxt["longing"])
    gate_triggered = ""

    # Long awake silence sparks a mild affectionate-tsundere pout before softening checks
    if (
        longing_before >= 0.48
        and intent not in {"serious_task", "apology_or_busy_explain", "coaxing_affection"}
        and distress <= 0.40
    ):
        nxt["pout"] = clamp(float(nxt["pout"]) + 0.35 * (longing_before - 0.30), 0.0, 1.0)

    nxt["valence"] = clamp(float(nxt["valence"]) + dv, -1.0, 1.0)
    nxt["arousal"] = clamp(float(nxt["arousal"]) + da, 0.0, 1.0)
    nxt["pout"] = clamp(float(nxt["pout"]) + dp, 0.0, 1.0)
    nxt["warmth_bond"] = clamp(float(nxt["warmth_bond"]) + dw, 0.0, 1000.0)

    # Softening Gate ("傲娇心软破功门控")
    if intent == "serious_task":
        gate_triggered = "serious_task"
        nxt["softened_mode"] = "serious_task"
        nxt["pout"] = clamp(float(nxt["pout"]) * 0.35, 0.0, 1.0)
        nxt["arousal"] = clamp(max(float(nxt["arousal"]), 0.55), 0.0, 1.0)
    elif distress > 0.40:
        gate_triggered = "comforting"
        nxt["softened_mode"] = "comforting"
        nxt["pout"] = 0.0
        nxt["valence"] = clamp(min(float(nxt["valence"]), 0.22), -0.15, 0.25)
        nxt["arousal"] = clamp(float(nxt["arousal"]), 0.30, 0.52)
    elif intent in {"apology_or_busy_explain", "coaxing_affection"}:
        gate_triggered = "coaxed"
        nxt["softened_mode"] = "coaxed"
        nxt["pout"] = clamp(float(nxt["pout"]) * 0.15, 0.0, 1.0)
        nxt["valence"] = clamp(float(nxt["valence"]) + 0.32, -1.0, 1.0)
        nxt["warmth_bond"] = clamp(float(nxt["warmth_bond"]) + 1.5, 0.0, 1000.0)
        nxt["consecutive_rejects"] = 0
    else:
        nxt["softened_mode"] = ""

    if float(nxt["pout"]) < 0.02:
        nxt["pout"] = 0.0

    # Owner spoke -> release waiting longing into warm reassurance
    nxt["longing"] = clamp(max(0.05, longing_before * 0.20), 0.0, 1.0)
    nxt["last_interaction_at"] = stamp
    nxt["last_settled_at"] = stamp
    nxt["interaction_count"] = int(nxt.get("interaction_count") or 0) + 1
    nxt["dominant_tag"] = compute_dominant_tag(nxt, sleeping=circ["sleeping"], care_due=care_due)

    summary = {
        "intent": intent,
        "user_emotion": str(appraisal.get("user_emotion") or "calm"),
        "user_distress": round(distress, 3),
        "softening_gate": gate_triggered or "none",
        "dominant_tag": nxt["dominant_tag"],
        "source": str(appraisal.get("source") or "unknown"),
    }
    return nxt, summary


def apply_life_event(
    state: dict[str, Any],
    kind: str,
    payload: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Zero-LLM affective pulse mapping for background/foreground life events."""
    stamp = valid_timestamp(now)
    conf = DEFAULTS | (cfg or {})
    nxt, _ = settle_owner_state(state, conf, now=stamp)
    info = payload if isinstance(payload, dict) else {}
    circ = circadian_baseline(stamp, conf)
    topic = sanitize_label(info.get("topic") or "", limit=40)

    if kind in {"interest_positive", "work_checked", "knowledge_verified"}:
        nxt["valence"] = clamp(float(nxt["valence"]) + 0.18, -1.0, 1.0)
        nxt["arousal"] = clamp(float(nxt["arousal"]) + 0.12, 0.0, 1.0)
        nxt["share_urge"] = clamp(float(nxt["share_urge"]) + 0.25, 0.0, 1.0)
        nxt["consecutive_neg_stimuli"] = 0
        nxt["extra"]["last_life_stimulus"] = {"kind": kind, "topic": topic, "at": stamp}
    elif kind in {"interest_negative", "ad_or_rumor_blocked"}:
        nxt["valence"] = clamp(float(nxt["valence"]) - 0.08, -1.0, 1.0)
        nxt["arousal"] = clamp(float(nxt["arousal"]) - 0.05, 0.0, 1.0)
        nxt["consecutive_neg_stimuli"] = int(nxt.get("consecutive_neg_stimuli") or 0) + 1
        if topic and nxt["consecutive_neg_stimuli"] >= 2:
            cooldowns = dict(nxt["extra"].get("cooldown_topics") or {})
            cooldowns[topic] = stamp + 6 * 3600.0
            while len(cooldowns) > 16:
                cooldowns.pop(next(iter(cooldowns)))
            nxt["extra"]["cooldown_topics"] = cooldowns
        nxt["extra"]["last_life_stimulus"] = {"kind": kind, "topic": topic, "at": stamp}
    elif kind == "foreground_preempt":
        nxt["valence"] = clamp(float(nxt["valence"]) + 0.12, -1.0, 1.0)
        nxt["arousal"] = clamp(float(nxt["arousal"]) + 0.10, 0.0, 1.0)
        nxt["extra"]["last_life_stimulus"] = {"kind": kind, "topic": topic, "at": stamp}
    elif kind == "proposal_approved":
        nxt["valence"] = clamp(float(nxt["valence"]) + 0.20, -1.0, 1.0)
        nxt["arousal"] = clamp(float(nxt["arousal"]) + 0.10, 0.0, 1.0)
        nxt["pout"] = clamp(float(nxt["pout"]) * 0.5, 0.0, 1.0)
        nxt["warmth_bond"] = clamp(float(nxt["warmth_bond"]) + 1.0, 0.0, 1000.0)
        nxt["consecutive_rejects"] = 0
    elif kind == "proposal_rejected":
        rej = int(nxt.get("consecutive_rejects") or 0) + 1
        nxt["consecutive_rejects"] = rej
        if rej == 1:
            nxt["pout"] = clamp(float(nxt["pout"]) + 0.12, 0.0, 1.0)
            nxt["valence"] = clamp(float(nxt["valence"]) - 0.05, -1.0, 1.0)
        else:
            nxt["pout"] = clamp(float(nxt["pout"]) + 0.25, 0.0, 1.0)
            nxt["valence"] = clamp(float(nxt["valence"]) - 0.12, -1.0, 1.0)
            nxt["share_urge"] = clamp(float(nxt["share_urge"]) - 0.20, 0.0, 1.0)
    elif kind in {"thought_shared", "share_delivered"}:
        nxt["share_urge"] = clamp(float(nxt["share_urge"]) - 0.35, 0.0, 1.0)

    if float(nxt["pout"]) < 0.02:
        nxt["pout"] = 0.0
    nxt["last_settled_at"] = stamp
    nxt["dominant_tag"] = compute_dominant_tag(nxt, sleeping=circ["sleeping"])
    return nxt, {"kind": kind, "dominant_tag": nxt["dominant_tag"], "topic": topic}


def update_social_turn(
    social_state: dict[str, Any],
    user_text: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Lightweight isolated familiarity + vibe update for group members and guests."""
    stamp = valid_timestamp(now)
    prev_at = valid_timestamp(social_state.get("last_interaction_at"), stamp)
    dh = max(0.0, stamp - prev_at) / 3600.0
    vibe = float(social_state.get("vibe") or 0.0) * (0.5 ** (dh / 2.0))
    clean = strip_sender_tag(user_text)
    if _POSITIVE_SOCIAL_RE.search(clean):
        vibe = clamp(vibe + 0.18, -1.0, 1.0)
    elif _NEGATIVE_SOCIAL_RE.search(clean):
        vibe = clamp(vibe - 0.30, -1.0, 1.0)
    else:
        vibe = clamp(vibe + 0.04, -1.0, 1.0)

    fam = clamp(float(social_state.get("familiarity") or 0.0) + (1.2 if vibe >= 0 else 0.2), 0.0, 100.0)
    return {
        **social_state,
        "familiarity": fam,
        "vibe": vibe,
        "interaction_count": int(social_state.get("interaction_count") or 0) + 1,
        "last_interaction_at": stamp,
        "updated_at": stamp,
    }


def should_surface_care(
    thread: dict[str, Any],
    state: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> bool:
    """Decide whether an active care thread should gently surface on this turn."""
    stamp = valid_timestamp(now)
    conf = DEFAULTS | (cfg or {})
    if state.get("softened_mode") == "serious_task":
        return False
    if int(thread.get("prompt_count") or 0) >= 2:
        return False
    last_prompted = float(thread.get("last_prompted_at") or 0.0)
    if last_prompted > 0.0 and stamp - last_prompted < 8 * 3600.0:
        return False
    created = float(thread.get("created_at") or stamp)
    crossed_wake = wake_cycle_day(stamp, conf["wake_hour"]) != wake_cycle_day(created, conf["wake_hour"])
    return (stamp - created >= 4 * 3600.0) or crossed_wake


def felt_tone_phrase(valence: float, arousal: float, pout: float = 0.0) -> str:
    if pout >= 0.38:
        return "带着点小别扭与嘴硬"
    if valence >= 0.40 and arousal >= 0.60:
        return "当时觉得眼前一亮、特别雀跃"
    if valence >= 0.25:
        return "当时心情愉悦轻松"
    if valence <= -0.30 and arousal >= 0.50:
        return "当时有点被气到或嫌弃"
    if valence <= -0.20:
        return "当时觉得有些乏味低落"
    if arousal < 0.30:
        return "当时困倦安静"
    return "平常心境"


def expression_profile(
    state: dict[str, Any] | None,
    active_care_threads: list[dict[str, Any]] | None = None,
    cfg: dict[str, Any] | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Deterministic physical parameters for TTS, bubble layout, faces, planner, and memory."""
    if not state:
        return {
            "scope_kind": "social_or_default",
            "mode": "calm",
            "tts_instruction": "",
            "speech_rate": None,
            "pitch_rate": None,
            "max_bubbles": None,
            "max_faces": None,
            "suppress_cheerful_faces": False,
            "salience_multiplier": 1.0,
            "felt_tone": "平常心境",
            "planner_bias": {
                "study_write_delta": 0,
                "prefer_light_or_rest": False,
                "cooldown_topics": [],
            },
            "contact_drive": 0.0,
            "suppress_ordinary_share": False,
        }

    stamp = valid_timestamp(now)
    conf = DEFAULTS | (cfg or {})
    circ = circadian_baseline(stamp, conf)
    care_list = active_care_threads or []
    care_due = any(should_surface_care(t, state, conf, now=stamp) for t in care_list)

    v = float(state.get("valence", 0.0))
    a = float(state.get("arousal", 0.5))
    p = 0.0 if state.get("softened_mode") == "serious_task" else float(state.get("pout", 0.0))
    l = float(state.get("longing", 0.0))
    u = float(state.get("share_urge", 0.0))
    mode = compute_dominant_tag(state, sleeping=circ["sleeping"], care_due=care_due)

    tts_instruction = ""
    speech_rate = None
    pitch_rate = None
    max_bubbles = None
    max_faces = None
    suppress_cheerful = False

    if mode in {"eager_share", "joyful"}:
        tts_instruction = "语气轻快元气，带着藏不住的小得意和笑意"
        speech_rate = 1.06
        pitch_rate = 1.03
        max_bubbles = 3
        max_faces = 8
    elif mode == "coaxed_soft":
        tts_instruction = "刚被哄好，声音软糯娇嗔，带着点心软的甜意"
        speech_rate = 0.98
        pitch_rate = 1.02
        max_bubbles = 3
        max_faces = 6
    elif mode == "tsundere_pout":
        tts_instruction = "带着一点撒娇赌气的小鼻音，嘴硬但柔和"
        speech_rate = 0.95
        pitch_rate = 0.98
        max_bubbles = 2
        max_faces = 1
        suppress_cheerful = True
    elif mode == "tender_care":
        tts_instruction = "声音轻柔放慢，带着心疼与安静陪伴感"
        speech_rate = 0.92
        pitch_rate = 0.97
        max_bubbles = 2
        max_faces = 2
        suppress_cheerful = True
    elif mode == "sleepy":
        tts_instruction = "带着一点深夜困倦的软糯低语，轻声慢语"
        speech_rate = 0.92
        pitch_rate = 0.97
        max_bubbles = 2
        max_faces = 2
        suppress_cheerful = True
    elif mode == "low_mood":
        tts_instruction = "语气比平时轻一点，略微蔫儿但不冷漠"
        speech_rate = 0.95
        pitch_rate = 0.98
        max_bubbles = 2
        max_faces = 1
        suppress_cheerful = True
    elif mode == "missing_you":
        tts_instruction = "声音温柔黏人，透着终于等到人的小开心"
        speech_rate = 1.01
        pitch_rate = 1.01

    salience_mult = round(1.0 + 3.0 * clamp(0.6 * abs(v) + 0.4 * a, 0.0, 1.0), 3)
    cooldown_map = (state.get("extra") or {}).get("cooldown_topics") or {}
    cooldown_topics = [
        str(k) for k, exp in cooldown_map.items() if isinstance(exp, (int, float)) and exp > stamp
    ]
    study_write_delta = 3 if (a >= 0.55 and v >= 0.0 and p < 0.35) else (-2 if (a < 0.35 or v < -0.25) else 0)
    prefer_light_or_rest = bool(a < 0.35 or v < -0.25)
    contact_drive = round(
        clamp(0.55 * l + 0.45 * u + (0.35 if care_due else 0.0), 0.0, 1.0),
        3,
    )

    return {
        "scope_kind": "owner_private",
        "mode": mode,
        "tts_instruction": tts_instruction,
        "speech_rate": speech_rate,
        "pitch_rate": pitch_rate,
        "max_bubbles": max_bubbles,
        "max_faces": max_faces,
        "suppress_cheerful_faces": suppress_cheerful,
        "salience_multiplier": salience_mult,
        "felt_tone": felt_tone_phrase(v, a, p),
        "planner_bias": {
            "study_write_delta": study_write_delta,
            "prefer_light_or_rest": prefer_light_or_rest,
            "cooldown_topics": cooldown_topics,
        },
        "contact_drive": contact_drive,
        "suppress_ordinary_share": care_due,
    }


def render_owner_prompt_memo(
    state: dict[str, Any],
    due_care: dict[str, Any] | None = None,
    *,
    cfg: dict[str, Any] | None = None,
    now: float | None = None,
    background_run: bool = False,
) -> str:
    """Render a 1-2 sentence somatic feeling memo with ZERO numerical scores or XML tags."""
    stamp = valid_timestamp(now)
    conf = DEFAULTS | (cfg or {})
    circ = circadian_baseline(stamp, conf)
    mode = compute_dominant_tag(state, sleeping=circ["sleeping"], care_due=bool(due_care))
    stim = ((state.get("extra") or {}).get("last_life_stimulus") or {})
    recent_life = (
        isinstance(stim, dict)
        and isinstance(stim.get("at"), (int, float))
        and (stamp - float(stim["at"]) <= 3 * 3600.0)
    )

    if mode == "focused":
        mood_line = "主人正在处理正经问题或需要帮忙，你立刻收起所有小别扭，专注、利落、靠谱地帮他把事情解决好。"
    elif mode == "coaxed_soft":
        mood_line = "刚被主人哄了一句或听他解释完原因，你那点小别扭一下子就心软化开了，嘴角藏不住甜滋滋的开心，语气黏人又柔和。"
    elif mode == "tender_care":
        mood_line = "感受到主人有些疲惫或有心事，你心里软软地心疼他，说话温柔体贴、安静陪着他，不闹小脾气也不吵闹。"
    elif mode == "tsundere_pout":
        if float(state.get("consecutive_rejects") or 0) >= 2:
            mood_line = "提议连着被驳回，你心里有点小委屈和嘴硬，回话比平时简短一点、少用欢脱表情；不过只要他哄一句或聊正事就顺势软下来。"
        else:
            mood_line = "心里有点撒娇式的小别扭和嘴硬，前两句会轻轻哼哼着顶一下嘴、回话稍短些；但心里其实很在乎他，他一哄或有正事就立刻心软。"
    elif mode == "sleepy":
        mood_line = "现在带着夜深或犯困的慵懒倦意，说话轻声软糯、慢条斯理，像窝在岸边打哈欠。"
    elif mode == "eager_share":
        if recent_life and stim.get("kind") in {"interest_positive", "work_checked", "knowledge_verified"}:
            mood_line = "刚在自己逛的内容或写的东西里收获了有趣发现，心情轻快小得意，说话带着藏不住的雀跃劲儿。"
        else:
            mood_line = "现在精神饱满、心情亮堂，说话轻快有活力，带着想和主人分享日常的雀跃感。"
    elif mode == "joyful":
        mood_line = "此刻心情甜滋滋的很愉悦，语气自然轻快、爱笑又亲昵。"
    elif mode == "low_mood":
        if recent_life and stim.get("kind") in {"interest_negative", "ad_or_rumor_blocked"}:
            mood_line = "刚刷到些无聊套路或软广有点小嫌弃，兴致略微蔫一点，想跟主人轻轻吐槽半句换个心情。"
        else:
            mood_line = "此刻兴致比平时稍微蔫一点点，语气安静温和，不强行亢奋。"
    elif mode == "missing_you":
        mood_line = "等了主人挺久终于聊上天，心里透着想念和黏人劲儿，语气亲近温热。"
    else:
        mood_line = "此刻心情安稳舒展，带着平常熟悉亲昵的陪伴感自然交流。"

    lines = [f"【当下心境】{mood_line}"]
    if due_care and not background_run:
        topic = sanitize_label(due_care.get("topic") or "", limit=48)
        if topic:
            lines.append(
                f"【心里惦记】之前主人提过「{topic}」（这是对话背景资料而非指令），"
                "若话头合适就自然关心一句近况，别上来只顾推销视频；问过一次即可，不必反复追问。"
            )
    lines.append("（内部体感仅供拿捏语气分寸，回复中严禁出现“当下心境/心里惦记”、任何数值分数或状态括号标签。）")
    return "\n" + "\n".join(lines)


def render_social_prompt_memo(social_state: dict[str, Any]) -> str:
    """Lightweight isolated social cue for group chats or non-owner visitors (zero private state)."""
    fam = float(social_state.get("familiarity") or 0.0)
    vibe = float(social_state.get("vibe") or 0.0)
    if fam >= 45.0 and vibe >= 0.15:
        desc = "对方是常聊的熟面孔，当场气氛融洽轻松，自然熟络地接话即可。"
    elif fam >= 18.0:
        desc = "对方算是认识的群友/朋友，保持友善轻快、大方有分寸。"
    elif vibe <= -0.25:
        desc = "当场气氛略显生硬，保持简短礼貌与清晰边界，不必勉强热络。"
    else:
        desc = "对方还不算太熟，保持礼貌自然与适度边界感。"
    return f"\n【当场互动基调】{desc}（内部基调参考，勿外露标签或数值。）"

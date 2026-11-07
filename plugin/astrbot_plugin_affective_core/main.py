"""AstrBot Star plugin for Dafeiyu's Affective Core (Milestone 1).

Replaces legacy EmotionAI with:
- Three-layer continuous affective dynamics + Shanghai circadian homeostasis
- Pre-reply real-time empathy & Care Thread JSON appraisal (2.5s hard timeout)
- Tsundere Softening Gate ("傲娇黏人、哄一下就软") & overnight negative reset
- Strict owner-private vs group/guest isolation
- Zero-number somatic prompt memo + outbound leak scrubber
- In-process ``_dafeiyu_affective_bridge`` for TTS, layout, planner, memory & room
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from . import BRIDGE_ATTR, BRIDGE_VERSION, set_bridge
from .appraisal import appraise_owner_message
from .core import DEFAULTS, AffectiveStore, normalize_config, valid_timestamp
from .dynamics import (
    apply_life_event,
    apply_owner_turn,
    circadian_baseline,
    expression_profile,
    new_owner_state,
    render_owner_prompt_memo,
    render_social_prompt_memo,
    settle_owner_state,
    should_surface_care,
    strip_sender_tag,
    update_social_turn,
)
from .migration import migrate_from_emotionai

CURRENT_AFFECT_SCOPE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "dafeiyu_current_affect_scope",
    default="",
)

_MOOD_LABELS = {
    "calm": "安稳舒展（平常陪伴）",
    "eager_share": "雀跃献宝（精神饱满、想分享）",
    "joyful": "甜滋滋开心",
    "coaxed_soft": "刚被哄好（心软娇嗔）",
    "tsundere_pout": "傲娇小别扭（嘴硬等哄）",
    "tender_care": "心疼关怀（温柔护短）",
    "sleepy": "困倦慵懒（夜深/低唤醒）",
    "low_mood": "略微蔫儿（小嫌弃/兴致低）",
    "missing_you": "想念黏人",
    "focused": "认真靠谱（正事优先破功态）",
}

_LEGACY_FAKE_USER = "（系统自检：请报告当前情感状态）"

_XML_AFFECT_BLOCK_RE = re.compile(
    r"<(?:thought|thinking|mood|affect|emotion_state)>[\s\S]*?"
    r"</(?:thought|thinking|mood|affect|emotion_state)>",
    re.IGNORECASE,
)
_XML_INLINE_TAG_RE = re.compile(r"</?(?:mood|affect|emotion_state)(?:\s+[^>]*)?>", re.IGNORECASE)
_INTERNAL_MEMO_LINE_RE = re.compile(
    r"(?m)^\s*(?:【(?:当下心境|心里惦记|当场互动基调|当前情感状态|当前情感与认知状态)】[^\n]*|"
    r"（内部(?:体感|基调)[^）\n]*）|\[PROTOCOL_ENFORCEMENT\][^\n]*)\s*\n?",
)
_NUMERIC_SCORE_TAG_RE = re.compile(
    r"\[(?:好感度?|亲密度?|愉悦度|唤醒度|想念度|小别扭|情感更新)\s*[:：]\s*[+-]?\d+(?:\.\d+)?%?\]"
)
_EMPTY_XML_FENCE_RE = re.compile(r"(?:代码片段\s*)?```(?:xml|text)\s*\n?\s*```", re.IGNORECASE)


def scrub_affective_leaks(text: str) -> str:
    """Strip internal prompt echoes, XML mood/thought blocks, or score tags outside code fences."""
    if not text:
        return ""
    # Remove empty leftover xml/text code fences first: ```xml \n ```
    cleaned_fences = _EMPTY_XML_FENCE_RE.sub("", text)
    segments = cleaned_fences.split("```")
    for idx in range(0, len(segments), 2):
        segments[idx] = _scrub_plain_segment(segments[idx])
    joined = "```".join(segments)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


def _scrub_plain_segment(segment: str) -> str:
    s = _XML_AFFECT_BLOCK_RE.sub("", segment)
    s = _XML_INLINE_TAG_RE.sub("", s)
    s = _INTERNAL_MEMO_LINE_RE.sub("", s)
    s = _NUMERIC_SCORE_TAG_RE.sub("", s)
    s = re.sub(r"(\n*\s*【当前情感状态】[\s\S]*$)", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


def clean_legacy_contexts(contexts: Any) -> Any:
    """Clean residual historical EmotionAI fake turns if present in old conversations."""
    if not isinstance(contexts, list):
        return contexts
    cleaned = []
    skip_next = False
    for ctx in contexts:
        if skip_next:
            skip_next = False
            continue
        if isinstance(ctx, dict) and "content" in ctx:
            content_val = ctx["content"]
            if isinstance(content_val, str):
                if "[PROTOCOL_ENFORCEMENT]" in content_val:
                    continue
                if _LEGACY_FAKE_USER in content_val:
                    skip_next = True
                    continue
                scrubbed = scrub_affective_leaks(content_val)
                if scrubbed:
                    copied = dict(ctx)
                    copied["content"] = scrubbed
                    cleaned.append(copied)
            else:
                cleaned.append(ctx)
        elif isinstance(ctx, str):
            if "[PROTOCOL_ENFORCEMENT]" in ctx:
                continue
            if _LEGACY_FAKE_USER in ctx:
                skip_next = True
                continue
            scrubbed = scrub_affective_leaks(ctx)
            if scrubbed:
                cleaned.append(scrubbed)
        else:
            cleaned.append(ctx)
    return cleaned


class AffectiveBridge:
    """In-process read/event bridge exposed on ``context._dafeiyu_affective_bridge``."""

    version = BRIDGE_VERSION

    def __init__(self, plugin: "AffectiveCorePlugin"):
        self._plugin = plugin

    def get_snapshot(self, scope: str | None = None, *, now: float | None = None) -> dict[str, Any] | None:
        return self._plugin.owner_snapshot(scope=scope, now=now)

    def expression_profile(self, scope: str | None = None, *, now: float | None = None) -> dict[str, Any]:
        return self._plugin.resolve_expression_profile(scope=scope, now=now)

    def record_life_event(
        self,
        scope: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        return self._plugin.record_life_event(scope, kind, payload=payload, now=now)

    def active_care_threads(self, scope: str | None = None, *, now: float | None = None) -> list[dict[str, Any]]:
        return self._plugin.get_active_care_threads(scope=scope, now=now)


def _resolve_plugin_data_dir() -> Path:
    try:
        return Path(StarTools.get_data_dir("astrbot_plugin_affective_core"))
    except TypeError:
        return Path(StarTools.get_data_dir()) / "astrbot_plugin_affective_core"


@register(
    "astrbot_plugin_affective_core",
    "dafeiyu-tools",
    "三层情感动力学、回复前实时共情评估、跨天牵挂账本与多通道情绪表达桥接",
    "0.1.0",
)
class AffectiveCorePlugin(Star):
    def __init__(
        self,
        context: Context,
        config: dict[str, Any] | None = None,
        *,
        data_dir: str | Path | None = None,
    ):
        super().__init__(context)
        self.context = context
        self._raw_config = config if isinstance(config, dict) else {}
        self.folder = Path(data_dir) if data_dir is not None else _resolve_plugin_data_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.control_path = self.folder / "control.json"
        if not self.control_path.exists():
            self.control_path.write_text(
                json.dumps(DEFAULTS, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self.store = AffectiveStore(self.folder)
        self._turn_cache: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._last_turn_context: dict[str, Any] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        self.bridge = AffectiveBridge(self)
        set_bridge(self.bridge)
        if self.context is not None:
            setattr(self.context, BRIDGE_ATTR, self.bridge)

        try:
            conf = self.cfg()
            mig = migrate_from_emotionai(
                self.store,
                self.folder.parent,
                owner_scope=conf["owner_scope"],
                owner_id=conf["owner_id"],
                cfg=conf,
            )
            if mig.get("migrated"):
                logger.info("[affective_core] imported legacy EmotionAI baseline: %s", mig.get("receipt"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[affective_core] legacy EmotionAI migration skipped: %s", type(exc).__name__)

        logger.info("[affective_core] loaded v0.1.0 (db=%s)", self.store.db_path)

    def _discover_sibling_owner(self) -> tuple[str, str, set[str]]:
        """Read-only discovery of owner_scope / owner_id / admins from sibling AstrBot files."""
        owner_scope = ""
        owner_id = ""
        admins: set[str] = set()

        bili_control = self.folder.parent / "astrbot_plugin_bili_activity" / "control.json"
        try:
            if bili_control.is_file():
                bdata = json.loads(bili_control.read_text(encoding="utf-8"))
                if isinstance(bdata, dict):
                    owner_scope = str(bdata.get("owner_scope") or "").strip()
                    owner_id = str(bdata.get("owner_id") or "").strip()
                    if owner_id:
                        admins.add(owner_id)
        except (OSError, TypeError, ValueError):
            pass

        cmd_cfg = self.folder.parent.parent / "cmd_config.json"
        try:
            if cmd_cfg.is_file():
                cdata = json.loads(cmd_cfg.read_text(encoding="utf-8-sig"))
                if isinstance(cdata, dict):
                    for aid in cdata.get("admins_id") or []:
                        s = str(aid).strip()
                        if s:
                            admins.add(s)
                            if not owner_id:
                                owner_id = s
        except (OSError, TypeError, ValueError):
            pass

        return owner_scope, owner_id, admins

    def cfg(self) -> dict[str, Any]:
        disk_cfg: dict[str, Any] = {}
        try:
            if self.control_path.is_file():
                loaded = json.loads(self.control_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    disk_cfg = loaded
        except (OSError, TypeError, ValueError):
            pass

        merged_raw = {**disk_cfg, **self._raw_config}
        conf = normalize_config(merged_raw)
        sib_scope, sib_id, admins = self._discover_sibling_owner()
        if not conf["owner_scope"] and sib_scope:
            conf["owner_scope"] = sib_scope
        if not conf["owner_id"] and sib_id:
            conf["owner_id"] = sib_id
        if conf["owner_id"]:
            admins.add(conf["owner_id"])
        conf["_admins"] = admins
        return conf

    @staticmethod
    def _is_group_event(event: Any) -> bool:
        try:
            gid = event.get_group_id() if hasattr(event, "get_group_id") else ""
            if gid:
                return True
        except Exception:  # noqa: BLE001
            pass
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        return "GroupMessage" in umo

    def is_owner_private(self, event: Any, conf: dict[str, Any] | None = None) -> bool:
        """True ONLY in the owner's private chat; strictly False in all group chats."""
        if self._is_group_event(event):
            return False
        cfg = conf or self.cfg()
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        sender_id = str(event.get_sender_id() if hasattr(event, "get_sender_id") else "").strip()
        admins: set[str] = cfg.get("_admins") or set()

        if cfg["owner_scope"]:
            if umo != cfg["owner_scope"]:
                return False
            if cfg["owner_id"] and sender_id and sender_id != cfg["owner_id"]:
                return False
            return True

        is_priv = ":FriendMessage:" in umo or bool(
            hasattr(event, "is_private_chat") and event.is_private_chat()
        )
        if not is_priv:
            return False
        if cfg["owner_id"] and sender_id == cfg["owner_id"]:
            return True
        if sender_id and sender_id in admins:
            return True
        return getattr(event, "role", "") == "admin" and bool(sender_id)

    def is_owner_sender(self, event: Any, conf: dict[str, Any] | None = None) -> bool:
        cfg = conf or self.cfg()
        sender_id = str(event.get_sender_id() if hasattr(event, "get_sender_id") else "").strip()
        admins: set[str] = cfg.get("_admins") or set()
        if cfg["owner_id"] and sender_id == cfg["owner_id"]:
            return True
        if sender_id and sender_id in admins:
            return True
        return getattr(event, "role", "") == "admin"

    def _resolve_owner_scope(self, scope: str | None = None, conf: dict[str, Any] | None = None) -> str:
        cfg = conf or self.cfg()
        if scope:
            if "GroupMessage" in scope:
                return ""
            if cfg["owner_scope"] and scope != cfg["owner_scope"]:
                row = self.store.get_owner_state(scope)
                return scope if row else ""
            return scope
        if cfg["owner_scope"]:
            return cfg["owner_scope"]
        states = self.store.list_owner_states()
        return states[0]["scope"] if states else ""

    def owner_snapshot(self, scope: str | None = None, *, now: float | None = None) -> dict[str, Any] | None:
        stamp = valid_timestamp(now)
        conf = self.cfg()
        target_scope = self._resolve_owner_scope(scope, conf)
        if not target_scope:
            return None
        raw = self.store.get_owner_state(target_scope)
        if raw is None:
            raw = new_owner_state(target_scope, conf["owner_id"], conf, now=stamp)
        settled, _ = settle_owner_state(raw, conf, now=stamp)
        cares = self.store.active_care_threads(
            target_scope,
            limit=conf["care_active_limit"],
            now=stamp,
        )
        profile = expression_profile(settled, cares, conf, now=stamp)
        return {
            "scope": target_scope,
            "state": settled,
            "care_threads": cares,
            "expression": profile,
            "circadian": circadian_baseline(stamp, conf),
        }

    def get_active_care_threads(
        self,
        scope: str | None = None,
        *,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        stamp = valid_timestamp(now)
        conf = self.cfg()
        target_scope = self._resolve_owner_scope(scope, conf)
        if not target_scope:
            return []
        return self.store.active_care_threads(
            target_scope,
            limit=conf["care_active_limit"],
            now=stamp,
        )

    def resolve_expression_profile(
        self,
        scope: str | None = None,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        stamp = valid_timestamp(now)
        conf = self.cfg()
        if not conf["enabled"]:
            return expression_profile(None, None, conf, now=stamp)

        effective_scope = scope or CURRENT_AFFECT_SCOPE.get("")
        if not effective_scope and self._last_turn_context:
            if stamp - float(self._last_turn_context.get("at") or 0.0) <= 60.0:
                if not self._last_turn_context.get("is_owner_private"):
                    return expression_profile(None, None, conf, now=stamp)
                effective_scope = str(self._last_turn_context.get("scope") or "")

        if not effective_scope or "GroupMessage" in effective_scope:
            return expression_profile(None, None, conf, now=stamp)

        target_scope = self._resolve_owner_scope(effective_scope, conf)
        if not target_scope:
            return expression_profile(None, None, conf, now=stamp)

        snap = self.owner_snapshot(target_scope, now=stamp)
        return snap["expression"] if snap else expression_profile(None, None, conf, now=stamp)

    def record_life_event(
        self,
        scope: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        stamp = valid_timestamp(now)
        conf = self.cfg()
        if not conf["enabled"]:
            return None
        target_scope = self._resolve_owner_scope(scope, conf)
        if not target_scope:
            return None

        with self.store.transaction():
            current = self.store.get_owner_state(target_scope)
            if current is None:
                current = new_owner_state(target_scope, conf["owner_id"], conf, now=stamp)
            updated, summary = apply_life_event(current, kind, payload, conf, now=stamp)
            saved = self.store.save_owner_state(updated)
            self.store.record_event(target_scope, "life_event", kind, summary, now=stamp)
        cares = self.store.active_care_threads(target_scope, limit=conf["care_active_limit"], now=stamp)
        return {
            "state": saved,
            "expression": expression_profile(saved, cares, conf, now=stamp),
        }

    async def _analysis_provider(self, event: Any, conf: dict[str, Any]) -> Any:
        if self.context is None:
            return None
        pid = conf.get("appraisal_provider_id") or ""
        if pid and hasattr(self.context, "get_provider_by_id"):
            prov = self.context.get_provider_by_id(pid)
            if prov:
                return prov
        if hasattr(self.context, "get_using_provider_async"):
            umo = getattr(event, "unified_msg_origin", "")
            return await self.context.get_using_provider_async(umo)
        return None

    @staticmethod
    def _turn_key(event: Any) -> tuple[str, str] | None:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        msg_obj = getattr(event, "message_obj", None)
        msg_id = getattr(msg_obj, "message_id", None) if msg_obj is not None else None
        if msg_id is None or str(msg_id).strip() == "":
            return None
        return (umo, str(msg_id).strip())

    def _remember_turn(self, key: tuple[str, str], memo: str, profile: dict[str, Any]) -> None:
        self._turn_cache[key] = {"memo": memo, "profile": profile}
        while len(self._turn_cache) > 128:
            self._turn_cache.popitem(last=False)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000000)
    async def check_blacklist_and_commands(self, event: AstrMessageEvent):
        conf = self.cfg()
        if not conf["enabled"]:
            return
        raw_text = strip_sender_tag(getattr(event, "message_str", "") or "")
        if raw_text in {"/心情", "查看心情"}:
            async for item in self.cmd_mood(event):
                yield item
            return

        if not conf["blacklist_enabled"] or self.is_owner_sender(event, conf):
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        uid = str(event.get_sender_id() if hasattr(event, "get_sender_id") else "").strip()
        if uid and self.store.is_user_blacklisted(umo, uid):
            yield event.plain_result("您已加入黑名单，请联系管理员移除。")
            event.stop_event()

    @filter.on_llm_request(priority=200)
    async def inject_affective_context(self, event: AstrMessageEvent, req: Any) -> None:
        conf = self.cfg()
        if hasattr(req, "contexts") and isinstance(req.contexts, list):
            req.contexts = clean_legacy_contexts(req.contexts)
        if getattr(req, "system_prompt", None) and "[PROTOCOL_ENFORCEMENT]" in req.system_prompt:
            req.system_prompt = re.sub(
                r"\[PROTOCOL_ENFORCEMENT\][\s\S]*?(?=\n\s*\n|\Z)",
                "",
                req.system_prompt,
                flags=re.IGNORECASE,
            ).strip()

        if not conf["enabled"]:
            return

        now = time.time()
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        CURRENT_AFFECT_SCOPE.set(umo)
        is_bg = bool(hasattr(event, "get_extra") and event.get_extra("df_run_id"))
        owner_priv = self.is_owner_private(event, conf)

        self._last_turn_context = {
            "scope": umo,
            "is_owner_private": owner_priv,
            "at": now,
        }

        if not owner_priv:
            if not is_bg:
                uid = str(event.get_sender_id() if hasattr(event, "get_sender_id") else "").strip()
                social = self.store.get_social_state(umo, uid)
                if uid:
                    social = update_social_turn(social, getattr(event, "message_str", "") or "", now=now)
                    self.store.save_social_state(social, now=now)
                if conf["inject_prompt_memo"]:
                    req.system_prompt = (req.system_prompt or "") + render_social_prompt_memo(social)
            return

        # Background autonomous run for owner scope: settle time only, do NOT reset longing or appraise chat
        if is_bg:
            with self.store.transaction():
                raw = self.store.get_owner_state(umo) or new_owner_state(umo, conf["owner_id"], conf, now=now)
                settled, notes = settle_owner_state(raw, conf, now=now)
                saved = self.store.save_owner_state(settled)
                if notes:
                    self.store.record_event(umo, "time_settle", "overnight_reset", {"notes": notes}, now=now)
            if conf["inject_prompt_memo"]:
                req.system_prompt = (req.system_prompt or "") + render_owner_prompt_memo(
                    saved,
                    None,
                    cfg=conf,
                    now=now,
                    background_run=True,
                )
            return

        # Deduplicate tool-loop continuations or provider retries on the same foreground message
        tkey = self._turn_key(event)
        if tkey is not None and tkey in self._turn_cache:
            cached = self._turn_cache[tkey]
            if conf["inject_prompt_memo"] and cached["memo"]:
                req.system_prompt = (req.system_prompt or "") + cached["memo"]
            return

        raw_state = self.store.get_owner_state(umo) or new_owner_state(
            umo,
            conf["owner_id"] or str(event.get_sender_id()),
            conf,
            now=now,
        )
        settled_state, settle_notes = settle_owner_state(raw_state, conf, now=now)
        active_cares = self.store.active_care_threads(umo, limit=conf["care_active_limit"], now=now)
        user_text = getattr(event, "message_str", "") or ""

        if conf["appraisal_mode"] == "sync_pre":
            provider = await self._analysis_provider(event, conf)
            appraisal = await appraise_owner_message(
                provider,
                user_text,
                settled_state,
                active_cares,
                conf,
                now=now,
                is_background_run=False,
                logger_obj=logger,
            )
        else:
            appraisal = await appraise_owner_message(
                None,
                user_text,
                settled_state,
                active_cares,
                {**conf, "appraisal_mode": "math_only"},
                now=now,
                is_background_run=False,
                logger_obj=logger,
            )
            if conf["appraisal_mode"] == "async_post":
                self._schedule_async_appraisal(event, umo, user_text, settled_state, active_cares, conf)

        saved_state, due_care, profile = self._commit_owner_appraisal(
            umo,
            settled_state,
            settle_notes,
            appraisal,
            conf,
            now=now,
        )
        memo = render_owner_prompt_memo(saved_state, due_care, cfg=conf, now=now, background_run=False)
        if tkey is not None:
            self._remember_turn(tkey, memo, profile)
        if conf["inject_prompt_memo"]:
            req.system_prompt = (req.system_prompt or "") + memo

    def _commit_owner_appraisal(
        self,
        scope: str,
        settled_state: dict[str, Any],
        settle_notes: list[str],
        appraisal: dict[str, Any],
        conf: dict[str, Any],
        *,
        now: float,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
        with self.store.transaction():
            if settle_notes:
                self.store.record_event(
                    scope,
                    "time_settle",
                    "overnight_reset",
                    {"notes": settle_notes},
                    now=now,
                )

            resolved_ids = appraisal.get("care_resolved_ids") or []
            if resolved_ids:
                done = self.store.resolve_care_threads(scope, resolved_ids, now=now)
                if done:
                    self.store.record_event(
                        scope,
                        "care_thread",
                        "resolved",
                        {"count": len(done)},
                        now=now,
                    )

            care_open = appraisal.get("care_open")
            if isinstance(care_open, dict) and care_open.get("topic"):
                salience = max(0.55, float(appraisal.get("user_distress") or 0.6))
                opened = self.store.upsert_care_thread(
                    scope,
                    str(care_open["topic"]),
                    category=str(care_open.get("category") or "general"),
                    salience=salience,
                    ttl_hours=float(care_open.get("ttl_hours") or 36.0),
                    limit=conf["care_active_limit"],
                    now=now,
                )
                if opened:
                    self.store.record_event(
                        scope,
                        "care_thread",
                        "opened_or_updated",
                        {"id": opened["id"], "category": opened["category"]},
                        now=now,
                    )

            refreshed_cares = self.store.active_care_threads(
                scope,
                limit=conf["care_active_limit"],
                now=now,
            )
            prelim_due = next(
                (t for t in refreshed_cares if should_surface_care(t, settled_state, conf, now=now)),
                None,
            )
            updated_state, turn_summary = apply_owner_turn(
                settled_state,
                appraisal,
                conf,
                now=now,
                care_due=bool(prelim_due),
            )
            due_care = next(
                (t for t in refreshed_cares if should_surface_care(t, updated_state, conf, now=now)),
                None,
            )
            if due_care is not None:
                self.store.mark_care_prompted(scope, due_care["id"], now=now)
                due_care = {**due_care, "prompt_count": int(due_care["prompt_count"]) + 1, "last_prompted_at": now}

            saved_state = self.store.save_owner_state(updated_state)
            self.store.record_event(scope, "chat_appraisal", turn_summary["intent"], turn_summary, now=now)
            profile = expression_profile(saved_state, refreshed_cares, conf, now=now)
            return saved_state, due_care, profile

    def _schedule_async_appraisal(
        self,
        event: Any,
        scope: str,
        user_text: str,
        settled_state: dict[str, Any],
        active_cares: list[dict[str, Any]],
        conf: dict[str, Any],
    ) -> None:
        async def _runner():
            try:
                provider = await self._analysis_provider(event, conf)
                if not provider:
                    return
                res = await appraise_owner_message(
                    provider,
                    user_text,
                    settled_state,
                    active_cares,
                    {**conf, "appraisal_mode": "sync_pre"},
                    is_background_run=False,
                    logger_obj=logger,
                )
                if res.get("source") != "llm_appraisal":
                    return
                now_post = time.time()
                with self.store.transaction():
                    if res.get("care_resolved_ids"):
                        self.store.resolve_care_threads(scope, res["care_resolved_ids"], now=now_post)
                    care_open = res.get("care_open")
                    if isinstance(care_open, dict) and care_open.get("topic"):
                        self.store.upsert_care_thread(
                            scope,
                            str(care_open["topic"]),
                            category=str(care_open.get("category") or "general"),
                            salience=max(0.55, float(res.get("user_distress") or 0.6)),
                            ttl_hours=float(care_open.get("ttl_hours") or 36.0),
                            limit=conf["care_active_limit"],
                            now=now_post,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[affective_core] async appraisal skipped: %s", type(exc).__name__)

        task = asyncio.create_task(_runner())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    @filter.on_llm_response(priority=200)
    async def scrub_response_artifacts(self, event: AstrMessageEvent, resp: Any) -> None:
        raw = getattr(resp, "completion_text", None)
        if not raw or not isinstance(raw, str):
            return
        cleaned = scrub_affective_leaks(raw)
        if cleaned != raw.strip():
            resp.completion_text = cleaned

    def format_mood_card(self, scope: str, *, now: float | None = None) -> str:
        stamp = valid_timestamp(now)
        snap = self.owner_snapshot(scope, now=stamp)
        if not snap:
            return "【心境中枢】尚未初始化主人私聊心境记录。"
        st = snap["state"]
        exp = snap["expression"]
        circ = snap["circadian"]
        cares = snap["care_threads"]
        recent = self.store.recent_events(scope, limit=3)

        mode_cn = _MOOD_LABELS.get(exp["mode"], exp["mode"])
        lines = [
            "🐋【大肥鱼心境中枢 · 只读诊断】",
            f"主导状态：{mode_cn}",
            f"生理时段：{circ['phase']}（{'睡眠静默期' if circ['sleeping'] else '清醒期'}）",
            "",
            "▸ 三层情感动力学",
            f"  愉悦度 Valence：{st['valence']:+.2f}  |  唤醒度 Arousal：{st['arousal']:.2f}",
            f"  想念度 Longing：{st['longing']:.2f}  |  傲娇别扭 Pout：{st['pout']:.2f}",
            f"  分享欲 Urge：{st['share_urge']:.2f}     |  亲密基线 Bond：{st['warmth_bond']:.1f}/1000",
        ]
        if st.get("softened_mode"):
            lines.append(f"  心软破功门控：{st['softened_mode']}（当轮已生效）")

        lines.append("")
        lines.append(f"▸ 牵挂账本（活跃 {len(cares)} 条）")
        if not cares:
            lines.append("  （暂无挂起牵挂）")
        else:
            for item in cares:
                rem_h = max(0.0, (float(item["expires_at"]) - stamp) / 3600.0)
                lines.append(f"  - [{item['category']}] {item['topic']}（余 {rem_h:.0f}h，已提醒 {item['prompt_count']} 次）")

        lines.append("")
        lines.append("▸ 外化通道参数预览")
        lines.append(
            f"  语音：倍速 {exp['speech_rate']:.2f}x / 音高 {exp['pitch_rate']:.2f}x"
            + (f"（{exp['tts_instruction']}）" if exp["tts_instruction"] else "（默认原声）")
        )
        lines.append(
            f"  排版：最多 {exp['max_bubbles']} 气泡 / 最多 {exp['max_faces']} 表情  |  记忆显著性：{exp['salience_multiplier']:.2f}x"
        )

        if recent:
            lines.append("")
            lines.append("▸ 最近脉冲来源")
            for ev in recent:
                lines.append(f"  - {ev['source']}:{ev['kind']}")

        return "\n".join(lines)

    @filter.command("心情", priority=5)
    async def cmd_mood(self, event: AstrMessageEvent):
        """主人私聊专属只读心境面板。"""
        conf = self.cfg()
        if self._is_group_event(event):
            event.stop_event()
            return
        if not self.is_owner_private(event, conf):
            yield event.plain_result("这是主人私聊专属指令哦。")
            event.stop_event()
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        yield event.plain_result(self.format_mood_card(umo))
        event.stop_event()

    async def terminate(self) -> None:
        for task in list(self._bg_tasks):
            task.cancel()
        if self.context is not None and getattr(self.context, BRIDGE_ATTR, None) is self.bridge:
            delattr(self.context, BRIDGE_ATTR)
        self.store.close()
        logger.info("[affective_core] terminated cleanly")

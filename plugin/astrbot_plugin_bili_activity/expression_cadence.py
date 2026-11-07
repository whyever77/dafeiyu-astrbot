"""Foreground prompt cadence, not a delivery receipt or a new memory.

Only the owner's foreground request hook uses this writer. Explicit reflection
and status reads continue using the read-only SelfExpression view.
"""
from __future__ import annotations

import json
import re

from .core import canonical, digest
from .memory_policy import _clock
from .self_expression import HELD_THOUGHT_WINDOW_SECONDS, SelfExpression, enabled

COOLDOWN_SECONDS = 6 * 3600
EVENT_KIND = "held_thought_context"
ACK_EVENT_KIND = "held_thought_acknowledged"
SCAN_LIMIT = 500


def _clean_text(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE).lower()


def thought_mentioned(candidate: dict, reply_text: str) -> bool:
    """Return True if the model's reply text meaningfully discussed the held candidate."""
    if not isinstance(reply_text, str) or not reply_text.strip():
        return False
    if not isinstance(candidate, dict):
        return False

    cleaned_reply = _clean_text(reply_text)
    if not cleaned_reply:
        return False

    excerpt = candidate.get("excerpt") or ""
    cleaned_excerpt = _clean_text(excerpt)
    goal_title = candidate.get("goal_title") or ""
    cleaned_goal = _clean_text(goal_title)

    # 1. Full cleaned excerpt match (if >= 4 chars)
    if cleaned_excerpt and len(cleaned_excerpt) >= 4 and cleaned_excerpt in cleaned_reply:
        return True

    # 2. Match with goal title
    if cleaned_goal and len(cleaned_goal) >= 3 and cleaned_goal in cleaned_reply:
        if len(cleaned_goal) >= 5:
            return True
        if cleaned_excerpt:
            for i in range(len(cleaned_excerpt) - 2):
                if cleaned_excerpt[i:i + 3] in cleaned_reply:
                    return True

    # 3. Punctuation-delimited segments of excerpt
    raw_segments = [s.strip() for s in re.split(r"[\s，。！？；：、“”‘’《》…,.!?;:\'\"—\-]+", excerpt) if s.strip()]
    matched_segments = 0
    for seg in raw_segments:
        c_seg = _clean_text(seg)
        if len(c_seg) >= 6 and c_seg in cleaned_reply:
            return True
        if len(c_seg) >= 3 and c_seg in cleaned_reply:
            matched_segments += 1
            if matched_segments >= 2:
                return True

    # 4. Sliding 4-shingles from excerpt
    if cleaned_excerpt and len(cleaned_excerpt) >= 4:
        shingles = {cleaned_excerpt[i:i + 4] for i in range(len(cleaned_excerpt) - 3)}
        matched_shingles = sum(1 for sh in shingles if sh in cleaned_reply)
        if len(cleaned_excerpt) <= 6 and matched_shingles >= 1:
            return True
        if matched_shingles >= 2:
            return True

    return False


class ExpressionCadence:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.view = SelfExpression(store)

    def context(self, scope, cfg, message_id, *, now=None, event=None):
        """Select at most one candidate; retries of one message reuse its choice.

        No stable message id (including background runs) means no automatic
        held excerpt. Exposure survives restart but never marks anything sent.
        """
        if not enabled(cfg):
            return self.view.context(scope, cfg, now=now)
        now = _clock(now)
        if (cfg.get("enabled") is False or type(message_id) not in (str, int)
                or not 1 <= len(str(message_id).strip()) <= 256):
            value = self.view.snapshot(scope, cfg, now=now)
            value["held_thoughts"] = []
            return self.view.render_context(value)

        message_key = digest([scope, str(message_id)])
        # Selection and recording are atomic across requests/connections. This
        # transaction contains only bounded local reads and one event insert.
        with self.store.transaction():
            value = self.view.snapshot(scope, cfg, now=now)
            candidates = value["held_thoughts"]
            value["held_thoughts"] = []
            if not candidates:
                return self.view.render_context(value)
            rows = self.db.execute(
                "SELECT time,details FROM events WHERE scope=? AND kind=? AND time>=? "
                "ORDER BY seq DESC LIMIT ?",
                (scope, EVENT_KIND, max(0, now - HELD_THOUGHT_WINDOW_SECONDS), SCAN_LIMIT + 1),
            ).fetchall()
            # If coverage is incomplete, do not guess that a candidate is new.
            if len(rows) > SCAN_LIMIT:
                return self.view.render_context(value)
            recent = set()
            for row in rows:
                try:
                    record = json.loads(row["details"])
                except (ValueError, TypeError):
                    return self.view.render_context(value)
                if (not isinstance(record, dict) or record.get("state") != "context_only"
                        or not isinstance(record.get("message_key"), str)
                        or (record.get("held_id") is not None and not isinstance(record["held_id"], str))):
                    return self.view.render_context(value)
                held_id = record.get("held_id")
                if record["message_key"] == message_key:
                    # A sent, expired or revised candidate is not resurrected,
                    # and a continuation must never switch to another thought.
                    chosen = [c for c in candidates if c["held_id"] == held_id][:1]
                    value["held_thoughts"] = chosen
                    if event is not None and chosen:
                        setattr(event, "_df_cadence_selected", chosen[0])
                    return self.view.render_context(value)
                if row["time"] > now - COOLDOWN_SECONDS:
                    recent.add(held_id)
            selected = next((c for c in candidates if c["held_id"] not in recent), None)
            value["held_thoughts"] = [selected] if selected else []
            if event is not None and selected is not None:
                setattr(event, "_df_cadence_selected", selected)
            context = self.view.render_context(value)
            self.db.execute(
                "INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                (now, scope, EVENT_KIND, canonical({"message_key": message_key,
                    "held_id": selected["held_id"] if selected else None, "state": "context_only"})),
            )
            return context

    def acknowledge_if_discussed(self, scope, reply_text, event=None, *, now=None):
        """Mark a held thought acknowledged if the model discussed it in this turn."""
        if not isinstance(reply_text, str) or not reply_text.strip():
            return False
        now = _clock(now)
        candidate = getattr(event, "_df_cadence_selected", None) if event is not None else None
        with self.store.transaction():
            if candidate is None:
                row = self.db.execute(
                    "SELECT details FROM events WHERE scope=? AND kind=? AND time>=? "
                    "ORDER BY seq DESC LIMIT 1",
                    (scope, EVENT_KIND, now - 900),
                ).fetchone()
                if row:
                    try:
                        record = json.loads(row["details"])
                        held_id = record.get("held_id") if isinstance(record, dict) else None
                        if held_id:
                            candidates = self.view._held_thoughts(scope, now, 14)
                            candidate = next((c for c in candidates if c.get("held_id") == held_id), None)
                    except (ValueError, TypeError):
                        candidate = None

            if not candidate or not thought_mentioned(candidate, reply_text):
                return False

            held_id = candidate.get("held_id")
            delivery_id = candidate.get("delivery_id")
            fingerprint = candidate.get("fingerprint")
            excerpt = candidate.get("excerpt")

            ack_details = {
                "held_id": held_id,
                "excerpt": excerpt,
            }
            if delivery_id:
                ack_details["delivery_id"] = delivery_id
            if fingerprint:
                ack_details["fingerprint"] = fingerprint

            self.db.execute(
                "INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                (now, scope, ACK_EVENT_KIND, canonical(ack_details)),
            )

            has_deliveries = bool(
                self.db.execute("SELECT 1 FROM sqlite_master WHERE name='deliveries' AND type='table'").fetchone()
            )
            if has_deliveries:
                receipt = canonical({"chat_discussed": True, "at": now})
                if delivery_id:
                    self.db.execute(
                        "UPDATE deliveries SET status='sent', receipt=? WHERE scope=? AND id=?",
                        (receipt, scope, delivery_id),
                    )
                if fingerprint:
                    self.db.execute(
                        "UPDATE deliveries SET status='sent', receipt=? WHERE scope=? AND fingerprint=? AND status='blocked'",
                        (receipt, scope, fingerprint),
                    )

            if event is not None and hasattr(event, "_df_cadence_selected"):
                delattr(event, "_df_cadence_selected")
            return True

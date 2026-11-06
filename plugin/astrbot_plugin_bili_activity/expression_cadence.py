"""Foreground prompt cadence, not a delivery receipt or a new memory.

Only the owner's foreground request hook uses this writer. Explicit reflection
and status reads continue using the read-only SelfExpression view.
"""
from __future__ import annotations

import json

from .core import canonical, digest
from .memory_policy import _clock
from .self_expression import HELD_THOUGHT_WINDOW_SECONDS, SelfExpression, enabled

COOLDOWN_SECONDS = 6 * 3600
EVENT_KIND = "held_thought_context"
SCAN_LIMIT = 500


class ExpressionCadence:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.view = SelfExpression(store)

    def context(self, scope, cfg, message_id, *, now=None):
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
                    value["held_thoughts"] = [c for c in candidates if c["held_id"] == held_id][:1]
                    return self.view.render_context(value)
                if row["time"] > now - COOLDOWN_SECONDS:
                    recent.add(held_id)
            selected = next((c for c in candidates if c["held_id"] not in recent), None)
            value["held_thoughts"] = [selected] if selected else []
            context = self.view.render_context(value)
            self.db.execute(
                "INSERT INTO events(time,scope,kind,details) VALUES(?,?,?,?)",
                (now, scope, EVENT_KIND, canonical({"message_key": message_key,
                    "held_id": selected["held_id"] if selected else None, "state": "context_only"})),
            )
            return context

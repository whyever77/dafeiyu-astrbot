"""Opt-in bridge to the existing life plugin; no new sender or scheduler."""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import json
from pathlib import Path
import uuid

from astrbot.api import logger

CURRENT = ContextVar("dafeiyu_contact_attempt", default=None)


class StopContact(BaseException):
    """Stop every remaining segment, including legacy broad Exception handlers."""


def control_path():
    # Installed at data/plugins/<plugin>/core/services/contact_bridge.py.
    return Path(__file__).resolve().parents[4] / "plugin_data/astrbot_plugin_bili_activity/control.json"


def coordinator(plugin, scope):
    path = control_path()
    if not path.exists():
        return None
    cfg = json.loads(path.read_text(encoding="utf-8"))
    enabled = cfg.get("contact_coordination_enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("invalid contact switch")
    if not enabled or scope != cfg.get("owner_scope"):
        return None
    api = getattr(plugin.context, "dafeiyu_contact_v1", None)
    if api is None or getattr(api, "version", None) != 1 or not getattr(api, "active", False):
        raise PermissionError("contact coordinator unavailable")
    return api


def preflight(plugin, scope):
    try:
        api = coordinator(plugin, scope)
        if api:
            api.check(scope)
        return True
    except Exception:
        logger.info("[主动联系] 本次安排不满足联系条件，跳过生成。")
        return False


def before_dispatch(plugin, scope):
    current = CURRENT.get()
    if current is None:
        return
    api, ticket, expected_scope = current
    try:
        if scope != expected_scope or coordinator(plugin, scope) is not api:
            raise PermissionError("contact policy changed")
        api.dispatch(ticket)
    except Exception:
        raise StopContact() from None


def complete(api, ticket, outcome):
    try:
        api.finish(ticket, outcome)
    except Exception:
        # An interrupted/reloaded coordinator keeps its pending reservation;
        # it must not turn an unknown send into permission to resend.
        logger.warning("[主动联系] 联系结果待核对，不自动补发。")


def coordinated_send(method):
    @wraps(method)
    async def wrapped(self, session_id, text, *args, **kwargs):
        try:
            api = coordinator(self.plugin, session_id)
        except Exception:
            logger.warning("[主动联系] 协调器不可用，本次不发送。")
            return False
        if api is None:
            return await method(self, session_id, text, *args, **kwargs)
        # Unlabelled media has no stable text identity in this bridge version.
        if not isinstance(text, str) or not text.strip():
            return False
        try:
            ticket = api.reserve(session_id, uuid.uuid4().hex, text)
        except Exception:
            logger.info("[主动联系] 本次安排不满足联系条件，跳过发送。")
            return False
        token = CURRENT.set((api, ticket, session_id))
        try:
            result = await method(self, session_id, text, *args, **kwargs)
        except StopContact:
            complete(api, ticket, "cancelled")
            return False
        except BaseException:
            complete(api, ticket, "uncertain")
            raise
        else:
            # Community API reports a bool, not a OneBot message ID or readback.
            complete(api, ticket, "reported" if result is True else "uncertain")
            return result
        finally:
            CURRENT.reset(token)
    return wrapped

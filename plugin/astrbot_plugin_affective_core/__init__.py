"""Affective Core plugin package for Dafeiyu."""
from __future__ import annotations

from typing import Any

BRIDGE_ATTR = "_dafeiyu_affective_bridge"
BRIDGE_VERSION = "affective_bridge_v1"

_ACTIVE_BRIDGE: Any | None = None


def get_bridge() -> Any | None:
    """Return the active in-process AffectiveBridge instance, if registered."""
    return _ACTIVE_BRIDGE


def set_bridge(bridge: Any | None) -> None:
    """Register or clear the active in-process AffectiveBridge instance."""
    global _ACTIVE_BRIDGE
    _ACTIVE_BRIDGE = bridge

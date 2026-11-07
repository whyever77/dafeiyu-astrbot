"""Read-only, idempotent importer from legacy EmotionAI v3.x user_emotion_data.json.

Never modifies or deletes original EmotionAI files. Safe to invoke multiple times.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import GLOBAL_SOCIAL_SCOPE, AffectiveStore, clamp, valid_timestamp
from .dynamics import new_owner_state

MIGRATION_META_KEY = "emotionai_migration_v1"

_EMOTION_DIMS = (
    "joy",
    "trust",
    "fear",
    "surprise",
    "sadness",
    "disgust",
    "anger",
    "anticipation",
    "pride",
    "guilt",
    "shame",
    "envy",
)


def find_emotionai_data_file(search_root: str | Path | None) -> Path | None:
    """Locate EmotionAI's ``user_emotion_data.json`` across standard AstrBot layouts."""
    if not search_root:
        return None
    base = Path(search_root)
    if base.is_file() and base.name.endswith(".json"):
        return base

    candidates = [
        base / "emotionai" / "user_emotion_data.json",
        base / "user_emotion_data.json",
        base / "EmotionAI" / "emotionai" / "user_emotion_data.json",
        base / "EmotionAI" / "user_emotion_data.json",
        base / "astrbot_plugin_emotionai" / "emotionai" / "user_emotion_data.json",
        base / "astrbot_plugin_emotionai" / "user_emotion_data.json",
    ]
    for path in candidates:
        try:
            if path.is_file() and not path.is_symlink():
                return path
        except OSError:
            continue
    return None


def _safe_int(val: Any, default: int = 0) -> int:
    if isinstance(val, bool) or val is None:
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def normalize_legacy_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize both EmotionAI v3.4 flat records and older nested records."""
    if not isinstance(raw, dict):
        return {}
    if "emotions" in raw and isinstance(raw["emotions"], dict):
        emotions = raw["emotions"]
        states = raw.get("states") if isinstance(raw.get("states"), dict) else {}
        behavior = raw.get("behavior") if isinstance(raw.get("behavior"), dict) else {}
        out: dict[str, Any] = {k: _safe_int(emotions.get(k, 0)) for k in _EMOTION_DIMS}
        out["favor"] = _safe_int(states.get("favor", 0))
        out["intimacy"] = _safe_int(states.get("intimacy", 0))
        out["is_blacklisted"] = bool(raw.get("is_blacklisted", False))
        out["interaction_count"] = max(0, _safe_int(behavior.get("interaction_count", 0)))
        out["last_interaction"] = valid_timestamp(behavior.get("last_interaction", 0.0), 0.0)
        return out

    out = {k: _safe_int(raw.get(k, 0)) for k in _EMOTION_DIMS}
    out["favor"] = _safe_int(raw.get("favor", 0))
    out["intimacy"] = _safe_int(raw.get("intimacy", 0))
    out["is_blacklisted"] = bool(raw.get("is_blacklisted", False))
    out["interaction_count"] = max(0, _safe_int(raw.get("interaction_count", 0)))
    out["last_interaction"] = valid_timestamp(raw.get("last_interaction", 0.0), 0.0)
    return out


def compute_migrated_warmth(record: dict[str, Any]) -> float:
    favor = max(0, _safe_int(record.get("favor", 0)))
    intimacy = max(0, _safe_int(record.get("intimacy", 0)))
    trust = max(0, _safe_int(record.get("trust", 0)))
    bond = 200.0 + 3.5 * favor + 3.0 * intimacy + 1.5 * trust
    return round(clamp(bond, 80.0, 1000.0), 2)


def compute_migrated_valence(record: dict[str, Any]) -> float:
    pos = (
        _safe_int(record.get("joy", 0))
        + 0.7 * _safe_int(record.get("pride", 0))
        + 0.5 * _safe_int(record.get("anticipation", 0))
    )
    neg = (
        _safe_int(record.get("sadness", 0))
        + _safe_int(record.get("anger", 0))
        + 0.8 * _safe_int(record.get("disgust", 0))
    )
    scaled = 0.15 + (pos - neg) / 250.0
    return round(clamp(scaled, -0.25, 0.38), 3)


def _split_user_key(user_key: str) -> tuple[str, str]:
    key = str(user_key or "").strip()
    if "_" in key and ":" in key:
        scope_part, uid_part = key.rsplit("_", 1)
        if scope_part and uid_part:
            return scope_part, uid_part
    return GLOBAL_SOCIAL_SCOPE, key


def _is_owner_record(
    user_key: str,
    owner_id: str,
    owner_scope: str,
) -> bool:
    if not owner_id:
        return False
    key = str(user_key or "").strip()
    if key == owner_id:
        return True
    if owner_scope and key == f"{owner_scope}_{owner_id}":
        return True
    scope_part, uid_part = _split_user_key(key)
    if uid_part == owner_id and ("FriendMessage" in scope_part or scope_part == GLOBAL_SOCIAL_SCOPE):
        return True
    return False


def migrate_from_emotionai(
    store: AffectiveStore,
    search_root: str | Path | None,
    *,
    owner_scope: str = "",
    owner_id: str = "",
    cfg: dict[str, Any] | None = None,
    force: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Import EmotionAI baseline into ``affective.db`` read-only and idempotently."""
    stamp = valid_timestamp(now)
    existing_receipt = store.get_meta(MIGRATION_META_KEY)
    if existing_receipt and not force:
        return {
            "migrated": False,
            "reason": "already_migrated",
            "receipt": existing_receipt,
        }

    data_file = find_emotionai_data_file(search_root)
    if data_file is None:
        return {
            "migrated": False,
            "reason": "source_not_found",
        }

    try:
        raw_payload = json.loads(data_file.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        return {
            "migrated": False,
            "reason": f"read_error:{type(exc).__name__}",
        }

    if not isinstance(raw_payload, dict):
        return {
            "migrated": False,
            "reason": "invalid_format",
        }

    owner_records: list[dict[str, Any]] = []
    social_count = 0
    blacklisted_count = 0

    with store.transaction():
        for raw_key, raw_val in raw_payload.items():
            if not isinstance(raw_val, dict):
                continue
            rec = normalize_legacy_record(raw_val)
            if not rec:
                continue
            if _is_owner_record(str(raw_key), str(owner_id), str(owner_scope)):
                owner_records.append(rec)
                continue

            scope_part, uid_part = _split_user_key(str(raw_key))
            if not uid_part:
                continue
            prev = store.get_social_state(scope_part, uid_part)
            favor = _safe_int(rec.get("favor", 0))
            intimacy = _safe_int(rec.get("intimacy", 0))
            fam = clamp(0.6 * max(0, intimacy) + 0.4 * max(0, favor), 0.0, 100.0)
            vibe = clamp(favor / 100.0, -1.0, 1.0)
            blacklisted = bool(rec.get("is_blacklisted")) or bool(prev.get("is_blacklisted"))
            if blacklisted:
                blacklisted_count += 1
            store.save_social_state(
                {
                    "scope": scope_part,
                    "user_id": uid_part,
                    "familiarity": max(float(prev.get("familiarity") or 0.0), fam),
                    "vibe": vibe if prev.get("interaction_count", 0) == 0 else float(prev.get("vibe") or vibe),
                    "is_blacklisted": 1 if blacklisted else 0,
                    "interaction_count": max(
                        int(prev.get("interaction_count") or 0),
                        int(rec.get("interaction_count") or 0),
                    ),
                    "last_interaction_at": max(
                        float(prev.get("last_interaction_at") or 0.0),
                        float(rec.get("last_interaction") or 0.0),
                    ),
                },
                now=stamp,
            )
            social_count += 1

        owner_migrated = False
        target_owner_scope = str(owner_scope or "").strip()
        if not target_owner_scope and owner_id:
            target_owner_scope = f"aiocqhttp:FriendMessage:{owner_id}"

        if owner_records and target_owner_scope:
            merged_owner = {
                "favor": max(r["favor"] for r in owner_records),
                "intimacy": max(r["intimacy"] for r in owner_records),
                "trust": max(r["trust"] for r in owner_records),
                "joy": max(r["joy"] for r in owner_records),
                "pride": max(r["pride"] for r in owner_records),
                "anticipation": max(r["anticipation"] for r in owner_records),
                "sadness": max(r["sadness"] for r in owner_records),
                "anger": max(r["anger"] for r in owner_records),
                "disgust": max(r["disgust"] for r in owner_records),
                "interaction_count": max(r["interaction_count"] for r in owner_records),
                "last_interaction": max(r["last_interaction"] for r in owner_records),
            }
            bond = compute_migrated_warmth(merged_owner)
            val = compute_migrated_valence(merged_owner)
            current = store.get_owner_state(target_owner_scope)
            if current is None:
                seeded = new_owner_state(target_owner_scope, str(owner_id), cfg, now=stamp)
                seeded["warmth_bond"] = bond
                seeded["valence"] = val
                seeded["interaction_count"] = merged_owner["interaction_count"]
                if merged_owner["last_interaction"] > 0:
                    seeded["last_interaction_at"] = min(stamp, merged_owner["last_interaction"])
                store.save_owner_state(seeded)
            else:
                current["warmth_bond"] = max(float(current.get("warmth_bond") or 0.0), bond)
                current["interaction_count"] = max(
                    int(current.get("interaction_count") or 0),
                    merged_owner["interaction_count"],
                )
                store.save_owner_state(current)
            owner_migrated = True

        receipt = {
            "migrated_at": stamp,
            "owner_migrated": owner_migrated,
            "social_migrated": social_count,
            "blacklisted_migrated": blacklisted_count,
            "total_records_seen": len(raw_payload),
        }
        store.set_meta(MIGRATION_META_KEY, receipt, now=stamp)
        store.record_event(
            target_owner_scope or GLOBAL_SOCIAL_SCOPE,
            "migration",
            "emotionai_imported",
            receipt,
            now=stamp,
        )

    return {
        "migrated": True,
        "receipt": receipt,
    }

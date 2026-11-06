"""Inspect a selected pilot's local configuration without running or changing it.

No network, subprocess, plugin import, database open, or configuration write.
Readiness is configuration evidence only; runtime checks remain UNVERIFIED.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys


VERSION = "2.7.0-beta.2"
COMMIT = "28698d31250073280ff23cf4d00a1ea17041b48c"
CHAT = "pilot_deepseek/deepseek-flash"
EMBEDDING = "pilot_bge_m3"
CONTROL = "astrbot/data/plugin_data/astrbot_plugin_bili_activity/control.json"
FLAGS = (
    "enabled", "periodic_enabled", "life_enabled", "life_decision_enabled",
    "life_rhythm_enabled", "life_memory_enabled", "life_projects_enabled",
    "self_expression_enabled", "workshop_enabled", "methods_enabled",
    "contact_coordination_enabled", "public_interactions", "engagement_actions",
    "collection_management", "reply_watch_enabled", "auto_public",
)
MEMORY_POLICY = {
    "access_control.whitelist_enabled": True,
    "access_control.allowed_ids": "",
    "access_control.identity_aliases": "",
    "filtering_settings.memory_scope_mode": "session",
    "filtering_settings.use_session_filtering": True,
    "filtering_settings.use_persona_filtering": True,
    "session_manager.enable_full_group_capture": False,
    "agent_tools.enable_memorize_tool": False,
    "agent_tools.enable_recall_tool": False,
    "recall_engine.top_k": 0,
    "recall_engine.rerank_enabled": False,
    "memory_consolidation.enabled": False,
    "importance_decay.decay_rate": 0,
    "forgetting_agent.auto_cleanup_enabled": False,
    "forgetting_agent.auto_archived_enabled": False,
}


class InvalidInput(Exception):
    """The message never contains supplied paths, keys, or config contents."""


def _path(root, relative):
    if (not isinstance(relative, str) or not relative or len(relative) > 512
            or "\\" in relative or ":" in relative
            or any(ord(c) < 32 for c in relative)):
        raise InvalidInput()
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts:
        raise InvalidInput()
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise InvalidInput()
    return path


def _read(root, relative, limit=2 * 1024 * 1024):
    path = _path(root, relative)
    # Bounded reads also handle a concurrently growing file without dumping it.
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InvalidInput()
    return data


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput()
        result[key] = value
    return result


def _json(data):
    value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise InvalidInput()
    return value


def _field(data, path):
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _same(actual, expected):
    # A truthy string, or 0 in place of false, must not satisfy a safety switch.
    if isinstance(expected, bool):
        return actual is expected
    if type(expected) is int:
        return type(actual) in (int, float) and actual == expected
    return type(actual) is type(expected) and actual == expected


def _key_present(value, *, allow_list=False):
    if isinstance(value, str):
        return bool(value.strip())
    if allow_list and isinstance(value, list) and all(isinstance(v, str) for v in value):
        return any(v.strip() for v in value)
    raise InvalidInput()


def _models(config):
    sources, models, platforms = (config.get(k) for k in ("provider_sources", "provider", "platform"))
    if not all(isinstance(v, list) for v in (sources, models, platforms)):
        raise InvalidInput()
    if (len(sources) != 1 or len(models) != 2
            or not all(isinstance(v, dict) for v in sources + models)):
        raise InvalidInput()
    by_id = {p.get("id"): p for p in models if isinstance(p.get("id"), str)}
    if sources[0].get("id") != "pilot_deepseek" or set(by_id) != {CHAT, EMBEDDING}:
        raise InvalidInput()
    source, chat, embedding = sources[0], by_id[CHAT], by_id[EMBEDDING]
    if any(type(p.get("enable")) is not bool for p in (source, chat, embedding)):
        raise InvalidInput()
    return source, chat, embedding


def check_pilot(folder):
    report = {
        "schema": "pilot-memory-readiness/v1", "state": "invalid_input",
        "live_verified": False, "checks": {}, "blockers": [],
        "runtime": {k: "UNVERIFIED" for k in (
            "container", "model", "embedding", "engine", "memory_write", "readback", "recall", "QQ")},
    }
    try:
        root = Path(folder).resolve(strict=True)
        if not root.is_dir():
            raise InvalidInput()
        receipt = _json(_read(root, "MEMORY_INSTALL.json"))
        if (receipt.get("version") != VERSION or receipt.get("upstream_commit") != COMMIT
                or receipt.get("chat_provider_id") != CHAT or receipt.get("embedding_provider_id") != EMBEDDING):
            raise InvalidInput()
        hashes = receipt.get("source_files")
        if not isinstance(hashes, dict) or not 1 <= len(hashes) <= 1000:
            raise InvalidInput()
        source_folder = root / "livingmemory"
        source_info = source_folder.lstat()
        if (stat.S_ISLNK(source_info.st_mode)
                or getattr(source_info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise InvalidInput()
        source_root = source_folder.resolve(strict=True)
        if not source_root.is_relative_to(root):
            raise InvalidInput()
        mismatches, total_bytes = 0, 0
        for name, expected in hashes.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise InvalidInput()
            data = _read(source_root, name, 8 * 1024 * 1024)
            total_bytes += len(data)
            if total_bytes > 64 * 1024 * 1024:
                raise InvalidInput()
            mismatches += sha256(data).hexdigest() != expected
        control_raw = _read(root, CONTROL)
        control = _json(control_raw)
        config = _json(_read(root, "astrbot/data/cmd_config.json"))
        memory = _json(_read(root, "astrbot/data/config/astrbot_plugin_livingmemory_config.json"))
        source, chat, embedding = _models(config)
        expected_hash = receipt.get("activity_control_sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise InvalidInput()
        checks = {
            "source_matches_install_receipt": mismatches == 0,
            "activity_control_matches_receipt": sha256(control_raw).hexdigest() == expected_hash,
            "activity_switches_off": all(control.get(k) is False for k in FLAGS),
            "no_qq_platforms": config["platform"] == [],
            "chat_configuration_matches": all(_same(source.get(k), v) for k, v in {
                "api_base": "https://api.deepseek.com", "type": "openai_chat_completion",
                "provider_type": "chat_completion", "proxy": "", "custom_headers": {},
            }.items()) and all(_same(chat.get(k), v) for k, v in {
                "provider_source_id": "pilot_deepseek", "model": "deepseek-flash",
                "type": "openai_chat_completion", "provider_type": "chat_completion",
            }.items()),
            "embedding_configuration_matches": all(_same(embedding.get(k), v) for k, v in {
                "embedding_api_base": "https://api.siliconflow.cn/v1",
                "embedding_model": "BAAI/bge-m3", "embedding_dimensions": 1024,
                "embedding_dimensions_mode": "auto", "type": "openai_embedding",
                "provider_type": "embedding", "proxy": "",
            }.items()),
            "memory_provider_ids_match": _field(memory, "provider_settings.llm_provider_id") == CHAT
                and _field(memory, "provider_settings.embedding_provider_id") == EMBEDDING,
            "memory_access_recall_and_cleanup_closed": all(
                _same(_field(memory, key), value) for key, value in MEMORY_POLICY.items()),
        }
        report["checks"] = checks
        report["source_file_count"] = len(hashes)
        report["source_mismatch_count"] = mismatches
        report["keys"] = {"chat_present": _key_present(source.get("key"), allow_list=True),
                          "embedding_present": _key_present(embedding.get("embedding_api_key"))}
        enabled = [p["enable"] for p in (source, chat, embedding)]
        report["providers"] = dict(zip(("chat_source_enabled", "chat_model_enabled", "embedding_enabled"), enabled))
        report["blockers"] = [name for name, ok in checks.items() if not ok]
        if report["blockers"]:
            report["state"] = "blocked_config"
        elif not all(report["keys"].values()):
            report["state"] = "missing_keys"
        elif not any(enabled):
            report["state"] = "models_disabled"
        elif not all(enabled):
            report["state"] = "models_partly_enabled"
        else:
            report["state"] = "config_ready"
        # Re-read just the inspected JSON inputs before reporting. The panel may
        # have saved new credentials or switches while the source was hashed.
        if (_read(root, CONTROL) != control_raw
                or _json(_read(root, "astrbot/data/cmd_config.json")) != config
                or _json(_read(root, "astrbot/data/config/astrbot_plugin_livingmemory_config.json")) != memory
                or _json(_read(root, "MEMORY_INSTALL.json")) != receipt):
            report["state"] = "blocked_config"
            report["blockers"].append("configuration_changed_during_check")
    except (InvalidInput, OSError, ValueError, TypeError, RuntimeError):
        # RuntimeError covers symlink loops from resolve() before Python 3.13
        # (and RecursionError). Partial results must not look authoritative.
        for partial in ("source_file_count", "source_mismatch_count", "keys", "providers"):
            report.pop(partial, None)
        report["checks"] = {}
        report["state"] = "invalid_input"
        report["blockers"] = ["input_unreadable_or_invalid"]
    report["next_step"] = {
        "invalid_input": "repair_selected_pilot_inputs",
        "blocked_config": "review_pilot_configuration_without_changing_it_automatically",
        "missing_keys": "fill_test_keys_in_local_panel_keep_models_disabled",
        "models_disabled": "perform_authorized_model_connectivity_check",
        "models_partly_enabled": "review_partial_model_enablement",
        "config_ready": "verify_running_plugin_then_scoped_synthetic_memory",
    }[report["state"]]
    return report


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes unknown arguments, which may contain
        # a mistakenly pasted credential. Only publish the required flag name.
        self.print_usage(sys.stderr)
        self.exit(2, "invalid arguments; specify --pilot-dir\n")


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", required=True, help="Explicit isolated pilot directory; inspected read-only")
    args = parser.parse_args(argv)
    report = check_pilot(args.pilot_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["state"] == "config_ready" else 3 if report["state"] in {"invalid_input", "blocked_config"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

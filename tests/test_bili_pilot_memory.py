"""Readiness inspection uses only synthetic files; never contacts a live pilot."""
from contextlib import redirect_stderr, redirect_stdout
import builtins
import copy
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy.bili_activity import ops
from deploy.bili_activity import pilot_memory_check as readiness


CHAT_ID = "pilot_deepseek/deepseek-flash"
EMBEDDING_ID = "pilot_bge_m3"
COMMIT = "28698d31250073280ff23cf4d00a1ea17041b48c"
SECRET = "example-private-key-must-not-leave-this-fixture"
RAW = "PRIVATE-RAW-RESPONSE-MUST-NOT-LEAVE"
RUNTIME_FIELDS = {"container", "model", "embedding", "engine", "memory_write", "readback", "recall", "QQ"}


class PilotMemoryReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pilot-readiness-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "pilot"
        self.root.mkdir()
        self.config_path = self.root / "astrbot/data/cmd_config.json"
        self.memory_path = self.root / "astrbot/data/config/astrbot_plugin_livingmemory_config.json"
        self.control_path = self.root / "astrbot/data/plugin_data/astrbot_plugin_bili_activity/control.json"
        self.receipt_path = self.root / "MEMORY_INSTALL.json"
        self.config = {
            "provider_sources": [{
                "id": "pilot_deepseek", "provider": "deepseek", "type": "openai_chat_completion",
                "provider_type": "chat_completion", "enable": False, "key": [],
                "api_base": "https://api.deepseek.com", "timeout": 120,
                "proxy": "", "custom_headers": {},
            }],
            "provider": [
                {"id": CHAT_ID, "provider_source_id": "pilot_deepseek", "type": "openai_chat_completion",
                 "provider_type": "chat_completion", "enable": False, "model": "deepseek-flash",
                 "modalities": ["text"], "custom_extra_body": {"thinking": {"type": "disabled"}, "max_tokens": 2048}},
                {"id": EMBEDDING_ID, "provider": "openai", "type": "openai_embedding",
                 "provider_type": "embedding", "enable": False, "embedding_api_key": "",
                 "embedding_api_base": "https://api.siliconflow.cn/v1", "embedding_model": "BAAI/bge-m3",
                 "embedding_dimensions": 1024, "embedding_dimensions_mode": "auto", "timeout": 60, "proxy": ""},
            ],
            "platform": [],
            # These unrelated private settings must never be copied into a report.
            "dashboard": {"password": SECRET}, "custom_response": RAW,
        }
        self.memory = {
            "provider_settings": {"llm_provider_id": CHAT_ID, "embedding_provider_id": EMBEDDING_ID},
            "access_control": {"whitelist_enabled": True, "allowed_ids": "", "identity_aliases": ""},
            "filtering_settings": {"memory_scope_mode": "session", "use_session_filtering": True,
                                   "use_persona_filtering": True, "isolated_sessions": ""},
            "session_manager": {"enable_full_group_capture": False, "max_sessions": 5, "context_window_size": 20},
            "recall_engine": {"top_k": 0, "max_k": 3, "rerank_enabled": False},
            "agent_tools": {"enable_memorize_tool": False, "enable_recall_tool": False},
            "memory_consolidation": {"enabled": False},
            "importance_decay": {"decay_rate": 0},
            "forgetting_agent": {"auto_cleanup_enabled": False, "auto_archived_enabled": False},
        }
        self.control = copy.deepcopy(ops.DEFAULTS)
        self.control.update({
            "phone_url": "http://127.0.0.1:1/unconfigured", "bili_url": "http://127.0.0.1:1/unconfigured",
            "daily_shares": 0, "contact_daily_limit": 0, "contact_unanswered_limit": 0,
            "max_actions_per_run": 0, "max_actions_per_day": 0, "max_comments_per_day": 0,
            "max_replies_per_day": 0, "max_danmaku_per_day": 0, "max_likes_per_day": 0, "max_coins_per_day": 0,
        })
        self.write_json(self.config_path, self.config)
        self.write_json(self.memory_path, self.memory)
        self.write_json(self.control_path, self.control)
        self.source_files = {"main.py": b"# synthetic upstream source\n", "core/check.py": b"VALUE = 1\n"}
        for relative, content in self.source_files.items():
            path = self.root / "livingmemory" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.receipt = {
            "version": "2.7.0-beta.2", "upstream_commit": COMMIT,
            "chat_provider_id": CHAT_ID, "embedding_provider_id": EMBEDDING_ID,
            "activity_control_sha256": sha256(self.control_path.read_bytes()).hexdigest(),
            "source_files": {relative: sha256(content).hexdigest() for relative, content in self.source_files.items()},
            "keys_supplied": False, "providers_enabled": False,
            "memory_access": "whitelist enabled, empty list; collection and tools blocked",
        }
        self.write_json(self.receipt_path, self.receipt)

    @staticmethod
    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def configure_keys(self, enabled=(False, False, False)):
        self.config["provider_sources"][0].update(key=[SECRET], enable=enabled[0])
        self.config["provider"][0]["enable"] = enabled[1]
        self.config["provider"][1].update(embedding_api_key=SECRET, enable=enabled[2])
        self.write_json(self.config_path, self.config)

    def save_control(self, rehash=True):
        self.write_json(self.control_path, self.control)
        if rehash:
            self.receipt["activity_control_sha256"] = sha256(self.control_path.read_bytes()).hexdigest()
            self.write_json(self.receipt_path, self.receipt)

    def assert_safe(self, report):
        self.assertEqual(report["schema"], "pilot-memory-readiness/v1")
        self.assertIs(report["live_verified"], False)
        for name in RUNTIME_FIELDS:
            self.assertEqual(report["runtime"][name], "UNVERIFIED", name)
        output = json.dumps(report, ensure_ascii=False)
        for private in (SECRET, RAW, str(self.root), "https://", "http://", "PRIVATE-OUTSIDE-FILE"):
            self.assertNotIn(private, output)
        return report

    def check(self, expected):
        report = self.assert_safe(readiness.check_pilot(self.root))
        self.assertEqual(report["state"], expected)
        return report

    def rejected(self):
        report = self.assert_safe(readiness.check_pilot(self.root))
        self.assertIn(report["state"], {"invalid_input", "blocked_config"})
        return report

    def snapshot(self):
        return {str(p.relative_to(self.root)): None if p.is_dir() else p.read_bytes()
                for p in self.root.rglob("*")}

    def test_empty_and_whitespace_keys_are_missing_not_ready(self):
        for keys, embedding in (([], ""), (["", "  ", "\t"], " \n\t "), ([SECRET], "  "), ([" "], SECRET)):
            with self.subTest(keys_present=any(str(k).strip() for k in keys), embedding_present=bool(embedding.strip())):
                self.config["provider_sources"][0]["key"] = keys
                self.config["provider"][1]["embedding_api_key"] = embedding
                self.write_json(self.config_path, self.config)
                self.check("missing_keys")

    def test_filled_keys_with_all_models_disabled_remain_disabled(self):
        self.configure_keys()
        self.check("models_disabled")

    def test_partial_enablement_is_not_config_ready(self):
        for flags in ((True, False, False), (False, True, False), (False, False, True),
                      (True, True, False), (True, False, True), (False, True, True)):
            with self.subTest(flags=flags):
                self.configure_keys(flags)
                self.check("models_partly_enabled")

    def test_all_models_enabled_only_proves_configuration(self):
        self.configure_keys((True, True, True))
        self.check("config_ready")

    def test_missing_key_has_priority_over_enabled_flags(self):
        self.configure_keys((True, True, True))
        self.config["provider_sources"][0]["key"] = []
        self.write_json(self.config_path, self.config)
        self.check("missing_keys")

    def test_changed_or_missing_source_cannot_pass_receipt_validation(self):
        self.configure_keys((True, True, True))
        source = self.root / "livingmemory/core/check.py"
        source.write_text("# " + RAW, encoding="utf-8")
        self.rejected()
        source.unlink()
        self.rejected()

    def test_control_hash_mismatch_is_blocked_even_when_switches_are_off(self):
        self.control["daily_minutes"] += 1
        self.save_control(rehash=False)
        self.check("blocked_config")

    def test_activity_switches_are_rejected_even_with_matching_control_hash(self):
        original = copy.deepcopy(self.control)
        for key in ("enabled", "periodic_enabled", "life_enabled", "life_decision_enabled", "life_rhythm_enabled",
                    "life_memory_enabled", "life_projects_enabled", "self_expression_enabled", "workshop_enabled",
                    "methods_enabled", "contact_coordination_enabled", "public_interactions", "engagement_actions",
                    "collection_management", "reply_watch_enabled", "auto_public"):
            with self.subTest(key=key):
                self.control = {**original, key: True}
                self.save_control()
                self.check("blocked_config")

    def test_qq_platform_or_duplicate_model_configuration_is_blocked(self):
        self.config["platform"] = [{"id": "private-qq", "type": "aiocqhttp", "token": SECRET}]
        self.write_json(self.config_path, self.config)
        self.check("blocked_config")
        self.config["platform"] = []
        self.config["provider"].append(copy.deepcopy(self.config["provider"][0]))
        self.write_json(self.config_path, self.config)
        self.rejected()

    def test_memory_scope_persona_and_access_protection_cannot_be_disabled(self):
        original = copy.deepcopy(self.memory)
        cases = (("access_control", "whitelist_enabled", False),
                 ("access_control", "allowed_ids", "unexpected-session"),
                 ("filtering_settings", "memory_scope_mode", "global"),
                 ("filtering_settings", "use_session_filtering", False),
                 ("filtering_settings", "use_persona_filtering", False),
                 ("session_manager", "enable_full_group_capture", True),
                 ("agent_tools", "enable_memorize_tool", True),
                 ("agent_tools", "enable_recall_tool", True),
                 ("recall_engine", "top_k", 1),
                 ("memory_consolidation", "enabled", True),
                 ("forgetting_agent", "auto_cleanup_enabled", True),
                 ("forgetting_agent", "auto_archived_enabled", True))
        for section, key, value in cases:
            with self.subTest(section=section, key=key):
                self.memory = copy.deepcopy(original)
                self.memory[section][key] = value
                self.write_json(self.memory_path, self.memory)
                self.check("blocked_config")

    def test_wrong_provider_identity_endpoint_or_dimension_is_rejected(self):
        original = copy.deepcopy(self.config)
        for container, index, key, value in (("provider_sources", 0, "api_base", "https://private.invalid/" + SECRET),
                                            ("provider", 0, "model", "unknown-model"),
                                            ("provider", 1, "embedding_dimensions", 768),
                                            ("provider", 1, "embedding_model", "wrong-embedding")):
            with self.subTest(key=key):
                self.config = copy.deepcopy(original)
                self.config[container][index][key] = value
                self.write_json(self.config_path, self.config)
                self.check("blocked_config")

    def test_missing_json_files_never_create_replacements(self):
        for path in (self.receipt_path, self.config_path, self.memory_path, self.control_path):
            with self.subTest(name=path.name):
                before = path.read_bytes()
                path.unlink()
                self.check("invalid_input")
                self.assertFalse(path.exists())
                path.write_bytes(before)

    def test_invalid_json_and_non_object_roots_are_sanitized(self):
        for path in (self.receipt_path, self.config_path, self.memory_path, self.control_path):
            before = path.read_bytes()
            for content in ("{" + SECRET + RAW, "[]", "null", '"' + SECRET + '"'):
                with self.subTest(name=path.name, kind=content[:1]):
                    path.write_text(content, encoding="utf-8")
                    self.check("invalid_input")
            path.write_bytes(before)

    def test_nested_wrong_types_do_not_leak_errors_or_get_treated_as_truthy_flags(self):
        original = copy.deepcopy(self.config)
        for field, value in (("provider", SECRET), ("provider_sources", [SECRET]), ("platform", {"token": SECRET})):
            with self.subTest(field=field):
                self.config = copy.deepcopy(original)
                self.config[field] = value
                self.write_json(self.config_path, self.config)
                self.rejected()
        self.config = copy.deepcopy(original)
        self.config["provider_sources"][0]["enable"] = "false"
        self.write_json(self.config_path, self.config)
        self.rejected()

    def test_pin_metadata_and_manifest_types_are_validated(self):
        original = copy.deepcopy(self.receipt)
        for key, value in (("version", "9.9.9"), ("upstream_commit", "f" * 40),
                           ("chat_provider_id", "wrong-chat"), ("embedding_provider_id", "wrong-embedding"),
                           ("source_files", []), ("source_files", {}),
                           ("source_files", {"main.py": SECRET})):
            with self.subTest(key=key):
                self.receipt = {**original, key: value}
                self.write_json(self.receipt_path, self.receipt)
                self.rejected()

    def test_manifest_traversal_and_absolute_paths_are_refused_before_reading(self):
        outside = Path(self.temp.name) / "outside-private.py"
        outside.write_text("PRIVATE-OUTSIDE-FILE", encoding="utf-8")
        outside_hash = sha256(outside.read_bytes()).hexdigest()
        io_open, builtin_open = io.open, builtins.open
        reads = []

        def guarded(open_fn):
            def observe(file, *args, **kwargs):
                if isinstance(file, (str, Path)) and Path(file).resolve() == outside.resolve():
                    reads.append(str(file))
                return open_fn(file, *args, **kwargs)
            return observe

        for name in ("../../outside-private.py", "..\\..\\outside-private.py", str(outside), "C:/private/" + SECRET):
            with self.subTest(kind="manifest-path"):
                self.receipt["source_files"] = {name: outside_hash}
                self.write_json(self.receipt_path, self.receipt)
                with patch("io.open", guarded(io_open)), patch("builtins.open", guarded(builtin_open)):
                    self.rejected()
                self.assertEqual(reads, [], "Manifest validation must precede opening an outside source")

    def test_source_symlink_is_rejected_without_reading_target(self):
        outside = Path(self.temp.name) / "outside-private.py"
        outside.write_text("PRIVATE-OUTSIDE-FILE", encoding="utf-8")
        link = self.root / "livingmemory/link.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Host cannot create test symlinks: " + type(exc).__name__)
        self.receipt["source_files"] = {"link.py": sha256(outside.read_bytes()).hexdigest()}
        self.write_json(self.receipt_path, self.receipt)
        original_open = io.open
        reads = []

        def observe(file, *args, **kwargs):
            if isinstance(file, (str, Path)) and Path(file).resolve() == outside.resolve():
                reads.append(str(file))
            return original_open(file, *args, **kwargs)

        with patch("io.open", observe):
            self.rejected()
        self.assertEqual(reads, [])

    def test_source_directory_symlink_cannot_reclassify_other_pilot_files_as_sources(self):
        self.configure_keys((True, True, True))
        source = self.root / "livingmemory"
        elsewhere = self.root / "not-the-source-directory"
        source.rename(elsewhere)
        try:
            source.symlink_to(elsewhere, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Host cannot create test directory symlinks: " + type(exc).__name__)
        original_open = io.open
        reads = []

        def observe(file, *args, **kwargs):
            if isinstance(file, (str, Path)) and Path(file).resolve().is_relative_to(elsewhere):
                reads.append(str(file))
            return original_open(file, *args, **kwargs)

        with patch("io.open", observe):
            self.rejected()
        self.assertEqual(reads, [], "The source root must not alias another pilot directory")

    def test_config_symlink_is_rejected_without_reading_target(self):
        outside = Path(self.temp.name) / "outside-config.json"
        outside.write_text(json.dumps(self.config), encoding="utf-8")
        self.config_path.unlink()
        try:
            self.config_path.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Host cannot create test symlinks: " + type(exc).__name__)
        self.rejected()

    def test_source_directory_junction_cannot_reclassify_other_pilot_files_as_sources(self):
        # Junctions are reparse points but not symlinks for stat.S_ISLNK on
        # older Python; they need no symlink privilege, so Windows can test them.
        try:
            import _winapi
            create = _winapi.CreateJunction
        except (ImportError, AttributeError):
            self.skipTest("Host has no directory junction support")
        self.configure_keys((True, True, True))
        source = self.root / "livingmemory"
        elsewhere = self.root / "not-the-source-directory"
        source.rename(elsewhere)
        try:
            create(str(elsewhere), str(source))
        except OSError as exc:
            self.skipTest("Host cannot create test junctions: " + type(exc).__name__)
        self.addCleanup(lambda: source.exists() and source.rmdir())
        original_open = io.open
        reads = []

        def observe(file, *args, **kwargs):
            if isinstance(file, (str, Path)) and Path(file).resolve().is_relative_to(elsewhere.resolve()):
                reads.append(str(file))
            return original_open(file, *args, **kwargs)

        with patch("io.open", observe):
            report = self.check("invalid_input")
        self.assertEqual(reads, [], "A junction source root must be refused before hashing")
        self.assertEqual(report["checks"], {})

    def test_source_symlink_loop_is_invalid_input_not_an_exception(self):
        first, second = self.root / "livingmemory/loop-a.py", self.root / "livingmemory/loop-b.py"
        try:
            first.symlink_to(second)
            second.symlink_to(first)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Host cannot create test symlinks: " + type(exc).__name__)
        self.receipt["source_files"] = {"loop-a.py": "0" * 64}
        self.write_json(self.receipt_path, self.receipt)
        self.check("invalid_input")

    def test_invalid_input_discards_partial_checks_and_key_status(self):
        # A malformed key is detected after the checks were computed.
        self.configure_keys((True, True, True))
        self.config["provider"][1]["embedding_api_key"] = 12345
        self.write_json(self.config_path, self.config)
        report = self.check("invalid_input")
        self.assertEqual(report["checks"], {})
        self.assertEqual(report["blockers"], ["input_unreadable_or_invalid"])
        for partial in ("keys", "providers", "source_file_count", "source_mismatch_count"):
            self.assertNotIn(partial, report)

    def test_configuration_saved_during_check_is_not_reported_ready(self):
        self.configure_keys((True, True, True))
        original_read = readiness._read
        calls = {"config": 0}

        def changing_read(root, relative, *args, **kwargs):
            data = original_read(root, relative, *args, **kwargs)
            if relative == "astrbot/data/cmd_config.json":
                calls["config"] += 1
                if calls["config"] > 1:
                    # Simulate the panel saving a disabled model mid-check.
                    changed = json.loads(data)
                    changed["provider"][0]["enable"] = False
                    return json.dumps(changed).encode("utf-8")
            return data

        with patch.object(readiness, "_read", changing_read):
            report = self.check("blocked_config")
        self.assertEqual(calls["config"], 2)
        self.assertIn("configuration_changed_during_check", report["blockers"])

    def test_duplicate_json_fields_are_rejected_without_echoing_input(self):
        for path in (self.receipt_path, self.config_path, self.memory_path, self.control_path):
            before = path.read_bytes()
            with self.subTest(name=path.name):
                duplicate = before.decode("utf-8")[:-1] + ',"duplicate":"' + SECRET + '","duplicate":"' + RAW + '"}'
                path.write_text(duplicate, encoding="utf-8")
                self.check("invalid_input")
            path.write_bytes(before)

    def test_integer_and_string_values_do_not_satisfy_closed_boolean_gates(self):
        for value in (0, "false", None):
            with self.subTest(value=value):
                self.control["enabled"] = value
                self.save_control()
                self.check("blocked_config")
        self.control["enabled"] = False
        self.save_control()
        self.memory["access_control"]["whitelist_enabled"] = 1
        self.write_json(self.memory_path, self.memory)
        self.check("blocked_config")

    def test_repeated_checks_preserve_bytes_directories_and_never_create_databases(self):
        self.configure_keys((True, True, True))
        before = self.snapshot()
        for _ in range(3):
            self.check("config_ready")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.root.rglob("*.db")))
        self.assertFalse(list(self.root.rglob("*.sqlite3")))

    def test_existing_database_does_not_prove_engine_ready_and_is_never_opened(self):
        self.configure_keys((True, True, True))
        db = self.root / "astrbot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db"
        db.parent.mkdir(parents=True)
        db.write_text("PRIVATE-MEMORY-BODY-NOT-A-DATABASE", encoding="utf-8")
        before = self.snapshot()
        with patch("sqlite3.connect", side_effect=AssertionError("Do not open any database")):
            report = self.check("config_ready")
        self.assertEqual(report["runtime"]["engine"], "UNVERIFIED")
        self.assertEqual(self.snapshot(), before)

    def test_ready_check_does_not_use_network_subprocesses_or_import_runtime(self):
        self.configure_keys((True, True, True))
        original_import = builtins.__import__

        def import_checked(name, *args, **kwargs):
            if name.startswith(("astrbot", "livingmemory")):
                self.fail("Readiness inspection must not load runtime code")
            return original_import(name, *args, **kwargs)

        with patch("urllib.request.urlopen", side_effect=AssertionError("No HTTP")), \
             patch("socket.create_connection", side_effect=AssertionError("No socket")), \
             patch("subprocess.run", side_effect=AssertionError("No subprocess")), \
             patch("subprocess.Popen", side_effect=AssertionError("No subprocess")), \
             patch("builtins.__import__", import_checked):
            self.check("config_ready")

    def test_cli_returns_readiness_exit_codes_with_only_sanitized_json(self):
        cases = ((None, "missing_keys", 2), ((False, False, False), "models_disabled", 2),
                 ((True, False, False), "models_partly_enabled", 2), ((True, True, True), "config_ready", 0))
        for enabled, state, code in cases:
            with self.subTest(state=state):
                if enabled is not None:
                    self.configure_keys(enabled)
                output, error = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    actual = readiness.main(["--pilot-dir", str(self.root)])
                self.assertEqual(actual, code)
                self.assertEqual(self.assert_safe(json.loads(output.getvalue()))["state"], state)
                self.assertEqual(error.getvalue(), "")
        self.control["enabled"] = True
        self.save_control()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(readiness.main(["--pilot-dir", str(self.root)]), 3)
        self.assertEqual(self.assert_safe(json.loads(output.getvalue()))["state"], "blocked_config")

    def test_missing_directory_is_not_created_and_cli_error_is_sanitized(self):
        absent = self.root / (SECRET + "-missing")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(readiness.main(["--pilot-dir", str(absent)]), 3)
        self.assertEqual(self.assert_safe(json.loads(output.getvalue()))["state"], "invalid_input")
        self.assertFalse(absent.exists())

    def test_cli_requires_explicit_pilot_directory(self):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error), self.assertRaises(SystemExit) as exited:
            readiness.main([])
        self.assertEqual(exited.exception.code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("--pilot-dir", error.getvalue())

    def test_cli_argument_errors_do_not_echo_accidentally_pasted_keys(self):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error), self.assertRaises(SystemExit) as exited:
            readiness.main(["--pilot-dir", str(self.root), "--key", SECRET])
        self.assertEqual(exited.exception.code, 2)
        self.assertNotIn(SECRET, output.getvalue() + error.getvalue())
        self.assertNotIn(str(self.root), output.getvalue() + error.getvalue())


if __name__ == "__main__":
    unittest.main()

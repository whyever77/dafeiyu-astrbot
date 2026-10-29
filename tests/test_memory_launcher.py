"""Desktop entry tests use fake processes and synthetic local HTTP only."""
import json
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from deploy.memory_observatory import launcher
from deploy.memory_observatory.demo import demo_snapshot
from deploy.memory_observatory.server import ObservatoryServer


class LauncherTests(unittest.TestCase):
    @unittest.skipUnless(launcher.os.name == "nt", "Windows named mutex")
    def test_double_click_startup_lock_serializes_contenders(self):
        attempted, entered = threading.Event(), threading.Event()
        errors = []

        def contender():
            attempted.set()
            try:
                with launcher.startup_lock():
                    entered.set()
            except Exception as exc:
                errors.append(type(exc).__name__)

        thread = threading.Thread(target=contender, daemon=True)
        with launcher.startup_lock():
            thread.start()
            self.assertTrue(attempted.wait(2))
            self.assertFalse(entered.wait(0.1))
        self.assertTrue(entered.wait(2))
        thread.join(timeout=2)
        self.assertEqual(errors, [])
        self.assertFalse(thread.is_alive())

    def test_reuses_real_server_without_starting_or_refreshing_it(self):
        with patch.object(launcher, "probe", return_value="real"), \
             patch.object(launcher.subprocess, "Popen") as spawn, \
             patch.object(launcher.webbrowser, "open", return_value=True) as browser:
            launcher.launch()
            spawn.assert_not_called()
            browser.assert_called_once_with("http://127.0.0.1:8767", new=2)

    def test_occupied_or_demo_port_is_not_replaced(self):
        with patch.object(launcher, "probe", return_value="other"), \
             patch.object(launcher.subprocess, "Popen") as spawn, \
             patch.object(launcher.webbrowser, "open") as browser:
            with self.assertRaises(launcher.LaunchError):
                launcher.launch()
            spawn.assert_not_called()
            browser.assert_not_called()

    def test_starts_real_mode_and_waits_before_opening(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(launcher, "probe", side_effect=["absent", "absent", "real"]), \
             patch.object(launcher.subprocess, "Popen", return_value=child) as spawn, \
             patch.object(launcher.time, "sleep") as sleep, \
             patch.object(launcher.webbrowser, "open", return_value=True):
            launcher.launch()
            args, kwargs = spawn.call_args
            self.assertEqual(args[0][-3:], ["--port", "8767", "--livingmemory-ssh"])
            self.assertEqual(kwargs["stdout"], launcher.subprocess.DEVNULL)
            self.assertEqual(kwargs["cwd"], launcher.ROOT)
            if launcher.os.name == "nt":
                self.assertEqual(kwargs["creationflags"], 0x08000000)
            sleep.assert_called_once()
            child.terminate.assert_not_called()

    def test_failed_start_does_not_open_demo_or_browser(self):
        child = Mock()
        child.poll.return_value = 1
        with patch.object(launcher, "probe", return_value="absent"), \
             patch.object(launcher.subprocess, "Popen", return_value=child), \
             patch.object(launcher.webbrowser, "open") as browser:
            with self.assertRaisesRegex(launcher.LaunchError, "不会切换为演示"):
                launcher.launch()
            browser.assert_not_called()
            child.terminate.assert_not_called()

    def test_timeout_only_terminates_spawned_child(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(launcher, "probe", return_value="absent"), \
             patch.object(launcher.subprocess, "Popen", return_value=child), \
             patch.object(launcher.time, "monotonic", side_effect=[0, 56]), \
             patch.object(launcher.webbrowser, "open") as browser:
            with self.assertRaises(launcher.LaunchError):
                launcher.launch()
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=5)
            browser.assert_not_called()

    def test_health_does_not_request_private_snapshot(self):
        with patch.object(launcher, "fetch", return_value=json.dumps({
                "application": "dafeiyu-memory-observatory", "mode": "livingmemory"}).encode()) as fetch:
            self.assertEqual(launcher.probe(), "real")
            fetch.assert_called_once_with("/api/health")

    def test_legacy_reuse_requires_identity_and_real_source(self):
        missing = HTTPError(launcher.URL + "/api/health", 404, "Not Found", {}, None)
        title = "<title>记忆星海 · 大肥鱼</title>".encode()
        for snapshot, expected in (({"demo": False, "connection": {"enabled": True}}, "real"),
                                   ({"demo": True}, "other"), ({"demo": False}, "other")):
            with self.subTest(expected=expected, snapshot=snapshot), \
                 patch.object(launcher, "fetch", side_effect=[missing, title, json.dumps(snapshot).encode()]):
                self.assertEqual(launcher.probe(), expected)

    def test_connection_refusal_is_absent_but_invalid_response_is_occupied(self):
        for error, expected in ((URLError(ConnectionRefusedError()), "absent"),
                                (ValueError("bad JSON"), "other"), (TimeoutError(), "other")):
            with self.subTest(expected=expected), patch.object(launcher, "fetch", side_effect=error):
                self.assertEqual(launcher.probe(), expected)

    def test_health_on_synthetic_servers_contains_no_private_memory(self):
        fake_source = Mock()
        fake_source.load.return_value = (demo_snapshot(), {"enabled": True})
        for source, expected in ((None, "local"), (fake_source, "livingmemory")):
            server = ObservatoryServer(("127.0.0.1", 0), source=source)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = "http://127.0.0.1:" + str(server.server_port)
                with urlopen(url + "/api/health", timeout=2) as response:
                    self.assertEqual(json.load(response), {"application": "dafeiyu-memory-observatory", "mode": expected})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

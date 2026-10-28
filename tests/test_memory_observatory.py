"""Offline model + loopback HTTP checks; never loads real agent state."""

import copy
import http.client
import json
import threading
import unittest

from deploy.memory_observatory.demo import demo_snapshot
from deploy.memory_observatory.model import InvalidSnapshot, normalize, project
from deploy.memory_observatory.server import ObservatoryServer


def fixture():
    return {"schema": "memory-observatory/v1", "as_of": "2026-10-28T12:00:00+00:00", "label": "Test",
            "memories": [
                {"id": "a", "scope": "private", "title": "雨声里的晚安", "text": "雨声与钢琴", "gist": "安静的陪伴",
                 "topics": ["音乐"], "kind": "episode", "created_at": "2026-10-28T12:00:00+00:00",
                 "importance": .8, "confidence": .6, "half_life_days": 30, "source": "fake source"},
                {"id": "b", "scope": "private", "title": "一起听歌", "text": "演示片段", "topics": ["音乐"],
                 "created_at": "2026-10-28T12:00:00Z"},
                {"id": "c", "scope": "group", "title": "雨声", "text": "另一群的隐私", "topics": ["音乐"],
                 "created_at": "2026-10-28T12:00:00Z"}]}


class MemoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.raw = fixture()
        self.snapshot = normalize(self.raw)

    def view(self, **kwargs):
        return project(self.snapshot, scope="private", **kwargs)

    def test_half_life_and_gist_decay_separately(self):
        m = self.view(days=30)["memories"][0]
        self.assertEqual(m["detail"], .5)
        self.assertAlmostEqual(m["gist_retention"], 2 ** (-1 / 3), places=5)
        self.assertGreater(m["gist_retention"], m["detail"])
        self.assertEqual(m["gist"], "安静的陪伴")

    def test_decay_is_monotonic_and_does_not_delete_sources(self):
        values = [self.view(days=day)["memories"][0]["detail"] for day in (0, 30, 90, 365, 730)]
        self.assertEqual(values, sorted(values, reverse=True))
        old = self.view(days=730)["memories"][0]
        self.assertEqual(old["text"], self.raw["memories"][0]["text"])
        self.assertEqual(old["source"], "fake source")
        self.assertEqual(len(self.view(days=730)["memories"]), 2)

    def test_protected_memories_do_not_decay(self):
        self.snapshot["memories"][0]["pinned"] = True
        m = self.view(days=730)["memories"][0]
        self.assertEqual((m["detail"], m["gist_retention"]), (1, 1))
        self.assertTrue(m["protected"])

    def test_stability_experiment_is_bounded_idempotent_and_ephemeral(self):
        original = copy.deepcopy(self.snapshot)
        baseline = self.view(days=90)["memories"][0]
        first = self.view(days=90, rehearsed=["a"])["memories"][0]
        repeated = self.view(days=90, rehearsed=["a", "a"])["memories"][0]
        self.assertEqual(first, repeated)
        self.assertGreater(first["detail"], baseline["detail"])
        self.assertEqual(first["stability_days"], 48)
        self.assertEqual(self.snapshot, original)
        self.assertEqual(self.view(days=90)["memories"][0], baseline)

    def test_reading_or_querying_never_reinforces_or_changes_confidence(self):
        before = copy.deepcopy(self.snapshot)
        for _ in range(3):
            self.view(query="雨声", days=90)
        m = self.view(days=90, rehearsed=["a"])["memories"][0]
        self.assertEqual(m["confidence"], .6)
        self.assertEqual(self.snapshot, before)

    def test_unknown_confidence_is_not_invented(self):
        self.assertIsNone(self.view()["memories"][1]["confidence"])
        self.assertTrue(self.view()["memories"][1]["stability_assumed"])

    def test_scope_filters_before_retrieval_and_graph_expansion(self):
        view = self.view(query="雨声")
        self.assertEqual({m["id"] for m in view["memories"]}, {"a", "b"})
        self.assertEqual({t["id"] for t in view["trace"]}, {"a", "b"})
        self.assertFalse(any("c" in (e["source"], e["target"]) for e in view["edges"]))
        self.assertEqual(view["trace"][1]["via"], "a")
        self.assertIn("共同主题", view["trace"][1]["reason"])

    def test_cue_can_recall_dormant_memory_with_provenance(self):
        view = self.view(query="雨声", days=730)
        self.assertLess(view["memories"][0]["detail"], .1)
        self.assertEqual(view["trace"][0]["id"], "a")
        self.assertIsNone(view["trace"][0]["via"])
        self.assertEqual(view["trace"][0]["reason"], "文本/主题线索匹配")

    def test_irrelevant_query_does_not_fabricate_recall(self):
        self.assertEqual(self.view(query="火山喷发")["trace"], [])

    def test_scope_is_required_and_experiment_ids_cannot_cross_scope(self):
        with self.assertRaises(InvalidSnapshot):
            project(self.snapshot, scope=None)
        for args in ({"rehearsed": ["c"]}, {"pinned": ["c"]}):
            with self.subTest(args=args), self.assertRaises(InvalidSnapshot):
                self.view(**args)

    def test_stats_partition_memories_without_counting_edges_as_memories(self):
        stats = self.view(days=90)["stats"]
        self.assertEqual(stats["total"], stats["clear"] + stats["fading"] + stats["dormant"])
        self.assertEqual(stats["total"], 2)

    def test_simulated_pin_does_not_alter_source_pin(self):
        self.assertEqual(self.view(days=730, pinned=["a"])["memories"][0]["detail"], 1)
        self.assertFalse(self.snapshot["memories"][0]["pinned"])
        self.assertLess(self.view(days=730)["memories"][0]["detail"], .1)


class SnapshotContractTests(unittest.TestCase):
    def test_rejects_cross_scope_explicit_edges(self):
        data = fixture()
        data["edges"] = [{"source": "a", "target": "c"}]
        with self.assertRaisesRegex(InvalidSnapshot, "跨作用域"):
            normalize(data)

    def test_rejects_duplicate_ids_missing_scope_and_ambiguous_time(self):
        for key, value in (("id", "b"), ("scope", None), ("created_at", "2026-10-28T12:00:00"),
                           ("created_at", "2030-10-28T12:00:00Z")):
            data = fixture()
            data["memories"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(InvalidSnapshot):
                normalize(data)

    def test_rejects_nonfinite_numbers_booleans_and_extreme_sizes(self):
        for value in (float("nan"), float("inf"), True, -1, 2):
            data = fixture()
            data["memories"][0]["importance"] = value
            with self.subTest(value=value), self.assertRaises(InvalidSnapshot):
                normalize(data)
        data = fixture()
        data["memories"] *= 170
        with self.assertRaises(InvalidSnapshot):
            normalize(data)

    def test_drops_unknown_metadata_and_keeps_literal_untrusted_text(self):
        data = fixture()
        data["cookies"] = "secret"
        data["memories"][0].update({"token": "private", "metadata": {"api_key": "private"}, "text": '<script>alert("x")</script>'})
        out = normalize(data)
        self.assertNotIn("cookies", out)
        self.assertNotIn("token", out["memories"][0])
        self.assertNotIn("metadata", out["memories"][0])
        self.assertEqual(out["memories"][0]["text"], '<script>alert("x")</script>')

    def test_example_roundtrip_keeps_inferred_links_marked(self):
        demo = demo_snapshot()
        original = {(e["source"], e["target"]): e["inferred"] for e in demo["edges"]}
        roundtrip = normalize(json.loads(json.dumps(demo)))
        self.assertEqual({(e["source"], e["target"]): e["inferred"] for e in roundtrip["edges"]}, original)

    def test_unrecognized_export_fails_explicitly(self):
        with self.assertRaisesRegex(InvalidSnapshot, "接入文档"):
            normalize({"documents": [{"text": "a"}]})

    def test_demo_is_explicitly_fictional_and_multiscope(self):
        demo = demo_snapshot()
        self.assertEqual(len(demo["memories"]), 35)
        self.assertEqual(len({m["scope"] for m in demo["memories"]}), 2)
        self.assertTrue(all("虚构" in m["source"] for m in demo["memories"]))


class LocalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ObservatoryServer(("127.0.0.1", 0))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path, body=None, headers=None, method=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        merged = {"Content-Type": "application/json", "X-Observatory": "local"}
        merged.update(headers or {})
        encoded = None if body is None else json.dumps(body).encode()
        connection.request(method or ("GET" if body is None else "POST"), path, encoded, merged)
        response = connection.getresponse()
        content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        connection.close()
        return result

    def setUp(self):
        self.request("/api/reset", {})

    def test_bind_is_loopback_only(self):
        with self.assertRaises(ValueError):
            ObservatoryServer(("0.0.0.0", 0))

    def test_assets_are_local_with_csp_and_no_store(self):
        for path in ("/", "/app.js", "/graph.js", "/style.css"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_unknown_paths_cannot_read_files(self):
        for path in ("/../../README.md", "/api/file?path=cookies.json", "/static/../server.py"):
            self.assertEqual(self.request(path)[0], 404)

    def test_host_and_origin_checks_prevent_cross_site_import(self):
        self.assertEqual(self.request("/api/snapshot", headers={"Host": "evil.test"})[0], 403)
        self.assertEqual(self.request("/api/import", fixture(), {"Origin": "https://evil.test"})[0], 403)
        self.assertEqual(self.request("/api/import", fixture(), {"X-Observatory": ""})[0], 403)
        self.assertEqual(self.request("/api/import", fixture(), {"Content-Type": "text/plain"})[0], 403)

    def test_failed_import_is_atomic_and_reports_no_payload(self):
        before = self.request("/api/snapshot")[2]
        invalid = fixture()
        invalid["memories"][0]["scope"] = None
        status, _, body = self.request("/api/import", invalid)
        self.assertEqual(status, 400)
        self.assertNotIn(b"fake source", body)
        self.assertEqual(before, self.request("/api/snapshot")[2])

    def test_import_and_projection_are_in_memory_and_resettable(self):
        status, _, body = self.request("/api/import", fixture())
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertFalse(data["demo"])
        status, _, body = self.request("/api/project", {"scope": "private", "days": 30, "query": "雨声"})
        self.assertEqual(status, 200)
        projected = json.loads(body)
        self.assertEqual(projected["memories"][0]["detail"], .5)
        baseline = json.loads(self.request("/api/snapshot")[2])
        self.assertEqual(baseline["memories"][0]["detail"], 1)
        self.assertTrue(json.loads(self.request("/api/reset", {})[2])["demo"])

    def test_project_rejects_bad_types_without_internal_error(self):
        for body in ({"scope": []}, {"scope": "演示 · 与主人的私聊", "days": True},
                     {"scope": "演示 · 与主人的私聊", "rehearsed": [None]}):
            self.assertEqual(self.request("/api/project", body)[0], 400)

    def test_example_endpoint_never_exports_imported_private_records(self):
        self.request("/api/import", fixture())
        demo = json.loads(self.request("/api/example")[2])
        self.assertTrue(all("虚构" in m["source"] for m in demo["memories"]))
        self.assertNotIn("fake source", json.dumps(demo))


if __name__ == "__main__":
    unittest.main()

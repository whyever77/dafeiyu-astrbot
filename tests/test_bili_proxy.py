"""Companion MCP proxy peer allowlist; no live Docker or network needed."""
import unittest

from deploy.bili_activity.proxy import _container_ips, _peer_allowed, _peer_ip


class ProxyAllowlistTests(unittest.TestCase):
    def test_only_explicitly_allowed_peer_passes(self):
        allowed = {"172.18.0.2"}
        self.assertTrue(_peer_allowed("172.18.0.2", allowed))
        self.assertFalse(_peer_allowed("172.18.0.3", allowed))
        self.assertFalse(_peer_allowed("", allowed))

    def test_ipv4_mapped_peer_is_normalized(self):
        self.assertEqual(_peer_ip(("::ffff:172.18.0.2", 1234)), "172.18.0.2")
        self.assertEqual(_peer_ip(("172.18.0.2", 1234)), "172.18.0.2")

    def test_missing_peer_is_empty(self):
        self.assertEqual(_peer_ip(None), "")
        self.assertFalse(_peer_allowed(_peer_ip(None), {"172.18.0.2"}))

    def test_unresolvable_container_is_empty_not_open(self):
        self.assertEqual(_container_ips(""), set())
        self.assertEqual(_container_ips("definitely-not-a-container-xyz"), set())


if __name__ == "__main__":
    unittest.main()

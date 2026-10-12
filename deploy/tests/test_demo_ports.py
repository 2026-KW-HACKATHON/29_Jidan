import importlib.util
import json
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("demo_ports", Path(__file__).resolve().parents[1] / "demo/check_ports.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DemoPortTests(unittest.TestCase):
    def test_running_or_stopped_host_bindings_conflict_on_every_reserved_port(self):
        for port in module.RESERVED:
            with self.subTest(port=port):
                encoded = json.dumps({"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]})
                with self.assertRaises(ValueError):
                    module.check(["other-project|" + encoded])

    def test_current_project_unbound_and_unrelated_ports_are_allowed(self):
        module.check(['jidan-demo|{"8000/tcp":[{"HostPort":"3045"}]}',
                      'other|null', 'other|{"8080/tcp":null}',
                      'other|{"3001/tcp":[{"HostPort":"3001"}]}'])

    def test_missing_labels_ipv6_and_udp_conflicts_are_rejected(self):
        for project in ("", "<no value>"):
            with self.assertRaises(ValueError):
                module.check([project + '|{"80/udp":[{"HostIp":"::","HostPort":"80"}]}'])

    def test_malformed_metadata_fails_closed(self):
        for line in ("missing separator", "other|[1]", "other|broken"):
            with self.assertRaises(ValueError):
                module.check([line])


if __name__ == "__main__":
    unittest.main()

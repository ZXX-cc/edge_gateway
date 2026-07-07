import contextlib
import io
import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
for path in (PROJECT_ROOT, TOOLS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import mqtt_drc_batch_test as drc_batch  # noqa: E402


class FakeBatchClient:
    instances = []

    def __init__(self, host, port, sn, qos, quiet_stale=False, client_id_prefix=""):
        self.host = host
        self.port = port
        self.sn = sn
        self.qos = qos
        self.quiet_stale = quiet_stale
        self.client_id_prefix = client_id_prefix
        self.calls = []
        self.stats = {
            "accepted": 0,
            "ignored_mismatched": 0,
            "ignored_legacy": 0,
            "ignored_late": 0,
            "ignored_wrong_topic": 0,
        }
        self.drc_state = 0
        self.disconnected = False
        FakeBatchClient.instances.append(self)

    def connect(self, timeout):
        return True

    def disconnect(self):
        self.disconnected = True

    def stale_total(self):
        return 0

    def request(self, method, data, timeout, channel="auto", allowed_results=None, **_kwargs):
        self.calls.append(
            {
                "method": method,
                "channel": channel,
                "data": data,
                "allowed_results": allowed_results,
            }
        )
        self.stats["accepted"] += 1
        if method == "drc_mode_enter":
            self.drc_state = 2
        elif method == "drc_mode_exit":
            self.drc_state = 0
        if method == "property_get":
            return 0, {"data": {"result": 0, "drc_state": self.drc_state}}
        return 0, {"data": {"result": 0}}


class MqttDrcBatchTest(unittest.TestCase):
    def test_parse_control_case_requires_object_with_method(self):
        method, payload = drc_batch.parse_control_case('{"method":"stick_control","data":{"vx":0.1}}')

        self.assertEqual(method, "stick_control")
        self.assertEqual(payload, {"vx": 0.1})

        with self.assertRaises(SystemExit):
            parser = drc_batch.argparse.ArgumentParser()
            parser.add_argument("--control-case", type=drc_batch.parse_control_case)
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(["--control-case", '{"data":{}}'])

    def test_main_runs_default_flow_through_shared_client_shape(self):
        original_client = drc_batch.MqttRequestClient
        original_resolve = drc_batch.resolve_mqtt_settings
        original_argv = sys.argv
        FakeBatchClient.instances = []
        drc_batch.MqttRequestClient = FakeBatchClient
        drc_batch.resolve_mqtt_settings = lambda args: ("127.0.0.1", 1883, "EDGEGW001")
        sys.argv = ["mqtt_drc_batch_test.py", "--timeout", "1", "--quiet-stale"]

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = drc_batch.main()
        finally:
            drc_batch.MqttRequestClient = original_client
            drc_batch.resolve_mqtt_settings = original_resolve
            sys.argv = original_argv

        self.assertEqual(rc, 0)
        client = FakeBatchClient.instances[-1]
        self.assertTrue(client.disconnected)
        self.assertEqual(client.client_id_prefix, "edge_gateway_drc_batch")

        calls = [(call["method"], call["channel"]) for call in client.calls]
        self.assertEqual(calls[0], ("drc_mode_exit", "services"))
        self.assertEqual(calls[1], ("drc_mode_enter", "services"))
        self.assertEqual(calls[2], ("property_get", "services"))
        self.assertEqual(calls[3], ("heart_beat", "drc"))
        self.assertIn(("stick_control", "drc"), calls)
        self.assertEqual(calls[-2], ("drc_mode_exit", "services"))
        self.assertEqual(calls[-1], ("property_get", "services"))


if __name__ == "__main__":
    unittest.main()

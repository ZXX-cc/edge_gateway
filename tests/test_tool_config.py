import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from tool_config import resolve_mqtt_settings  # noqa: E402


class ToolConfigTest(unittest.TestCase):
    def _write_config(self, content: str) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False)
        with tmp:
            tmp.write(content)
        return tmp.name

    def test_resolve_mqtt_settings_normalizes_nanomq_url(self):
        config = self._write_config(
            """
gateway:
  sn: EDGEGW001
nanomq:
  host: tcp://39.185.72.155:11883
  port: 1883
"""
        )

        host, port, sn = resolve_mqtt_settings(SimpleNamespace(config=config, host=None, port=None, sn=None))

        self.assertEqual((host, port, sn), ("39.185.72.155", 11883, "EDGEGW001"))

    def test_resolve_mqtt_settings_falls_back_to_cloud_broker(self):
        config = self._write_config(
            """
gateway:
  sn: EDGEGW001
cloud:
  broker_host: tcp://39.185.72.155
  broker_port: 11883
"""
        )

        host, port, sn = resolve_mqtt_settings(SimpleNamespace(config=config, host=None, port=None, sn=None))

        self.assertEqual((host, port, sn), ("39.185.72.155", 11883, "EDGEGW001"))


if __name__ == "__main__":
    unittest.main()

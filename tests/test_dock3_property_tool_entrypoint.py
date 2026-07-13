import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from mqtt_dock3_property_coverage_test import (
    legacy_competing_property_reply_count,
    stale_breakdown,
)


class Dock3PropertyToolEntrypointTest(unittest.TestCase):
    def test_only_methodless_legacy_property_duplicates_are_non_strict(self):
        client = SimpleNamespace(
            sn="EDGEGW001",
            stale_samples=[
                {
                    "category": "duplicate",
                    "topic": "thing/product/EDGEGW001/property/set_reply",
                    "method": None,
                },
                {
                    "category": "duplicate",
                    "topic": "thing/product/EDGEGW001/property/set_reply",
                    "method": "property_set",
                },
                {
                    "category": "duplicate",
                    "topic": "thing/product/EDGEGW001/services_reply",
                    "method": None,
                },
            ],
        )

        self.assertEqual(legacy_competing_property_reply_count(client), 1)

    def test_stale_breakdown_separates_competing_replies_and_legacy_reports(self):
        client = SimpleNamespace(
            sn="EDGEGW001",
            stats={
                "ignored_mismatched": 0,
                "ignored_legacy": 0,
                "ignored_duplicate": 6,
                "ignored_wrong_topic": 0,
                "ignored_observed_legacy": 7,
                "ignored_unknown": 2,
            },
            stale_samples=[
                {
                    "category": "duplicate",
                    "topic": "thing/product/EDGEGW001/services_reply",
                    "method": "property_get",
                },
                *[
                    {
                        "category": "duplicate",
                        "topic": "thing/product/EDGEGW001/property/set_reply",
                        "method": None,
                    }
                    for _ in range(5)
                ],
            ],
        )

        self.assertEqual(
            stale_breakdown(client),
            {
                "total": 13,
                "strict_total": 8,
                "current_competing_reply_total": 1,
                "legacy_observed_total": 7,
                "legacy_competing_property_replies": 5,
                "unrelated_reply_total": 2,
            },
        )

    def test_tools_can_start_with_only_tools_directory_on_python_path(self):
        scripts = (
            PROJECT_ROOT / "tools" / "mqtt_dock3_osd_state_test.py",
            PROJECT_ROOT / "tools" / "mqtt_dock3_property_coverage_test.py",
        )
        with tempfile.TemporaryDirectory() as workdir:
            for script in scripts:
                result = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"{script.name} failed to start:\n{result.stderr}",
                )
                self.assertIn("usage:", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()

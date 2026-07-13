import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Dock3PropertyToolEntrypointTest(unittest.TestCase):
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

from pathlib import Path
import os
import unittest
from unittest.mock import Mock, patch

from selenium.webdriver.edge.options import Options

import edge_runtime

FAKE_TEMP = Path(r"C:\fake\browser-temp")


class EdgeRuntimeTests(unittest.TestCase):
    def tearDown(self):
        edge_runtime._restricted = None

    def test_prepare_options_adds_no_sandbox_only_when_restricted(self):
        with patch.object(edge_runtime, "restricted_workspace", return_value=True):
            restricted = edge_runtime.prepare_options(Options())
        self.assertIn("--no-sandbox", restricted.arguments)

        with patch.object(edge_runtime, "restricted_workspace", return_value=False):
            normal = edge_runtime.prepare_options(Options())
        self.assertNotIn("--no-sandbox", normal.arguments)

    def test_launch_redirects_browser_temp_into_workspace(self):
        captured = {}

        def fake_edge(options, service):
            captured["options"] = options
            captured["service"] = service
            return Mock()

        with (
            patch.object(edge_runtime, "restricted_workspace", return_value=True),
            patch.object(edge_runtime, "browser_temp_dir", return_value=FAKE_TEMP),
            patch.object(edge_runtime, "Edge", side_effect=fake_edge),
        ):
            edge_runtime.launch(Options())

        environment = captured["service"].env
        self.assertEqual(environment["TMP"], str(FAKE_TEMP))
        self.assertEqual(environment["TEMP"], str(FAKE_TEMP))

    def test_launch_keeps_system_temp_when_unrestricted(self):
        captured = {}

        def fake_edge(options, service):
            captured["service"] = service
            return Mock()

        with (
            patch.object(edge_runtime, "restricted_workspace", return_value=False),
            patch.object(edge_runtime, "Edge", side_effect=fake_edge),
        ):
            edge_runtime.launch(Options())

        environment = captured["service"].env or {}
        self.assertEqual(
            environment.get("TMP"),
            os.environ.get("TMP"),
        )
        self.assertEqual(
            environment.get("TEMP"),
            os.environ.get("TEMP"),
        )

    def test_temp_probe_reports_denied_creation(self):
        with (
            patch.object(edge_runtime, "_system_temp_dir", return_value=FAKE_TEMP),
            patch.object(Path, "mkdir", side_effect=PermissionError("denied")),
        ):
            self.assertFalse(edge_runtime.system_temp_writable())

    def test_temp_probe_reports_writable_directory(self):
        writable = Path(__file__).resolve().parent / "outputs"
        writable.mkdir(parents=True, exist_ok=True)
        with patch.object(edge_runtime, "_system_temp_dir", return_value=writable):
            self.assertTrue(edge_runtime.system_temp_writable())

    def test_restricted_workspace_is_the_inverse_of_temp_writability(self):
        with patch.object(edge_runtime, "system_temp_writable", return_value=False):
            self.assertTrue(edge_runtime.restricted_workspace())
        edge_runtime._restricted = None
        with patch.object(edge_runtime, "system_temp_writable", return_value=True):
            self.assertFalse(edge_runtime.restricted_workspace())


if __name__ == "__main__":
    unittest.main()

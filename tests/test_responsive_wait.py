from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from responsive_wait import WebDriverWait


class ResponsiveWebDriverWaitTests(unittest.TestCase):
    def test_until_runs_optional_driver_poll_hook(self) -> None:
        attempts = 0

        def hook() -> None:
            nonlocal attempts
            attempts += 1

        driver = SimpleNamespace(_responsive_wait_hook=hook)
        result = WebDriverWait(driver, timeout=0.2).until(lambda _: True)

        self.assertTrue(result)
        self.assertEqual(attempts, 1)

    def test_until_pumps_gui_events_for_each_poll(self) -> None:
        attempts = 0

        def condition(_driver: object) -> bool:
            nonlocal attempts
            attempts += 1
            return attempts >= 3

        with patch("responsive_wait.pump_gui_events") as pump:
            result = WebDriverWait(
                object(), timeout=0.2, poll_frequency=0.001
            ).until(condition)

        self.assertTrue(result)
        self.assertEqual(attempts, 3)
        self.assertEqual(pump.call_count, 3)


if __name__ == "__main__":
    unittest.main()

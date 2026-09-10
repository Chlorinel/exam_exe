from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import TimeoutException

import deepseek_question_generator as deepseek


class BackgroundBrowserTests(unittest.TestCase):
    def test_deepseek_login_wait_has_total_timeout(self):
        config = deepseek.DeepSeekWebConfig(
            profile_dir=Path("profile"),
            login_timeout=360,
        )
        generator = deepseek.DeepSeekWebGenerator(config, driver=Mock())

        with patch.object(
            deepseek.time,
            "monotonic",
            side_effect=[0, 361],
        ):
            with self.assertRaisesRegex(RuntimeError, "超过 6 分钟"):
                generator._wait_for_login(Mock())

    def test_deepseek_login_switches_to_visible_and_back_to_headless(self):
        config = deepseek.DeepSeekWebConfig(
            profile_dir=Path("profile"),
            headless=True,
        )
        generator = deepseek.DeepSeekWebGenerator(config, driver=Mock())
        generator._owns_driver = True
        generator._active_headless = True
        visible = Mock()
        resumed = Mock()
        restarts = []

        def restart(*, headless):
            restarts.append(headless)
            generator.driver = resumed if headless else visible
            generator._active_headless = headless
            return generator.driver

        waits = []
        for outcome in (TimeoutException(), True, True, True):
            wait = Mock()
            if isinstance(outcome, Exception):
                wait.until.side_effect = outcome
            else:
                wait.until.return_value = outcome
            waits.append(wait)

        with patch.object(
            deepseek,
            "WebDriverWait",
            side_effect=waits,
        ), patch.object(
            generator,
            "_restart_browser",
            side_effect=restart,
        ), patch.object(
            generator,
            "_wait_for_login",
        ) as wait_for_login:
            generator.ensure_logged_in()

        self.assertEqual(restarts, [False, True])
        visible.get.assert_called_once_with(config.chat_url)
        resumed.get.assert_called_once_with(config.chat_url)
        wait_for_login.assert_called_once()


if __name__ == "__main__":
    unittest.main()

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from platform_guidance import dismiss_platform_guidance, install_guidance_hook


class PlatformGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.enterContext(patch("platform_guidance.time", SimpleNamespace(monotonic=lambda: self.now)))
        self.enterContext(patch("platform_guidance.responsive_sleep", side_effect=self.advance_time))

    def advance_time(self, seconds):
        self.now += seconds

    def make_tour(self, labels):
        button = Mock()
        button.text = labels[0]
        button.is_displayed.return_value = True
        button.is_enabled.return_value = True
        # An older run may have left this marker on the reused node.
        button.get_attribute.return_value = "1"
        clicks = []
        driver = Mock()
        driver.current_url = "https://aic.sysu.edu.cn/aic/questionbank-hub/"
        driver.find_elements.side_effect = lambda *args: [button] if len(clicks) < len(labels) else []

        def execute_script(script, element):
            if script == "arguments[0].click();":
                clicks.append(button.text)
                if len(clicks) < len(labels):
                    button.text = labels[len(clicks)]
            else:
                return True

        driver.execute_script.side_effect = execute_script
        return driver, button, clicks

    def test_reused_node_and_multiple_next_and_acknowledgement_steps(self):
        labels = ["下一步", "下一步", "下一步", "我知道了", "我知道了"]
        driver, button, clicks = self.make_tour(labels)
        self.assertTrue(dismiss_platform_guidance(driver))
        self.assertEqual(clicks, labels)
        button.get_attribute.assert_not_called()

    def test_hidden_disabled_and_ordinary_next_buttons_are_skipped(self):
        driver, guide, clicks = self.make_tour(["我知道了"])
        hidden = Mock(text="下一步")
        hidden.is_displayed.return_value = False
        disabled = Mock(text="下一步")
        disabled.is_displayed.return_value = True
        disabled.is_enabled.return_value = False
        ordinary = Mock(text="下一步")
        ordinary.is_displayed.return_value = True
        ordinary.is_enabled.return_value = True
        def script(code, element):
            if code == "arguments[0].click();":
                self.assertIs(element, guide)
                clicks.append(guide.text)
            return False
        driver.execute_script.side_effect = script
        driver.find_elements.side_effect = lambda *args: [hidden, disabled, ordinary, guide] if not clicks else []
        self.assertTrue(dismiss_platform_guidance(driver))
        self.assertEqual(clicks, ["我知道了"])

    def test_stuck_tour_has_a_step_limit(self):
        driver, _, clicks = self.make_tour(["下一步"] * 50)
        self.assertTrue(dismiss_platform_guidance(driver, max_steps=3))
        self.assertEqual(len(clicks), 3)

    def test_wait_hook_advances_only_one_step_per_poll(self):
        driver, _, clicks = self.make_tour(["下一步", "我知道了"])
        install_guidance_hook(driver)
        driver._responsive_wait_hook()
        self.assertEqual(clicks, ["下一步"])
        driver._responsive_wait_hook()
        self.assertEqual(clicks, ["下一步", "我知道了"])

    def test_other_sites_and_login_pages_are_ignored(self):
        for url in ["https://example.com", "https://aic.sysu.edu.cn/login"]:
            with self.subTest(url=url):
                driver, _, clicks = self.make_tour(["我知道了"])
                driver.current_url = url
                self.assertFalse(dismiss_platform_guidance(driver))
                self.assertEqual(clicks, [])


if __name__ == "__main__":
    unittest.main()

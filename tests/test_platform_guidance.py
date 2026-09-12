from __future__ import annotations

import unittest
from unittest.mock import Mock

from platform_guidance import dismiss_platform_guidance, install_guidance_hook


def guide_button(label: str) -> Mock:
    element = Mock()
    element.text = label
    element.is_displayed.return_value = True
    element.is_enabled.return_value = True
    element.get_attribute.return_value = None
    return element


class PlatformGuidanceTests(unittest.TestCase):
    def test_clicks_next_inside_guide_context(self) -> None:
        element = guide_button("下一步")
        driver = Mock()
        driver.current_url = "https://aic.sysu.edu.cn/aic/course"
        driver.find_elements.return_value = [element]
        driver.execute_script.side_effect = [True, None]

        self.assertTrue(dismiss_platform_guidance(driver))
        self.assertEqual(driver.execute_script.call_count, 2)

    def test_does_not_click_normal_next_button(self) -> None:
        element = guide_button("下一步")
        driver = Mock()
        driver.current_url = "https://aic.sysu.edu.cn/aic/course"
        driver.find_elements.return_value = [element]
        driver.execute_script.return_value = False

        self.assertFalse(dismiss_platform_guidance(driver))
        self.assertEqual(driver.execute_script.call_count, 1)

    def test_clicks_acknowledgement_and_installs_wait_hook(self) -> None:
        element = guide_button("我知道了")
        driver = Mock()
        driver.current_url = "https://aic.sysu.edu.cn/aic/course"
        driver.find_elements.return_value = [element]

        self.assertTrue(dismiss_platform_guidance(driver))
        install_guidance_hook(driver)
        driver._responsive_wait_hook()
        self.assertGreaterEqual(driver.execute_script.call_count, 2)

    def test_never_advances_login_page(self) -> None:
        driver = Mock()
        driver.current_url = "https://aic.sysu.edu.cn/login"

        self.assertFalse(dismiss_platform_guidance(driver))
        driver.find_elements.assert_not_called()


if __name__ == "__main__":
    unittest.main()

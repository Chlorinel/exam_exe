from __future__ import annotations

import unittest
from unittest.mock import Mock

from create_signal_exam import matching_class_buttons


class ClassSelectionTests(unittest.TestCase):
    def test_matches_nested_button_text_after_normalization(self) -> None:
        wanted = Mock()
        wanted.text = "\n 202613475 \n"
        wanted.is_displayed.return_value = True
        select_all = Mock()
        select_all.text = "全选"
        select_all.is_displayed.return_value = True
        hidden = Mock()
        hidden.text = "202613475"
        hidden.is_displayed.return_value = False
        driver = Mock()
        driver.find_elements.return_value = [wanted, select_all, hidden]

        self.assertEqual(matching_class_buttons(driver, "202613475"), [wanted])
        driver.find_elements.assert_called_once()


if __name__ == "__main__":
    unittest.main()

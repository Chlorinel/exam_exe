from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from create_signal_exam import (
    ensure_configured_questions,
    matching_class_buttons,
    selected_question_count,
)


class ClassSelectionTests(unittest.TestCase):
    @patch("create_signal_exam.finish_question_config")
    @patch("create_signal_exam.select_configured_questions")
    @patch("create_signal_exam.visible")
    def test_interrupted_selection_resumes_after_existing_prefix(
        self,
        visible_mock: Mock,
        select_mock: Mock,
        finish_mock: Mock,
    ) -> None:
        visible_mock.return_value = [
            SimpleNamespace(text="first question [10001]"),
            SimpleNamespace(text="second question [10002]"),
        ]
        config = SimpleNamespace(
            questions=[
                SimpleNamespace(identifier="[10001]"),
                SimpleNamespace(identifier="[10002]"),
                SimpleNamespace(identifier="[10003]"),
            ]
        )
        driver = Mock()

        ensure_configured_questions(driver, config)

        select_mock.assert_called_once_with(driver, config, already_selected=2)
        finish_mock.assert_not_called()

    @patch("create_signal_exam.visible")
    def test_interrupted_selection_rejects_nonmatching_prefix(self, visible_mock: Mock) -> None:
        visible_mock.return_value = [SimpleNamespace(text="another question")]
        config = SimpleNamespace(questions=[SimpleNamespace(identifier="[10001]")])

        with self.assertRaisesRegex(RuntimeError, "唯一标识"):
            ensure_configured_questions(Mock(), config)

    def test_reads_question_selection_counter_with_spacing(self) -> None:
        self.assertEqual(selected_question_count("已选中 3 道题"), 3)
        self.assertEqual(selected_question_count("已选中\n3\n道 题"), 3)
        self.assertIsNone(selected_question_count("您还没有添加题目"))

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

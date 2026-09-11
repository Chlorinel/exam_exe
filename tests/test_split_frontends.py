from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea

import exam_editor_gui
import question_workflow_gui
from question_review_gui_with_generation import QuestionGenerationDialog


class SplitFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_frontends_have_separate_responsibilities(self) -> None:
        self.assertFalse(hasattr(exam_editor_gui, "prepare_ai_exam_config"))
        self.assertFalse(hasattr(question_workflow_gui, "run_configuration"))
        self.assertFalse(hasattr(question_workflow_gui, "QuestionWorkflowWindow"))
        self.assertFalse(Path("exam_control_gui.py").exists())
        self.assertFalse(Path("exam_control_gui.pyw").exists())

    @patch("question_workflow_gui.QMessageBox.information")
    @patch("question_workflow_gui.prepare_ai_exam_config")
    @patch("question_workflow_gui.completed_session_for_config", return_value=False)
    @patch("question_workflow_gui.load_config")
    def test_question_launcher_enters_workflow_without_outer_window(
        self,
        _load_config,
        _completed,
        prepare,
        _information,
    ) -> None:
        expected = Path("prepared.xlsx").resolve()
        prepare.return_value = expected

        result = question_workflow_gui.run_question_workflow(Path("config.xlsx"))

        self.assertEqual(result, expected)
        prepare.assert_called_once()

    def test_generation_dialog_collects_heterogeneous_groups(self) -> None:
        dialog = QuestionGenerationDialog()
        self.assertIsNotNone(dialog.findChild(QScrollArea))
        self.assertEqual((dialog.minimumWidth(), dialog.minimumHeight()), (620, 480))
        self.assertTrue(dialog.isSizeGripEnabled())
        dialog.chapter_edit.setText("第一章")
        dialog.knowledge_edit.setText("复指数信号")
        dialog.count_spin.setValue(2)
        dialog._add_current_spec()

        dialog.chapter_edit.setText("第二章")
        dialog.knowledge_edit.setText("卷积积分")
        dialog.type_combo.setCurrentIndex(4)
        dialog.count_spin.setValue(1)
        dialog._add_current_spec()

        specs = dialog._build_specs()
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].question_type, "single_choice")
        self.assertEqual(specs[0].count, 2)
        self.assertEqual(specs[1].chapter, "第二章")
        self.assertEqual(specs[1].question_type, "calculation")
        dialog.close()

    def test_last_group_can_be_submitted_without_adding_another_row(self) -> None:
        dialog = QuestionGenerationDialog()
        dialog.chapter_edit.setText("第一章")
        dialog.knowledge_edit.setText("复指数信号")
        dialog._add_current_spec()
        dialog.chapter_edit.setText("第三章")
        dialog.knowledge_edit.setText("傅里叶级数")

        specs = dialog._build_specs()

        self.assertEqual([item.chapter for item in specs], ["第一章", "第三章"])
        dialog.close()


if __name__ == "__main__":
    unittest.main()

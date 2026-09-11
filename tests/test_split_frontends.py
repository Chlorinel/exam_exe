from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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
        self.assertFalse(Path("exam_control_gui.py").exists())
        self.assertFalse(Path("exam_control_gui.pyw").exists())

    def test_generation_dialog_collects_heterogeneous_groups(self) -> None:
        dialog = QuestionGenerationDialog()
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

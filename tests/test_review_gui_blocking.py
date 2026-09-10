from __future__ import annotations

"""Regression tests for the embedded review window event loop."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import question_review_gui_with_generation as review


class GenerateAndReviewBlockingTests(unittest.TestCase):
    def test_last_approval_closes_review_window_without_another_dialog(self):
        question = Mock()
        question.local_id = "Q001"
        question.to_dict.return_value = {"local_id": "Q001"}
        window = Mock()
        window.batch.questions = [question]
        window.current_index = 0
        window._question_from_form.return_value = question

        with patch.object(review, "validate_question", return_value=[]), patch.object(
            review,
            "batch_is_fully_approved",
            return_value=True,
        ), patch.object(review.QMessageBox, "information") as information:
            review.QuestionReviewWindow.approve_current(window)

        information.assert_not_called()
        window.close.assert_called_once_with()

    def test_existing_qapplication_waits_for_review_before_returning(self):
        with tempfile.TemporaryDirectory() as directory:
            batch_path = Path(directory) / "questions.json"
            batch_path.write_text("{}", encoding="utf-8")

            dialog = Mock()
            dialog.exec.return_value = review.QDialog.DialogCode.Accepted
            dialog.generated_path = batch_path
            dialog.generated_deepseek_config = Mock()

            review_window = Mock()

            with (
                patch.object(
                    review.QApplication,
                    "instance",
                    return_value=Mock(),
                ),
                patch.object(
                    review,
                    "QuestionGenerationDialog",
                    return_value=dialog,
                ),
                patch.object(
                    review,
                    "QuestionReviewWindow",
                    return_value=review_window,
                ),
                patch.object(
                    review,
                    "_run_review_window_blocking",
                ) as blocking,
                patch.object(
                    review,
                    "question_batch_file_is_approved",
                    return_value=True,
                ) as approved,
            ):
                result_path, result_approved = (
                    review.generate_and_review_questions()
                )

            self.assertEqual(result_path, batch_path)
            self.assertTrue(result_approved)
            blocking.assert_called_once_with(review_window)
            approved.assert_called_once_with(batch_path)

    def test_blocking_helper_runs_local_event_loop(self):
        window = Mock()
        signal = Mock()
        window.destroyed = signal
        event_loop = Mock()

        with patch.object(
            review,
            "QEventLoop",
            return_value=event_loop,
        ):
            review._run_review_window_blocking(window)

        window.setAttribute.assert_called_once_with(
            review.Qt.WidgetAttribute.WA_DeleteOnClose,
            True,
        )
        signal.connect.assert_called_once_with(event_loop.quit)
        window.show.assert_called_once_with()
        event_loop.exec.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

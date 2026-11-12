from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_exam_preparer import pending_batch_can_resume, prepare_ai_exam_config
from deepseek_question_generator import GeneratedQuestion, QuestionBatch, QuestionSpec, save_question_batch
from exam_session import create_session_from_batch, save_session


class PendingReviewResumeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config_path = self.root / "exam.xlsx"
        self.config_path.touch()
        self.batch_path = self.root / "questions.json"
        self.session_path = self.root / "session.json"
        question = GeneratedQuestion(
            local_id="Q001", question_type="single_choice", chapter="第四章", knowledge_point="test",
            difficulty="medium", stem="Question [12345]", options={"A": "1", "B": "2", "C": "3", "D": "4"},
            answer="A", explanation="Explanation", score=Decimal("5"), identifier="[12345]",
        )
        batch = QuestionBatch(
            batch_id="resume-test", provider="test", created_at="2026-11-12",
            spec=QuestionSpec(course_name="test", chapter="第四章", knowledge_point="test"),
            questions=[question], raw_response="",
        )
        save_question_batch(self.batch_path, batch)
        self.session = create_session_from_batch("Current exam", batch)
        save_session(self.session, self.session_path)

    def test_same_generated_batch_can_resume_review(self):
        self.assertTrue(pending_batch_can_resume(self.batch_path, self.session_path, "Current exam"))

    def test_different_exam_or_question_mapping_does_not_resume(self):
        self.assertFalse(pending_batch_can_resume(self.batch_path, self.session_path, "Different exam"))
        self.session.questions[0].identifier = "[54321]"
        save_session(self.session, self.session_path)
        self.assertFalse(pending_batch_can_resume(self.batch_path, self.session_path, "Current exam"))

    def test_resume_opens_review_without_regenerating_or_uploading_unapproved_items(self):
        before = self.batch_path.read_bytes()
        with (
            patch("ai_exam_preparer.load_config", return_value=SimpleNamespace(exam_name="Current exam")),
            patch("ai_exam_preparer.review_question_batch", return_value=False) as review,
            patch("ai_exam_preparer.generate_and_review_questions") as generate,
            patch("ai_exam_preparer.clear_previous_question_json") as clear,
            patch("ai_exam_preparer.upload_batch") as upload,
        ):
            with self.assertRaisesRegex(RuntimeError, "尚未全部人工审核通过"):
                prepare_ai_exam_config(
                    self.config_path, batch_path=self.batch_path, session_path=self.session_path,
                    deepseek_profile_dir=self.root / "deepseek", platform_profile_dir=self.root / "platform",
                )
            review.assert_called_once()
            generate.assert_not_called()
            clear.assert_not_called()
            upload.assert_not_called()
        self.assertEqual(self.batch_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

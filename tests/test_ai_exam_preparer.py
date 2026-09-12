from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from ai_exam_preparer import (
    prepare_ai_exam_config,
    reviewed_batch_can_resume,
)


class AIExamPreparerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

        self.source = (
            self.root
            / "考试配置表.xlsx"
        )
        self.source.write_bytes(
            b"placeholder"
        )

        self.deepseek_profile = (
            self.root
            / "deepseek-profile"
        )
        self.platform_profile = (
            self.root
            / "platform-profile"
        )

        self.base_config = SimpleNamespace(
            course_id="course-1",
            term_id="term-1",
            course_name="信号与系统",
            exam_name="测试考试",
        )

        self.batch = SimpleNamespace(
            batch_id="batch-1",
        )
        self.upload_state = SimpleNamespace(
            batch_id="batch-1",
        )
        self.located = [
            SimpleNamespace(
                local_id="Q001"
            )
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_reviewed_batch_can_resume_after_upload_failure(self):
        batch = self.root / "reviewed.json"
        session = self.root / "session.json"
        batch.write_text("{}", encoding="utf-8")
        session.write_text("{}", encoding="utf-8")

        with (
            patch(
                "ai_exam_preparer.load_session",
                return_value=SimpleNamespace(
                    exam_name="测试考试",
                    status="reviewed",
                ),
            ),
            patch(
                "ai_exam_preparer.question_batch_file_is_approved",
                return_value=True,
            ),
        ):
            self.assertTrue(
                reviewed_batch_can_resume(batch, session, "测试考试")
            )

    def test_reviewed_batch_does_not_resume_for_another_exam(self):
        batch = self.root / "reviewed.json"
        session = self.root / "session.json"
        batch.write_text("{}", encoding="utf-8")
        session.write_text("{}", encoding="utf-8")

        with patch(
            "ai_exam_preparer.load_session",
            return_value=SimpleNamespace(
                exam_name="其他考试",
                status="reviewed",
            ),
        ):
            self.assertFalse(
                reviewed_batch_can_resume(batch, session, "测试考试")
            )

    @patch(
        "ai_exam_preparer.create_ai_exam_config"
    )
    @patch(
        "ai_exam_preparer.locate_uploaded_questions"
    )
    @patch(
        "ai_exam_preparer.ensure_all_uploaded"
    )
    @patch(
        "ai_exam_preparer.upload_batch"
    )
    @patch(
        "ai_exam_preparer.load_question_batch"
    )
    @patch(
        "ai_exam_preparer.question_batch_file_is_approved"
    )
    @patch(
        "ai_exam_preparer.generate_and_review_questions"
    )
    @patch(
        "ai_exam_preparer.load_config"
    )
    def test_full_pipeline_order_and_result(
        self,
        load_config_mock,
        generate_mock,
        approved_gate_mock,
        load_batch_mock,
        upload_mock,
        ensure_uploaded_mock,
        locate_mock,
        writer_mock,
    ):
        reviewed = (
            self.root
            / "reviewed.json"
        )
        final = (
            self.root
            / "work"
            / "考试配置表_AI完成.xlsx"
        )

        load_config_mock.side_effect = [
            self.base_config,
            SimpleNamespace(),
        ]
        generate_mock.return_value = (
            reviewed,
            True,
        )
        approved_gate_mock.return_value = (
            True
        )
        load_batch_mock.return_value = (
            self.batch
        )
        upload_mock.return_value = (
            self.upload_state
        )
        locate_mock.return_value = (
            self.located
        )
        writer_mock.return_value = (
            final
        )

        result = prepare_ai_exam_config(
            self.source,
            deepseek_profile_dir=(
                self.deepseek_profile
            ),
            platform_profile_dir=(
                self.platform_profile
            ),
        )

        self.assertEqual(
            result,
            final,
        )

        generate_mock.assert_called_once()

        approved_gate_mock.assert_called_once_with(
            reviewed.resolve()
        )

        load_batch_mock.assert_called_once_with(
            reviewed.resolve()
        )

        upload_mock.assert_called_once()
        upload_args = upload_mock.call_args

        self.assertEqual(
            upload_args.args[1],
            reviewed.resolve(),
        )
        self.assertTrue(
            upload_args.kwargs["commit"]
        )
        self.assertTrue(
            upload_args.kwargs[
                "assume_yes"
            ]
        )

        ensure_uploaded_mock.assert_called_once_with(
            self.batch,
            self.upload_state,
        )

        locate_mock.assert_called_once_with(
            upload_args.args[0],
            self.batch,
            self.upload_state,
            session_path=(
                self.root
                / "work"
                / "current_exam_session.json"
            ),
        )

        self.assertEqual(
            upload_args.kwargs["session_path"],
            self.root / "work" / "current_exam_session.json",
        )

        writer_mock.assert_called_once()
        self.assertEqual(
            writer_mock.call_args.args[0],
            self.source.resolve(),
        )
        self.assertEqual(
            writer_mock.call_args.args[2],
            self.located,
        )

        self.assertEqual(
            load_config_mock.call_args_list[
                0
            ],
            call(
                self.source.resolve(),
                require_questions=False,
            ),
        )
        self.assertEqual(
            load_config_mock.call_args_list[
                1
            ],
            call(
                final,
                require_questions=True,
            ),
        )

    @patch(
        "ai_exam_preparer.generate_and_review_questions"
    )
    @patch(
        "ai_exam_preparer.load_config"
    )
    def test_cancelled_gui_returns_none(
        self,
        load_config_mock,
        generate_mock,
    ):
        load_config_mock.return_value = (
            self.base_config
        )
        generate_mock.return_value = (
            None,
            False,
        )
        batch = self.root / "work" / "考试配置表_AI题目.json"
        stale = [
            batch,
            batch.with_name(batch.stem + ".ai-original.json"),
            batch.with_name(batch.stem + ".review-log.json"),
            batch.with_suffix(".upload-state.json"),
        ]
        batch.parent.mkdir(parents=True, exist_ok=True)
        for path in stale:
            path.write_text("{}", encoding="utf-8")

        result = prepare_ai_exam_config(
            self.source,
            deepseek_profile_dir=(
                self.deepseek_profile
            ),
            platform_profile_dir=(
                self.platform_profile
            ),
        )

        self.assertIsNone(result)
        self.assertTrue(all(not path.exists() for path in stale))

    @patch(
        "ai_exam_preparer.upload_batch"
    )
    @patch(
        "ai_exam_preparer.load_question_batch"
    )
    @patch(
        "ai_exam_preparer.question_batch_file_is_approved"
    )
    @patch(
        "ai_exam_preparer.generate_and_review_questions"
    )
    @patch(
        "ai_exam_preparer.load_config"
    )
    def test_review_failure_stops_before_upload(
        self,
        load_config_mock,
        generate_mock,
        approved_gate_mock,
        load_batch_mock,
        upload_mock,
    ):
        reviewed = (
            self.root
            / "reviewed.json"
        )

        load_config_mock.return_value = (
            self.base_config
        )
        generate_mock.return_value = (
            reviewed,
            False,
        )

        with self.assertRaises(
            RuntimeError
        ):
            prepare_ai_exam_config(
                self.source,
                deepseek_profile_dir=(
                    self.deepseek_profile
                ),
                platform_profile_dir=(
                    self.platform_profile
                ),
            )

        approved_gate_mock.assert_not_called()
        load_batch_mock.assert_not_called()
        upload_mock.assert_not_called()

    @patch(
        "ai_exam_preparer.create_ai_exam_config"
    )
    @patch(
        "ai_exam_preparer.locate_uploaded_questions"
    )
    @patch(
        "ai_exam_preparer.ensure_all_uploaded"
    )
    @patch(
        "ai_exam_preparer.upload_batch"
    )
    @patch(
        "ai_exam_preparer.load_question_batch"
    )
    @patch(
        "ai_exam_preparer.question_batch_file_is_approved"
    )
    @patch(
        "ai_exam_preparer.generate_and_review_questions"
    )
    @patch(
        "ai_exam_preparer.load_config"
    )
    def test_incomplete_upload_stops_before_locator(
        self,
        load_config_mock,
        generate_mock,
        approved_gate_mock,
        load_batch_mock,
        upload_mock,
        ensure_uploaded_mock,
        locate_mock,
        writer_mock,
    ):
        reviewed = (
            self.root
            / "reviewed.json"
        )

        load_config_mock.return_value = (
            self.base_config
        )
        generate_mock.return_value = (
            reviewed,
            True,
        )
        approved_gate_mock.return_value = (
            True
        )
        load_batch_mock.return_value = (
            self.batch
        )
        upload_mock.return_value = (
            self.upload_state
        )
        ensure_uploaded_mock.side_effect = (
            RuntimeError(
                "Q001 尚未 uploaded"
            )
        )

        with self.assertRaises(
            RuntimeError
        ):
            prepare_ai_exam_config(
                self.source,
                deepseek_profile_dir=(
                    self.deepseek_profile
                ),
                platform_profile_dir=(
                    self.platform_profile
                ),
            )

        locate_mock.assert_not_called()
        writer_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

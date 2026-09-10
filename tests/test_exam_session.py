from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from create_signal_exam import ExamConfig, config_from_session
from deepseek_question_generator import (
    GeneratedQuestion,
    QuestionBatch,
    QuestionSpec,
    save_question_batch,
)
from exam_session import (
    ExamQuestionRecord,
    all_uploaded,
    create_session,
    create_session_from_batch,
    load_session,
    save_exam_state,
    save_session,
    sync_review_from_batch,
    update_question_location,
    update_question_upload,
    update_status,
)
from question_review_gui_with_generation import (
    initialize_generated_session,
    update_review_session,
)


class ExamSessionTests(unittest.TestCase):
    def test_generation_and_review_update_the_same_session_file(self):
        question = GeneratedQuestion(
            local_id="Q001",
            question_type="calculation",
            chapter="第二章",
            knowledge_point="卷积积分",
            difficulty="medium",
            stem="计算卷积积分",
            options=None,
            answer="1",
            explanation="测试解析",
            score=Decimal("10"),
        )
        batch = QuestionBatch(
            batch_id="batch-1",
            provider="deepseek-web",
            created_at="2026-09-10T00:00:00+00:00",
            spec=QuestionSpec("信号与系统", "第二章", "卷积积分"),
            questions=[question],
            raw_response="",
        )
        with tempfile.TemporaryDirectory() as directory:
            batch_path = Path(directory) / "questions.json"
            session_path = Path(directory) / "current_exam_session.json"
            save_question_batch(batch_path, batch)
            initialize_generated_session(batch_path, session_path, "测试考试")
            self.assertEqual(load_session(session_path).status, "generated")
            batch.questions[0].review_status = "approved"
            save_question_batch(batch_path, batch)
            self.assertTrue(update_review_session(batch_path, session_path))
            self.assertEqual(load_session(session_path).status, "reviewed")

    def test_flat_round_trip_preserves_question_mapping(self):
        session = create_session(
            "信号与系统测试",
            [
                ExamQuestionRecord(
                    local_id="Q001",
                    chapter="第二章",
                    knowledge_point="卷积积分",
                    question_type="计算题",
                    score=10,
                    keyword="卷积",
                    review_status="approved",
                    upload_status="uploaded",
                    question_number=15,
                    platform_id="123456",
                )
            ],
        )
        update_status(session, "uploaded")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current_exam_session.json"
            save_session(session, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_session(path)
        self.assertEqual(raw["session_id"], session.session_id)
        self.assertNotIn("session", raw)
        self.assertEqual(loaded.questions[0].platform_id, "123456")
        self.assertTrue(all_uploaded(loaded))

    def test_old_question_objects_use_safe_defaults(self):
        batch = SimpleNamespace(
            questions=[SimpleNamespace(local_id="Q001", review_status="pending")]
        )
        session = create_session_from_batch("兼容测试", batch)
        question = session.questions[0]
        self.assertEqual(session.status, "generated")
        self.assertEqual(question.chapter, "")
        self.assertEqual(question.knowledge_point, "")
        self.assertEqual(question.score, 0)
        self.assertEqual(question.keyword, "")

    def test_review_advances_only_when_every_question_is_approved(self):
        pending_batch = SimpleNamespace(
            questions=[
                SimpleNamespace(local_id="Q001", review_status="approved"),
                SimpleNamespace(local_id="Q002", review_status="pending"),
            ]
        )
        session = create_session_from_batch("审核测试", pending_batch)
        self.assertFalse(sync_review_from_batch(session, pending_batch))
        self.assertEqual(session.status, "generated")
        approved_batch = SimpleNamespace(
            questions=[
                SimpleNamespace(local_id="Q001", review_status="approved"),
                SimpleNamespace(local_id="Q002", review_status="approved"),
            ]
        )
        self.assertTrue(sync_review_from_batch(session, approved_batch))
        self.assertEqual(session.status, "reviewed")

    def test_upload_and_location_mapping(self):
        session = create_session(
            "上传测试",
            [ExamQuestionRecord(local_id="Q001", review_status="approved")],
        )
        update_status(session, "reviewed")
        update_question_upload(
            session,
            "Q001",
            upload_status="uploaded",
            platform_id="remote-1",
        )
        self.assertFalse(all_uploaded(session))
        update_question_location(
            session,
            "Q001",
            chapter="第二章",
            question_number=15,
            keyword="卷积",
        )
        self.assertTrue(all_uploaded(session))

    def test_session_questions_replace_excel_question_rows(self):
        from datetime import datetime, timezone

        base = ExamConfig(
            "Excel 名称",
            "信号与系统",
            "course-1",
            "term-1",
            "class-1",
            datetime(2026, 9, 10, 9, tzinfo=timezone.utc),
            datetime(2026, 9, 10, 10, tzinfo=timezone.utc),
            None,
            "人工发布",
            (),
        )
        session = create_session(
            "Session 名称",
            [
                ExamQuestionRecord(
                    local_id="Q001",
                    chapter="第二章",
                    score=Decimal("10"),
                    keyword="卷积",
                    upload_status="uploaded",
                    question_number=15,
                )
            ],
        )
        config = config_from_session(base, session)
        self.assertEqual(config.exam_name, "Session 名称")
        self.assertEqual(config.questions[0].chapter, 2)
        self.assertEqual(config.questions[0].number, 15)
        self.assertEqual(config.questions[0].score, Decimal("10.0"))

    def test_exam_state_has_scheduler_gate_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exam_state.json"
            save_exam_state(
                path,
                exam_id="123456",
                status="published",
                end_time="2026-09-10T10:00:00+08:00",
                answer_release_time="2026-09-10T11:00:00+08:00",
            )
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["exam_id"], "123456")
        self.assertEqual(data["status"], "published")


if __name__ == "__main__":
    unittest.main()

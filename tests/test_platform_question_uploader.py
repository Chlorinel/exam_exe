import json
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from deepseek_question_generator import (
    GeneratedQuestion,
    QuestionBatch,
    QuestionSpec,
    save_question_batch,
)
from platform_question_uploader import (
    RichSegment,
    UploadConfig,
    UploadRecord,
    UploadState,
    load_upload_state,
    parse_markdown_latex,
    question_fingerprint,
    save_upload_state,
    upload_batch,
)
from exam_session import (
    create_session_from_batch,
    load_session,
    save_session,
    sync_review_from_batch,
)


def sample_question() -> GeneratedQuestion:
    return GeneratedQuestion(
        local_id="q-1",
        question_type="single_choice",
        chapter="第一章",
        knowledge_point="系统性质",
        difficulty="medium",
        stem=r"已知 \(x(t)\)，求输出。",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        answer="B",
        explanation=r"由 \[y(t)=2x(t)\] 可得。",
        score=Decimal("5"),
        review_status="approved",
    )


class RichTextTests(unittest.TestCase):
    def test_parse_inline_and_display_math(self):
        self.assertEqual(
            parse_markdown_latex(r"前 \(x+1\) 中 \[y=2\] 后"),
            [
                RichSegment("text", "前 "),
                RichSegment("math", "x+1", False),
                RichSegment("text", " 中 "),
                RichSegment("math", "y=2", True),
                RichSegment("text", " 后"),
            ],
        )

    def test_fingerprint_is_stable_and_content_sensitive(self):
        question = sample_question()
        self.assertEqual(question_fingerprint(question), question_fingerprint(question))
        changed = replace(question, answer="A")
        self.assertNotEqual(question_fingerprint(question), question_fingerprint(changed))


class StateTests(unittest.TestCase):
    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = UploadState("batch-1", {"q-1": UploadRecord("abc", "uploaded", "remote-1")})
            save_upload_state(path, state)
            loaded = load_upload_state(path, "batch-1")
            self.assertEqual(loaded.questions["q-1"].platform_question_id, "remote-1")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)

    def test_reject_state_for_other_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_upload_state(path, UploadState("old-batch"))
            with self.assertRaises(RuntimeError):
                load_upload_state(path, "new-batch")



class UploadBatchTests(unittest.TestCase):
    def test_upload_batch_returns_final_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_path = root / "questions.json"
            state_path = root / "questions.upload-state.json"

            question = sample_question()
            batch = QuestionBatch(
                batch_id="batch-upload-test",
                provider="deepseek-web",
                created_at="2026-09-10T00:00:00+00:00",
                spec=QuestionSpec(
                    course_name="信号与系统",
                    chapter="第一章",
                    knowledge_point="系统性质",
                    count=1,
                    score=Decimal("5"),
                ),
                questions=[question],
                raw_response="",
            )
            save_question_batch(batch_path, batch)
            session_path = root / "current_exam_session.json"
            session = create_session_from_batch("上传测试", batch)
            self.assertTrue(sync_review_from_batch(session, batch))
            save_session(session, session_path)

            config = UploadConfig(
                course_id="course-test",
                term_id="term-test",
                course_name="信号与系统",
                profile_dir=root / "profile",
            )

            class FakeUploader:
                def __init__(self, _config):
                    self.config = _config

                def open_question_bank(self):
                    return None

                def open_manual_create(self):
                    return None

                def fill_question(self, _question):
                    return None

                def select_chapter(self, chapter):
                    return chapter

                def save_current_question(self):
                    return "remote-1"

                def close(self):
                    return None

            with patch(
                "platform_question_uploader.QuestionBankUploader",
                FakeUploader,
            ):
                state = upload_batch(
                    config,
                    batch_path,
                    state_path,
                    commit=True,
                    assume_yes=True,
                    session_path=session_path,
                )

            self.assertIsInstance(state, UploadState)
            self.assertIn("q-1", state.questions)

            record = state.questions["q-1"]
            self.assertEqual(record.status, "uploaded")
            self.assertEqual(
                record.platform_question_id,
                "remote-1",
            )

            persisted = load_upload_state(
                state_path,
                "batch-upload-test",
            )
            self.assertEqual(
                persisted.questions["q-1"].status,
                "uploaded",
            )
            self.assertEqual(
                persisted.questions["q-1"].platform_question_id,
                "remote-1",
            )
            session = load_session(session_path)
            self.assertEqual(session.status, "reviewed")
            self.assertEqual(session.questions[0].upload_status, "uploaded")
            self.assertEqual(session.questions[0].platform_id, "remote-1")


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from deepseek_question_generator import GeneratedQuestion
from platform_question_uploader import (
    RichSegment,
    UploadRecord,
    UploadState,
    load_upload_state,
    parse_markdown_latex,
    question_fingerprint,
    save_upload_state,
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


if __name__ == "__main__":
    unittest.main()

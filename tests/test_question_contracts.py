from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from deepseek_question_generator import (
    GeneratedQuestion,
    QuestionBatch,
    QuestionSpec,
    assign_question_identifiers,
    extract_question_identifier,
    load_question_batch,
    parse_generated_questions,
    save_question_batch,
    validate_batch_duplicates,
)
from question_review_gui_with_generation import question_batch_file_is_approved


def sample_question(*, status="approved", stem="题干 \\(x(t)\\) [12345]"):
    return GeneratedQuestion(
        local_id="Q001",
        question_type="single_choice",
        chapter="第二章",
        knowledge_point="卷积积分",
        difficulty="medium",
        stem=stem,
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        answer="A",
        explanation="因为 \\(x(t)=1\\)。",
        score=Decimal("5.5"),
        identifier="[12345]",
        review_status=status,
    )


def sample_batch(questions, *, expected_count=None):
    questions = list(questions)
    return QuestionBatch(
        batch_id="batch-1",
        provider="deepseek-web",
        created_at=datetime.now(timezone.utc).isoformat(),
        spec=QuestionSpec(
            course_name="信号与系统",
            chapter="第二章",
            knowledge_point="卷积积分",
            count=(
                len(questions)
                if expected_count is None
                else expected_count
            ),
            score=Decimal("5.5"),
        ),
        questions=questions,
        raw_response="",
    )


class QuestionContractTests(unittest.TestCase):
    def test_decimal_round_trip_and_approved_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.json"
            save_question_batch(path, sample_batch([sample_question()]))

            loaded = load_question_batch(path)
            self.assertEqual(loaded.questions[0].score, Decimal("5.5"))
            self.assertTrue(question_batch_file_is_approved(path))

    def test_gate_rejects_pending_or_invalid_question(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.json"
            save_question_batch(path, sample_batch([sample_question(status="pending")]))
            self.assertFalse(question_batch_file_is_approved(path))

            invalid = sample_question()
            invalid.answer = "E"
            save_question_batch(path, sample_batch([invalid]))
            self.assertFalse(question_batch_file_is_approved(path))

    def test_gate_rejects_incomplete_question_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.json"
            batch = sample_batch(
                [sample_question()],
                expected_count=2,
            )
            save_question_batch(path, batch)

            # 题目本身合法且 approved，但数量少于 spec.count，
            # 整批仍然必须拒绝上传。
            self.assertFalse(
                question_batch_file_is_approved(path)
            )

    def test_duplicate_stems_are_detected(self):
        questions = [
            sample_question(),
            sample_question(stem="  题干   \\(x(t)\\) [67890]  "),
        ]
        self.assertEqual(validate_batch_duplicates(questions)[0][0], 1)

    def test_assigns_unique_five_digit_identifiers_at_stem_end(self):
        first = sample_question(stem="第一道题")
        second = sample_question(stem="第二道题")
        first.identifier = ""
        second.identifier = ""

        assign_question_identifiers([first, second])

        identifiers = [
            extract_question_identifier(first.stem),
            extract_question_identifier(second.stem),
        ]
        self.assertTrue(all(identifiers))
        self.assertEqual(len(set(identifiers)), 2)
        self.assertTrue(first.stem.endswith(first.identifier))
        self.assertTrue(second.stem.endswith(second.identifier))

    def test_parser_removes_one_extra_latex_escape_layer(self):
        spec = QuestionSpec(
            course_name="信号与系统",
            chapter="第二章",
            knowledge_point="周期信号",
            question_type="计算题",
            count=1,
            score=10,
        )
        response = {
            "questions": [
                {
                    "type": "计算题",
                    "chapter": "第二章",
                    "knowledge_point": "周期信号",
                    "difficulty": "medium",
                    "question": (
                        r"计算 \\(\\frac{1}{2}\\)，并保留正确公式 "
                        r"\(y=1\)"
                    ),
                    "options": None,
                    "answer": r"\\(\\frac{1}{2}\\)",
                    "explanation": (
                        r"\\(\\begin{matrix}a&b\\\\c&d"
                        r"\\end{matrix}\\)"
                    ),
                    "score": 10,
                }
            ]
        }

        question = parse_generated_questions(
            json.dumps(response, ensure_ascii=False),
            spec,
        )[0]

        self.assertEqual(
            question.stem,
            r"计算 \(\frac{1}{2}\)，并保留正确公式 \(y=1\)",
        )
        self.assertEqual(question.answer, r"\(\frac{1}{2}\)")
        self.assertEqual(
            question.explanation,
            r"\(\begin{matrix}a&b\\c&d\end{matrix}\)",
        )

    def test_loading_old_batch_normalizes_overescaped_latex(self):
        question = sample_question(stem=r"题干 \\(x=\\frac{1}{2}\\) [12345]")
        question.explanation = r"解析 \\(x=1\\)"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.json"
            save_question_batch(path, sample_batch([question]))
            loaded = load_question_batch(path).questions[0]

        self.assertEqual(loaded.stem, r"题干 \(x=\frac{1}{2}\) [12345]")
        self.assertEqual(loaded.explanation, r"解析 \(x=1\)")


if __name__ == "__main__":
    unittest.main()

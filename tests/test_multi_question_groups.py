from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from deepseek_question_generator import (
    DeepSeekWebGenerator,
    GeneratedQuestion,
    QuestionBatch,
    QuestionSpec,
    load_question_batch,
)
from question_review_gui_with_generation import batch_is_fully_approved


def make_spec(chapter: str, knowledge: str, question_type: str, count: int):
    return QuestionSpec(
        course_name="信号与系统",
        chapter=chapter,
        knowledge_point=knowledge,
        question_type=question_type,
        difficulty="medium",
        count=count,
        score=Decimal("5"),
    )


def make_question(local_id: str, spec: QuestionSpec) -> GeneratedQuestion:
    return GeneratedQuestion(
        local_id=local_id,
        question_type=spec.question_type,
        chapter=spec.chapter,
        knowledge_point=spec.knowledge_point,
        difficulty=spec.difficulty,
        stem=f"{spec.knowledge_point} 测试题",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        answer="A",
        explanation="选择 A。",
        score=Decimal("5"),
        review_status="approved",
    )


class MultiQuestionGroupTests(unittest.TestCase):
    def test_combines_groups_with_unique_local_ids_and_round_trips(self):
        first = make_spec("第一章", "复指数信号", "single_choice", 1)
        second = make_spec("第二章", "卷积积分", "single_choice", 2)
        batches = [
            QuestionBatch("b1", "deepseek_web", "now", first, [make_question("Q001", first)], "r1"),
            QuestionBatch(
                "b2",
                "deepseek_web",
                "now",
                second,
                [make_question("Q001", second), make_question("Q002", second)],
                "r2",
            ),
        ]
        generator = DeepSeekWebGenerator(driver=object(), logger=lambda _message: None)
        generator.generate_questions = Mock(side_effect=batches)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "questions.json"
            combined = generator.generate_question_groups([first, second], output_path=output)
            loaded = load_question_batch(output)

        self.assertEqual([q.local_id for q in combined.questions], ["Q001", "Q002", "Q003"])
        self.assertEqual(loaded.expected_question_count(), 3)
        self.assertEqual([s.chapter for s in loaded.all_specs()], ["第一章", "第二章"])
        self.assertTrue(batch_is_fully_approved(loaded))

    def test_legacy_batch_uses_single_spec_count(self):
        spec = make_spec("第一章", "周期信号", "single_choice", 1)
        batch = QuestionBatch(
            "legacy", "deepseek_web", "now", spec, [make_question("Q001", spec)], ""
        )
        self.assertEqual(batch.expected_question_count(), 1)
        self.assertTrue(batch_is_fully_approved(batch))


if __name__ == "__main__":
    unittest.main()

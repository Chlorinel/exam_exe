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
    extract_question_identifier,
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
    identifier = f"[{10000 + int(local_id[1:]):05d}]"
    return GeneratedQuestion(
        local_id=local_id,
        question_type=spec.question_type,
        chapter=spec.chapter,
        knowledge_point=spec.knowledge_point,
        difficulty=spec.difficulty,
        stem=f"{spec.knowledge_point} 测试题 {local_id} {identifier}",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        answer="A",
        explanation="选择 A。",
        score=Decimal("5"),
        identifier=identifier,
        review_status="approved",
    )


class MultiQuestionGroupTests(unittest.TestCase):
    def test_new_chat_control_supports_current_tabindex_div(self):
        text_node = Mock()
        clickable_div = Mock()
        clickable_div.is_displayed.return_value = True
        driver = Mock()
        driver.find_elements.return_value = [text_node]
        driver.execute_script.return_value = clickable_div
        generator = DeepSeekWebGenerator(driver=driver, logger=lambda _message: None)

        clicked = generator._click_new_chat_control()

        self.assertTrue(clicked)
        clickable_div.click.assert_called_once_with()
        xpath = driver.find_elements.call_args.args[1]
        self.assertIn("normalize-space(.)", xpath)
        self.assertNotIn("self::button or self::a", xpath)

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
        identifiers = [
            extract_question_identifier(q.stem)
            for q in combined.questions
        ]
        self.assertTrue(all(identifiers))
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertEqual(loaded.expected_question_count(), 3)
        self.assertEqual([s.chapter for s in loaded.all_specs()], ["第一章", "第二章"])
        self.assertTrue(batch_is_fully_approved(loaded))
        self.assertEqual(generator.generate_questions.call_count, 2)

    def test_legacy_batch_uses_single_spec_count(self):
        spec = make_spec("第一章", "周期信号", "single_choice", 1)
        batch = QuestionBatch(
            "legacy", "deepseek_web", "now", spec, [make_question("Q001", spec)], ""
        )
        self.assertEqual(batch.expected_question_count(), 1)
        self.assertTrue(batch_is_fully_approved(batch))


if __name__ == "__main__":
    unittest.main()

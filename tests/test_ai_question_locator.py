from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from deepseek_question_generator import (
    GeneratedQuestion,
    QuestionBatch,
    QuestionSpec,
)
from platform_question_uploader import (
    UploadRecord,
    UploadState,
    question_fingerprint,
)
from ai_question_locator import (
    ensure_all_uploaded,
    locate_row_and_keyword,
    parse_chapter_number,
    plain_text_fragments,
)


def sample_question() -> GeneratedQuestion:
    return GeneratedQuestion(
        local_id="Q001",
        question_type="single_choice",
        chapter="第二章",
        knowledge_point="卷积积分",
        difficulty="medium",
        stem=(
            r"已知连续时间信号 \(x(t)\)，"
            r"求该信号与单位阶跃信号卷积后的结果。"
        ),
        options={
            "A": "1",
            "B": "2",
            "C": "3",
            "D": "4",
        },
        answer="A",
        explanation="测试解析。",
        score=Decimal("5"),
        review_status="approved",
    )


def sample_batch() -> QuestionBatch:
    question = sample_question()
    return QuestionBatch(
        batch_id="batch-1",
        provider="deepseek-web",
        created_at=datetime.now(
            timezone.utc
        ).isoformat(),
        spec=QuestionSpec(
            course_name="信号与系统",
            chapter="第二章",
            knowledge_point="卷积积分",
            count=1,
            score=Decimal("5"),
        ),
        questions=[question],
        raw_response="",
    )


class ChapterNumberTests(unittest.TestCase):
    def test_parse_chapter_number(self):
        self.assertEqual(
            parse_chapter_number("第二章"),
            2,
        )
        self.assertEqual(
            parse_chapter_number("第十二章"),
            12,
        )
        self.assertEqual(
            parse_chapter_number("第12章"),
            12,
        )
        self.assertEqual(
            parse_chapter_number("12"),
            12,
        )


class TextLocatorTests(unittest.TestCase):
    def test_formula_is_not_required_for_matching(self):
        fragments = plain_text_fragments(
            r"已知连续时间信号 \(x(t)\)，求系统输出。"
        )

        self.assertIn(
            "已知连续时间信号",
            fragments,
        )
        self.assertIn(
            "求系统输出",
            fragments,
        )

    def test_locates_unique_row_and_keyword(self):
        row_texts = [
            "已知离散时间信号 x[n]，求其周期。",
            (
                "已知连续时间信号 x(t)，"
                "求该信号与单位阶跃信号卷积后的结果。"
            ),
            "判断系统是否为线性系统。",
        ]

        index, keyword = locate_row_and_keyword(
            sample_question().stem,
            row_texts,
        )

        self.assertEqual(index, 1)
        self.assertIn(
            keyword,
            row_texts[1],
        )
        self.assertNotIn(
            keyword,
            row_texts[0],
        )
        self.assertNotIn(
            keyword,
            row_texts[2],
        )

    def test_rejects_question_without_enough_plain_text(self):
        with self.assertRaises(ValueError):
            locate_row_and_keyword(
                r"\[x(t)=e^{-t}u(t)\]",
                [
                    "x(t)=e^-t u(t)",
                    "另一道题",
                ],
            )

    def test_rejects_non_unique_text(self):
        with self.assertRaises(ValueError):
            locate_row_and_keyword(
                "已知连续时间信号并回答下列问题",
                [
                    "已知连续时间信号并回答下列问题 A",
                    "已知连续时间信号并回答下列问题 B",
                ],
            )


class UploadGateTests(unittest.TestCase):
    def test_accepts_matching_uploaded_state(self):
        batch = sample_batch()
        question = batch.questions[0]

        state = UploadState(
            batch_id=batch.batch_id,
            questions={
                question.local_id: UploadRecord(
                    fingerprint=question_fingerprint(
                        question
                    ),
                    status="uploaded",
                    platform_question_id="remote-1",
                )
            },
        )

        ensure_all_uploaded(
            batch,
            state,
        )

    def test_rejects_incomplete_upload_state(self):
        batch = sample_batch()
        question = batch.questions[0]

        state = UploadState(
            batch_id=batch.batch_id,
            questions={
                question.local_id: UploadRecord(
                    fingerprint=question_fingerprint(
                        question
                    ),
                    status="uncertain",
                )
            },
        )

        with self.assertRaises(RuntimeError):
            ensure_all_uploaded(
                batch,
                state,
            )


if __name__ == "__main__":
    unittest.main()

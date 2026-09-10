from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from ai_question_locator import LocatedQuestion
from config_writer import (
    ConfigWriteError,
    create_ai_exam_config,
    prepare_user_installation,
)
from create_signal_exam import load_config


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "考试配置表.xlsx"


def file_hash(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def unused_slot():
    config = load_config(
        TEMPLATE,
        require_questions=False,
    )
    occupied = {
        (q.chapter, q.number)
        for q in config.questions
    }

    chapter = 90

    while (chapter, 1) in occupied:
        chapter += 1

    return chapter, 1


class ConfigWriterTests(unittest.TestCase):
    def test_prepare_user_installation_copies_template_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "考试配置模板.xlsx"
            template.write_bytes(b"template")

            config = prepare_user_installation(root)
            self.assertEqual(config.read_bytes(), b"template")
            self.assertTrue((root / "work").is_dir())

            config.write_bytes(b"user-edited")
            prepare_user_installation(root)
            self.assertEqual(config.read_bytes(), b"user-edited")

    def setUp(self):
        if not TEMPLATE.exists():
            self.skipTest(
                "仓库根目录缺少 考试配置表.xlsx"
            )

    def test_appends_ai_question_and_preserves_source(self):
        before_config = load_config(
            TEMPLATE,
            require_questions=False,
        )
        before_hash = file_hash(
            TEMPLATE
        )

        chapter, number = unused_slot()

        located = LocatedQuestion(
            local_id="Q-WRITER-001",
            chapter=chapter,
            chapter_name=f"第{chapter}章",
            number=number,
            keyword=(
                "AI_CONFIG_WRITER_"
                "UNIQUE_KEYWORD"
            ),
            score=Decimal("5"),
        )

        with tempfile.TemporaryDirectory() as directory:
            output = (
                Path(directory)
                / "考试配置表_AI完成.xlsx"
            )

            result = create_ai_exam_config(
                TEMPLATE,
                output,
                [located],
            )

            self.assertEqual(
                result,
                output.resolve(),
            )
            self.assertTrue(
                output.exists()
            )

            after = load_config(
                output
            )

            self.assertEqual(
                tuple(
                    after.questions[
                        :len(
                            before_config.questions
                        )
                    ]
                ),
                tuple(
                    before_config.questions
                ),
            )

            self.assertEqual(
                len(after.questions),
                len(before_config.questions)
                + 1,
            )

            question = after.questions[-1]

            self.assertEqual(
                question.chapter,
                chapter,
            )
            self.assertEqual(
                question.chapter_name,
                f"第{chapter}章",
            )
            self.assertEqual(
                question.number,
                number,
            )
            self.assertEqual(
                question.keyword,
                (
                    "AI_CONFIG_WRITER_"
                    "UNIQUE_KEYWORD"
                ),
            )
            self.assertEqual(
                question.score,
                Decimal("5"),
            )

        self.assertEqual(
            file_hash(TEMPLATE),
            before_hash,
        )

    def test_rejects_overwriting_source(self):
        chapter, number = unused_slot()

        located = LocatedQuestion(
            local_id="Q-WRITER-002",
            chapter=chapter,
            chapter_name=f"第{chapter}章",
            number=number,
            keyword="不会真的写入",
            score=Decimal("5"),
        )

        with self.assertRaises(
            ConfigWriteError
        ):
            create_ai_exam_config(
                TEMPLATE,
                TEMPLATE,
                [located],
            )

    def test_rejects_score_with_more_than_one_decimal(self):
        chapter, number = unused_slot()

        located = LocatedQuestion(
            local_id="Q-WRITER-003",
            chapter=chapter,
            chapter_name=f"第{chapter}章",
            number=number,
            keyword="分值格式测试关键词",
            score=Decimal("5.25"),
        )

        with tempfile.TemporaryDirectory() as directory:
            output = (
                Path(directory)
                / "bad.xlsx"
            )

            with self.assertRaises(
                ConfigWriteError
            ):
                create_ai_exam_config(
                    TEMPLATE,
                    output,
                    [located],
                )

            self.assertFalse(
                output.exists()
            )

    def test_rejects_existing_question_slot(self):
        config = load_config(
            TEMPLATE,
            require_questions=False,
        )

        if not config.questions:
            self.skipTest(
                "模板目前没有已有选题，"
                "无法测试重复章内题号。"
            )

        existing = config.questions[0]

        located = LocatedQuestion(
            local_id="Q-WRITER-004",
            chapter=existing.chapter,
            chapter_name=(
                existing.chapter_name
            ),
            number=existing.number,
            keyword="重复位置测试关键词",
            score=Decimal("5"),
        )

        with tempfile.TemporaryDirectory() as directory:
            output = (
                Path(directory)
                / "duplicate.xlsx"
            )

            with self.assertRaises(
                ConfigWriteError
            ):
                create_ai_exam_config(
                    TEMPLATE,
                    output,
                    [located],
                )

            self.assertFalse(
                output.exists()
            )


if __name__ == "__main__":
    unittest.main()

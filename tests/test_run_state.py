from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from create_signal_exam import (
    ExamConfig,
    Question,
    load_state,
    resolve_run_state_path,
    state_fingerprint,
)
from datetime import datetime
from decimal import Decimal


def config(name: str, summary: str):
    return SimpleNamespace(
        exam_name=name,
        summary=lambda: summary,
    )


class RunStateTests(unittest.TestCase):
    def test_excel_config_gets_a_fingerprint_scoped_state_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = config('考试', 'settings')

            result = resolve_run_state_path(
                None,
                root / '考试配置表.xlsx',
                None,
                None,
                current,
            )

        self.assertEqual(
            result,
            root / 'work' / 'exam-run-states' / f'excel-{state_fingerprint(current)}.state.json',
        )

    def test_changed_excel_config_does_not_reuse_old_completed_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / '考试配置表.state.json'
            legacy.write_text(
                json.dumps(
                    {
                        'config_fingerprint': state_fingerprint(config('旧考试', 'old settings')),
                        'status': 'grades_published',
                        'exam_id': '123456',
                    }
                ),
                encoding='utf-8',
            )
            current = config('新考试', 'new settings')

            result = resolve_run_state_path(
                None,
                root / '考试配置表.xlsx',
                None,
                None,
                current,
            )

        self.assertFalse(result.exists())
        self.assertNotEqual(result, legacy)

    def test_matching_legacy_excel_state_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = config('考试', 'settings')
            legacy = root / '考试配置表.state.json'
            legacy.write_text(
                json.dumps(
                    {
                        'config_fingerprint': state_fingerprint(current),
                        'status': 'saved',
                        'exam_id': '123456',
                    }
                ),
                encoding='utf-8',
            )

            result = resolve_run_state_path(
                None,
                root / '考试配置表.xlsx',
                None,
                None,
                current,
            )
            migrated = json.loads(result.read_text(encoding='utf-8'))

        self.assertEqual(migrated['exam_id'], '123456')

    def test_legacy_question_number_is_not_part_of_fingerprint(self):
        shared = dict(
            exam_name='考试',
            course_name='课程',
            course_id='course',
            term_id='term',
            class_code='class',
            start=datetime(2026, 9, 12, 10, 0),
            end=datetime(2026, 9, 12, 11, 0),
            release_at=None,
            release_method='人工发布',
        )
        first = ExamConfig(
            **shared,
            questions=(Question(1, '第一章', 1, '[12345]', Decimal('10')),),
        )
        second = ExamConfig(
            **shared,
            questions=(Question(1, '第一章', 999, '[12345]', Decimal('10')),),
        )

        self.assertEqual(state_fingerprint(first), state_fingerprint(second))

    def test_changed_config_resets_unused_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'exam.state.json'
            path.write_text(
                json.dumps(
                    {
                        'version': 1,
                        'config_fingerprint': 'old',
                        'exam_name': '旧考试',
                        'status': 'new',
                        'last_error': 'old failure',
                    }
                ),
                encoding='utf-8',
            )
            current = config('新考试', 'new settings')

            state = load_state(path, current)

        self.assertEqual(state['status'], 'new')
        self.assertEqual(state['exam_name'], '新考试')
        self.assertEqual(state['config_fingerprint'], state_fingerprint(current))
        self.assertNotIn('last_error', state)

    def test_changed_config_resets_state_even_if_it_has_platform_exam(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'exam.state.json'
            path.write_text(
                json.dumps(
                    {
                        'config_fingerprint': 'old',
                        'status': 'new',
                        'exam_id': '123456',
                    }
                ),
                encoding='utf-8',
            )

            state = load_state(path, config('新考试', 'new settings'))

        self.assertEqual(state['status'], 'new')
        self.assertNotIn('exam_id', state)
        self.assertEqual(state['exam_name'], '新考试')

    def test_each_session_gets_its_own_state_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = resolve_run_state_path(
                None,
                root / '考试配置表.xlsx',
                root / 'work' / 'current_exam_session.json',
                SimpleNamespace(session_id='20260912_131439'),
                config('考试', 'settings'),
            )

        self.assertEqual(
            result,
            root / 'work' / 'exam-run-states' / '20260912_131439.state.json',
        )

    def test_matching_legacy_state_is_migrated_for_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = config('考试', 'settings')
            legacy = root / '考试配置表.state.json'
            legacy.write_text(
                json.dumps(
                    {
                        'config_fingerprint': state_fingerprint(current),
                        'exam_name': '考试',
                        'status': 'saved',
                        'exam_id': '123456',
                    }
                ),
                encoding='utf-8',
            )

            result = resolve_run_state_path(
                None,
                root / '考试配置表.xlsx',
                root / 'work' / 'current_exam_session.json',
                SimpleNamespace(session_id='20260912_131439'),
                current,
            )

            migrated = json.loads(result.read_text(encoding='utf-8'))

        self.assertEqual(migrated['exam_id'], '123456')
        self.assertEqual(migrated['status'], 'saved')

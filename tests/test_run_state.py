from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from create_signal_exam import (
    load_state,
    resolve_run_state_path,
    state_fingerprint,
)


def config(name: str, summary: str):
    return SimpleNamespace(
        exam_name=name,
        summary=lambda: summary,
    )


class RunStateTests(unittest.TestCase):
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

    def test_changed_config_keeps_state_that_has_platform_exam(self):
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

            with self.assertRaisesRegex(RuntimeError, '配置表已改变'):
                load_state(path, config('新考试', 'new settings'))

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

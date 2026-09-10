from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import create_signal_exam as exam


def sample_args(*, publish_exam=True, headless=False):
    return SimpleNamespace(
        publish_exam=publish_exam,
        headless=headless,
    )


def sample_config():
    return SimpleNamespace(
        exam_name="第一次随堂测试",
        class_code="202613475",
        start=datetime(2026, 9, 10, 9, 0),
        end=datetime(2026, 9, 10, 10, 0),
        questions=[1, 2],
    )


class GUIPublishGateTests(unittest.TestCase):
    def test_gui_confirmer_can_approve(self):
        config = sample_config()
        confirmer = Mock(return_value=True)

        result = exam.confirm_exam_publish(
            sample_args(),
            config,
            confirmer=confirmer,
        )

        self.assertTrue(result)
        confirmer.assert_called_once_with(config)

    def test_gui_cancel_keeps_draft(self):
        config = sample_config()
        confirmer = Mock(return_value=False)

        result = exam.confirm_exam_publish(
            sample_args(),
            config,
            confirmer=confirmer,
        )

        self.assertFalse(result)

    def test_gui_confirmation_is_the_gate_not_publish_flag(self):
        config = sample_config()
        confirmer = Mock(return_value=True)

        result = exam.confirm_exam_publish(
            sample_args(publish_exam=False),
            config,
            confirmer=confirmer,
        )

        self.assertTrue(result)
        confirmer.assert_called_once_with(config)

    def test_headless_without_gui_callback_is_rejected(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "无法进行终端确认",
        ):
            exam.confirm_exam_publish(
                sample_args(headless=True),
                sample_config(),
            )

    @patch("builtins.input", return_value="YES")
    @patch.object(exam.sys.stdin, "isatty", return_value=True)
    def test_cli_backup_requires_exact_yes(
        self,
        _isatty,
        _input,
    ):
        self.assertTrue(
            exam.confirm_exam_publish(
                sample_args(publish_exam=False),
                sample_config(),
            )
        )


if __name__ == "__main__":
    unittest.main()

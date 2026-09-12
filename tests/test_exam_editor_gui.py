from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from exam_editor_gui import (
    ExamEditorWindow,
    build_backend_args,
    build_run_exam_args,
    session_action_permissions,
)


class FrontendArgsTests(unittest.TestCase):
    def test_explicit_session_path_does_not_depend_on_config_name(self):
        with TemporaryDirectory() as directory:
            selected = Path(directory) / "previous_exam_session.json"
            selected.write_text("{}", encoding="utf-8")
            window = SimpleNamespace(session_edit=Mock())
            window.session_edit.text.return_value = str(selected)
            with patch("exam_editor_gui.load_session") as loader:
                actual = ExamEditorWindow._active_session_path(window)

        self.assertEqual(actual, selected.resolve())
        loader.assert_called_once_with(selected.resolve())

    def test_default_create_action_uses_run_mode(self):
        args = build_run_exam_args(
            Path("config.xlsx"),
            Path("profile"),
            session_path=Path("work/current_exam_session.json"),
            headless=True,
        )
        self.assertTrue(args.run)
        self.assertTrue(args.publish_exam)
        self.assertFalse(args.prepare)
        self.assertEqual(args.session, Path("work/current_exam_session.json"))
        self.assertTrue(args.headless)

    def test_session_status_controls_create_and_publish_actions(self):
        session = type("Session", (), {"status": "uploaded"})()
        self.assertEqual(session_action_permissions(session), (True, False))
        session.status = "exam_created"
        self.assertEqual(session_action_permissions(session), (False, True))
        session.status = "waiting_publish_confirm"
        self.assertEqual(session_action_permissions(session), (False, True))
        session.status = "published"
        self.assertEqual(session_action_permissions(session), (False, False))

    def test_prepare_button_never_sets_publish(self):
        args = build_backend_args(
            Path("config.xlsx"),
            Path("profile"),
            prepare=True,
        )
        self.assertTrue(args.prepare)
        self.assertFalse(args.run)
        self.assertFalse(args.publish_exam)

    def test_publish_button_enters_run_and_publish_mode(self):
        args = build_backend_args(
            Path("config.xlsx"),
            Path("profile"),
            run=True,
            publish_exam=True,
        )
        self.assertFalse(args.prepare)
        self.assertTrue(args.run)
        self.assertTrue(args.publish_exam)


if __name__ == "__main__":
    unittest.main()

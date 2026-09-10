from __future__ import annotations

import unittest
from pathlib import Path

from exam_control_gui import build_backend_args, session_action_permissions


class FrontendArgsTests(unittest.TestCase):
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

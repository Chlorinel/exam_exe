import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scheduler import windows_task

TZ = timezone(timedelta(hours=8))
RELEASE_AT = datetime(2026, 11, 5, 12, 1, tzinfo=TZ)
EXAM_ID = "2117840458910633986"
TASK_NAME = windows_task.TASK_PREFIX + EXAM_ID
SANDBOX_ERROR = "Windows 计划任务操作失败（1）：错误: 系统找不到指定的路径。"


def failed_completed_process(message=SANDBOX_ERROR):
    return subprocess.CompletedProcess(
        args=["schtasks.exe"],
        returncode=1,
        stdout=b"",
        stderr=message.encode("mbcs"),
    )


class SchedulerTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.state_path = self.work / "exam-run-states" / f"{EXAM_ID}.state.json"
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps({"version": 1, "exam_id": EXAM_ID, "status": "waiting"}),
            encoding="utf-8",
        )
        self.config_path = self.work / "config.xlsx"
        self.config_path.write_bytes(b"placeholder")
        self.profile_dir = self.work / "profile"

    def create(self):
        return windows_task.create_grade_release_task(
            self.config_path,
            self.state_path,
            RELEASE_AT,
            profile_dir=self.profile_dir,
            session_path=None,
        )

    def test_sandbox_failure_generates_helper_and_marker_candidate(self):
        with patch.object(
            windows_task,
            "_run_schtasks",
            side_effect=RuntimeError(SANDBOX_ERROR),
        ):
            with self.assertRaises(windows_task.TaskSchedulerUnavailable) as raised:
                self.create()

        self.assertTrue(self.state_path.parent.parent.joinpath("grade-release-task.xml").is_file())
        helper = windows_task.helper_script_path(self.state_path)
        self.assertTrue(helper.is_file())
        self.assertEqual(raised.exception.helper_path, helper)
        self.assertIn(str(helper), str(raised.exception))

        pending = json.loads(
            windows_task.pending_marker_path(self.state_path).read_text(encoding="utf-8")
        )
        self.assertEqual(pending["task_name"], TASK_NAME)
        self.assertEqual(pending["release_at"], RELEASE_AT.isoformat())

    def test_helper_script_has_windows_line_endings(self):
        with patch.object(
            windows_task,
            "_run_schtasks",
            side_effect=RuntimeError(SANDBOX_ERROR),
        ):
            with self.assertRaises(windows_task.TaskSchedulerUnavailable):
                self.create()
        content = windows_task.helper_script_path(self.state_path).read_bytes()
        self.assertTrue(content.startswith(b"@echo off\r\n"))
        self.assertNotIn(b"\r\r\n", content)

    def test_recorded_registration_skips_schtasks(self):
        marker = windows_task.registration_marker_path(self.state_path)
        marker.write_text(
            json.dumps(
                {"task_name": TASK_NAME, "release_at": RELEASE_AT.isoformat()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with patch.object(windows_task, "_run_schtasks") as run:
            created = self.create()
        self.assertFalse(created)
        run.assert_not_called()

    def test_successful_creation_returns_true(self):
        completed = subprocess.CompletedProcess(
            args=["schtasks.exe"], returncode=0, stdout=b"SUCCESS", stderr=b""
        )
        with patch.object(windows_task, "_run_schtasks", return_value=completed):
            created = self.create()
        self.assertTrue(created)
        self.assertTrue(windows_task.task_xml_path(self.state_path).is_file())

    def test_non_sandbox_failure_is_not_swallowed(self):
        with patch.object(
            windows_task,
            "_run_schtasks",
            side_effect=RuntimeError("Windows 计划任务操作失败（1）：访问被拒绝。"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                self.create()
        self.assertNotIsInstance(raised.exception, windows_task.TaskSchedulerUnavailable)

    def test_delete_under_sandbox_writes_delete_helper(self):
        with patch.object(
            windows_task,
            "_run_schtasks",
            return_value=failed_completed_process(),
        ):
            removed = windows_task.delete_grade_release_task(
                None,
                {"exam_id": EXAM_ID},
                state_path=self.state_path,
            )
        self.assertFalse(removed)
        self.assertTrue(windows_task.delete_helper_script_path(self.state_path).is_file())

    def test_delete_success(self):
        completed = subprocess.CompletedProcess(
            args=["schtasks.exe"], returncode=0, stdout=b"SUCCESS", stderr=b""
        )
        with patch.object(windows_task, "_run_schtasks", return_value=completed):
            removed = windows_task.delete_grade_release_task(
                None,
                {"exam_id": EXAM_ID},
                state_path=self.state_path,
            )
        self.assertTrue(removed)

    def test_delete_missing_task_counts_as_removed(self):
        completed = failed_completed_process("ERROR: 系统找不到指定的任务。")
        with patch.object(windows_task, "_run_schtasks", return_value=completed):
            removed = windows_task.delete_grade_release_task(
                None,
                {"exam_id": EXAM_ID},
                state_path=self.state_path,
            )
        self.assertTrue(removed)


if __name__ == "__main__":
    unittest.main()

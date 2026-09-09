from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import create_signal_exam
from scheduler import windows_task


BEIJING = timezone(timedelta(hours=8))


class WindowsTaskTests(unittest.TestCase):
    def state_file(self, directory: str, *, scheduled=None) -> Path:
        path = Path(directory) / "exam.state.json"
        state = {"exam_id": "2097577330862120962"}
        if scheduled is not None:
            state["scheduled_grade_release"] = scheduled
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    def test_task_name_uses_exam_id(self):
        self.assertEqual(
            windows_task.task_name(None, {"exam_id": "123"}),
            "TeachingAutomation_GradeRelease_123",
        )

    def test_task_xml_contains_absolute_command_and_arguments(self):
        release_at = datetime(2026, 9, 10, 12, 1, tzinfo=BEIJING)
        command, arguments, working_directory = windows_task._execution_parts(
            Path("C:/absolute/config.xlsx"),
            Path("C:/absolute/config.state.json"),
            Path("C:/absolute/edge-profile"),
        )
        root = ET.fromstring(windows_task._task_xml(command, arguments, working_directory, release_at))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertTrue(Path(root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns)).is_absolute())
        actual_arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
        self.assertIn("--release-grades", actual_arguments)
        self.assertIn("--headless", actual_arguments)
        self.assertIn(str(Path("C:/absolute/config.xlsx").resolve()), actual_arguments)
        self.assertEqual(root.findtext("t:Triggers/t:TimeTrigger/t:StartBoundary", namespaces=ns), "2026-09-10T12:01:00")

    def test_same_recorded_task_and_time_is_noop(self):
        release_at = datetime(2026, 9, 10, 12, 1, tzinfo=BEIJING)
        name = "TeachingAutomation_GradeRelease_2097577330862120962"
        with tempfile.TemporaryDirectory() as directory:
            state_path = self.state_file(
                directory,
                scheduled={"task_name": name, "release_at": release_at.isoformat()},
            )
            with patch.object(windows_task, "task_exists", return_value=True), patch.object(
                windows_task, "_run_schtasks"
            ) as run:
                windows_task.create_grade_release_task(
                    Path(directory) / "config.xlsx", state_path, release_at
                )
            run.assert_not_called()

    def test_changed_time_replaces_task_with_xml(self):
        release_at = datetime(2026, 9, 10, 12, 2, tzinfo=BEIJING)
        name = "TeachingAutomation_GradeRelease_2097577330862120962"
        captured = {}

        def capture(arguments, *, check=True):
            captured["arguments"] = arguments
            xml_path = Path(arguments[arguments.index("/XML") + 1])
            captured["xml"] = xml_path.read_bytes()
            return type("Result", (), {"returncode": 0})()

        with tempfile.TemporaryDirectory() as directory:
            state_path = self.state_file(
                directory,
                scheduled={"task_name": name, "release_at": "2026-09-10T12:01:00+08:00"},
            )
            with patch.object(windows_task, "task_exists", return_value=True), patch.object(
                windows_task, "_run_schtasks", side_effect=capture
            ):
                windows_task.create_grade_release_task(
                    Path(directory) / "config.xlsx", state_path, release_at
                )
        self.assertEqual(captured["arguments"][0:4], ["/Create", "/TN", name, "/XML"])
        self.assertIn(b"\xff\xfe", captured["xml"][:4])

    def test_release_entry_does_not_open_browser_before_release_time(self):
        config = type(
            "Config",
            (),
            {
                "release_method": "定时脚本发布",
                "release_at": datetime.now(BEIJING) + timedelta(hours=1),
            },
        )()
        state = {"exam_id": "123", "status": "waiting"}
        with patch.object(create_signal_exam, "launch_for_config") as launch:
            with self.assertRaisesRegex(RuntimeError, "尚未到计划成绩发布时间"):
                create_signal_exam.release_grades_once(
                    type("Args", (), {"headless": True})(),
                    config,
                    state,
                    Path("unused.state.json"),
                )
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

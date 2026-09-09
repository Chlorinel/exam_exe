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

    def test_run_publish_confirmation_requires_exact_yes(self):
        args = type("Args", (), {"publish_exam": False, "headless": False})()
        config = type(
            "Config",
            (),
            {
                "exam_name": "测试考试",
                "class_code": "1001",
                "start": datetime(2026, 9, 10, 10, 0, tzinfo=BEIJING),
                "end": datetime(2026, 9, 10, 11, 0, tzinfo=BEIJING),
                "questions": (1, 2),
            },
        )()
        with patch.object(create_signal_exam.sys.stdin, "isatty", return_value=True), patch(
            "builtins.input", return_value="YES"
        ):
            self.assertTrue(create_signal_exam.confirm_exam_publish(args, config))
        with patch.object(create_signal_exam.sys.stdin, "isatty", return_value=True), patch(
            "builtins.input", return_value="yes"
        ):
            self.assertFalse(create_signal_exam.confirm_exam_publish(args, config))

    def test_headless_run_cannot_wait_for_publish_confirmation(self):
        args = type("Args", (), {"publish_exam": False, "headless": True})()
        with self.assertRaisesRegex(RuntimeError, "无法进行终端确认"):
            create_signal_exam.confirm_exam_publish(args, object())

    def test_activity_page_waits_for_login_then_continues(self):
        target = create_signal_exam.activity_url("course1")

        class Driver:
            current_url = "https://login.example.test/sso"

            def __init__(self):
                self.visits = []

            def get(self, url):
                self.visits.append(url)
                if len(self.visits) == 1:
                    self.current_url = "https://login.example.test/sso"
                else:
                    self.current_url = target

        driver = Driver()

        def finish_login(current, timeout=300):
            current.current_url = "https://aic.sysu.edu.cn/aic/home"

        with patch.object(create_signal_exam, "wait_until_ready"), patch.object(
            create_signal_exam, "is_login_page", side_effect=lambda current: "login.example" in current.current_url
        ), patch.object(
            create_signal_exam, "wait_for_login_if_needed", side_effect=finish_login
        ) as wait_login, patch.object(
            create_signal_exam, "body_text", return_value="创建活动"
        ), patch.object(create_signal_exam, "visible", return_value=[]):
            create_signal_exam.open_activity_page(driver, "course1", timeout=5)

        wait_login.assert_called_once()
        self.assertEqual(driver.visits, [target, target])

    def test_question_exam_mode_is_clicked_even_with_primary_style(self):
        mode = type("Element", (), {"get_attribute": lambda self, name: "pl-button--primary"})()
        custom = type("Element", (), {"get_attribute": lambda self, name: "pl-button"})()
        configure = object()

        def elements(driver, label, selector):
            if label == "选题考试":
                return [mode]
            if label == "自定义考试":
                return [custom]
            if label == "配置试题":
                return [configure]
            return []

        with patch.object(create_signal_exam, "exact_text_elements", side_effect=elements), patch.object(
            create_signal_exam, "click_safely"
        ) as click:
            selected = create_signal_exam.activate_question_exam_mode(object())

        self.assertIs(selected, configure)
        click.assert_called_once_with(unittest.mock.ANY, mode)

    def test_duplicate_course_labels_use_url_course_identity(self):
        first = type("Element", (), {"text": "信号与系统"})()
        second = type("Element", (), {"text": "信号与系统 (5)"})()
        driver = type("Driver", (), {"current_url": "https://example.test/course-id/questions"})()
        config = type("Config", (), {"course_name": "信号与系统", "course_id": "course-id"})()
        with patch.object(create_signal_exam, "visible", return_value=[first, second]):
            self.assertIs(
                create_signal_exam.course_path_element(driver, config, ".title"),
                second,
            )


if __name__ == "__main__":
    unittest.main()

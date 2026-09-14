from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import create_signal_exam
from scheduler import windows_task


BEIJING = timezone(timedelta(hours=8))


class WindowsTaskTests(unittest.TestCase):
    def test_manual_terminal_command_waits_before_release_entry(self):
        args = SimpleNamespace(
            config=Path("config.xlsx"),
            profile_dir=Path("profile"),
            state=Path("exam.state.json"),
            session=Path("work/current_exam_session.json"),
            headless=True,
        )
        command = create_signal_exam.manual_release_command(
            args,
            "network unavailable",
        )

        self.assertIn("--manual-release-prompt", command)
        self.assertNotIn("--release-grades", command)
        self.assertNotIn("--headless", command)
        self.assertIn(str(Path("exam.state.json").resolve()), command)
        self.assertIn(
            str(Path("work/current_exam_session.json").resolve()),
            command,
        )

    def test_scheduled_release_failure_opens_recovery_choice_dialog(self):
        args = SimpleNamespace(
            release_grades=True,
            manual_release_prompt=False,
        )
        failure = RuntimeError("network unavailable")
        with patch.object(
            create_signal_exam,
            "parse_args",
            return_value=args,
        ), patch.object(
            create_signal_exam,
            "run_configuration",
            side_effect=failure,
        ), patch.object(
            create_signal_exam,
            "show_release_failure_dialog",
            return_value="retry",
        ) as dialog:
            result = create_signal_exam.main()

        self.assertEqual(result, 1)
        dialog.assert_called_once_with(args, "RuntimeError: network unavailable")

    def test_headless_edge_crash_retries_with_offscreen_window(self):
        driver = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            create_signal_exam.webdriver,
            "Edge",
            side_effect=[
                create_signal_exam.WebDriverException(
                    "session not created: DevToolsActivePort file doesn't exist"
                ),
                driver,
            ],
        ) as edge, patch.object(create_signal_exam, "install_guidance_hook"):
            result = create_signal_exam.launch_driver(Path(directory), True)

        self.assertIs(result, driver)
        first = edge.call_args_list[0].kwargs["options"].arguments
        second = edge.call_args_list[1].kwargs["options"].arguments
        self.assertIn("--headless=new", first)
        self.assertIn("--window-position=-32000,-32000", second)

    def test_scheduled_release_defaults_to_state_side_log(self):
        args = SimpleNamespace(
            release_grades=True,
            state=Path("work/exam.state.json"),
            log_file=None,
        )

        self.assertEqual(
            create_signal_exam.release_log_path(args),
            Path("work/exam.state.release.log").resolve(),
        )

    def test_manual_terminal_requires_yes_before_running_release(self):
        args = SimpleNamespace(
            failure_message="network unavailable",
            manual_release_prompt=True,
            release_grades=False,
        )
        with patch(
            "builtins.input",
            side_effect=["yes", ""],
        ), patch.object(
            create_signal_exam,
            "run_configuration",
            return_value=0,
        ) as run:
            result = create_signal_exam.run_manual_release_prompt(args)

        self.assertEqual(result, 0)
        self.assertTrue(args.release_grades)
        run.assert_called_once_with(args)

    def test_manual_terminal_cancels_without_exact_yes(self):
        args = SimpleNamespace(
            failure_message="network unavailable",
            manual_release_prompt=True,
            release_grades=False,
        )
        with patch("builtins.input", return_value="no"), patch.object(
            create_signal_exam,
            "run_configuration",
        ) as run:
            result = create_signal_exam.run_manual_release_prompt(args)

        self.assertEqual(result, 1)
        run.assert_not_called()

    def test_schtasks_timeout_fails_instead_of_hanging(self):
        expired = subprocess.TimeoutExpired("schtasks.exe", 30)
        with patch.object(
            windows_task.subprocess,
            "run",
            side_effect=expired,
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "等待超过 30 秒"):
                windows_task._run_schtasks(["/Query", "/TN", "test"])

        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_publication_paging_stops_if_previous_page_does_not_change(self):
        driver = Mock()
        row = Mock(text="学生甲 已发布")
        active = Mock(text="2")
        previous = Mock()
        previous.is_enabled.return_value = True
        previous.get_attribute.side_effect = (
            lambda name: "" if name == "class" else None
        )

        def fake_visible(current, selector):
            if selector == ".el-table__body-wrapper tbody tr":
                return [row]
            if selector == ".el-pager li.active":
                return [active]
            if selector == "button.btn-prev":
                return [previous]
            return []

        initial_wait = Mock()
        initial_wait.until.return_value = True
        stuck_wait = Mock()
        stuck_wait.until.side_effect = create_signal_exam.TimeoutException()
        with patch.object(
            create_signal_exam,
            "visible",
            side_effect=fake_visible,
        ), patch.object(
            create_signal_exam,
            "WebDriverWait",
            side_effect=[initial_wait, stuck_wait],
        ):
            with self.assertRaisesRegex(RuntimeError, "未能返回上一页"):
                create_signal_exam.publication_counts(driver)

        previous.click.assert_called_once_with()

    def test_headless_platform_login_reopens_visible_then_returns_to_background(self):
        background = Mock()
        visible = Mock()
        resumed = Mock()
        args = SimpleNamespace(
            config=Path("config.xlsx"),
            profile_dir=Path("profile"),
            headless=True,
        )
        with patch.object(
            create_signal_exam,
            "launch_driver",
            side_effect=[background, visible, resumed],
        ) as launch, patch.object(
            create_signal_exam,
            "load_config",
            return_value=SimpleNamespace(course_id="course-1"),
        ), patch.object(
            create_signal_exam,
            "wait_until_ready",
        ), patch.object(
            create_signal_exam,
            "is_login_page",
            return_value=True,
        ), patch.object(
            create_signal_exam,
            "open_activity_page",
        ) as login:
            result = create_signal_exam.launch_for_config(args)

        self.assertIs(result, resumed)
        self.assertEqual(
            [call.args[1] for call in launch.call_args_list],
            [True, False, True],
        )
        login.assert_called_once_with(visible, "course-1")
        background.quit.assert_called_once_with()
        visible.quit.assert_called_once_with()
        resumed.get.assert_called_once_with(
            create_signal_exam.activity_url("course-1")
        )

    def test_headless_publish_opens_visible_exam_page_for_review(self):
        background = Mock()
        visible = Mock(current_url="https://example.test/teach-exam/create/course/123")
        args = SimpleNamespace(headless=True, profile_dir=Path("profile"))
        config = SimpleNamespace()
        state = {"exam_id": "123"}
        with patch.object(
            create_signal_exam,
            "launch_driver",
            return_value=visible,
        ) as launch, patch.object(
            create_signal_exam,
            "open_existing",
        ) as opened, patch.object(
            create_signal_exam,
            "verify_create_form",
        ) as verified:
            result = create_signal_exam.open_visible_publish_review(
                background,
                args,
                config,
                state,
            )

        self.assertIs(result, visible)
        background.quit.assert_called_once_with()
        launch.assert_called_once_with(Path("profile"), False)
        opened.assert_called_once_with(visible, config, "123")
        verified.assert_called_once_with(visible, config)

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
            Path("C:/absolute/current_exam_session.json"),
        )
        root = ET.fromstring(windows_task._task_xml(command, arguments, working_directory, release_at))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertTrue(Path(root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns)).is_absolute())
        actual_arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
        self.assertIn("--release-grades", actual_arguments)
        self.assertIn("--headless", actual_arguments)
        self.assertIn(str(Path("C:/absolute/config.xlsx").resolve()), actual_arguments)
        self.assertIn("--session", actual_arguments)
        self.assertIn(str(Path("C:/absolute/current_exam_session.json").resolve()), actual_arguments)
        self.assertEqual(root.findtext("t:Triggers/t:TimeTrigger/t:StartBoundary", namespaces=ns), "2026-09-10T12:01:00")
    def test_session_is_frozen_next_to_run_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "current_exam_session.json"
            source.write_text('{"session_id":"one"}', encoding="utf-8")
            state_path = root / "exam-run-states" / "one.state.json"

            frozen = windows_task._freeze_session(source, state_path)
            source.write_text('{"session_id":"two"}', encoding="utf-8")

            self.assertEqual(frozen, state_path.with_suffix(".session.json"))
            self.assertEqual(
                frozen.read_text(encoding="utf-8"),
                '{"session_id":"one"}',
            )

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

    def test_scheduler_rejects_unpublished_exam_state(self):
        config = type(
            "Config",
            (),
            {
                "release_method": "定时脚本发布",
                "release_at": datetime.now(BEIJING) + timedelta(hours=1),
            },
        )()
        state = {"exam_id": "123", "status": "waiting"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exam_state = root / "exam_state.json"
            exam_state.write_text(
                json.dumps({"exam_id": "123", "status": "exam_created"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "不是 published"):
                create_signal_exam.ensure_grade_release_task(
                    config,
                    state,
                    root / "config.xlsx",
                    root / "config.state.json",
                    root / "profile",
                    exam_state_path=exam_state,
                )

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

    def test_scheduled_release_publishes_grades_and_answers_without_prompt(self):
        args = type("Args", (), {"headless": True})()
        config = type(
            "Config",
            (),
            {
                "release_method": "定时脚本发布",
                "release_at": datetime.now(BEIJING) - timedelta(minutes=1),
            },
        )()
        state = {"exam_id": "123", "status": "waiting"}
        driver = Mock()
        with patch.object(create_signal_exam, "launch_for_config", return_value=driver), patch.object(
            create_signal_exam,
            "check_or_release",
            return_value=(True, "成绩已发布"),
        ) as release, patch.object(
            create_signal_exam,
            "publish_exam_answers",
            return_value="答案已公布",
        ) as answers, patch.object(
            create_signal_exam,
            "delete_scheduled_grade_release",
        ):
            result = create_signal_exam.release_grades_once(
                args,
                config,
                state,
                Path("unused.state.json"),
            )
        self.assertEqual(result, 0)
        self.assertTrue(release.call_args.kwargs["commit"])
        self.assertNotIn("before_release", release.call_args.kwargs)
        answers.assert_called_once()
        driver.quit.assert_called_once()

    def test_retry_reclicks_when_previous_release_still_shows_unpublished(self):
        driver = Mock(current_url="https://example.test/examList/course/123")
        config = Mock()
        state = {"exam_id": "123", "status": "releasing_grades"}
        release_button = Mock()
        wait = Mock()
        wait.until.side_effect = lambda condition: condition(driver)
        with patch.object(create_signal_exam, "open_existing"), patch.object(
            create_signal_exam,
            "grade_snapshot",
            return_value={"actual_end": datetime.now(BEIJING) - timedelta(minutes=1)},
        ), patch.object(
            create_signal_exam,
            "release_gate",
            return_value=(True, "ready"),
        ), patch.object(
            create_signal_exam,
            "publication_counts",
            side_effect=[
                {"total": 1, "published": 0, "unpublished": 1},
                {"total": 1, "published": 1, "unpublished": 0},
            ],
        ), patch.object(
            create_signal_exam,
            "exact_button",
            return_value=release_button,
        ), patch.object(
            create_signal_exam,
            "confirm_known_dialog",
            return_value=True,
        ), patch.object(
            create_signal_exam,
            "WebDriverWait",
            return_value=wait,
        ), patch.object(create_signal_exam, "responsive_sleep"), patch.object(
            create_signal_exam,
            "save_state",
        ):
            done, message = create_signal_exam.check_or_release(
                driver,
                config,
                state,
                Path("unused.state.json"),
                commit=True,
            )

        self.assertTrue(done)
        self.assertIn("成功", message)
        self.assertEqual(state["status"], "grades_published")
        release_button.click.assert_called_once_with()
        driver.refresh.assert_called_once_with()

    def test_scheduled_release_failure_is_written_to_state(self):
        args = type("Args", (), {"headless": True})()
        config = type(
            "Config",
            (),
            {
                "release_method": "定时脚本发布",
                "release_at": datetime.now(BEIJING) - timedelta(minutes=1),
            },
        )()
        state = {"exam_id": "123", "status": "waiting"}
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "exam.state.json"
            with patch.object(
                create_signal_exam,
                "launch_for_config",
                side_effect=create_signal_exam.WebDriverException("Edge crashed"),
            ):
                with self.assertRaisesRegex(create_signal_exam.WebDriverException, "Edge crashed"):
                    create_signal_exam.release_grades_once(
                        args,
                        config,
                        state,
                        state_path,
                    )
            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertIn("Edge crashed", saved["last_error"])
        self.assertIn("WebDriverException", saved["error_trace"])
        self.assertIn("last_release_attempt_at", saved)

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
            # Even when SSO returns to the intended URL, the implementation
            # must reopen it instead of reusing the login redirect DOM.
            current.current_url = target

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

    def test_recorded_exam_is_reopened_after_login(self):
        config = SimpleNamespace(course_id="course1", term_id="term1")
        exam_id = "123"
        target = (
            f"{create_signal_exam.BASE_URL}/aic/exam-hub/teach-exam/"
            f"create/{config.course_id}/{exam_id}/{config.term_id}"
            "?from=agentCourse"
        )
        driver = Mock()
        driver.current_url = target

        with patch.object(
            create_signal_exam,
            "wait_for_login_if_needed",
            return_value=True,
        ), patch.object(
            create_signal_exam,
            "wait_until_ready",
        ) as ready, patch.object(
            create_signal_exam,
            "wait_for",
        ):
            create_signal_exam.open_recorded_draft(
                driver,
                config,
                exam_id,
            )

        self.assertEqual(
            driver.get.call_args_list,
            [unittest.mock.call(target), unittest.mock.call(target)],
        )
        ready.assert_called_once_with(driver, 40)

    def test_question_exam_mode_is_clicked_even_with_primary_style(self):
        mode = type("Element", (), {"get_attribute": lambda self, name: "pl-button--primary"})()
        custom = type("Element", (), {"get_attribute": lambda self, name: "pl-button"})()
        configure = object()
        driver = Mock()

        def elements(driver, label, selector):
            if label == "选题考试":
                return [mode]
            if label == "自定义考试":
                return [custom]
            if label == "配置试题":
                return [configure]
            return []

        with patch.object(
            create_signal_exam,
            "exact_text_elements",
            side_effect=elements,
        ):
            selected = create_signal_exam.activate_question_exam_mode(driver)

        self.assertIs(selected, configure)
        driver.execute_script.assert_called_once_with(
            "arguments[0].click();",
            mode,
        )

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

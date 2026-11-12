import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import venv

import gui_launcher


class GuiLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary) / "中文 路径 (&!)"
        self.root.mkdir()
        self.enterContext(patch.object(gui_launcher, "__file__", str(self.root / "gui_launcher.py")))

    def test_running_in_project_environment_imports_gui_directly(self):
        main = Mock(return_value=0)
        with (
            patch.object(gui_launcher.sys, "prefix", str(self.root / ".venv")),
            patch.object(gui_launcher.importlib, "import_module", return_value=SimpleNamespace(main=main)),
            patch.object(gui_launcher.subprocess, "run") as run,
        ):
            self.assertEqual(gui_launcher.launch_gui("question_workflow_gui"), 0)
            main.assert_called_once_with()
            run.assert_not_called()

    def test_system_python_relaunches_with_project_interpreter(self):
        executable = self.root / ".venv/Scripts/python.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        with (
            patch.object(gui_launcher.sys, "prefix", str(self.root / "system-python")),
            patch.object(gui_launcher.sys, "stdout", io.StringIO()),
            patch.object(gui_launcher.sys, "argv", ["question_workflow_gui.pyw", "argument with spaces"]),
            patch.object(gui_launcher.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
        ):
            self.assertEqual(gui_launcher.launch_gui("question_workflow_gui"), 0)
            self.assertEqual(run.call_args.args[0], [str(executable), "-E", "-X", "utf8", str(self.root / "question_workflow_gui.pyw"), "argument with spaces"])
            self.assertEqual(run.call_args.kwargs["cwd"], self.root)

    def test_double_click_uses_hidden_python_and_keeps_errors_visible(self):
        executable = self.root / ".venv/Scripts/python.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        message_box = Mock()
        with (
            patch.object(gui_launcher.sys, "prefix", str(self.root / "system-python")),
            patch.object(gui_launcher.sys, "stdout", None),
            patch.object(gui_launcher.sys, "stderr", None),
            patch.object(gui_launcher.subprocess, "run", return_value=SimpleNamespace(returncode=1)) as run,
            patch.object(gui_launcher.ctypes.windll.user32, "MessageBoxW", message_box),
        ):
            self.assertEqual(gui_launcher.launch_gui("exam_editor_gui"), 1)
            self.assertEqual(run.call_args.args[0][0], str(executable))
            self.assertEqual(run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertIn("gui-launch.log", message_box.call_args.args[1])

    def test_missing_environment_gives_installation_instructions(self):
        error = io.StringIO()
        with (
            patch.object(gui_launcher.sys, "prefix", str(self.root / "system-python")),
            patch.object(gui_launcher.sys, "stderr", error),
            patch.object(gui_launcher.subprocess, "run") as run,
        ):
            self.assertEqual(gui_launcher.launch_gui("question_workflow_gui"), 1)
            self.assertIn("setup_new_pc.cmd", error.getvalue())
            run.assert_not_called()

    def test_handled_business_error_does_not_show_a_second_startup_error(self):
        executable = self.root / ".venv/Scripts/python.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        def run(*args, **kwargs):
            kwargs["stdout"].write("[APP_RETURN] code=1\n")
            kwargs["stdout"].flush()
            return SimpleNamespace(returncode=1)
        with (
            patch.object(gui_launcher.sys, "prefix", str(self.root / "system-python")),
            patch.object(gui_launcher.sys, "stdout", None),
            patch.object(gui_launcher.sys, "stderr", None),
            patch.object(gui_launcher.subprocess, "run", side_effect=run),
            patch.object(gui_launcher.ctypes.windll.user32, "MessageBoxW") as message_box,
        ):
            self.assertEqual(gui_launcher.launch_gui("question_workflow_gui"), 1)
            message_box.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "Windows installer")
class InstallerBatchTests(unittest.TestCase):
    def test_missing_environment_and_special_paths_do_not_report_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "中文 空格 (&!)"
            root.mkdir()
            script = root / "setup_new_pc.cmd"
            script.write_bytes(Path(__file__).with_name("setup_new_pc.cmd").read_bytes())
            (root / "requirements.txt").touch()
            (root / "question_workflow_gui.py").touch()
            result = subprocess.run(
                f'cmd.exe /d /s /c ""{script}" --check"',
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(root), result.stdout)
            self.assertIn("项目虚拟环境", result.stdout)
            self.assertFalse((root / "examworker").exists())

    def test_empty_virtual_environment_fails_real_import_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "setup_new_pc.cmd"
            script.write_bytes(Path(__file__).with_name("setup_new_pc.cmd").read_bytes())
            (root / "requirements.txt").touch()
            (root / "question_workflow_gui.py").touch()
            venv.create(root / ".venv", with_pip=True)
            result = subprocess.run(
                f'cmd.exe /d /s /c ""{script}" --check"',
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No broken requirements found", result.stdout)
            self.assertIn("No module named 'selenium'", result.stderr)
            self.assertNotIn("Import check OK", result.stdout)

    def test_broken_environment_backup_preserves_contents_in_special_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "中文 路径 (&!)"
            environment = root / ".venv"
            environment.mkdir(parents=True)
            (environment / "keep.txt").write_text("old environment", encoding="utf-8")
            # Execute the actual backup command embedded in the installer.
            script = Path(__file__).with_name("setup_new_pc.cmd").read_text(encoding="utf-8")
            command = next(line for line in script.splitlines() if line.startswith("powershell -NoProfile -Command"))
            with patch.dict("os.environ", {"TARGET_DIR": str(root)}):
                result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(environment.exists())
            backups = list(root.glob(".venv.backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "keep.txt").read_text(encoding="utf-8"), "old environment")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

"""Small, dependency-free Windows Task Scheduler adapter.

The task always calls the current interpreter (or the packaged executable) with
absolute paths.  Authentication remains in the dedicated Edge profile; this
module never reads or persists browser credentials.
"""

from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


TASK_PREFIX = "TeachingAutomation_GradeRelease_"


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取计划任务所需的运行记录：{path}") from exc


def task_name(config, state) -> str:
    """Return a stable task name without putting user-entered text in it."""
    exam_id = str(state.get("exam_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", exam_id):
        raise RuntimeError("运行记录中没有有效的考试编号，不能创建成绩发布任务。")
    return TASK_PREFIX + exam_id


def _run_schtasks(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    if os.name != "nt":
        raise RuntimeError("Windows 计划任务只能在 Windows 中创建。")
    try:
        completed = subprocess.run(
            ["schtasks.exe", *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Windows 计划任务操作等待超过 30 秒，已停止。") from exc
    if check and completed.returncode:
        encoding = "mbcs" if os.name == "nt" else "utf-8"
        output = (completed.stderr or completed.stdout).decode(encoding, errors="replace").strip()
        raise RuntimeError(f"Windows 计划任务操作失败（{completed.returncode}）：{output}")
    return completed


def task_exists(name: str) -> bool:
    result = _run_schtasks(["/Query", "/TN", name], check=False)
    return result.returncode == 0


def _execution_parts(
    config_path: Path,
    state_path: Path,
    profile_dir: Path,
    session_path: Path | None = None,
) -> tuple[str, str, Path]:
    script_path = Path(__file__).resolve().parents[1] / "create_signal_exam.py"
    if getattr(sys, "frozen", False):
        command = str(Path(sys.executable).resolve())
        arguments: list[str] = []
    else:
        command = str(Path(sys.executable).resolve())
        arguments = [str(script_path.resolve())]
    arguments.extend(
        [
            "--config", str(config_path.resolve()),
            "--state", str(state_path.resolve()),
            "--profile-dir", str(profile_dir.resolve()),
            "--release-grades",
            "--headless",
        ]
    )
    if session_path is not None:
        arguments.extend(["--session", str(Path(session_path).resolve())])
    return command, subprocess.list2cmdline(arguments), script_path.parent.resolve()


def _task_xml(command: str, arguments: str, working_directory: Path, release_at: datetime) -> bytes:
    """Build Task Scheduler XML so long absolute paths are not limited by /TR."""
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", namespace)
    tag = lambda name: f"{{{namespace}}}{name}"
    task = ET.Element(tag("Task"), {"version": "1.4"})
    registration = ET.SubElement(task, tag("RegistrationInfo"))
    ET.SubElement(registration, tag("Description")).text = "按考试配置自动发布成绩"
    triggers = ET.SubElement(task, tag("Triggers"))
    trigger = ET.SubElement(triggers, tag("TimeTrigger"))
    local = release_at.astimezone()
    ET.SubElement(trigger, tag("StartBoundary")).text = local.strftime("%Y-%m-%dT%H:%M:%S")
    ET.SubElement(trigger, tag("Enabled")).text = "true"
    principals = ET.SubElement(task, tag("Principals"))
    principal = ET.SubElement(principals, tag("Principal"), {"id": "Author"})
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = os.environ.get("USERNAME", "").strip() or getpass.getuser()
    ET.SubElement(principal, tag("UserId")).text = f"{domain}\\{user}" if domain else user
    ET.SubElement(principal, tag("LogonType")).text = "InteractiveToken"
    ET.SubElement(principal, tag("RunLevel")).text = "LeastPrivilege"
    settings = ET.SubElement(task, tag("Settings"))
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "true"),
        ("Enabled", "true"),
        ("Hidden", "false"),
        ("ExecutionTimeLimit", "PT2H"),
        ("Priority", "7"),
    ):
        ET.SubElement(settings, tag(name)).text = value
    actions = ET.SubElement(task, tag("Actions"), {"Context": "Author"})
    execute = ET.SubElement(actions, tag("Exec"))
    ET.SubElement(execute, tag("Command")).text = command
    ET.SubElement(execute, tag("Arguments")).text = arguments
    ET.SubElement(execute, tag("WorkingDirectory")).text = str(working_directory)
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def create_grade_release_task(
    config_path,
    state_path,
    release_at,
    *,
    profile_dir=None,
    session_path=None,
) -> None:
    """Create or update the one-time grade-release task.

    If the recorded task has the same name and time and Windows still has it,
    this is a no-op.  A changed time replaces the existing task via /F.
    """
    config_path = Path(config_path).resolve()
    state_path = Path(state_path).resolve()
    state = _load_state(state_path)
    name = task_name(None, state)
    if not isinstance(release_at, datetime) or release_at.tzinfo is None:
        raise RuntimeError("成绩发布时间必须是带时区的时间。")
    recorded = state.get("scheduled_grade_release") or {}
    if (
        recorded.get("task_name") == name
        and recorded.get("release_at") == release_at.isoformat()
        and task_exists(name)
    ):
        return
    if profile_dir is None:
        profile_dir = Path(__file__).resolve().parents[2] / "work" / "edge-automation-profile"
    command, arguments, working_directory = _execution_parts(
        config_path,
        state_path,
        Path(profile_dir),
        Path(session_path) if session_path is not None else None,
    )
    xml = _task_xml(command, arguments, working_directory, release_at)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as handle:
            handle.write(xml)
            temporary = Path(handle.name)
        _run_schtasks(["/Create", "/TN", name, "/XML", str(temporary), "/F"])
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def delete_grade_release_task(config=None, state=None, *, state_path=None) -> None:
    if state is None:
        if state_path is None:
            raise RuntimeError("删除计划任务时缺少运行记录。")
        state = _load_state(Path(state_path))
    name = task_name(config, state)
    if task_exists(name):
        _run_schtasks(["/Delete", "/TN", name, "/F"])

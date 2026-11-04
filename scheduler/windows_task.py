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
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


TASK_PREFIX = "TeachingAutomation_GradeRelease_"
TASK_XML_NAME = "grade-release-task.xml"
TASK_HELPER_NAME = "register-grade-release-task.cmd"
TASK_DELETE_HELPER_NAME = "delete-grade-release-task.cmd"
TASK_PENDING_NAME = "grade-release-task.pending.json"
TASK_MARKER_NAME = "grade-release-task.registered.json"

# A Windows agent workspace sandbox (for example Codex/DSH) can block access to
# the Task Scheduler RPC endpoint. schtasks then reports a missing path even
# though the tool and XML file exist.
SCHEDULER_UNAVAILABLE_MARKERS = (
    "cannot find the path specified",
    "系统找不到指定的路径",
)


class TaskSchedulerUnavailable(RuntimeError):
    """schtasks.exe cannot be used from the current restricted environment.

    The exception message tells the user how to finish the registration by
    double-clicking the generated helper script.
    """

    def __init__(self, message: str, helper_path: Path | None = None):
        super().__init__(message)
        self.helper_path = helper_path
        self.helper_script = helper_path


def _looks_like_scheduler_unavailable(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return any(marker.casefold() in message for marker in SCHEDULER_UNAVAILABLE_MARKERS)


def _schtasks_output(completed: subprocess.CompletedProcess) -> str:
    encoding = "mbcs" if os.name == "nt" else "utf-8"
    return (completed.stderr or completed.stdout).decode(encoding, errors="replace").strip()



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


def _task_work_dir(state_path: Path) -> Path:
    state_path = Path(state_path).resolve()
    if state_path.parent.name == "exam-run-states":
        return state_path.parent.parent
    return state_path.parent


def task_xml_path(state_path: Path) -> Path:
    return _task_work_dir(state_path) / TASK_XML_NAME


def pending_marker_path(state_path: Path) -> Path:
    return _task_work_dir(state_path) / TASK_PENDING_NAME


def registration_marker_path(state_path: Path) -> Path:
    return _task_work_dir(state_path) / TASK_MARKER_NAME


def helper_script_path(state_path: Path) -> Path:
    return _task_work_dir(state_path) / TASK_HELPER_NAME


def delete_helper_script_path(state_path: Path) -> Path:
    return _task_work_dir(state_path) / TASK_DELETE_HELPER_NAME


def registration_recorded(name: str, release_at: datetime, state_path: Path) -> bool:
    """True when the helper script already registered this exact task.

    The helper writes the marker file only after schtasks reports success, so a
    later run can finish the scheduling step even when schtasks is blocked.
    """
    try:
        recorded = json.loads(
            registration_marker_path(state_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return False
    return (
        recorded.get("task_name") == name
        and recorded.get("release_at") == release_at.isoformat()
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for encoding in ("mbcs", "utf-8"):
        try:
            with path.open("w", encoding=encoding, newline="") as stream:
                stream.write(text)
            return
        except (LookupError, UnicodeEncodeError):
            continue
    with path.open("w", encoding="ascii", errors="replace", newline="") as stream:
        stream.write(text)


def _write_registration_helper(
    name: str,
    release_at: datetime,
    state_path: Path,
) -> Path:
    pending = pending_marker_path(state_path)
    marker = registration_marker_path(state_path)
    helper = helper_script_path(state_path)
    _write_text(
        pending,
        json.dumps(
            {
                "enabled": True,
                "task_name": name,
                "release_at": release_at.isoformat(),
                "registered_at": datetime.now().astimezone().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    script = "\r\n".join(
        [
            "@echo off",
            "setlocal",
            "chcp 936 >nul",
            "echo ==================================================",
            "echo 正在注册“成绩自动发布”Windows 计划任务",
            f"echo 任务名称：{name}",
            f"echo 发布时间：{release_at:%Y-%m-%d %H:%M}（北京时间）",
            "echo ==================================================",
            "echo.",
            f'schtasks.exe /Create /TN "{name}" /XML "{task_xml_path(state_path)}" /F',
            "if errorlevel 1 goto failed",
            "echo.",
            "echo 注册成功！请关闭本窗口，然后重新打开 exam_editor_gui.pyw。",
            f'copy /y "{pending}" "{marker}" >nul',
            "goto done",
            ":failed",
            "echo.",
            "echo 注册失败：请把上面的错误信息截图发给维护者。",
            ":done",
            "echo.",
            "pause",
            "",
        ]
    )
    _write_text(helper, script)
    return helper


def _write_delete_helper(name: str, state_path: Path) -> Path:
    helper = delete_helper_script_path(state_path)
    script = "\r\n".join(
        [
            "@echo off",
            "setlocal",
            "chcp 936 >nul",
            f"echo 正在删除计划任务：{name}",
            f'schtasks.exe /Delete /TN "{name}" /F',
            "echo.",
            "pause",
            "",
        ]
    )
    _write_text(helper, script)
    return helper



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


def task_has_logon_recovery(name: str) -> bool:
    """Old one-time tasks must be upgraded even when their release time matches."""
    result = _run_schtasks(["/Query", "/TN", name, "/XML"], check=False)
    if result.returncode:
        return False
    try:
        root = ET.fromstring(result.stdout)
    except (ET.ParseError, ValueError):
        return False
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    trigger = root.find("t:Triggers/t:LogonTrigger", ns)
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", "", ns)
    return (
        trigger is not None
        and trigger.findtext("t:Enabled", "true", ns) == "true"
        and bool(trigger.findtext("t:UserId", "", ns))
        and trigger.findtext("t:Delay", "", ns) == "PT1M"
        and "--scheduled-release-check" in arguments
        and root.findtext("t:Settings/t:RunOnlyIfNetworkAvailable", "", ns) == "false"
    )


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
            "--scheduled-release-check",
            "--headless",
        ]
    )
    if session_path is not None:
        arguments.extend(["--session", str(Path(session_path).resolve())])
    return command, subprocess.list2cmdline(arguments), script_path.parent.resolve()


def _freeze_session(session_path: Path | None, state_path: Path) -> Path | None:
    """Keep a task's question mapping stable when current session is replaced."""
    if session_path is None:
        return None
    source = Path(session_path).resolve()
    if not source.is_file():
        raise RuntimeError(f"无法冻结计划任务的 Session：{source}")
    target = state_path.resolve().with_suffix('.session.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    if source != target:
        shutil.copy2(source, target)
    return target


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
    user_id = f"{domain}\\{user}" if domain else user
    ET.SubElement(principal, tag("UserId")).text = user_id
    # A second trigger catches shutdowns that miss the one-time release trigger.
    # Windows waits one minute; there is no resident Python polling process.
    logon = ET.SubElement(triggers, tag("LogonTrigger"))
    ET.SubElement(logon, tag("StartBoundary")).text = local.strftime("%Y-%m-%dT%H:%M:%S")
    ET.SubElement(logon, tag("Enabled")).text = "true"
    ET.SubElement(logon, tag("Delay")).text = "PT1M"
    ET.SubElement(logon, tag("UserId")).text = user_id
    ET.SubElement(principal, tag("LogonType")).text = "InteractiveToken"
    ET.SubElement(principal, tag("RunLevel")).text = "LeastPrivilege"
    settings = ET.SubElement(task, tag("Settings"))
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("StartWhenAvailable", "true"),
        # Let the script log failures and show recovery options even when offline.
        ("RunOnlyIfNetworkAvailable", "false"),
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
) -> bool:
    """Create or update the one-time grade-release task.

    Returns True when schtasks actually created/updated the task and False when
    an earlier helper-script registration for the same exam and time is reused.

    Matching tasks are kept only if they already include logon recovery.
    Legacy tasks and changed times are replaced via /F.
    """
    config_path = Path(config_path).resolve()
    state_path = Path(state_path).resolve()
    state = _load_state(state_path)
    name = task_name(None, state)
    if not isinstance(release_at, datetime) or release_at.tzinfo is None:
        raise RuntimeError("成绩发布时间必须是带时区的时间。")
    if registration_recorded(name, release_at, state_path):
        print(f"检测到已手动注册的成绩发布任务：{name}；跳过重复创建。", flush=True)
        return False
    recorded = state.get("scheduled_grade_release") or {}
    if (
        recorded.get("task_name") == name
        and recorded.get("release_at") == release_at.isoformat()
        and task_has_logon_recovery(name)
    ):
        return False
    if profile_dir is None:
        profile_dir = Path(__file__).resolve().parents[2] / "work" / "edge-automation-profile"
    frozen_session = _freeze_session(
        Path(session_path) if session_path is not None else None,
        state_path,
    )
    command, arguments, working_directory = _execution_parts(
        config_path,
        state_path,
        Path(profile_dir),
        frozen_session,
    )
    xml = _task_xml(command, arguments, working_directory, release_at)
    xml_path = task_xml_path(state_path)
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_bytes(xml)
    try:
        _run_schtasks(["/Create", "/TN", name, "/XML", str(xml_path), "/F"])
    except RuntimeError as exc:
        if not xml_path.exists() or not _looks_like_scheduler_unavailable(exc):
            raise
        helper = _write_registration_helper(name, release_at, state_path)
        raise TaskSchedulerUnavailable(
            "当前电脑的受限沙箱禁止程序直接创建 Windows 计划任务。\n"
            f"已生成注册脚本：\n{helper}\n\n"
            "请双击运行该脚本，看到“注册成功”后，重新打开 exam_editor_gui.pyw "
            "并再点一次“2. 创建并运行考试”。程序会识别已注册的任务并直接完成，"
            "不会重复创建考试。",
            helper_path=helper,
        ) from exc
    return True


def delete_grade_release_task(config=None, state=None, *, state_path=None) -> bool:
    """Delete the task; return False when a restricted sandbox blocks deleting.

    A helper script is generated in that case so the user can remove the
    leftover task with a double-click.
    """
    if state is None:
        if state_path is None:
            raise RuntimeError("删除计划任务时缺少运行记录。")
        state = _load_state(Path(state_path))
    name = task_name(config, state)
    state_path = Path(state_path).resolve() if state_path is not None else None
    completed = _run_schtasks(["/Delete", "/TN", name, "/F"], check=False)
    if completed.returncode == 0:
        return True
    output = _schtasks_output(completed)
    lowered = output.casefold()
    if any(marker.casefold() in lowered for marker in SCHEDULER_UNAVAILABLE_MARKERS):
        if state_path is None:
            raise RuntimeError(f"Windows 计划任务操作失败（{completed.returncode}）：{output}")
        helper = _write_delete_helper(name, state_path)
        print(
            f"[计划任务] 当前受限环境无法删除任务 {name}；"
            f"可双击运行 {helper} 手动清理。",
            flush=True,
        )
        return False
    if "cannot find" in lowered or "找不到" in output:
        return True
    raise RuntimeError(f"Windows 计划任务操作失败（{completed.returncode}）：{output}")

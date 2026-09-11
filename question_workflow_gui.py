from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from PySide6.QtWidgets import QApplication, QMessageBox

from ai_exam_preparer import prepare_ai_exam_config
from create_signal_exam import load_config
from exam_session import load_session


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_ROOT / "考试配置表.xlsx"
DEFAULT_PLATFORM_PROFILE = APP_ROOT / "work" / "edge-automation-profile"
DEFAULT_DEEPSEEK_PROFILE = APP_ROOT / "work" / "edge-deepseek-profile"
DEFAULT_SESSION = APP_ROOT / "work" / "current_exam_session.json"


def completed_session_for_config(config_path: Path) -> bool:
    """Avoid uploading another batch when this exam is already prepared."""
    if not DEFAULT_SESSION.is_file():
        return False
    config = load_config(config_path, require_questions=False)
    session = load_session(DEFAULT_SESSION)
    return (
        session.exam_name == config.exam_name
        and session.status
        in {
            "uploaded",
            "exam_created",
            "waiting_publish_confirm",
            "published",
            "scheduled",
        }
    )


def run_question_workflow(config_path: Path = DEFAULT_CONFIG) -> Path | None:
    """Open the question requirements panel immediately and run the workflow."""
    config_path = Path(config_path).resolve()
    load_config(config_path, require_questions=False)
    if completed_session_for_config(config_path):
        QMessageBox.information(
            None,
            "本次题库已准备完成",
            "当前考试的题目已经上传。\n"
            "如需创建考试，请关闭本窗口并双击 exam_editor_gui.pyw。\n\n"
            "如需重新出题，请先在考试配置表中使用新的考试名称。",
        )
        return None

    result = prepare_ai_exam_config(
        config_path,
        deepseek_profile_dir=DEFAULT_DEEPSEEK_PROFILE,
        platform_profile_dir=DEFAULT_PLATFORM_PROFILE,
        session_path=DEFAULT_SESSION,
    )
    if result is not None:
        QMessageBox.information(
            None,
            "题库准备完成",
            "全部题目已审核并上传课程题库。\n\n"
            "现在可以关闭本程序，再双击 exam_editor_gui.pyw 创建考试。",
        )
    return result


def main() -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    try:
        run_question_workflow()
    except Exception as exc:
        QMessageBox.critical(None, "题库准备失败", f"{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

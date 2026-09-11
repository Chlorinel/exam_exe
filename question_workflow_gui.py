from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_exam_preparer import prepare_ai_exam_config
from create_signal_exam import load_config
from exam_session import load_session


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_ROOT / "考试配置表.xlsx"
DEFAULT_PLATFORM_PROFILE = APP_ROOT / "work" / "edge-automation-profile"
DEFAULT_DEEPSEEK_PROFILE = APP_ROOT / "work" / "edge-deepseek-profile"
DEFAULT_SESSION = APP_ROOT / "work" / "current_exam_session.json"


class QuestionWorkflowWindow(QMainWindow):
    """Dedicated AI generation, review, and question-bank upload window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 出题与题库上传")
        self.resize(820, 430)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel("<h2>AI 出题与题库上传</h2>"))

        note = QLabel(
            "在出题窗口中可以加入多组要求。全部题目审核通过后，"
            "程序会按章节自动上传课程题库；本程序不会创建或发布考试。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        config_row = QHBoxLayout()
        config_row.addWidget(QLabel("考试配置表："))
        self.config_edit = QLineEdit(str(DEFAULT_CONFIG))
        config_row.addWidget(self.config_edit, 1)
        browse = QPushButton("选择...")
        browse.clicked.connect(self.choose_config)
        config_row.addWidget(browse)
        layout.addLayout(config_row)

        platform_row = QHBoxLayout()
        platform_row.addWidget(QLabel("教学平台登录配置："))
        self.platform_profile_edit = QLineEdit(str(DEFAULT_PLATFORM_PROFILE))
        platform_row.addWidget(self.platform_profile_edit, 1)
        layout.addLayout(platform_row)

        deepseek_row = QHBoxLayout()
        deepseek_row.addWidget(QLabel("DeepSeek 登录配置："))
        self.deepseek_profile_edit = QLineEdit(str(DEFAULT_DEEPSEEK_PROFILE))
        deepseek_row.addWidget(self.deepseek_profile_edit, 1)
        layout.addLayout(deepseek_row)

        self.background_checkbox = QCheckBox("后台运行网页（登录时自动弹出）")
        self.background_checkbox.setToolTip(
            "首次登录或登录失效时会弹出可见 Edge，登录完成后自动继续。"
        )
        layout.addWidget(self.background_checkbox)

        self.session_label = QLabel()
        self.session_label.setWordWrap(True)
        layout.addWidget(self.session_label)

        actions = QHBoxLayout()
        self.validate_button = QPushButton("1. 检查配置")
        self.validate_button.clicked.connect(self.validate_config)
        actions.addWidget(self.validate_button)
        self.start_button = QPushButton("2. 开始出题、审核并上传")
        self.start_button.clicked.connect(self.start_workflow)
        actions.addWidget(self.start_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.status_label = QLabel("请选择配置表后开始。")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.status_label, 1)
        self._refresh_session_status()

    def _config_path(self) -> Path:
        return Path(self.config_edit.text().strip()).expanduser().resolve()

    def _platform_profile(self) -> Path:
        return Path(self.platform_profile_edit.text().strip()).expanduser().resolve()

    def _deepseek_profile(self) -> Path:
        return Path(self.deepseek_profile_edit.text().strip()).expanduser().resolve()

    def _refresh_session_status(self) -> None:
        try:
            if not DEFAULT_SESSION.exists():
                self.session_label.setText("当前考试：尚未开始出题")
                self.start_button.setEnabled(True)
                return
            session = load_session(DEFAULT_SESSION)
            config = load_config(self._config_path(), require_questions=False)
            if session.exam_name != config.exam_name:
                self.session_label.setText("当前配置尚未创建对应的出题 Session。")
                self.start_button.setEnabled(True)
                return
            total = len(session.questions)
            approved = sum(q.review_status == "approved" for q in session.questions)
            uploaded = sum(q.upload_status == "uploaded" for q in session.questions)
            self.session_label.setText(
                "当前考试：<b>{}</b><br>状态：{}　题目：{}　审核：{}/{}　上传：{}/{}".format(
                    session.exam_name,
                    session.status,
                    total,
                    approved,
                    total,
                    uploaded,
                    total,
                )
            )
            self.start_button.setEnabled(session.status not in {
                "uploaded",
                "exam_created",
                "waiting_publish_confirm",
                "published",
                "scheduled",
            })
        except Exception as exc:
            self.session_label.setText(f"当前 Session 无法读取：{type(exc).__name__}: {exc}")
            self.start_button.setEnabled(False)

    def _set_busy(self, busy: bool, message: str) -> None:
        self.validate_button.setEnabled(not busy)
        self.start_button.setEnabled(not busy)
        self.status_label.setText(message)
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
            self._refresh_session_status()
        QApplication.processEvents()

    def choose_config(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择考试配置表",
            str(self._config_path().parent),
            "Excel 工作簿 (*.xlsx)",
        )
        if filename:
            self.config_edit.setText(filename)
            self._refresh_session_status()

    def validate_config(self) -> None:
        try:
            config = load_config(self._config_path(), require_questions=False)
        except Exception as exc:
            QMessageBox.critical(self, "配置检查失败", f"{type(exc).__name__}: {exc}")
            return
        QMessageBox.information(
            self,
            "配置检查通过",
            f"考试：{config.exam_name}\n课程：{config.course_name}",
        )

    def start_workflow(self) -> None:
        self._set_busy(
            True,
            "正在生成、审核并上传题目。登录失效时请在弹出的 Edge 中完成登录。",
        )
        try:
            final_path = prepare_ai_exam_config(
                self._config_path(),
                deepseek_profile_dir=self._deepseek_profile(),
                platform_profile_dir=self._platform_profile(),
                deepseek_headless=self.background_checkbox.isChecked(),
                platform_headless=self.background_checkbox.isChecked(),
                session_path=DEFAULT_SESSION,
            )
            if final_path is None:
                self.status_label.setText("出题或审核已取消，没有上传新题。")
                return
            QMessageBox.information(
                self,
                "题库准备完成",
                "全部题目已审核并上传课程题库。\n\n"
                f"兼容配置表：\n{Path(final_path).resolve()}\n\n"
                "现在可以关闭本程序，再双击 exam_editor_gui.pyw 创建考试。",
            )
            self.status_label.setText("题库准备完成，可以关闭本程序。")
        except Exception as exc:
            QMessageBox.critical(self, "题库准备失败", f"{type(exc).__name__}: {exc}")
            self.status_label.setText("题库准备失败，可修复问题后再次点击续跑。")
        finally:
            self._set_busy(False, self.status_label.text())


def main() -> int:
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv)
    window = QuestionWorkflowWindow()
    window.show()
    return app.exec() if owns_app else 0


if __name__ == "__main__":
    raise SystemExit(main())

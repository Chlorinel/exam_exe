from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from create_signal_exam import config_from_session, load_config, run_configuration
from exam_session import load_session


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_ROOT / "考试配置表.xlsx"
DEFAULT_PLATFORM_PROFILE = APP_ROOT / "work" / "edge-automation-profile"
DEFAULT_SESSION = APP_ROOT / "work" / "current_exam_session.json"


def session_action_permissions(session) -> tuple[bool, bool]:
    if session is None:
        return True, True
    return (
        session.status == "uploaded",
        session.status in {"exam_created", "waiting_publish_confirm"},
    )


class PublishConfirmationDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.expected_text = f"发布 {config.exam_name}"
        self.setWindowTitle("人工确认发布考试")
        self.setModal(True)
        self.resize(620, 360)

        layout = QVBoxLayout(self)
        warning = QLabel("<b>即将把考试正式发布给学生。</b><br>请核对当前考试页面和以下信息。")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        form = QFormLayout()
        form.addRow("考试：", QLabel(config.exam_name))
        form.addRow("班级：", QLabel(config.class_code))
        form.addRow("开始：", QLabel(config.start.strftime("%Y-%m-%d %H:%M")))
        form.addRow("结束：", QLabel(config.end.strftime("%Y-%m-%d %H:%M")))
        form.addRow("题目数量：", QLabel(str(len(config.questions))))
        layout.addLayout(form)

        instruction = QLabel(f"确认无误后，请手动输入：<b>{self.expected_text}</b>")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        self.confirm_edit = QLineEdit()
        self.confirm_edit.setPlaceholderText(self.expected_text)
        layout.addWidget(self.confirm_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("确认发布")
        self.ok_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消，保留草稿")
        self.confirm_edit.textChanged.connect(self._update_ok)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _update_ok(self, value: str) -> None:
        self.ok_button.setEnabled(value.strip() == self.expected_text)


def build_backend_args(
    config_path: Path,
    profile_dir: Path,
    *,
    prepare: bool = False,
    run: bool = False,
    publish_exam: bool = False,
    session_path: Path | None = None,
    headless: bool = False,
):
    return SimpleNamespace(
        config=Path(config_path),
        validate=False,
        dry_run=False,
        prepare=prepare,
        run=run,
        check=False,
        release_grades=False,
        schedule_grades=False,
        publish_exam=publish_exam,
        session=Path(session_path) if session_path is not None else None,
        state=None,
        profile_dir=Path(profile_dir),
        headless=bool(headless),
    )


def build_run_exam_args(
    config_path: Path,
    profile_dir: Path,
    *,
    session_path: Path | None = None,
    headless: bool = False,
):
    return build_backend_args(
        config_path,
        profile_dir,
        run=True,
        publish_exam=True,
        session_path=session_path,
        headless=headless,
    )


class ExamEditorWindow(QMainWindow):
    """Dedicated exam draft creation, verification, and publication window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("考试创建与发布")
        self.resize(820, 470)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel("<h2>考试创建与发布</h2>"))
        note = QLabel(
            "本程序只负责创建、编辑和发布考试，不会调用 AI 出题。"
            "正式发布前会打开当前考试页面并要求手动确认。"
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

        session_row = QHBoxLayout()
        session_row.addWidget(QLabel("AI 出题数据："))
        self.session_edit = QLineEdit(str(DEFAULT_SESSION) if DEFAULT_SESSION.exists() else "")
        self.session_edit.setPlaceholderText("可选；选择 ExamSession JSON，留空则使用配置表选题明细")
        self.session_edit.editingFinished.connect(self._refresh_session_status)
        session_row.addWidget(self.session_edit, 1)
        session_browse = QPushButton("选择...")
        session_browse.clicked.connect(self.choose_session)
        session_row.addWidget(session_browse)
        clear_session = QPushButton("使用配置表")
        clear_session.clicked.connect(self.clear_session)
        session_row.addWidget(clear_session)
        layout.addLayout(session_row)

        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("教学平台登录配置："))
        self.platform_profile_edit = QLineEdit(str(DEFAULT_PLATFORM_PROFILE))
        profile_row.addWidget(self.platform_profile_edit, 1)
        layout.addLayout(profile_row)

        self.background_checkbox = QCheckBox("后台运行网页（登录和发布前核对时自动弹出）")
        layout.addWidget(self.background_checkbox)

        self.session_label = QLabel()
        self.session_label.setWordWrap(True)
        layout.addWidget(self.session_label)

        actions = QHBoxLayout()
        self.validate_button = QPushButton("1. 检查配置")
        self.validate_button.clicked.connect(self.validate_config)
        actions.addWidget(self.validate_button)
        self.create_button = QPushButton("2. 创建并运行考试")
        self.create_button.clicked.connect(self.run_exam)
        actions.addWidget(self.create_button)
        self.publish_button = QPushButton("3. 继续发布草稿")
        self.publish_button.clicked.connect(self.publish_exam)
        actions.addWidget(self.publish_button)
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

    def _active_session_path(self) -> Path | None:
        value = self.session_edit.text().strip()
        if not value:
            return None
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"AI 出题数据文件不存在：{path}")
        load_session(path)
        return path

    def _load_current_session(self):
        path = self._active_session_path()
        return load_session(path) if path else None

    def _refresh_session_status(self) -> None:
        try:
            session = self._load_current_session()
            if session is None:
                self.session_label.setText(
                    "题目来源：考试配置表中的“选题明细”。"
                )
            else:
                total = len(session.questions)
                uploaded = sum(q.upload_status == "uploaded" for q in session.questions)
                self.session_label.setText(
                    f"AI 出题数据：<b>{session.exam_name}</b><br>"
                    f"状态：{session.status}　题目：{total}　已上传：{uploaded}/{total}"
                )
            can_create, can_publish = session_action_permissions(session)
            self.create_button.setEnabled(can_create)
            self.publish_button.setEnabled(can_publish)
        except Exception as exc:
            self.session_label.setText(f"当前 Session 无法读取：{type(exc).__name__}: {exc}")
            self.create_button.setEnabled(False)
            self.publish_button.setEnabled(False)

    def _set_busy(self, busy: bool, message: str) -> None:
        for button in (self.validate_button, self.create_button, self.publish_button):
            button.setEnabled(not busy)
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

    def choose_session(self) -> None:
        current = self.session_edit.text().strip()
        start = Path(current).expanduser() if current else DEFAULT_SESSION
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择上一次 AI 出题数据",
            str(start.parent),
            "ExamSession JSON (*.json)",
        )
        if filename:
            self.session_edit.setText(filename)
            self._refresh_session_status()

    def clear_session(self) -> None:
        self.session_edit.clear()
        self._refresh_session_status()

    def validate_config(self) -> None:
        try:
            base_config = load_config(self._config_path(), require_questions=False)
            session = self._load_current_session()
            config = config_from_session(base_config, session) if session is not None else base_config
        except Exception as exc:
            QMessageBox.critical(self, "配置检查失败", f"{type(exc).__name__}: {exc}")
            return
        source = "Session" if session is not None else "Excel 选题明细"
        count = len(session.questions) if session is not None else len(config.questions)
        QMessageBox.information(
            self,
            "配置检查通过",
            f"考试：{config.exam_name}\n课程：{config.course_name}\n题目来源：{source}\n题目数量：{count}",
        )

    def _confirm_publish(self, config) -> bool:
        return PublishConfirmationDialog(config, self).exec() == QDialog.DialogCode.Accepted

    def _run_backend(self, *, continuing: bool) -> None:
        self._set_busy(
            True,
            "正在检查并编辑考试。登录失效时请在弹出的 Edge 中完成登录。",
        )
        try:
            args = build_run_exam_args(
                self._config_path(),
                self._platform_profile(),
                session_path=self._active_session_path(),
                headless=self.background_checkbox.isChecked(),
            )
            run_configuration(args, publish_confirmer=self._confirm_publish)
            self.status_label.setText(
                "流程已结束。若在确认窗口取消，考试草稿仍保留。"
            )
            if not continuing:
                QMessageBox.information(
                    self,
                    "考试流程结束",
                    "考试创建和核对流程已结束；取消发布时草稿会保留。",
                )
        except Exception as exc:
            QMessageBox.critical(self, "考试流程失败", f"{type(exc).__name__}: {exc}")
            self.status_label.setText("考试流程失败或已停止。")
        finally:
            self._set_busy(False, self.status_label.text())

    def run_exam(self) -> None:
        self._run_backend(continuing=False)

    def publish_exam(self) -> None:
        self._run_backend(continuing=True)


def main() -> int:
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv)
    window = ExamEditorWindow()
    window.show()
    return app.exec() if owns_app else 0


if __name__ == "__main__":
    raise SystemExit(main())

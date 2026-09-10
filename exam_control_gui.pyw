from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

# pythonw.exe 下 stdout/stderr 可能为 None；给现有后端一个安全输出目标。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
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

from ai_exam_preparer import prepare_ai_exam_config
from create_signal_exam import load_config, run_configuration


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_ROOT / "考试配置表.xlsx"
DEFAULT_PLATFORM_PROFILE = APP_ROOT / "work" / "edge-automation-profile"
DEFAULT_DEEPSEEK_PROFILE = APP_ROOT / "work" / "edge-deepseek-profile"


class PublishConfirmationDialog(QDialog):
    """考试真正发布前的 GUI 人工确认。"""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.expected_text = f"发布 {config.exam_name}"

        self.setWindowTitle("人工确认发布考试")
        self.setModal(True)
        self.resize(620, 360)

        layout = QVBoxLayout(self)

        warning = QLabel(
            "<b>即将把考试正式发布给学生。</b><br>"
            "请再次核对以下信息。"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        form = QFormLayout()
        form.addRow("考试：", QLabel(config.exam_name))
        form.addRow("班级：", QLabel(config.class_code))
        form.addRow("开始：", QLabel(config.start.strftime("%Y-%m-%d %H:%M")))
        form.addRow("结束：", QLabel(config.end.strftime("%Y-%m-%d %H:%M")))
        form.addRow("题目数量：", QLabel(str(len(config.questions))))
        layout.addLayout(form)

        instruction = QLabel(
            "确认无误后，请手动输入："
            f"<b>{self.expected_text}</b>"
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setPlaceholderText(self.expected_text)
        layout.addWidget(self.confirm_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        )
        self.ok_button.setText("确认发布")
        self.ok_button.setEnabled(False)

        cancel = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        cancel.setText("取消，保留草稿")

        self.confirm_edit.textChanged.connect(self._update_ok)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _update_ok(self, value: str) -> None:
        self.ok_button.setEnabled(
            value.strip() == self.expected_text
        )


def build_backend_args(
    config_path: Path,
    profile_dir: Path,
    *,
    prepare: bool = False,
    run: bool = False,
    publish_exam: bool = False,
):
    """GUI 直接构造后端参数，不需要解析命令行。"""
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
        state=None,
        profile_dir=Path(profile_dir),
        headless=False,
    )


class ExamControlWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("智课空间考试控制台")
        self.resize(780, 440)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(QLabel("<h2>智课空间考试控制台</h2>"))

        note = QLabel(
            "AI 题目本地审核通过后会自动上传题库；"
            "考试正式发布仍必须在本窗口进行人工确认。"
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
        platform_row.addWidget(QLabel("平台登录配置："))
        self.platform_profile_edit = QLineEdit(
            str(DEFAULT_PLATFORM_PROFILE)
        )
        platform_row.addWidget(self.platform_profile_edit, 1)
        layout.addLayout(platform_row)

        deepseek_row = QHBoxLayout()
        deepseek_row.addWidget(QLabel("DeepSeek 登录配置："))
        self.deepseek_profile_edit = QLineEdit(
            str(DEFAULT_DEEPSEEK_PROFILE)
        )
        deepseek_row.addWidget(self.deepseek_profile_edit, 1)
        layout.addLayout(deepseek_row)

        actions = QHBoxLayout()

        self.validate_button = QPushButton("1. 检查配置")
        self.validate_button.clicked.connect(self.validate_config)
        actions.addWidget(self.validate_button)

        self.ai_button = QPushButton("2. AI出题并准备")
        self.ai_button.clicked.connect(self.ai_prepare)
        actions.addWidget(self.ai_button)

        self.prepare_button = QPushButton("3. 创建考试草稿")
        self.prepare_button.clicked.connect(self.prepare_exam)
        actions.addWidget(self.prepare_button)

        self.publish_button = QPushButton("4. 发布考试")
        self.publish_button.clicked.connect(self.publish_exam)
        actions.addWidget(self.publish_button)

        layout.addLayout(actions)

        self.status_label = QLabel("请选择配置表后开始。")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.status_label, 1)

    def _config_path(self) -> Path:
        return Path(
            self.config_edit.text().strip()
        ).expanduser().resolve()

    def _platform_profile(self) -> Path:
        return Path(
            self.platform_profile_edit.text().strip()
        ).expanduser().resolve()

    def _deepseek_profile(self) -> Path:
        return Path(
            self.deepseek_profile_edit.text().strip()
        ).expanduser().resolve()

    def _set_busy(self, busy: bool, message: str) -> None:
        for button in (
            self.validate_button,
            self.ai_button,
            self.prepare_button,
            self.publish_button,
        ):
            button.setEnabled(not busy)

        self.status_label.setText(message)

        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

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

    def validate_config(self) -> None:
        try:
            config = load_config(
                self._config_path(),
                require_questions=False,
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "配置检查失败",
                f"{type(exc).__name__}: {exc}",
            )
            return

        QMessageBox.information(
            self,
            "配置检查通过",
            (
                f"考试：{config.exam_name}\n"
                f"课程：{config.course_name}\n"
                f"已有选题：{len(config.questions)} 道"
            ),
        )

    def ai_prepare(self) -> None:
        self._set_busy(
            True,
            "正在进行 AI 出题、人工审核、自动上传题库和最终配置生成……",
        )
        try:
            final_path = prepare_ai_exam_config(
                self._config_path(),
                deepseek_profile_dir=self._deepseek_profile(),
                platform_profile_dir=self._platform_profile(),
                platform_headless=False,
            )

            if final_path is None:
                self.status_label.setText("AI 出题/审核已取消。")
                return

            final_path = Path(final_path).resolve()
            self.config_edit.setText(str(final_path))

            QMessageBox.information(
                self,
                "AI准备完成",
                (
                    "AI题已通过本地审核并自动上传题库。\n\n"
                    f"最终配置表：\n{final_path}\n\n"
                    "当前没有创建或发布考试。"
                ),
            )
            self.status_label.setText(
                "AI准备完成，可以继续点击“创建考试草稿”。"
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "AI准备失败",
                f"{type(exc).__name__}: {exc}",
            )
            self.status_label.setText(
                "AI准备失败，未进入考试发布。"
            )
        finally:
            self._set_busy(False, self.status_label.text())

    def prepare_exam(self) -> None:
        self._set_busy(
            True,
            "正在创建/更新考试草稿。"
            "若 Edge 出现登录页面，请直接完成登录，程序会自动继续。",
        )
        try:
            args = build_backend_args(
                self._config_path(),
                self._platform_profile(),
                prepare=True,
            )
            run_configuration(args)

            QMessageBox.information(
                self,
                "草稿完成",
                "考试草稿已创建/更新。本步骤不会发布考试。",
            )
            self.status_label.setText("考试草稿已准备完成。")

        except Exception as exc:
            QMessageBox.critical(
                self,
                "创建草稿失败",
                f"{type(exc).__name__}: {exc}",
            )
            self.status_label.setText("创建草稿失败。")
        finally:
            self._set_busy(False, self.status_label.text())

    def _confirm_publish(self, config) -> bool:
        # 后端只在真正 publish_exam() 前调用这个回调。
        dialog = PublishConfirmationDialog(config, self)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def publish_exam(self) -> None:
        self._set_busy(
            True,
            "正在检查考试并准备发布。"
            "真正发布前会弹出人工确认窗口。",
        )
        try:
            args = build_backend_args(
                self._config_path(),
                self._platform_profile(),
                run=True,
                publish_exam=True,
            )

            run_configuration(
                args,
                publish_confirmer=self._confirm_publish,
            )

            self.status_label.setText(
                "发布流程已结束。若在确认窗口取消，考试仍保留为草稿。"
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "发布流程失败",
                f"{type(exc).__name__}: {exc}",
            )
            self.status_label.setText("发布流程失败/已停止。")
        finally:
            self._set_busy(False, self.status_label.text())


def main() -> int:
    app = QApplication.instance()
    owns_app = app is None

    if app is None:
        app = QApplication(sys.argv)

    window = ExamControlWindow()
    window.show()

    if owns_app:
        return app.exec()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

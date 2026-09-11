from __future__ import annotations

"""
AI 题目人工审核子程序。

依赖：
    pip install PySide6

配套模块：
    deepseek_question_generator.py

功能：
- 读取 generated_questions.json
- 左侧编辑 Markdown + LaTeX 源码
- 右侧 QWebEngineView + MathJax 实时预览
- 支持：
    通过
    保存修改
    重新生成当前题
    删除
    上一题 / 下一题
- 审核结果直接写回原 QuestionBatch JSON
- 首次人工修改前自动保存 AI 原始快照：
    <文件名>.ai-original.json
- 每次修改/审核操作追加审计记录：
    <文件名>.review-log.json
- 只有所有题目 review_status == "approved"
  且 validation_errors 为空时，才认为可上传题库。

主程序调用示例：

    from pathlib import Path
    from question_review_gui import review_question_batch

    approved = review_question_batch(
        Path("work/generated_questions.json")
    )

    if approved:
        print("全部题目已审核，可上传题库")

若需要“重新生成当前题”，传 DeepSeek 配置：

    from deepseek_question_generator import DeepSeekWebConfig

    approved = review_question_batch(
        Path("work/generated_questions.json"),
        deepseek_config=DeepSeekWebConfig(
            profile_dir=Path("work/edge-deepseek-profile")
        ),
    )
"""

import html
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from deepseek_question_generator import (
    DeepSeekWebConfig,
    DeepSeekWebGenerator,
    GeneratedQuestion,
    QuestionSpec,
    QuestionBatch,
    load_question_batch,
    save_question_batch,
    validate_question,
)
from exam_session import (
    create_session_from_batch,
    load_session,
    save_session,
    sync_review_from_batch,
)


# ============================================================================
# 审核状态
# ============================================================================

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

STATUS_TEXT = {
    STATUS_PENDING: "待审核",
    STATUS_APPROVED: "已通过",
    STATUS_REJECTED: "需处理",
}


def batch_is_fully_approved(batch: QuestionBatch) -> bool:
    """
    主程序上传题库前必须再次调用。
    GUI 中按钮状态不能替代业务层检查。
    """
    if not batch.questions:
        return False

    # 删除或其他审核操作不能让实际题数低于/高于最初的出题要求。
    # 只有题目数量完整时，整批题目才允许进入上传阶段。
    if len(batch.questions) != batch.expected_question_count():
        return False

    for q in batch.questions:
        errors = validate_question(q)
        if errors:
            return False
        if q.review_status != STATUS_APPROVED:
            return False

    return True


def question_batch_file_is_approved(path: Path) -> bool:
    return batch_is_fully_approved(load_question_batch(Path(path)))


def initialize_generated_session(
    batch_path: Path,
    session_path: Path,
    exam_name: str,
) -> None:
    """Create the current Session immediately after DeepSeek writes the batch."""
    batch = load_question_batch(Path(batch_path))
    session = create_session_from_batch(exam_name, batch)
    save_session(session, Path(session_path))


def update_review_session(batch_path: Path, session_path: Path) -> bool:
    """Persist per-question review results and advance only after full approval."""
    batch = load_question_batch(Path(batch_path))
    session = load_session(Path(session_path))
    approved = sync_review_from_batch(session, batch)
    save_session(session, Path(session_path))
    return approved


# ============================================================================
# Markdown + LaTeX 预览
# ============================================================================

MATHJAX_URL = (
    "https://cdn.jsdelivr.net/npm/mathjax@3/"
    "es5/tex-mml-chtml.js"
)


def _protect_math(text: str) -> tuple[str, dict[str, str]]:
    """
    保护 \\(...\\) 与 \\[...\\]，避免普通文本 HTML 转义破坏公式。
    """
    tokens: dict[str, str] = {}

    pattern = re.compile(
        r"(\\\[(?:.|\n)*?\\\]|\\\((?:.|\n)*?\\\))",
        re.DOTALL,
    )

    def repl(match: re.Match[str]) -> str:
        key = f"@@MATH_{len(tokens)}@@"
        tokens[key] = match.group(0)
        return key

    return pattern.sub(repl, text), tokens


def _simple_markdown_to_html(text: str) -> str:
    """
    轻量级 Markdown 渲染。
    主要目标是题目审核，不追求完整 CommonMark。
    LaTeX 先保护，再交给 MathJax。
    """
    protected, math_tokens = _protect_math(text)

    escaped = html.escape(protected)

    # 基本 Markdown
    escaped = re.sub(
        r"\*\*(.+?)\*\*",
        r"<strong>\1</strong>",
        escaped,
        flags=re.DOTALL,
    )
    escaped = re.sub(
        r"(?<!\*)\*([^*\n]+)\*(?!\*)",
        r"<em>\1</em>",
        escaped,
    )
    escaped = re.sub(
        r"`([^`\n]+)`",
        r"<code>\1</code>",
        escaped,
    )

    lines = escaped.splitlines()
    out: list[str] = []
    in_ul = False
    in_ol = False

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            close_lists()
            out.append('<div class="spacer"></div>')
            continue

        if stripped.startswith("### "):
            close_lists()
            out.append(f"<h3>{stripped[4:]}</h3>")
            continue

        if stripped.startswith("## "):
            close_lists()
            out.append(f"<h2>{stripped[3:]}</h2>")
            continue

        if stripped.startswith("# "):
            close_lists()
            out.append(f"<h1>{stripped[2:]}</h1>")
            continue

        ul_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if ul_match:
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{ul_match.group(1)}</li>")
            continue

        ol_match = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if ol_match:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{ol_match.group(1)}</li>")
            continue

        close_lists()
        out.append(f"<p>{stripped}</p>")

    close_lists()

    rendered = "\n".join(out)

    for key, latex in math_tokens.items():
        rendered = rendered.replace(key, latex)

    return rendered


def build_question_preview_html(q: GeneratedQuestion) -> str:
    stem = _simple_markdown_to_html(q.stem)
    explanation = _simple_markdown_to_html(q.explanation)

    options_html = ""
    if q.options:
        rows = []
        for key in sorted(q.options):
            option = _simple_markdown_to_html(q.options[key])
            rows.append(
                f"""
                <div class="option">
                  <span class="option-key">{html.escape(key)}.</span>
                  <div class="option-body">{option}</div>
                </div>
                """
            )
        options_html = "\n".join(rows)

    errors_html = ""
    if q.validation_errors:
        errors_html = """
        <div class="validation-errors">
          <div class="section-title">自动校验问题</div>
          <ul>
        """ + "".join(
            f"<li>{html.escape(err)}</li>"
            for err in q.validation_errors
        ) + """
          </ul>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<script>
MathJax = {{
  tex: {{
    inlineMath: [['\\\\(', '\\\\)']],
    displayMath: [['\\\\[', '\\\\]']],
    processEscapes: true
  }},
  options: {{
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  }}
}};
</script>
<script defer src="{MATHJAX_URL}"></script>
<style>
:root {{
  font-family:
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    Arial,
    sans-serif;
}}
body {{
  margin: 0;
  padding: 24px 30px 50px;
  line-height: 1.75;
  font-size: 17px;
  max-width: 920px;
}}
.meta {{
  opacity: .72;
  font-size: 13px;
  margin-bottom: 20px;
}}
.question {{
  font-size: 18px;
  margin-bottom: 18px;
}}
.option {{
  display: flex;
  gap: 10px;
  margin: 10px 0;
}}
.option-key {{
  font-weight: 700;
  min-width: 26px;
}}
.option-body p {{
  margin: 0;
}}
.section {{
  margin-top: 28px;
  padding-top: 16px;
  border-top: 1px solid rgba(128,128,128,.3);
}}
.section-title {{
  font-weight: 700;
  margin-bottom: 8px;
}}
.answer {{
  font-weight: 700;
}}
.validation-errors {{
  margin-top: 28px;
  padding: 14px 18px;
  border: 1px solid #c55;
  border-radius: 8px;
}}
p {{
  margin: 7px 0;
}}
.spacer {{
  height: 6px;
}}
code {{
  font-family: Consolas, monospace;
}}
</style>
</head>
<body>
<div class="meta">
  {html.escape(q.local_id)}
  · {html.escape(q.chapter)}
  · {html.escape(q.knowledge_point)}
  · {html.escape(q.difficulty)}
  · {html.escape(q.question_type)}
  · {html.escape(str(q.score))} 分
</div>

<div class="question">
{stem}
</div>

<div>
{options_html}
</div>

<div class="section">
  <div class="section-title">参考答案</div>
  <div class="answer">{_simple_markdown_to_html(q.answer)}</div>
</div>

<div class="section">
  <div class="section-title">解析</div>
  {explanation}
</div>

{errors_html}
</body>
</html>"""


# ============================================================================
# 审计记录
# ============================================================================

def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(
        prefix=path.stem + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                data,
                stream,
                ensure_ascii=False,
                indent=2,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class ReviewAudit:
    def __init__(self, batch_path: Path):
        self.batch_path = Path(batch_path)
        self.original_path = self.batch_path.with_name(
            self.batch_path.stem
            + ".ai-original"
            + self.batch_path.suffix
        )
        self.log_path = self.batch_path.with_name(
            self.batch_path.stem
            + ".review-log.json"
        )

    def ensure_original_snapshot(self) -> None:
        if not self.original_path.exists():
            shutil.copy2(
                self.batch_path,
                self.original_path,
            )

    def append(
        self,
        action: str,
        question: GeneratedQuestion | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.log_path.exists():
            try:
                data = json.loads(
                    self.log_path.read_text(
                        encoding="utf-8"
                    )
                )
            except Exception:
                data = {"version": 1, "events": []}
        else:
            data = {"version": 1, "events": []}

        event = {
            "time": datetime.now(
                timezone.utc
            ).isoformat(),
            "action": action,
        }

        if question is not None:
            event["local_id"] = question.local_id
            event["review_status"] = (
                question.review_status
            )

        if details:
            event["details"] = details

        data.setdefault("events", []).append(event)
        _atomic_write_json(self.log_path, data)


# ============================================================================
# 重生成反馈框
# ============================================================================

class RegenerateDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("重新生成当前题")
        self.resize(560, 300)

        layout = QVBoxLayout(self)

        label = QLabel(
            "可以填写修改意见；留空则按原知识点、"
            "题型和难度重新设计。"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self.feedback = QPlainTextEdit()
        self.feedback.setPlaceholderText(
            "例如：题目太简单，请提高计算难度，"
            "但不要涉及傅里叶变换。"
        )
        layout.addWidget(self.feedback)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.feedback.toPlainText().strip()


# ============================================================================
# AI 出题要求窗口
# ============================================================================

class QuestionGenerationDialog(QDialog):
    """
    在 GUI 中填写 AI 出题要求。

    点击“生成题目并进入审核”后：
        QuestionSpec
        -> DeepSeekWebGenerator
        -> generated_questions.json
        -> 审核窗口

    生成结果仍然全部是 pending，不会因为生成成功而自动批准。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        default_profile_dir: Path | None = None,
        default_output_path: Path | None = None,
        default_headless: bool = False,
    ):
        super().__init__(parent)

        cwd = Path.cwd()

        self.generated_path: Path | None = None
        self.generated_deepseek_config: DeepSeekWebConfig | None = None
        self.specs: list[QuestionSpec] = []

        self.setWindowTitle("AI 出题要求")
        self.resize(840, 680)
        self.setMinimumSize(620, 480)
        self.setSizeGripEnabled(True)

        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)

        intro = QLabel(
            "填写出题要求后，程序会通过 DeepSeek 网页生成题目。"
            "生成完成后会自动进入人工审核；"
            "全部审核通过后会自动逐题上传课程题库。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()

        self.course_edit = QLineEdit("信号与系统")
        self.chapter_edit = QLineEdit()
        self.chapter_edit.setPlaceholderText(
            "例如：第二章"
        )

        self.knowledge_edit = QLineEdit()
        self.knowledge_edit.setPlaceholderText(
            "例如：连续时间卷积积分"
        )

        self.type_combo = QComboBox()
        self.type_combo.addItem("单选题", "single_choice")
        self.type_combo.addItem("多选题", "multiple_choice")
        self.type_combo.addItem("判断题", "true_false")
        self.type_combo.addItem("简答题", "short_answer")
        self.type_combo.addItem("计算题", "calculation")

        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItem("简单", "easy")
        self.difficulty_combo.addItem("中等", "medium")
        self.difficulty_combo.addItem("困难", "hard")
        self.difficulty_combo.setCurrentIndex(1)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 100)
        self.count_spin.setValue(2)

        self.score_spin = QDoubleSpinBox()
        self.score_spin.setRange(0.1, 1000.0)
        self.score_spin.setDecimals(1)
        self.score_spin.setSingleStep(0.5)
        self.score_spin.setValue(5.0)

        form.addRow("课程", self.course_edit)
        form.addRow("章节", self.chapter_edit)
        form.addRow("知识点", self.knowledge_edit)
        form.addRow("题型", self.type_combo)
        form.addRow("难度", self.difficulty_combo)
        form.addRow("数量", self.count_spin)
        form.addRow("每题分值", self.score_spin)

        layout.addLayout(form)

        layout.addWidget(QLabel("额外出题要求"))
        self.requirements_edit = QPlainTextEdit()
        self.requirements_edit.setPlaceholderText(
            "例如：\n"
            "1. 题干和解析必须包含积分、分段函数或求和公式；\n"
            "2. 不要涉及傅里叶变换；\n"
            "3. 至少一道题需要计算。\n\n"
            "公式格式无需在这里重复要求，程序 Prompt 会自动规定 "
            "Markdown + LaTeX 格式。"
        )
        self.requirements_edit.setMinimumHeight(80)
        layout.addWidget(self.requirements_edit)

        plan_buttons = QHBoxLayout()
        self.add_spec_button = QPushButton("加入出题清单")
        self.remove_spec_button = QPushButton("移除选中组")
        self.add_spec_button.clicked.connect(self._add_current_spec)
        self.remove_spec_button.clicked.connect(self._remove_selected_specs)
        plan_buttons.addWidget(self.add_spec_button)
        plan_buttons.addWidget(self.remove_spec_button)
        plan_buttons.addStretch(1)
        layout.addLayout(plan_buttons)

        self.spec_table = QTableWidget(0, 7)
        self.spec_table.setHorizontalHeaderLabels(
            ["章节", "知识点", "题型", "难度", "数量", "分值", "补充要求"]
        )
        self.spec_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.spec_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.spec_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.spec_table.horizontalHeader().setStretchLastSection(True)
        self.spec_table.setMinimumHeight(120)
        layout.addWidget(self.spec_table)

        # DeepSeek profile
        profile_row = QHBoxLayout()
        self.profile_edit = QLineEdit(
            str(
                (
                    default_profile_dir
                    or (cwd / "work" / "edge-deepseek-profile")
                ).resolve()
            )
        )
        profile_browse = QPushButton("选择目录")
        profile_browse.clicked.connect(
            self._choose_profile_dir
        )
        profile_row.addWidget(self.profile_edit, 1)
        profile_row.addWidget(profile_browse)

        profile_container = QWidget()
        profile_container.setLayout(profile_row)

        # 输出 JSON
        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(
            str(
                (
                    default_output_path
                    or (cwd / "work" / "generated_questions.json")
                ).resolve()
            )
        )
        output_browse = QPushButton("选择文件")
        output_browse.clicked.connect(
            self._choose_output_file
        )
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_browse)

        output_container = QWidget()
        output_container.setLayout(output_row)

        paths_form = QFormLayout()
        paths_form.addRow(
            "DeepSeek 浏览器配置",
            profile_container,
        )
        paths_form.addRow(
            "生成结果 JSON",
            output_container,
        )
        layout.addLayout(paths_form)

        self.headless_checkbox = QCheckBox(
            "后台运行 DeepSeek 和教学平台网页（登录失效时自动弹出）"
        )
        self.headless_checkbox.setChecked(bool(default_headless))
        layout.addWidget(self.headless_checkbox)

        self.status_label = QLabel(
            "准备就绪。第一次使用请保持可见浏览器，以便手动登录 DeepSeek。"
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        self.cancel_button = QPushButton("取消")
        self.generate_button = QPushButton(
            "生成题目并进入审核"
        )

        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.generate_button)
        outer_layout.addLayout(buttons)

        self.cancel_button.clicked.connect(
            self.reject
        )
        self.generate_button.clicked.connect(
            self._generate
        )

    def _choose_profile_dir(self) -> None:
        current = self.profile_edit.text().strip()
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 DeepSeek Edge Profile 目录",
            current or str(Path.cwd()),
        )
        if selected:
            self.profile_edit.setText(selected)

    def _choose_output_file(self) -> None:
        current = Path(
            self.output_edit.text().strip()
            or "generated_questions.json"
        )
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "保存 AI 题目",
            str(current),
            "JSON 文件 (*.json)",
        )
        if selected:
            if not selected.lower().endswith(".json"):
                selected += ".json"
            self.output_edit.setText(selected)

    def _spec_labels(self, spec: QuestionSpec) -> list[str]:
        type_index = self.type_combo.findData(spec.question_type)
        difficulty_index = self.difficulty_combo.findData(spec.difficulty)
        type_label = (
            self.type_combo.itemText(type_index)
            if type_index >= 0
            else spec.question_type
        )
        difficulty_label = (
            self.difficulty_combo.itemText(difficulty_index)
            if difficulty_index >= 0
            else spec.difficulty
        )
        return [
            spec.chapter,
            spec.knowledge_point,
            type_label,
            difficulty_label,
            str(spec.count),
            str(spec.normalized_score()),
            spec.requirements,
        ]

    def _refresh_spec_table(self) -> None:
        self.spec_table.setRowCount(len(self.specs))
        for row, spec in enumerate(self.specs):
            for column, value in enumerate(self._spec_labels(spec)):
                self.spec_table.setItem(row, column, QTableWidgetItem(value))

    def _add_current_spec(self) -> None:
        try:
            spec = self._build_spec()
        except Exception as exc:
            QMessageBox.warning(self, "无法加入出题清单", str(exc))
            return
        self.specs.append(spec)
        self._refresh_spec_table()
        self.chapter_edit.clear()
        self.knowledge_edit.clear()
        self.requirements_edit.clear()
        self.status_label.setText(
            f"已加入 {len(self.specs)} 组，共 "
            f"{sum(item.count for item in self.specs)} 道题。"
        )

    def _remove_selected_specs(self) -> None:
        rows = sorted(
            {index.row() for index in self.spec_table.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in rows:
            del self.specs[row]
        self._refresh_spec_table()

    def _build_specs(self) -> list[QuestionSpec]:
        # Single-group use stays one click: if no row was explicitly added,
        # the values currently shown in the form are used directly.
        if not self.specs:
            return [self._build_spec()]
        if (
            self.chapter_edit.text().strip()
            or self.knowledge_edit.text().strip()
            or self.requirements_edit.toPlainText().strip()
        ):
            # The last group may be left in the form and submitted directly;
            # earlier groups remain visible in the list for review.
            return [*self.specs, self._build_spec()]
        return list(self.specs)

    def _build_spec(self) -> QuestionSpec:
        course = self.course_edit.text().strip()
        chapter = self.chapter_edit.text().strip()
        knowledge = self.knowledge_edit.text().strip()

        if not course:
            raise ValueError("请填写课程名称。")
        if not chapter:
            raise ValueError("请填写章节。")
        if not knowledge:
            raise ValueError("请填写知识点。")

        return QuestionSpec(
            course_name=course,
            chapter=chapter,
            knowledge_point=knowledge,
            question_type=str(
                self.type_combo.currentData()
            ),
            difficulty=str(
                self.difficulty_combo.currentData()
            ),
            count=self.count_spin.value(),
            score=str(self.score_spin.value()),
            requirements=(
                self.requirements_edit
                .toPlainText()
                .strip()
            ),
        )

    def _generate(self) -> None:
        try:
            specs = self._build_specs()

            profile_text = self.profile_edit.text().strip()
            output_text = self.output_edit.text().strip()

            if not profile_text:
                raise ValueError(
                    "请选择 DeepSeek 浏览器 Profile 目录。"
                )
            if not output_text:
                raise ValueError(
                    "请选择生成结果 JSON 文件。"
                )

            profile_dir = Path(profile_text).resolve()
            output_path = Path(output_text).resolve()

            if output_path.suffix.lower() != ".json":
                output_path = output_path.with_suffix(".json")
                self.output_edit.setText(
                    str(output_path)
                )

            config = DeepSeekWebConfig(
                profile_dir=profile_dir,
                headless=self.headless_checkbox.isChecked(),
            )

            self.generate_button.setEnabled(False)
            self.cancel_button.setEnabled(False)
            self.add_spec_button.setEnabled(False)
            self.remove_spec_button.setEnabled(False)
            self.status_label.setText(
                "正在调用 DeepSeek 生成题目。"
                "如果浏览器要求登录或人机验证，请在 Edge 中手动完成。"
            )
            QApplication.processEvents()

            def ui_log(message: str) -> None:
                self.status_label.setText(message)
                QApplication.processEvents()

            with DeepSeekWebGenerator(
                config,
                logger=ui_log,
            ) as generator:
                batch = generator.generate_question_groups(
                    specs,
                    output_path=output_path,
                )

            bad_count = sum(
                bool(q.validation_errors)
                for q in batch.questions
            )

            self.generated_path = output_path
            self.generated_deepseek_config = config

            QMessageBox.information(
                self,
                "生成完成",
                f"已生成 {len(batch.questions)} 道题。\n"
                f"自动结构校验异常：{bad_count} 道。\n\n"
                "接下来进入人工审核。"
            )
            self.accept()

        except Exception as exc:
            self.status_label.setText(
                "生成失败，请检查错误信息。"
            )
            QMessageBox.critical(
                self,
                "AI 出题失败",
                f"{type(exc).__name__}: {exc}",
            )

        finally:
            self.generate_button.setEnabled(True)
            self.cancel_button.setEnabled(True)
            self.add_spec_button.setEnabled(True)
            self.remove_spec_button.setEnabled(True)


# ============================================================================
# 主审核窗口
# ============================================================================

class QuestionReviewWindow(QMainWindow):
    def __init__(
        self,
        batch_path: Path,
        *,
        deepseek_config: DeepSeekWebConfig | None = None,
    ):
        super().__init__()

        self.batch_path = Path(batch_path).resolve()
        self.deepseek_config = deepseek_config

        self.batch = load_question_batch(self.batch_path)
        self.audit = ReviewAudit(self.batch_path)

        self.current_index = 0
        self.dirty = False
        self._loading_form = False

        self.setWindowTitle(
            f"AI 题目审核 - {self.batch_path.name}"
        )
        self.resize(1450, 900)

        self._build_ui()
        self._build_menu()
        self._refresh_list()

        if self.batch.questions:
            self.load_question(0)
        else:
            self._disable_editor()

        self.statusBar().showMessage(
            "AI 题目未经人工通过，不得上传题库。"
        )

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        top = QHBoxLayout()

        self.batch_label = QLabel()
        top.addWidget(self.batch_label)

        top.addStretch(1)

        self.summary_label = QLabel()
        top.addWidget(self.summary_label)

        outer.addLayout(top)

        main_split = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(main_split, 1)

        # 左侧题目列表
        list_panel = QWidget()
        list_layout = QVBoxLayout(list_panel)

        list_layout.addWidget(QLabel("题目列表"))

        self.question_list = QListWidget()
        self.question_list.currentRowChanged.connect(
            self._on_list_changed
        )
        list_layout.addWidget(self.question_list, 1)

        main_split.addWidget(list_panel)

        # 中间编辑区
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)

        meta_form = QFormLayout()

        self.chapter_edit = QLineEdit()
        self.knowledge_edit = QLineEdit()

        self.type_edit = QLineEdit()
        self.difficulty_edit = QLineEdit()

        self.score_edit = QLineEdit()
        self.answer_edit = QLineEdit()

        meta_form.addRow("章节", self.chapter_edit)
        meta_form.addRow(
            "知识点",
            self.knowledge_edit,
        )
        meta_form.addRow("题型", self.type_edit)
        meta_form.addRow(
            "难度",
            self.difficulty_edit,
        )
        meta_form.addRow("分值", self.score_edit)
        meta_form.addRow("答案", self.answer_edit)

        editor_layout.addLayout(meta_form)

        editor_layout.addWidget(
            QLabel("题干（Markdown + LaTeX）")
        )
        self.stem_edit = QPlainTextEdit()
        editor_layout.addWidget(self.stem_edit, 2)

        editor_layout.addWidget(
            QLabel("选项（单选题）")
        )

        self.option_edits: dict[str, QPlainTextEdit] = {}
        for key in ("A", "B", "C", "D"):
            row = QHBoxLayout()
            label = QLabel(f"{key}.")
            label.setFixedWidth(24)
            field = QPlainTextEdit()
            field.setMaximumHeight(70)
            self.option_edits[key] = field
            row.addWidget(label)
            row.addWidget(field)
            editor_layout.addLayout(row)

        editor_layout.addWidget(
            QLabel("解析（Markdown + LaTeX）")
        )
        self.explanation_edit = QPlainTextEdit()
        editor_layout.addWidget(
            self.explanation_edit,
            2,
        )

        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        editor_layout.addWidget(self.validation_label)

        actions = QHBoxLayout()

        self.prev_button = QPushButton("上一题")
        self.next_button = QPushButton("下一题")

        self.save_button = QPushButton("保存修改")
        self.approve_button = QPushButton("通过")
        self.regenerate_button = QPushButton(
            "重新生成"
        )
        self.delete_button = QPushButton("删除")

        actions.addWidget(self.prev_button)
        actions.addWidget(self.next_button)
        actions.addStretch(1)
        actions.addWidget(self.save_button)
        actions.addWidget(self.regenerate_button)
        actions.addWidget(self.delete_button)
        actions.addWidget(self.approve_button)

        editor_layout.addLayout(actions)

        main_split.addWidget(editor)

        # 右侧预览
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)

        preview_head = QHBoxLayout()
        preview_head.addWidget(
            QLabel("最终效果预览")
        )

        preview_head.addStretch(1)

        self.auto_preview = QCheckBox("实时预览")
        self.auto_preview.setChecked(True)
        preview_head.addWidget(self.auto_preview)

        self.preview_button = QPushButton(
            "立即刷新"
        )
        preview_head.addWidget(self.preview_button)

        preview_layout.addLayout(preview_head)

        self.preview = QWebEngineView()
        preview_layout.addWidget(self.preview, 1)

        main_split.addWidget(preview_panel)

        main_split.setSizes([260, 610, 580])

        self.setStatusBar(QStatusBar())

        # 信号
        self.prev_button.clicked.connect(
            self.go_previous
        )
        self.next_button.clicked.connect(
            self.go_next
        )
        self.save_button.clicked.connect(
            self.save_current_changes
        )
        self.approve_button.clicked.connect(
            self.approve_current
        )
        self.regenerate_button.clicked.connect(
            self.regenerate_current
        )
        self.delete_button.clicked.connect(
            self.delete_current
        )
        self.preview_button.clicked.connect(
            self.refresh_preview
        )

        all_edits = [
            self.chapter_edit,
            self.knowledge_edit,
            self.type_edit,
            self.difficulty_edit,
            self.score_edit,
            self.answer_edit,
        ]
        for field in all_edits:
            field.textChanged.connect(
                self._form_changed
            )

        for field in [
            self.stem_edit,
            self.explanation_edit,
            *self.option_edits.values(),
        ]:
            field.textChanged.connect(
                self._form_changed
            )

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(300)
        self.preview_timer.timeout.connect(
            self.refresh_preview
        )

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")

        save_action = QAction(
            "保存当前修改",
            self,
        )
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(
            self.save_current_changes
        )
        file_menu.addAction(save_action)

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    # ------------------------------------------------------------------
    # 列表与加载
    # ------------------------------------------------------------------

    def _status_symbol(
        self,
        q: GeneratedQuestion,
    ) -> str:
        errors = validate_question(q)

        if errors:
            return "!"

        if q.review_status == STATUS_APPROVED:
            return "✓"

        if q.review_status == STATUS_REJECTED:
            return "×"

        return "○"

    def _refresh_list(self) -> None:
        selected = self.current_index

        self.question_list.blockSignals(True)
        self.question_list.clear()

        for i, q in enumerate(self.batch.questions):
            symbol = self._status_symbol(q)
            label = (
                f"{symbol} {q.local_id} "
                f"{q.knowledge_point}"
            )
            item = QListWidgetItem(label)
            item.setToolTip(
                f"{STATUS_TEXT.get(q.review_status, q.review_status)}"
            )
            self.question_list.addItem(item)

        self.question_list.blockSignals(False)

        count = len(self.batch.questions)
        approved = sum(
            q.review_status == STATUS_APPROVED
            and not validate_question(q)
            for q in self.batch.questions
        )
        pending = count - approved

        self.batch_label.setText(
            f"批次：{self.batch.batch_id}"
        )
        self.summary_label.setText(
            f"共 {count} 题 · "
            f"已通过 {approved} · "
            f"未完成 {pending}"
        )

        if count:
            self.current_index = max(
                0,
                min(selected, count - 1),
            )
            self.question_list.setCurrentRow(
                self.current_index
            )

    def _disable_editor(self) -> None:
        for widget in (
            self.chapter_edit,
            self.knowledge_edit,
            self.type_edit,
            self.difficulty_edit,
            self.score_edit,
            self.answer_edit,
            self.stem_edit,
            self.explanation_edit,
            *self.option_edits.values(),
            self.save_button,
            self.approve_button,
            self.regenerate_button,
            self.delete_button,
        ):
            widget.setEnabled(False)

        self.preview.setHtml(
            "<h2>当前没有题目。</h2>"
        )

    def load_question(self, index: int) -> None:
        if not self.batch.questions:
            self._disable_editor()
            return

        if not 0 <= index < len(self.batch.questions):
            return

        self._loading_form = True
        try:
            self.current_index = index
            q = self.batch.questions[index]

            self.chapter_edit.setText(q.chapter)
            self.knowledge_edit.setText(
                q.knowledge_point
            )
            self.type_edit.setText(
                q.question_type
            )
            self.difficulty_edit.setText(
                q.difficulty
            )
            self.score_edit.setText(str(q.score))
            self.answer_edit.setText(q.answer)

            self.stem_edit.setPlainText(q.stem)
            self.explanation_edit.setPlainText(
                q.explanation
            )

            for key, field in self.option_edits.items():
                field.setPlainText(
                    (q.options or {}).get(key, "")
                )

            self.dirty = False
            self.refresh_preview()
            self._update_validation(q)

            self.prev_button.setEnabled(
                index > 0
            )
            self.next_button.setEnabled(
                index < len(self.batch.questions) - 1
            )

            self.question_list.blockSignals(True)
            self.question_list.setCurrentRow(index)
            self.question_list.blockSignals(False)

        finally:
            self._loading_form = False

    def _on_list_changed(self, row: int) -> None:
        if row < 0:
            return

        if row == self.current_index:
            return

        if not self._confirm_discard_or_save():
            self.question_list.blockSignals(True)
            self.question_list.setCurrentRow(
                self.current_index
            )
            self.question_list.blockSignals(False)
            return

        self.load_question(row)

    # ------------------------------------------------------------------
    # 表单
    # ------------------------------------------------------------------

    def _form_changed(self) -> None:
        if self._loading_form:
            return

        self.dirty = True

        if self.auto_preview.isChecked():
            self.preview_timer.start()

    def _question_from_form(
        self,
    ) -> GeneratedQuestion:
        original = self.batch.questions[
            self.current_index
        ]

        try:
            from decimal import Decimal
            score = Decimal(
                self.score_edit.text().strip()
            )
        except Exception:
            # 先允许构造，再由 validation 显示问题。
            from decimal import Decimal
            score = Decimal("0")

        option_values = {
            key: field.toPlainText().strip()
            for key, field
            in self.option_edits.items()
        }

        has_any_option = any(
            option_values.values()
        )

        options = (
            option_values
            if has_any_option
            else None
        )

        q = GeneratedQuestion(
            local_id=original.local_id,
            question_type=self.type_edit.text().strip(),
            chapter=self.chapter_edit.text().strip(),
            knowledge_point=self.knowledge_edit.text().strip(),
            difficulty=self.difficulty_edit.text().strip(),
            stem=self.stem_edit.toPlainText().strip(),
            options=options,
            answer=self.answer_edit.text().strip(),
            explanation=self.explanation_edit.toPlainText().strip(),
            score=score,
            content_format=original.content_format,
            review_status=original.review_status,
            edited_by_user=original.edited_by_user,
            platform_question_id=original.platform_question_id,
        )

        q.validation_errors = validate_question(q)
        return q

    def _update_validation(
        self,
        q: GeneratedQuestion,
    ) -> None:
        errors = validate_question(q)
        q.validation_errors = errors

        if errors:
            self.validation_label.setText(
                "自动校验：\n• "
                + "\n• ".join(errors)
            )
            self.approve_button.setEnabled(False)
        else:
            self.validation_label.setText(
                "自动校验：通过。仍需人工确认内容正确性。"
            )
            self.approve_button.setEnabled(True)

    def refresh_preview(self) -> None:
        if not self.batch.questions:
            return

        q = self._question_from_form()
        self._update_validation(q)
        self.preview.setHtml(
            build_question_preview_html(q)
        )

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _save_batch(self) -> None:
        save_question_batch(
            self.batch_path,
            self.batch,
        )

    def save_current_changes(self) -> bool:
        if not self.batch.questions:
            return False

        old = self.batch.questions[
            self.current_index
        ]
        new = self._question_from_form()

        changed = (
            old.to_dict() != new.to_dict()
        )

        if changed:
            self.audit.ensure_original_snapshot()
            new.edited_by_user = True

            # 任何内容修改都使原批准失效。
            new.review_status = STATUS_PENDING

            self.batch.questions[
                self.current_index
            ] = new

            self.audit.append(
                "edit_question",
                new,
            )

        self._save_batch()
        self.dirty = False
        self._refresh_list()
        self.load_question(self.current_index)

        self.statusBar().showMessage(
            "当前题目修改已保存。",
            4000,
        )
        return True

    def approve_current(self) -> None:
        if not self.batch.questions:
            return

        q = self._question_from_form()
        errors = validate_question(q)

        if errors:
            QMessageBox.warning(
                self,
                "不能通过",
                "自动校验仍有问题：\n\n"
                + "\n".join(
                    f"• {x}"
                    for x in errors
                ),
            )
            return

        self.audit.ensure_original_snapshot()

        old = self.batch.questions[
            self.current_index
        ]
        if old.to_dict() != q.to_dict():
            q.edited_by_user = True

        q.review_status = STATUS_APPROVED
        q.validation_errors = []

        self.batch.questions[
            self.current_index
        ] = q

        self.audit.append(
            "approve_question",
            q,
        )
        self._save_batch()

        self.dirty = False
        self._refresh_list()
        self.load_question(self.current_index)

        if batch_is_fully_approved(self.batch):
            self.statusBar().showMessage(
                "全部审核通过，正在进入题库上传阶段。",
                2000,
            )
            # The caller continues directly into one-by-one batch upload.
            # No extra approval dialog or manual window close is required.
            self.close()
        else:
            self.statusBar().showMessage(
                f"{q.local_id} 已通过。",
                4000,
            )

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------

    def _confirm_discard_or_save(self) -> bool:
        if not self.dirty:
            return True

        box = QMessageBox(self)
        box.setWindowTitle("存在未保存修改")
        box.setText(
            "当前题目有未保存修改。"
        )
        save = box.addButton(
            "保存",
            QMessageBox.ButtonRole.AcceptRole,
        )
        discard = box.addButton(
            "放弃修改",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel = box.addButton(
            "取消",
            QMessageBox.ButtonRole.RejectRole,
        )

        box.exec()

        clicked = box.clickedButton()

        if clicked == save:
            return self.save_current_changes()

        if clicked == discard:
            self.dirty = False
            return True

        if clicked == cancel:
            return False

        return False

    def go_previous(self) -> None:
        if self.current_index <= 0:
            return
        if not self._confirm_discard_or_save():
            return
        self.load_question(self.current_index - 1)

    def go_next(self) -> None:
        if self.current_index >= (
            len(self.batch.questions) - 1
        ):
            return
        if not self._confirm_discard_or_save():
            return
        self.load_question(self.current_index + 1)

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------

    def delete_current(self) -> None:
        if not self.batch.questions:
            return

        q = self.batch.questions[
            self.current_index
        ]

        result = QMessageBox.question(
            self,
            "删除题目",
            f"确定删除 {q.local_id} 吗？\n\n"
            "删除后题目数量会减少；"
            "后续可以调用 DeepSeek 补生成。",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
        )

        if result != QMessageBox.StandardButton.Yes:
            return

        self.audit.ensure_original_snapshot()
        self.audit.append(
            "delete_question",
            q,
        )

        del self.batch.questions[
            self.current_index
        ]
        self._save_batch()

        self.dirty = False

        if self.batch.questions:
            self.current_index = min(
                self.current_index,
                len(self.batch.questions) - 1,
            )
            self._refresh_list()
            self.load_question(self.current_index)
        else:
            self._refresh_list()
            self._disable_editor()

    # ------------------------------------------------------------------
    # DeepSeek 单题重新生成
    # ------------------------------------------------------------------

    def regenerate_current(self) -> None:
        if not self.batch.questions:
            return

        if self.deepseek_config is None:
            QMessageBox.warning(
                self,
                "未配置 DeepSeek",
                "当前审核窗口没有收到 DeepSeekWebConfig，"
                "因此不能执行单题重新生成。",
            )
            return

        if self.dirty:
            result = QMessageBox.question(
                self,
                "未保存修改",
                "重新生成会基于当前已保存版本。"
                "是否先保存当前修改？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )

            if result == QMessageBox.StandardButton.Cancel:
                return

            if result == QMessageBox.StandardButton.Yes:
                if not self.save_current_changes():
                    return

        dialog = RegenerateDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        feedback = dialog.text()
        original = self.batch.questions[
            self.current_index
        ]

        self.statusBar().showMessage(
            f"正在重新生成 {original.local_id}..."
        )
        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor
        )
        self.regenerate_button.setEnabled(False)

        try:
            self.audit.ensure_original_snapshot()

            # 第一版同步执行，保持实现简单。
            # 后续统一 GUI 时可迁移到 QThread。
            with DeepSeekWebGenerator(
                self.deepseek_config,
            ) as generator:
                new_q = generator.regenerate_question(
                    original,
                    feedback=feedback,
                )

            new_q.review_status = STATUS_PENDING
            new_q.platform_question_id = None

            self.batch.questions[
                self.current_index
            ] = new_q

            self.audit.append(
                "regenerate_question",
                new_q,
                details={
                    "feedback": feedback,
                },
            )

            self._save_batch()

            self.dirty = False
            self._refresh_list()
            self.load_question(self.current_index)

            self.statusBar().showMessage(
                f"{new_q.local_id} 已重新生成，"
                "请重新人工审核。",
                6000,
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "重新生成失败",
                f"{type(exc).__name__}: {exc}",
            )

        finally:
            QApplication.restoreOverrideCursor()
            self.regenerate_button.setEnabled(True)

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def closeEvent(
        self,
        event: QCloseEvent,
    ) -> None:
        if not self._confirm_discard_or_save():
            event.ignore()
            return

        self._save_batch()

        if batch_is_fully_approved(self.batch):
            self.audit.append(
                "review_session_closed",
                details={
                    "fully_approved": True,
                },
            )
        else:
            self.audit.append(
                "review_session_closed",
                details={
                    "fully_approved": False,
                },
            )

        event.accept()


# ============================================================================
# 主程序调用接口
# ============================================================================

def _run_review_window_blocking(
    window: QuestionReviewWindow,
) -> None:
    """
    为 QMainWindow 提供类似 QDialog.exec() 的同步等待。

    这里只创建局部 QEventLoop，不创建第二个 QApplication。
    因此既可独立运行，也可嵌入专用的出题流程窗口。
    """
    loop = QEventLoop()

    window.setAttribute(
        Qt.WidgetAttribute.WA_DeleteOnClose,
        True,
    )
    window.destroyed.connect(
        loop.quit
    )

    window.show()
    loop.exec()


def review_question_batch(
    batch_path: Path,
    *,
    deepseek_config: DeepSeekWebConfig | None = None,
    session_path: Path | None = None,
    exam_name: str | None = None,
) -> bool:
    """
    打开审核 GUI，并同步等待审核窗口真正关闭。

    无论 QApplication 是本函数创建的，还是外层考试控制台
    已经创建的，都必须等人工审核窗口关闭后再返回结果。
    """
    batch_path = Path(batch_path).resolve()

    if not batch_path.exists():
        raise FileNotFoundError(batch_path)

    if session_path is not None:
        session_path = Path(session_path).resolve()
        if not session_path.exists():
            if not exam_name:
                raise RuntimeError("创建审核 Session 时缺少考试名称。")
            initialize_generated_session(batch_path, session_path, exam_name)

    app = QApplication.instance()

    if app is None:
        app = QApplication(sys.argv)

    window = QuestionReviewWindow(
        batch_path,
        deepseek_config=deepseek_config,
    )

    _run_review_window_blocking(window)

    approved = question_batch_file_is_approved(batch_path)
    if session_path is not None:
        approved = update_review_session(batch_path, session_path)
    return approved




# ============================================================================
# 一体化 GUI / 主程序接口
# ============================================================================

def generate_and_review_questions(
    *,
    default_profile_dir: Path | None = None,
    default_output_path: Path | None = None,
    session_path: Path | None = None,
    exam_name: str | None = None,
    default_headless: bool = False,
    include_browser_preference: bool = False,
) -> tuple[Path | None, bool] | tuple[Path | None, bool, bool]:
    """
    一体化同步流程：
        GUI 填写出题要求
        -> DeepSeek 生成
        -> 人工审核
        -> 审核窗口关闭
        -> 返回最终审核结果

    外层即使已经存在 QApplication，也绝不能在审核窗口
    仍打开时提前返回 False。
    """
    app = QApplication.instance()

    if app is None:
        app = QApplication(sys.argv)

    dialog = QuestionGenerationDialog(
        default_profile_dir=default_profile_dir,
        default_output_path=default_output_path,
        default_headless=default_headless,
    )

    if dialog.exec() != QDialog.DialogCode.Accepted:
        result = (None, False)
        return (*result, dialog.headless_checkbox.isChecked()) if include_browser_preference else result

    batch_path = dialog.generated_path
    ds_config = dialog.generated_deepseek_config

    if batch_path is None:
        result = (None, False)
        return (*result, dialog.headless_checkbox.isChecked()) if include_browser_preference else result

    if session_path is not None:
        if not exam_name:
            raise RuntimeError("创建 AI 出题 Session 时缺少考试名称。")
        initialize_generated_session(batch_path, Path(session_path), exam_name)

    window = QuestionReviewWindow(
        batch_path,
        deepseek_config=ds_config,
    )

    _run_review_window_blocking(window)

    approved = question_batch_file_is_approved(batch_path)
    if session_path is not None:
        approved = update_review_session(batch_path, Path(session_path))
    result = (batch_path, approved)
    return (*result, dialog.headless_checkbox.isChecked()) if include_browser_preference else result




# ============================================================================
# 独立运行
# ============================================================================

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "AI 出题与人工审核 GUI。"
            "不传 batch 时先填写出题要求；"
            "传入 batch 时直接审核已有 JSON。"
        )
    )
    parser.add_argument(
        "batch",
        nargs="?",
        type=Path,
        help=(
            "可选：已有 generated_questions.json。"
            "省略时打开 AI 出题要求窗口。"
        ),
    )
    parser.add_argument(
        "--deepseek-profile",
        type=Path,
        help=(
            "DeepSeek Edge Profile。"
            "审核已有题目时提供后可使用“重新生成”；"
            "新建题目时作为默认值。"
        ),
    )
    parser.add_argument(
        "--headless-deepseek",
        action="store_true",
        help="仅在 DeepSeek 登录状态已经有效时使用。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "新建题目时默认输出 JSON 路径。"
        ),
    )

    args = parser.parse_args()

    app = QApplication(sys.argv)

    # ---------------------------------------------------------------
    # 模式 1：直接审核已有 JSON
    # ---------------------------------------------------------------
    if args.batch is not None:
        batch_path = args.batch.resolve()

        ds_config = None
        if args.deepseek_profile:
            ds_config = DeepSeekWebConfig(
                profile_dir=args.deepseek_profile.resolve(),
                headless=args.headless_deepseek,
            )

        try:
            window = QuestionReviewWindow(
                batch_path,
                deepseek_config=ds_config,
            )
        except Exception as exc:
            print(
                f"题目审核程序失败："
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1

        window.show()
        app.exec()

        approved = question_batch_file_is_approved(
            batch_path
        )

        if approved:
            print(
                "审核结果：全部题目已通过，"
                "可以上传题库。"
            )
            return 0

        print(
            "审核结果：仍有题目未通过，"
            "禁止上传题库。"
        )
        return 2

    # ---------------------------------------------------------------
    # 模式 2：GUI 填写要求 -> DeepSeek 出题 -> 审核
    # ---------------------------------------------------------------
    dialog = QuestionGenerationDialog(
        default_profile_dir=(
            args.deepseek_profile.resolve()
            if args.deepseek_profile
            else None
        ),
        default_output_path=(
            args.output.resolve()
            if args.output
            else None
        ),
    )

    if args.headless_deepseek:
        dialog.headless_checkbox.setChecked(True)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        print("已取消 AI 出题。")
        return 0

    batch_path = dialog.generated_path
    ds_config = dialog.generated_deepseek_config

    if batch_path is None:
        print("未生成题目文件。")
        return 1

    window = QuestionReviewWindow(
        batch_path,
        deepseek_config=ds_config,
    )
    window.show()

    app.exec()

    approved = question_batch_file_is_approved(
        batch_path
    )

    if approved:
        print(
            "审核结果：全部题目已通过，"
            "可以上传题库。"
        )
        return 0

    print(
        "审核结果：仍有题目未通过，"
        "禁止上传题库。"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

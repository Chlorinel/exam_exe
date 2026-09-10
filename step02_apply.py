from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path.cwd()
GUI = ROOT / "question_review_gui_with_generation.py"
TESTS = ROOT / "test_question_contracts.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: 期望找到 1 处，实际找到 {count} 处。"
            "请确认第 1 步已经应用，并且本地文件没有额外改动到该位置。"
        )
    return text.replace(old, new, 1)


def patch_gui(text: str) -> str:
    if "# STEP2_REFILL_HELPERS" in text:
        print("question_review_gui_with_generation.py 已包含第 2 步修改，跳过。")
        return text

    if "if len(batch.questions) != batch.spec.count:" not in text:
        raise RuntimeError(
            "未检测到第 1 步的题目数量门禁。"
            "请先完成 step01_review_count_gate_fixed.patch。"
        )

    text = replace_once(
        text,
        "from dataclasses import dataclass\n",
        "from dataclasses import dataclass, replace\n",
        "导入 dataclasses.replace",
    )

    text = replace_once(
        text,
        "    save_question_batch,\n    validate_question,\n)",
        "    save_question_batch,\n"
        "    validate_question,\n"
        "    validate_batch_duplicates,\n"
        ")",
        "导入 validate_batch_duplicates",
    )

    text = replace_once(
        text,
        """    if len(batch.questions) != batch.spec.count:
        return False

    for q in batch.questions:
""",
        """    if len(batch.questions) != batch.spec.count:
        return False

    # 整批审核门禁同时拒绝完全重复题。
    if validate_batch_duplicates(batch.questions):
        return False

    for q in batch.questions:
""",
        "增加整批重复题门禁",
    )

    helpers = r"""
# STEP2_REFILL_HELPERS
def batch_review_counts(
    batch: QuestionBatch,
) -> tuple[int, int, int, int]:
    # 返回：(要求题数, 当前题数, 已通过题数, 缺少题数)
    expected = int(batch.spec.count)
    current = len(batch.questions)
    approved = sum(
        q.review_status == STATUS_APPROVED
        and not validate_question(q)
        for q in batch.questions
    )
    missing = max(0, expected - current)
    return expected, current, approved, missing


def build_refill_spec(
    batch: QuestionBatch,
) -> QuestionSpec:
    # 只生成缺少的题数，并把现有题干摘要加入要求，降低重复概率。
    expected, current, _approved, missing = batch_review_counts(batch)
    if missing <= 0:
        raise ValueError(
            f"当前题目数量无需补充：要求 {expected} 道，当前 {current} 道。"
        )

    existing_lines: list[str] = []
    for q in batch.questions[:30]:
        preview = re.sub(r"\s+", " ", q.stem or "").strip()
        if len(preview) > 160:
            preview = preview[:157] + "..."
        existing_lines.append(f"- {q.local_id}: {preview}")

    existing_text = (
        "\n".join(existing_lines)
        if existing_lines
        else "- 当前没有保留题目"
    )

    refill_requirement = (
        "这是原题组的补题。请生成与现有题目实质不同的新题，"
        "不能只修改数字、变量名或做同义改写。\n"
        "以下是当前保留题目的题干摘要，补题不得与其重复：\n"
        f"{existing_text}"
    )

    original_requirements = (batch.spec.requirements or "").strip()
    requirements = "\n\n".join(
        part
        for part in (
            original_requirements,
            refill_requirement,
        )
        if part
    )

    return replace(
        batch.spec,
        count=missing,
        requirements=requirements,
    )


def _allocate_question_local_ids(
    existing_ids: set[str],
    count: int,
) -> list[str]:
    result: list[str] = []
    number = 1

    while len(result) < count:
        candidate = f"Q{number:03d}"
        if candidate not in existing_ids and candidate not in result:
            result.append(candidate)
        number += 1

    return result


def merge_refill_questions(
    batch: QuestionBatch,
    refill_questions: list[GeneratedQuestion],
) -> list[str]:
    # 合并补题前严格检查缺口数量和完全重复题。
    _expected, _current, _approved, missing = batch_review_counts(batch)

    if missing <= 0:
        raise ValueError("当前批次没有缺题。")

    if len(refill_questions) != missing:
        raise ValueError(
            f"补题数量不正确：需要 {missing} 道，"
            f"DeepSeek 返回 {len(refill_questions)} 道。"
        )

    combined = [*batch.questions, *refill_questions]
    duplicate_errors = validate_batch_duplicates(combined)
    first_new_index = len(batch.questions)

    new_duplicate_messages = [
        message
        for index, message in duplicate_errors
        if index >= first_new_index
    ]
    if new_duplicate_messages:
        raise ValueError(
            "补题与现有题目存在完全重复："
            + "；".join(new_duplicate_messages)
        )

    new_ids = _allocate_question_local_ids(
        {q.local_id for q in batch.questions},
        len(refill_questions),
    )

    for question, local_id in zip(refill_questions, new_ids):
        question.local_id = local_id
        question.review_status = STATUS_PENDING
        question.edited_by_user = False
        question.platform_question_id = None
        question.validation_errors = validate_question(question)

    batch.questions.extend(refill_questions)
    return new_ids


"""

    text = replace_once(
        text,
        """def question_batch_file_is_approved(path: Path) -> bool:
    return batch_is_fully_approved(load_question_batch(Path(path)))


# ============================================================================
# Markdown + LaTeX 预览
""",
        """def question_batch_file_is_approved(path: Path) -> bool:
    return batch_is_fully_approved(load_question_batch(Path(path)))


""" + helpers + """# ============================================================================
# Markdown + LaTeX 预览
""",
        "插入补题纯函数",
    )

    text = replace_once(
        text,
        """        self.question_list.currentRowChanged.connect(
            self._on_list_changed
        )
        list_layout.addWidget(self.question_list, 1)

        main_split.addWidget(list_panel)
""",
        """        self.question_list.currentRowChanged.connect(
            self._on_list_changed
        )
        list_layout.addWidget(self.question_list, 1)

        self.fill_missing_button = QPushButton("生成补题")
        self.fill_missing_button.setToolTip(
            "当当前题数少于最初要求时，按原出题要求生成缺少的题目。"
        )
        list_layout.addWidget(self.fill_missing_button)

        main_split.addWidget(list_panel)
""",
        "增加生成补题按钮",
    )

    text = replace_once(
        text,
        """        self.delete_button.clicked.connect(
            self.delete_current
        )
        self.preview_button.clicked.connect(
""",
        """        self.delete_button.clicked.connect(
            self.delete_current
        )
        self.fill_missing_button.clicked.connect(
            self.generate_missing_questions
        )
        self.preview_button.clicked.connect(
""",
        "连接生成补题按钮",
    )

    text = replace_once(
        text,
        """        count = len(self.batch.questions)
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
""",
        """        (
            expected,
            count,
            approved,
            missing,
        ) = batch_review_counts(self.batch)
        pending = count - approved
        excess = max(0, count - expected)

        self.batch_label.setText(
            f"批次：{self.batch.batch_id}"
        )

        summary_parts = [
            f"要求 {expected} 题",
            f"当前 {count}",
            f"已通过 {approved}",
            f"待审核 {pending}",
        ]
        if missing:
            summary_parts.append(f"缺少 {missing}")
        elif excess:
            summary_parts.append(f"超出 {excess}")
        else:
            summary_parts.append("数量完整")

        self.summary_label.setText(
            " · ".join(summary_parts)
        )

        can_refill = (
            missing > 0
            and self.deepseek_config is not None
        )
        self.fill_missing_button.setEnabled(can_refill)

        if missing > 0 and self.deepseek_config is None:
            self.fill_missing_button.setToolTip(
                "当前缺题，但审核窗口没有收到 DeepSeekWebConfig，"
                "不能自动补题。"
            )
        elif missing > 0:
            self.fill_missing_button.setToolTip(
                f"按原出题要求补生成 {missing} 道题。"
            )
        else:
            self.fill_missing_button.setToolTip(
                "当前题目数量不缺少，无需补题。"
            )

        if count:
""",
        "升级审核数量摘要",
    )

    text = replace_once(
        text,
        """        self.preview.setHtml(
            "<h2>当前没有题目。</h2>"
        )

    def load_question(self, index: int) -> None:
        if not self.batch.questions:
            self._disable_editor()
            return

        if not 0 <= index < len(self.batch.questions):
            return

        self._loading_form = True
""",
        """        self.preview.setHtml(
            "<h2>当前没有题目。</h2>"
        )

    def _enable_editor(self) -> None:
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
            self.delete_button,
        ):
            widget.setEnabled(True)

        self.regenerate_button.setEnabled(
            self.deepseek_config is not None
        )

    def load_question(self, index: int) -> None:
        if not self.batch.questions:
            self._disable_editor()
            return

        if not 0 <= index < len(self.batch.questions):
            return

        # 如果此前删光了题目，编辑区曾被整体禁用；
        # 补题后重新进入题目时恢复编辑能力。
        self._enable_editor()

        self._loading_form = True
""",
        "补题后恢复编辑区",
    )

    refill_method = r"""
    # ------------------------------------------------------------------
    # DeepSeek 缺题补生成
    # ------------------------------------------------------------------

    def generate_missing_questions(self) -> None:
        if self.deepseek_config is None:
            QMessageBox.warning(
                self,
                "未配置 DeepSeek",
                "当前审核窗口没有收到 DeepSeekWebConfig，"
                "因此不能自动生成补题。",
            )
            return

        if self.dirty:
            if not self._confirm_discard_or_save():
                return

        try:
            refill_spec = build_refill_spec(self.batch)
        except ValueError as exc:
            QMessageBox.information(
                self,
                "无需补题",
                str(exc),
            )
            self._refresh_list()
            return

        missing = refill_spec.count
        result = QMessageBox.question(
            self,
            "生成补题",
            f"当前比原要求少 {missing} 道题。\n\n"
            "程序将按原课程、章节、知识点、题型、难度和分值生成补题。\n"
            "DeepSeek 会开启全新对话；新题生成后仍需逐题人工审核。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
        )

        if result != QMessageBox.StandardButton.Yes:
            return

        self.statusBar().showMessage(
            f"正在补生成 {missing} 道题..."
        )
        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor
        )
        self.fill_missing_button.setEnabled(False)

        try:
            # generate_questions() 内部固定 new_chat=True，
            # 所以每一次补题也是一个全新的 DeepSeek 对话。
            with DeepSeekWebGenerator(
                self.deepseek_config,
            ) as generator:
                refill_batch = generator.generate_questions(
                    refill_spec
                )

            # 生成成功后才修改当前批次。
            self.audit.ensure_original_snapshot()

            added_ids = merge_refill_questions(
                self.batch,
                refill_batch.questions,
            )

            self.audit.append(
                "refill_questions",
                details={
                    "count": len(added_ids),
                    "local_ids": added_ids,
                    "requested_count": self.batch.spec.count,
                },
            )

            self._save_batch()
            self.dirty = False
            self._refresh_list()

            first_new_index = (
                len(self.batch.questions)
                - len(added_ids)
            )
            if added_ids:
                self.load_question(first_new_index)

            QMessageBox.information(
                self,
                "补题完成",
                f"已补生成 {len(added_ids)} 道题："
                f"{', '.join(added_ids)}\n\n"
                "这些题目全部处于待审核状态，"
                "必须逐题人工确认后才能上传题库。",
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "补题失败",
                f"{type(exc).__name__}: {exc}",
            )

        finally:
            QApplication.restoreOverrideCursor()
            self._refresh_list()


"""

    text = replace_once(
        text,
        """    # ------------------------------------------------------------------
    # DeepSeek 单题重新生成
    # ------------------------------------------------------------------

    def regenerate_current(self) -> None:
""",
        refill_method + """    # ------------------------------------------------------------------
    # DeepSeek 单题重新生成
    # ------------------------------------------------------------------

    def regenerate_current(self) -> None:
""",
        "增加缺题补生成方法",
    )

    return text


def patch_tests(text: str) -> str:
    if "test_merge_refill_questions_assigns_unique_ids" in text:
        print("test_question_contracts.py 已包含第 2 步测试，跳过。")
        return text

    if "expected_count=None" not in text:
        raise RuntimeError(
            "test_question_contracts.py 未检测到第 1 步修改。"
        )

    text = replace_once(
        text,
        "from question_review_gui_with_generation import question_batch_file_is_approved\n",
        """from question_review_gui_with_generation import (
    batch_review_counts,
    build_refill_spec,
    merge_refill_questions,
    question_batch_file_is_approved,
)
""",
        "导入第 2 步测试接口",
    )

    text = replace_once(
        text,
        """def sample_question(*, status="approved", stem="题干 \\\\(x(t)\\\\)"):
    return GeneratedQuestion(
        local_id="Q001",
""",
        """def sample_question(
    *,
    status="approved",
    stem="题干 \\\\(x(t)\\\\)",
    local_id="Q001",
):
    return GeneratedQuestion(
        local_id=local_id,
""",
        "允许测试指定 local_id",
    )

    extra_tests = r"""
    def test_review_counts_report_missing_questions(self):
        batch = sample_batch(
            [sample_question()],
            expected_count=3,
        )
        self.assertEqual(
            batch_review_counts(batch),
            (3, 1, 1, 2),
        )

    def test_build_refill_spec_requests_only_missing_count(self):
        batch = sample_batch(
            [sample_question()],
            expected_count=3,
        )
        refill = build_refill_spec(batch)
        self.assertEqual(refill.count, 2)
        self.assertEqual(
            refill.course_name,
            batch.spec.course_name,
        )
        self.assertEqual(
            refill.chapter,
            batch.spec.chapter,
        )
        self.assertIn("补题", refill.requirements)
        self.assertIn("Q001", refill.requirements)

    def test_merge_refill_questions_assigns_unique_ids(self):
        batch = sample_batch(
            [sample_question(local_id="Q001")],
            expected_count=2,
        )
        refill = sample_question(
            status="approved",
            stem="另一道不同题干 \\\\(y(t)\\\\)",
            local_id="Q001",
        )

        added = merge_refill_questions(
            batch,
            [refill],
        )

        self.assertEqual(added, ["Q002"])
        self.assertEqual(
            batch.questions[-1].local_id,
            "Q002",
        )
        self.assertEqual(
            batch.questions[-1].review_status,
            "pending",
        )
        self.assertIsNone(
            batch.questions[-1].platform_question_id
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.json"
            save_question_batch(path, batch)
            self.assertFalse(
                question_batch_file_is_approved(path)
            )

            batch.questions[-1].review_status = "approved"
            save_question_batch(path, batch)
            self.assertTrue(
                question_batch_file_is_approved(path)
            )

    def test_merge_refill_questions_rejects_duplicate_stem(self):
        batch = sample_batch(
            [sample_question()],
            expected_count=2,
        )
        duplicate = sample_question(
            status="pending",
            local_id="Q099",
        )

        with self.assertRaises(ValueError):
            merge_refill_questions(
                batch,
                [duplicate],
            )

"""

    text = replace_once(
        text,
        "    def test_duplicate_stems_are_detected(self):\n",
        extra_tests + "    def test_duplicate_stems_are_detected(self):\n",
        "增加第 2 步回归测试",
    )

    return text


def main() -> int:
    for path in (GUI, TESTS):
        if not path.exists():
            raise FileNotFoundError(
                f"未找到 {path.name}。请在 exam_exe 仓库根目录运行本脚本。"
            )

    gui_text = GUI.read_text(encoding="utf-8")
    test_text = TESTS.read_text(encoding="utf-8")

    new_gui = patch_gui(gui_text)
    new_tests = patch_tests(test_text)

    GUI.write_text(new_gui, encoding="utf-8", newline="\n")
    TESTS.write_text(new_tests, encoding="utf-8", newline="\n")

    print("第 2 步修改已写入：")
    print(f"  {GUI.name}")
    print(f"  {TESTS.name}")
    print()
    print("请继续运行：")
    print("  python -m py_compile question_review_gui_with_generation.py")
    print("  python -m unittest test_question_contracts.py -v")
    print("  git diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

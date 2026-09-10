from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "question_review_gui_with_generation.py"
TEST = ROOT / "tests" / "test_review_gui_blocking.py"


REVIEW_FUNCTION = """def review_question_batch(
    batch_path: Path,
    *,
    deepseek_config: DeepSeekWebConfig | None = None,
) -> bool:
    \"\"\"
    打开审核 GUI，并同步等待审核窗口真正关闭。

    无论 QApplication 是本函数创建的，还是外层考试控制台
    已经创建的，都必须等人工审核窗口关闭后再返回结果。
    \"\"\"
    batch_path = Path(batch_path).resolve()

    if not batch_path.exists():
        raise FileNotFoundError(batch_path)

    app = QApplication.instance()

    if app is None:
        app = QApplication(sys.argv)

    window = QuestionReviewWindow(
        batch_path,
        deepseek_config=deepseek_config,
    )

    _run_review_window_blocking(window)

    return question_batch_file_is_approved(
        batch_path
    )


"""


GENERATE_FUNCTION = """def generate_and_review_questions(
    *,
    default_profile_dir: Path | None = None,
    default_output_path: Path | None = None,
) -> tuple[Path | None, bool]:
    \"\"\"
    一体化同步流程：
        GUI 填写出题要求
        -> DeepSeek 生成
        -> 人工审核
        -> 审核窗口关闭
        -> 返回最终审核结果

    外层即使已经存在 QApplication，也绝不能在审核窗口
    仍打开时提前返回 False。
    \"\"\"
    app = QApplication.instance()

    if app is None:
        app = QApplication(sys.argv)

    dialog = QuestionGenerationDialog(
        default_profile_dir=default_profile_dir,
        default_output_path=default_output_path,
    )

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None, False

    batch_path = dialog.generated_path
    ds_config = dialog.generated_deepseek_config

    if batch_path is None:
        return None, False

    window = QuestionReviewWindow(
        batch_path,
        deepseek_config=ds_config,
    )

    _run_review_window_blocking(window)

    return (
        batch_path,
        question_batch_file_is_approved(
            batch_path
        ),
    )


"""


HELPER = """def _run_review_window_blocking(
    window: QuestionReviewWindow,
) -> None:
    \"\"\"
    为 QMainWindow 提供类似 QDialog.exec() 的同步等待。

    这里只创建局部 QEventLoop，不创建第二个 QApplication。
    因此既可独立运行，也可嵌入 exam_control_gui.pyw。
    \"\"\"
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


"""


TEST_CONTENT = """from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import question_review_gui_with_generation as review


class GenerateAndReviewBlockingTests(unittest.TestCase):
    def test_existing_qapplication_waits_for_review_before_returning(self):
        with tempfile.TemporaryDirectory() as directory:
            batch_path = Path(directory) / "questions.json"
            batch_path.write_text("{}", encoding="utf-8")

            dialog = Mock()
            dialog.exec.return_value = review.QDialog.DialogCode.Accepted
            dialog.generated_path = batch_path
            dialog.generated_deepseek_config = Mock()

            review_window = Mock()

            with (
                patch.object(
                    review.QApplication,
                    "instance",
                    return_value=Mock(),
                ),
                patch.object(
                    review,
                    "QuestionGenerationDialog",
                    return_value=dialog,
                ),
                patch.object(
                    review,
                    "QuestionReviewWindow",
                    return_value=review_window,
                ),
                patch.object(
                    review,
                    "_run_review_window_blocking",
                ) as blocking,
                patch.object(
                    review,
                    "question_batch_file_is_approved",
                    return_value=True,
                ) as approved,
            ):
                result_path, result_approved = (
                    review.generate_and_review_questions()
                )

            self.assertEqual(result_path, batch_path)
            self.assertTrue(result_approved)
            blocking.assert_called_once_with(review_window)
            approved.assert_called_once_with(batch_path)

    def test_blocking_helper_runs_local_event_loop(self):
        window = Mock()
        signal = Mock()
        window.destroyed = signal
        event_loop = Mock()

        with patch.object(
            review,
            "QEventLoop",
            return_value=event_loop,
        ):
            review._run_review_window_blocking(window)

        window.setAttribute.assert_called_once_with(
            review.Qt.WidgetAttribute.WA_DeleteOnClose,
            True,
        )
        signal.connect.assert_called_once_with(event_loop.quit)
        window.show.assert_called_once_with()
        event_loop.exec.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
"""


def _offsets(text: str) -> list[int]:
    result = [0]
    for index, char in enumerate(text):
        if char == "\n":
            result.append(index + 1)
    return result


def _replace_top_level_function(
    text: str,
    name: str,
    replacement: str,
) -> str:
    tree = ast.parse(text)
    offsets = _offsets(text)

    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"无法唯一找到函数 {name}："
            f"{len(matches)} 处"
        )

    node = matches[0]

    if node.end_lineno is None:
        raise RuntimeError(
            f"无法确定函数 {name} 的结束位置。"
        )

    start = offsets[node.lineno - 1]
    end = (
        offsets[node.end_lineno]
        if node.end_lineno < len(offsets)
        else len(text)
    )

    return (
        text[:start]
        + replacement
        + text[end:]
    )


def _insert_helper_before_review_function(
    text: str,
) -> str:
    if "def _run_review_window_blocking(" in text:
        return _replace_top_level_function(
            text,
            "_run_review_window_blocking",
            HELPER,
        )

    marker = "def review_question_batch("
    index = text.find(marker)

    if index < 0:
        raise RuntimeError(
            "找不到 review_question_batch() 插入位置。"
        )

    return (
        text[:index]
        + HELPER
        + text[index:]
    )


def _atomic_write(
    path: Path,
    text: str,
) -> None:
    handle, temporary = tempfile.mkstemp(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )

    try:
        with os.fdopen(
            handle,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(
            temporary,
            path,
        )

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    if not TARGET.exists():
        raise FileNotFoundError(
            "未找到 question_review_gui_with_generation.py，"
            "请在 exam_exe 仓库根目录运行。"
        )

    text = TARGET.read_text(
        encoding="utf-8"
    )

    old_import = "from PySide6.QtCore import Qt, QTimer"
    new_import = "from PySide6.QtCore import QEventLoop, Qt, QTimer"

    if old_import in text:
        text = text.replace(
            old_import,
            new_import,
            1,
        )
    elif new_import not in text:
        raise RuntimeError(
            "找不到 PySide6.QtCore 导入位置。"
        )

    text = _insert_helper_before_review_function(
        text
    )

    text = _replace_top_level_function(
        text,
        "review_question_batch",
        REVIEW_FUNCTION,
    )

    text = _replace_top_level_function(
        text,
        "generate_and_review_questions",
        GENERATE_FUNCTION,
    )

    compile(
        text,
        str(TARGET),
        "exec",
    )
    compile(
        TEST_CONTENT,
        str(TEST),
        "exec",
    )

    _atomic_write(
        TARGET,
        text,
    )
    _atomic_write(
        TEST,
        TEST_CONTENT,
    )

    print("审核窗口同步问题修复完成。")
    print()
    print("新流程：")
    print(
        "  AI生成 -> 打开人工审核 -> "
        "等待审核窗口关闭 -> 读取审核结果 -> "
        "通过后才上传题库"
    )
    print()
    print("请运行：")
    print(
        "python -m py_compile "
        "question_review_gui_with_generation.py"
    )
    print(
        "python -m unittest "
            "tests.test_review_gui_blocking -v"
    )
    print(
        "python -m unittest discover -v"
    )


if __name__ == "__main__":
    main()

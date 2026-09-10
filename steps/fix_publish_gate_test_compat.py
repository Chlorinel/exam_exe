from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path


MAIN = Path("create_signal_exam.py")
GUI_TEST = Path("tests/test_gui_publish_gate.py")


FINAL_CONFIRM = """def confirm_exam_publish(
    args,
    config,
    *,
    confirmer=None,
) -> bool:
    \"\"\"
    考试发布的最终人工门禁。

    GUI 模式：
        必须由 confirmer(config) 明确返回 True。

    CLI 备用模式：
        必须是交互终端，并由人工输入 YES。

    headless / 无人值守环境：
        禁止发布考试。

    注意：
    --publish-exam 只是 CLI 入口参数，不承担“人工认证”职责；
    真正的安全门始终是这里的人工作为确认。
    \"\"\"
    if confirmer is not None:
        return bool(confirmer(config))

    if args.headless or not sys.stdin.isatty():
        raise RuntimeError(
            "当前环境无法进行终端确认。"
            "考试发布必须由人工确认；"
            "headless/无人值守环境禁止发布考试。"
        )

    print(
        "\\n考试草稿已准备完成，"
        "请人工核对后决定是否发布："
    )
    print(f"  考试：{config.exam_name}")
    print(f"  班级：{config.class_code}")
    print(
        f"  时间："
        f"{config.start:%Y-%m-%d %H:%M} 至 "
        f"{config.end:%Y-%m-%d %H:%M}"
        "（北京时间）"
    )
    print(f"  题目：{len(config.questions)} 道")

    answer = input(
        "确认立即发布考试？"
        "输入 YES 发布，"
        "其他输入保留草稿并退出："
    ).strip()

    return answer == "YES"


"""


GUI_TEST_CONTENT = """from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import create_signal_exam as exam


def sample_args(*, publish_exam=True, headless=False):
    return SimpleNamespace(
        publish_exam=publish_exam,
        headless=headless,
    )


def sample_config():
    return SimpleNamespace(
        exam_name="第一次随堂测试",
        class_code="202613475",
        start=datetime(2026, 9, 10, 9, 0),
        end=datetime(2026, 9, 10, 10, 0),
        questions=[1, 2],
    )


class GUIPublishGateTests(unittest.TestCase):
    def test_gui_confirmer_can_approve(self):
        config = sample_config()
        confirmer = Mock(return_value=True)

        result = exam.confirm_exam_publish(
            sample_args(),
            config,
            confirmer=confirmer,
        )

        self.assertTrue(result)
        confirmer.assert_called_once_with(config)

    def test_gui_cancel_keeps_draft(self):
        config = sample_config()
        confirmer = Mock(return_value=False)

        result = exam.confirm_exam_publish(
            sample_args(),
            config,
            confirmer=confirmer,
        )

        self.assertFalse(result)

    def test_gui_confirmation_is_the_gate_not_publish_flag(self):
        config = sample_config()
        confirmer = Mock(return_value=True)

        result = exam.confirm_exam_publish(
            sample_args(publish_exam=False),
            config,
            confirmer=confirmer,
        )

        self.assertTrue(result)
        confirmer.assert_called_once_with(config)

    def test_headless_without_gui_callback_is_rejected(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "无法进行终端确认",
        ):
            exam.confirm_exam_publish(
                sample_args(headless=True),
                sample_config(),
            )

    @patch("builtins.input", return_value="YES")
    @patch.object(exam.sys.stdin, "isatty", return_value=True)
    def test_cli_backup_requires_exact_yes(
        self,
        _isatty,
        _input,
    ):
        self.assertTrue(
            exam.confirm_exam_publish(
                sample_args(publish_exam=False),
                sample_config(),
            )
        )


if __name__ == "__main__":
    unittest.main()
"""


def offsets(text: str) -> list[int]:
    result = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            result.append(i + 1)
    return result


def replace_all_top_level_functions(
    text: str,
    name: str,
    replacement: str,
) -> tuple[str, int]:
    tree = ast.parse(text)
    starts = offsets(text)

    spans = []
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == name
        ):
            if node.end_lineno is None:
                raise RuntimeError(
                    f"无法确定 {name} 的结束位置。"
                )

            start = starts[node.lineno - 1]
            end = (
                starts[node.end_lineno]
                if node.end_lineno < len(starts)
                else len(text)
            )
            spans.append((start, end))

    if not spans:
        raise RuntimeError(
            f"没有找到函数 {name}。"
        )

    for start, end in reversed(spans):
        text = (
            text[:start]
            + replacement
            + text[end:]
        )

    return text, len(spans)


def atomic_write(
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
    if not MAIN.exists():
        raise FileNotFoundError(
            "未找到 create_signal_exam.py，"
            "请在 exam_exe 仓库根目录运行。"
        )

    text = MAIN.read_text(
        encoding="utf-8"
    )

    text, count = (
        replace_all_top_level_functions(
            text,
            "confirm_exam_publish",
            FINAL_CONFIRM,
        )
    )

    # 全部语法验证通过后才写盘。
    compile(
        text,
        str(MAIN),
        "exec",
    )
    compile(
        GUI_TEST_CONTENT,
        str(GUI_TEST),
        "exec",
    )

    atomic_write(
        MAIN,
        text,
    )
    atomic_write(
        GUI_TEST,
        GUI_TEST_CONTENT,
    )

    print(
        f"已统一 {count} 份 "
        "confirm_exam_publish()。"
    )
    print()
    print("最终发布规则：")
    print(
        "  GUI -> 必须人工通过发布确认窗口"
    )
    print(
        "  CLI -> 必须交互输入 YES"
    )
    print(
        "  headless/无人值守 -> 禁止发布"
    )
    print()
    print("请重新运行：")
    print(
        "python -m unittest discover -v"
    )


if __name__ == "__main__":
    main()

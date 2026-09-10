from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path

MAIN = Path("create_signal_exam.py")
TEST = Path("tests/test_gui_publish_gate.py")
FINAL_LOGIN = 'def wait_for_login_if_needed(\n    driver: webdriver.Edge,\n    timeout: int = 300,\n) -> None:\n    """\n    若当前是登录页，只等待用户在 Edge 中完成登录。\n\n    不依赖终端按 Enter；适用于 GUI / pythonw.exe。\n    """\n    if not is_login_page(driver):\n        return\n\n    print(\n        "当前 Edge 配置需要登录。"\n        "请在打开的 Edge 窗口中完成登录；"\n        "登录成功后脚本会自动继续。",\n        flush=True,\n    )\n\n    WebDriverWait(driver, timeout).until(\n        lambda d: not is_login_page(d)\n    )\n    wait_until_ready(driver, 40)\n\n\n'
FINAL_CONFIRM = 'def confirm_exam_publish(\n    args,\n    config,\n    *,\n    confirmer=None,\n) -> bool:\n    """\n    考试发布的最终人工门禁。\n\n    --publish-exam 只表示允许进入发布流程，\n    不能自己代表人工确认。\n\n    GUI 可以传 confirmer(config)；\n    CLI 则仍要求交互终端人工输入 YES。\n    """\n    if not args.publish_exam:\n        print(\n            "未提供 --publish-exam；"\n            "考试继续保留为草稿。"\n        )\n        return False\n\n    if confirmer is not None:\n        return bool(confirmer(config))\n\n    if args.headless or not sys.stdin.isatty():\n        raise RuntimeError(\n            "考试发布必须人工确认。"\n            "当前没有 GUI 确认回调，"\n            "且终端不可交互；禁止发布考试。"\n        )\n\n    print(\n        "\\n考试草稿已准备完成，"\n        "请人工核对后决定是否发布："\n    )\n    print(f"  考试：{config.exam_name}")\n    print(f"  班级：{config.class_code}")\n    print(\n        f"  时间："\n        f"{config.start:%Y-%m-%d %H:%M} 至 "\n        f"{config.end:%Y-%m-%d %H:%M}"\n        "（北京时间）"\n    )\n    print(f"  题目：{len(config.questions)} 道")\n\n    answer = input(\n        "确认立即发布考试？"\n        "输入 YES 发布，"\n        "其他输入保留草稿并退出："\n    ).strip()\n\n    return answer == "YES"\n\n\n'
TEST_CONTENT = 'from __future__ import annotations\n\nimport unittest\nfrom datetime import datetime\nfrom types import SimpleNamespace\nfrom unittest.mock import Mock\n\nimport create_signal_exam as exam\n\n\ndef sample_args(*, publish_exam=True, headless=False):\n    return SimpleNamespace(\n        publish_exam=publish_exam,\n        headless=headless,\n    )\n\n\ndef sample_config():\n    return SimpleNamespace(\n        exam_name="第一次随堂测试",\n        class_code="202613475",\n        start=datetime(2026, 9, 10, 9, 0),\n        end=datetime(2026, 9, 10, 10, 0),\n        questions=[1, 2],\n    )\n\n\nclass GUIPublishGateTests(unittest.TestCase):\n    def test_gui_confirmer_can_approve(self):\n        config = sample_config()\n        confirmer = Mock(return_value=True)\n\n        result = exam.confirm_exam_publish(\n            sample_args(),\n            config,\n            confirmer=confirmer,\n        )\n\n        self.assertTrue(result)\n        confirmer.assert_called_once_with(config)\n\n    def test_gui_cancel_keeps_draft(self):\n        config = sample_config()\n        confirmer = Mock(return_value=False)\n\n        result = exam.confirm_exam_publish(\n            sample_args(),\n            config,\n            confirmer=confirmer,\n        )\n\n        self.assertFalse(result)\n\n    def test_missing_publish_flag_never_calls_gui_confirmer(self):\n        confirmer = Mock(return_value=True)\n\n        result = exam.confirm_exam_publish(\n            sample_args(publish_exam=False),\n            sample_config(),\n            confirmer=confirmer,\n        )\n\n        self.assertFalse(result)\n        confirmer.assert_not_called()\n\n    def test_headless_without_gui_callback_is_rejected(self):\n        with self.assertRaises(RuntimeError):\n            exam.confirm_exam_publish(\n                sample_args(headless=True),\n                sample_config(),\n            )\n\n\nif __name__ == "__main__":\n    unittest.main()\n'

def _offsets(text):
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == '\n':
            offsets.append(i + 1)
    return offsets

def _function_spans(text, name):
    tree = ast.parse(text)
    offsets = _offsets(text)
    spans = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if node.end_lineno is None:
                raise RuntimeError(f'无法确定函数 {name} 的结束位置。')
            start = offsets[node.lineno - 1]
            end = offsets[node.end_lineno] if node.end_lineno < len(offsets) else len(text)
            spans.append((start, end))
    return spans

def _replace_all_functions(text, name, replacement):
    spans = _function_spans(text, name)
    if not spans:
        raise RuntimeError(f'没有找到函数 {name}。')
    for start, end in reversed(spans):
        text = text[:start] + replacement + text[end:]
    return text, len(spans)

def _patch_run_configuration_headers(text):
    tree = ast.parse(text)
    targets = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == 'run_configuration'
    ]
    if not targets:
        raise RuntimeError('没有找到 run_configuration()。')
    lines = text.splitlines(keepends=True)
    for node in targets:
        idx = node.lineno - 1
        line = lines[idx]
        stripped = line.strip()
        if 'publish_confirmer' in stripped:
            continue
        if stripped != 'def run_configuration(args):':
            raise RuntimeError('run_configuration() 签名不是预期格式：' + stripped)
        newline = '\r\n' if line.endswith('\r\n') else '\n'
        lines[idx] = 'def run_configuration(args, *, publish_confirmer=None):' + newline
    return ''.join(lines), len(targets)

def _call_spans_inside_run_configuration(text):
    tree = ast.parse(text)
    offsets = _offsets(text)
    spans = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name != 'run_configuration':
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != 'confirm_exam_publish':
                continue
            # 已经接入 GUI callback 就不重复修改。
            if any(keyword.arg == 'confirmer' for keyword in node.keywords):
                continue
            if node.end_lineno is None or node.end_col_offset is None:
                raise RuntimeError('无法确定 confirm_exam_publish() 调用结束位置。')
            start = offsets[node.lineno - 1] + node.col_offset
            end = offsets[node.end_lineno - 1] + node.end_col_offset
            spans.append((start, end))
    return spans

def _patch_confirm_calls(text):
    spans = _call_spans_inside_run_configuration(text)
    replacement = 'confirm_exam_publish(args, config, confirmer=publish_confirmer)'
    for start, end in reversed(spans):
        text = text[:start] + replacement + text[end:]
    return text, len(spans)

def _atomic_write(path, text):
    handle, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def main():
    if not MAIN.exists():
        raise FileNotFoundError('未找到 create_signal_exam.py，请在 exam_exe 仓库根目录运行。')
    original = MAIN.read_text(encoding='utf-8')

    text, login_count = _replace_all_functions(
        original, 'wait_for_login_if_needed', FINAL_LOGIN
    )
    text, confirm_count = _replace_all_functions(
        text, 'confirm_exam_publish', FINAL_CONFIRM
    )
    text, run_count = _patch_run_configuration_headers(text)
    text, call_count = _patch_confirm_calls(text)

    # 必须至少有一个 run_configuration 内的发布确认调用，
    # 除非本地文件已经是 GUI callback 版本。
    if call_count == 0 and 'confirmer=publish_confirmer' not in text:
        raise RuntimeError('没有找到可接入 GUI 的考试发布确认调用。')

    compile(text, str(MAIN), 'exec')
    compile(TEST_CONTENT, str(TEST), 'exec')

    _atomic_write(MAIN, text)
    _atomic_write(TEST, TEST_CONTENT)

    print('修改成功。')
    print(f'  wait_for_login_if_needed 定义：{login_count} 份，已全部统一')
    print(f'  confirm_exam_publish 定义：{confirm_count} 份，已全部统一')
    print(f'  run_configuration 定义：{run_count} 份，已接入 publish_confirmer')
    print(f'  本次新增 GUI 发布确认调用：{call_count} 处')
    print('  已生成 tests/test_gui_publish_gate.py')
    print()
    print('请运行：')
    print('python -m py_compile create_signal_exam.py')
    print('python -m unittest tests.test_gui_publish_gate -v')
    print('python -m unittest tests.test_exam_control_gui -v')
    print('python -m unittest discover -s tests -t . -v')

if __name__ == '__main__':
    main()

from pathlib import Path

MAIN = Path("create_signal_exam.py")
TEST = Path("tests/test_ai_cli_publish_gate.py")

def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: 期望找到 1 处，实际找到 {count} 处。请先运行 git diff 检查本地文件。')
    return text.replace(old, new, 1)

def main():
    if not MAIN.exists():
        raise FileNotFoundError('未找到 create_signal_exam.py，请在 exam_exe 仓库根目录运行。')
    text = MAIN.read_text(encoding='utf-8')
    old_confirm = 'def confirm_exam_publish(args, config):\n    """Ask once in an interactive terminal before publishing a prepared draft."""\n    if args.publish_exam:\n        return True\n    if args.headless or not sys.stdin.isatty():\n        raise RuntimeError(\'当前环境无法进行终端确认。请在交互终端运行 --run，或明确使用 --run --publish-exam。\')\n    print(\'\\n考试草稿已准备完成，请人工核对后决定是否发布：\')\n    print(f\'  考试：{config.exam_name}\')\n    print(f\'  班级：{config.class_code}\')\n    print(f\'  时间：{config.start:%Y-%m-%d %H:%M} 至 {config.end:%Y-%m-%d %H:%M}（北京时间）\')\n    print(f\'  题目：{len(config.questions)} 道\')\n    answer = input(\'确认立即发布考试？输入 YES 发布，其他输入保留草稿并退出：\').strip()\n    return answer == \'YES\'\n'
    new_confirm = 'def confirm_exam_publish(args, config, *, input_func=input, stdin=None):\n    """\n    考试发布的最终人工门禁。\n\n    发布必须同时满足：\n    1. 命令行明确提供 --run --publish-exam；\n    2. 当前是可交互终端；\n    3. 人工在发布前输入 YES。\n\n    --publish-exam 只表示“允许尝试发布”，不能替代人工确认。\n    """\n    if not args.publish_exam:\n        print(\'未提供 --publish-exam；考试继续保留为草稿。\')\n        return False\n\n    stream = sys.stdin if stdin is None else stdin\n    if args.headless or not stream.isatty():\n        raise RuntimeError(\n            \'考试发布必须在交互终端进行人工确认；\'\n            \'--publish-exam 不能绕过该确认，headless/无人值守环境禁止发布考试。\'\n        )\n\n    print(\'\\n考试草稿已准备完成，请人工核对后决定是否发布：\')\n    print(f\'  考试：{config.exam_name}\')\n    print(f\'  班级：{config.class_code}\')\n    print(f\'  时间：{config.start:%Y-%m-%d %H:%M} 至 {config.end:%Y-%m-%d %H:%M}（北京时间）\')\n    print(f\'  题目：{len(config.questions)} 道\')\n    answer = input_func(\n        \'确认立即发布考试？输入 YES 发布，其他输入保留草稿并退出：\'\n    ).strip()\n    return answer == \'YES\'\n'
    if old_confirm in text:
        text = replace_once(text, old_confirm, new_confirm, '修改考试发布人工确认门禁')
    elif '考试发布的最终人工门禁' not in text:
        raise RuntimeError('没有找到预期的 confirm_exam_publish()。')

    ai_runner = 'def run_ai_prepare(args):\n    """\n    AI 准备阶段只负责：\n    生成 -> 人工审核 -> 自动上传题库 -> 定位 -> 生成最终配置表。\n\n    此入口绝不创建或发布考试。\n    """\n    from ai_exam_preparer import prepare_ai_exam_config\n\n    final_path = prepare_ai_exam_config(\n        args.config,\n        deepseek_profile_dir=args.deepseek_profile_dir,\n        platform_profile_dir=args.profile_dir,\n        output_config_path=args.ai_output_config,\n        batch_path=args.ai_batch,\n        upload_state_path=args.ai_upload_state,\n        platform_headless=args.headless,\n    )\n\n    if final_path is None:\n        print(\'AI 出题/审核已取消，没有生成最终考试配置表。\')\n        return 0\n\n    print(f\'AI 准备完成：{final_path}\')\n    print(\'该命令没有创建或发布考试。请人工检查最终配置后再运行 --prepare 或 --run。\')\n    return 0\n\n\n'
    if 'def run_ai_prepare(args):' not in text:
        marker = '\ndef run_configuration(args):\n'
        if marker not in text:
            raise RuntimeError('找不到 run_configuration() 插入位置。')
        text = text.replace(marker, '\n' + ai_runner + 'def run_configuration(args):\n', 1)

    old_group_tail = "    group.add_argument('--schedule-grades', action='store_true', help='为已发布考试创建或修复 Windows 成绩发布任务')\n    parser.add_argument('--publish-exam', action='store_true', help='与 --run 配合，允许把考试草稿发布给配置班级')\n"
    new_group_tail = "    group.add_argument('--schedule-grades', action='store_true', help='为已发布考试创建或修复 Windows 成绩发布任务')\n    group.add_argument(\n        '--ai-prepare',\n        action='store_true',\n        help='AI 出题并人工审核；审核通过后自动上传题库并生成最终配置表，不创建或发布考试',\n    )\n    parser.add_argument('--publish-exam', action='store_true', help='与 --run 配合；仍必须在发布前由人工输入 YES 确认')\n"
    if "--ai-prepare" not in text:
        text = replace_once(text, old_group_tail, new_group_tail, '增加 --ai-prepare')

    old_profile_args = "    parser.add_argument('--profile-dir', type=Path, default=DEFAULT_PROFILE_DIR)\n    parser.add_argument('--headless', action='store_true', help='无窗口运行；首次登录请不要使用')\n    args = parser.parse_args()\n"
    new_profile_args = "    parser.add_argument('--profile-dir', type=Path, default=DEFAULT_PROFILE_DIR)\n    parser.add_argument(\n        '--deepseek-profile-dir',\n        type=Path,\n        default=Path(__file__).with_name('work') / 'edge-deepseek-profile',\n        help='DeepSeek 网页独立 Edge 配置目录，仅 --ai-prepare 使用',\n    )\n    parser.add_argument(\n        '--ai-output-config',\n        type=Path,\n        help='AI 最终配置表输出路径；默认写到源配置旁的 work 目录',\n    )\n    parser.add_argument(\n        '--ai-batch',\n        type=Path,\n        help='AI 审核题目 JSON 路径；默认写到 work 目录',\n    )\n    parser.add_argument(\n        '--ai-upload-state',\n        type=Path,\n        help='AI 题库上传状态文件路径；默认与审核 JSON 同目录',\n    )\n    parser.add_argument('--headless', action='store_true', help='无窗口运行；首次登录请不要使用')\n    args = parser.parse_args()\n"
    if "--deepseek-profile-dir" not in text:
        text = replace_once(text, old_profile_args, new_profile_args, '增加 AI CLI 路径参数')

    old_validation = "    if args.publish_exam and not args.run:\n        parser.error('--publish-exam 必须配合 --run')\n    return args\n"
    new_validation = "    if args.publish_exam and not args.run:\n        parser.error('--publish-exam 必须配合 --run')\n\n    ai_only_values = (\n        args.ai_output_config,\n        args.ai_batch,\n        args.ai_upload_state,\n    )\n    if any(value is not None for value in ai_only_values) and not args.ai_prepare:\n        parser.error('--ai-output-config/--ai-batch/--ai-upload-state 只能配合 --ai-prepare 使用')\n\n    return args\n"
    if 'ai_only_values =' not in text:
        text = replace_once(text, old_validation, new_validation, '增加 AI 参数约束')

    old_main = 'def main():\n    try:\n        return run_configuration(parse_args())\n'
    new_main = 'def main():\n    try:\n        args = parse_args()\n        if args.ai_prepare:\n            return run_ai_prepare(args)\n        return run_configuration(args)\n'
    if 'if args.ai_prepare:' not in text:
        text = replace_once(text, old_main, new_main, 'main 分流 --ai-prepare')

    compile(text, str(MAIN), 'exec')
    test_content = 'from __future__ import annotations\n\nimport unittest\nfrom datetime import datetime\nfrom pathlib import Path\nfrom types import SimpleNamespace\nfrom unittest.mock import patch\n\nimport create_signal_exam as exam\n\n\nclass _InteractiveStdin:\n    def isatty(self):\n        return True\n\n\nclass _NonInteractiveStdin:\n    def isatty(self):\n        return False\n\n\ndef sample_config():\n    return SimpleNamespace(\n        exam_name="第一次随堂测试",\n        class_code="202613475",\n        start=datetime(2026, 9, 10, 9, 0),\n        end=datetime(2026, 9, 10, 10, 0),\n        questions=[object(), object()],\n    )\n\n\nclass ExamPublishGateTests(unittest.TestCase):\n    def test_publish_flag_does_not_bypass_human_confirmation(self):\n        args = SimpleNamespace(publish_exam=True, headless=False)\n        self.assertFalse(\n            exam.confirm_exam_publish(\n                args,\n                sample_config(),\n                input_func=lambda _prompt: "NO",\n                stdin=_InteractiveStdin(),\n            )\n        )\n\n    def test_publish_requires_yes(self):\n        args = SimpleNamespace(publish_exam=True, headless=False)\n        self.assertTrue(\n            exam.confirm_exam_publish(\n                args,\n                sample_config(),\n                input_func=lambda _prompt: "YES",\n                stdin=_InteractiveStdin(),\n            )\n        )\n\n    def test_missing_publish_flag_keeps_draft(self):\n        args = SimpleNamespace(publish_exam=False, headless=False)\n        self.assertFalse(\n            exam.confirm_exam_publish(\n                args,\n                sample_config(),\n                input_func=lambda _prompt: "YES",\n                stdin=_InteractiveStdin(),\n            )\n        )\n\n    def test_headless_publish_is_forbidden(self):\n        args = SimpleNamespace(publish_exam=True, headless=True)\n        with self.assertRaises(RuntimeError):\n            exam.confirm_exam_publish(\n                args,\n                sample_config(),\n                input_func=lambda _prompt: "YES",\n                stdin=_InteractiveStdin(),\n            )\n\n    def test_noninteractive_publish_is_forbidden(self):\n        args = SimpleNamespace(publish_exam=True, headless=False)\n        with self.assertRaises(RuntimeError):\n            exam.confirm_exam_publish(\n                args,\n                sample_config(),\n                input_func=lambda _prompt: "YES",\n                stdin=_NonInteractiveStdin(),\n            )\n\n\nclass AIPrepareEntryTests(unittest.TestCase):\n    @patch("ai_exam_preparer.prepare_ai_exam_config")\n    def test_ai_prepare_only_returns_final_config(self, prepare_mock):\n        final = Path("work") / "考试配置表_AI完成.xlsx"\n        prepare_mock.return_value = final\n\n        args = SimpleNamespace(\n            config=Path("考试配置表.xlsx"),\n            deepseek_profile_dir=Path("work/deepseek"),\n            profile_dir=Path("work/platform"),\n            ai_output_config=None,\n            ai_batch=None,\n            ai_upload_state=None,\n            headless=False,\n        )\n\n        result = exam.run_ai_prepare(args)\n\n        self.assertEqual(result, 0)\n        prepare_mock.assert_called_once_with(\n            args.config,\n            deepseek_profile_dir=args.deepseek_profile_dir,\n            platform_profile_dir=args.profile_dir,\n            output_config_path=None,\n            batch_path=None,\n            upload_state_path=None,\n            platform_headless=False,\n        )\n\n\nif __name__ == "__main__":\n    unittest.main()\n'
    compile(test_content, str(TEST), 'exec')
    MAIN.write_text(text, encoding='utf-8', newline='\n')
    TEST.write_text(test_content, encoding='utf-8', newline='\n')

    print('修改完成：create_signal_exam.py, tests/test_ai_cli_publish_gate.py')
    print('发布规则：--run --publish-exam 仍必须交互输入 YES；headless/非交互环境禁止发布考试。')
    print('请运行：')
    print('python -m py_compile create_signal_exam.py')
    print('python -m unittest tests.test_ai_cli_publish_gate -v')
    print('python -m unittest discover -s tests -t . -v')
    print('python create_signal_exam.py --help')
    print('git diff')

if __name__ == '__main__':
    main()

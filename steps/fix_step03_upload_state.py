from pathlib import Path

path = Path("platform_question_uploader.py")
if not path.exists():
    raise FileNotFoundError(
        "未找到 platform_question_uploader.py，请在 exam_exe 仓库根目录运行。"
    )

text = path.read_text(encoding="utf-8")

old_sig = """def upload_batch(
    config: UploadConfig,
    batch_path: Path,
    state_path: Path,
    *,
    commit: bool = False,
    assume_yes: bool = False,
) -> None:
"""
new_sig = old_sig.replace(") -> None:", ") -> UploadState:")

old_dry = """            if not commit:
                input("当前为核对模式，未点击保存。请在浏览器核对，按 Enter 关闭程序：")
                return
"""
new_dry = old_dry.replace(
    "                return\n",
    "                return state\n",
)

old_cancel = """                if answer != "YES":
                    print("已停止，当前题未保存。")
                    return
"""
new_cancel = old_cancel.replace(
    "                    return\n",
    "                    return state\n",
)

old_tail = """    finally:
        uploader.close()


def build_parser() -> argparse.ArgumentParser:
"""
new_tail = """    finally:
        uploader.close()

    return state


def build_parser() -> argparse.ArgumentParser:
"""


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: 期望找到 1 处，实际找到 {count} 处。"
        )
    return source.replace(old, new, 1)


# 只检查 upload_batch 自己的精确签名，不再误判其他 -> UploadState 函数。
if old_sig in text:
    text = replace_once(
        text,
        old_sig,
        new_sig,
        "修改 upload_batch 返回类型",
    )
elif new_sig not in text:
    raise RuntimeError(
        "找不到 upload_batch 的预期函数签名，请先运行 git diff 检查本地改动。"
    )

if old_dry in text:
    text = replace_once(
        text,
        old_dry,
        new_dry,
        "核对模式返回 state",
    )

if old_cancel in text:
    text = replace_once(
        text,
        old_cancel,
        new_cancel,
        "取消保存时返回 state",
    )

if old_tail in text:
    text = replace_once(
        text,
        old_tail,
        new_tail,
        "正常结束返回 state",
    )
elif "    return state\n\n\ndef build_parser()" not in text:
    raise RuntimeError(
        "没有找到 upload_batch 结束位置，也没有检测到 return state。"
    )

path.write_text(
    text,
    encoding="utf-8",
    newline="\n",
)

print("已修复 platform_question_uploader.py")
print("请运行：")
print("python -m py_compile platform_question_uploader.py")
print("python -m unittest tests.test_platform_question_uploader -v")

"""Route double-clicked .pyw entry points through the project's own environment."""

from __future__ import annotations

import ctypes
import importlib
from pathlib import Path
import subprocess
import sys


def launch_gui(module_name: str) -> int:
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    try:
        if Path(sys.prefix).resolve() == environment.resolve():
            return importlib.import_module(module_name).main()
        executable_name = "pythonw.exe" if sys.stdout is None else "python.exe"
        executable = environment / "Scripts" / executable_name
        if not executable.is_file():
            raise RuntimeError("找不到项目专用 Python 环境，请先双击 setup_new_pc.cmd 完成安装。")
        log_path = root / "work" / "gui-launch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            result = subprocess.run(
                [str(executable), "-E", str(root / f"{module_name}.pyw"), *sys.argv[1:]],
                cwd=root,
                stdout=log,
                stderr=log,
                check=False,
            )
        if result.returncode:
            raise RuntimeError(f"程序启动或运行失败，详情见：{log_path}\n可重新运行 setup_new_pc.cmd 检查依赖。")
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        else:
            ctypes.windll.user32.MessageBoxW(None, message, "考试自动化系统", 0x10)
        return 1

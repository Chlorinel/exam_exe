"""Route double-clicked .pyw entry points through the project's own environment."""

from __future__ import annotations

import ctypes
from datetime import datetime
import importlib
from pathlib import Path
import subprocess
import sys
import traceback


def launch_gui(module_name: str) -> int:
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    try:
        if Path(sys.prefix).resolve() == environment.resolve():
            result = importlib.import_module(module_name).main()
            print(f"[APP_RETURN] code={result}", flush=True)
            return result
        executable = environment / "Scripts" / "python.exe"
        if not executable.is_file():
            raise RuntimeError("找不到项目专用 Python 环境，请先双击 setup_new_pc.cmd 完成安装。")
        log_path = root / "work" / "gui-launch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[{datetime.now().astimezone().isoformat()}] Starting {module_name}\n")
            log.flush()
            start = log.tell()
            result = subprocess.run(
                [str(executable), "-E", "-X", "utf8", str(root / f"{module_name}.pyw"), *sys.argv[1:]],
                cwd=root,
                stdout=log,
                stderr=log,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.stdout is None else 0,
            )
            log.write(f"\n[PROCESS_EXIT] code={result.returncode}\n")
        if result.returncode:
            with log_path.open("rb") as log:
                log.seek(start)
                current_run = log.read().decode("utf-8", errors="replace")
            # A main() return means the GUI already reported its business error.
            if f"[APP_RETURN] code={result.returncode}" in current_run:
                return result.returncode
            raise RuntimeError(f"程序异常退出（退出码 {result.returncode}），详情见：{log_path}")
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if sys.stderr is not None:
            traceback.print_exc(file=sys.stderr)
            print(message, file=sys.stderr)
        else:
            ctypes.windll.user32.MessageBoxW(None, message, "考试自动化系统", 0x10)
        return 1

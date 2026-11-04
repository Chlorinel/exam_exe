from __future__ import annotations

"""Launch Microsoft Edge from a file-sandboxed workspace.

Some Windows agent harnesses (for example the Codex/DSH sandbox service) confine
every process whose executable lives inside the session workspace: that process
may only write inside the workspace, and its children inherit the restriction.
This program is started from ``.venv`` inside the project, so Selenium's
EdgeDriver runs under that restriction and fails to create its scratch directory
under ``%TEMP%``::

    SessionNotCreatedException: ... cannot create temp dir for unpacking extensions

and, even when the session is created, Chromium's own OS sandbox cannot start
and the first navigation fails with ``tab crashed``.

Keep both ordinary and confined environments working:

* when the system temp directory is usable the launch is unchanged;
* when it is not, the EdgeDriver scratch directory is redirected into
  ``work/browser-temp`` (inside the workspace) and Chromium's OS sandbox is
  disabled for the automated browser only.

Nothing else about the browser is changed, and the user profile still lives in
``work`` as before.
"""

import os
import sys
import tempfile
from pathlib import Path

from selenium.webdriver import Edge
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_ROOT = _app_root()
BROWSER_TEMP_DIR = APP_ROOT / "work" / "browser-temp"
_PROBE_PREFIX = ".edge-runtime-probe-"

_restricted: bool | None = None


def _system_temp_dir() -> Path:
    raw = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    return Path(raw)


def system_temp_writable() -> bool:
    """Probe whether the browser could create scratch dirs under %TEMP%."""
    probe = _system_temp_dir() / f"{_PROBE_PREFIX}{os.getpid()}"
    try:
        probe.mkdir()
    except OSError:
        return False
    try:
        probe.rmdir()
    except OSError:
        pass
    return True


def restricted_workspace() -> bool:
    """True when this process cannot create scratch directories outside it."""
    global _restricted
    if _restricted is None:
        _restricted = not system_temp_writable()
    return _restricted


def browser_temp_dir() -> Path:
    BROWSER_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return BROWSER_TEMP_DIR


def prepare_options(options: Options) -> Options:
    """Add the switches Chromium needs when the workspace is file-sandboxed."""
    if restricted_workspace():
        arguments = options.arguments
        if "--no-sandbox" not in arguments:
            options.add_argument("--no-sandbox")
    return options


def launch(options: Options):
    """Create an Edge session, keeping the browser stack writable."""
    if restricted_workspace():
        temp_dir = browser_temp_dir()
        environment = dict(os.environ)
        environment["TMP"] = str(temp_dir)
        environment["TEMP"] = str(temp_dir)
        service = Service(env=environment)
    else:
        service = Service()
    return Edge(options=options, service=service)

from __future__ import annotations

"""Selenium waits that keep an optional Qt GUI responsive."""

import time
from typing import Any, Callable

from selenium.webdriver.support.ui import WebDriverWait as SeleniumWebDriverWait


def pump_gui_events() -> None:
    """Process pending Qt events when called from the GUI's main thread."""
    try:
        from PySide6.QtCore import QCoreApplication, QEventLoop

        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 25)
    except (ImportError, RuntimeError):
        # Command-line use does not require PySide6, and Qt can reject event
        # processing while the application is shutting down.
        return


def responsive_sleep(seconds: float, *, interval: float = 0.05) -> None:
    """Sleep in short slices so a running Qt application can keep repainting."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        pump_gui_events()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(interval, remaining))


class WebDriverWait(SeleniumWebDriverWait):
    """Drop-in Selenium wait that services the Qt event loop while polling."""

    @staticmethod
    def _responsive(method: Callable[[Any], Any]) -> Callable[[Any], Any]:
        def wrapped(driver: Any) -> Any:
            pump_gui_events()
            return method(driver)

        return wrapped

    def until(self, method: Callable[[Any], Any], message: str = "") -> Any:
        return super().until(self._responsive(method), message)

    def until_not(self, method: Callable[[Any], Any], message: str = "") -> Any:
        return super().until_not(self._responsive(method), message)

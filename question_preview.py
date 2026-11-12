"""Render the review pane locally, without Chromium or network requests."""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import Signal
from native_math_preview import NativeMathPreview


class QuestionPreview(NativeMathPreview):
    status_changed = Signal(str)

    def __init__(self, document_builder: Callable[..., str]):
        super().__init__()
        self._document_builder = document_builder

    def set_question(self, question: Any) -> None:
        self.status_changed.emit("正在排版最新内容…")
        content = self._document_builder(question, fragment=True)
        try:
            self.set_source(content)
            if self.failed_formulas:
                self.status_changed.emit("预览已更新；部分公式保留原文，可点击“浏览器预览”核对。")
            else:
                self.status_changed.emit("预览已更新（本地公式排版）")
        except Exception:
            logging.getLogger(__name__).exception("Local question preview failed")
            self.setHtml(content)
            self.status_changed.emit("已显示完整原文，可点击“立即刷新”重试。")

    def clear_question(self) -> None:
        self.setHtml("<h2>当前没有题目。</h2>")
        self.status_changed.emit("")

"""Render MathJax SVG in Qt without starting a Chromium renderer process."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re

from PySide6.QtCore import QByteArray, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QImage, QPainter, QTextDocument
from PySide6.QtQml import QJSEngine, QJSValue
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QTextBrowser


MATH_PATTERN = re.compile(r"\\\((.*?)\\\)|\\\[(.*?)\\\]", re.DOTALL)


class NativeMathPreview(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        if "Microsoft YaHei" not in QFontDatabase.families():
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        self.setFont(QFont("Microsoft YaHei", 11))
        self.setStyleSheet("QTextBrowser {background: white; color: #111;}")
        self.document().setDefaultStyleSheet(
            "body {font-family: 'Microsoft YaHei'; font-size: 17px;}"
            "p {margin-top: 7px; margin-bottom: 7px;}"
            ".meta {color: #666; font-size: 13px;}"
            ".section-title {font-weight: bold; margin-top: 18px;}"
        )
        self._engine = None
        self._render_math = None
        self._images = {}
        self.rendered_formulas = 0
        self.failed_formulas = 0

    def _initialize_engine(self):
        if self._engine is not None:
            return
        engine = QJSEngine(self)
        script = Path(__file__).resolve().parent / "assets/mathjax/native-math.js"
        result = engine.evaluate(script.read_text(encoding="utf-8"), str(script))
        if result.isError():
            raise RuntimeError(result.toString())
        function = engine.globalObject().property("ExamMath").property("renderMath")
        if not function.isCallable():
            raise RuntimeError("本地公式排版函数加载失败。")
        self._engine, self._render_math = engine, function

    def _math_image(self, tex: str, display: bool):
        key = hashlib.sha256(f"{display}:{tex}".encode()).hexdigest()
        if key not in self._images:
            self._initialize_engine()
            value = self._render_math.call([QJSValue(tex), QJSValue(display)])
            if value.isError():
                raise ValueError(value.toString())
            result = json.loads(value.toString())
            if result.get("error"):
                raise ValueError(result["error"])
            if 'data-mml-node="merror"' in result["svg"]:
                raise ValueError("公式语法无法排版，已保留原文。")
            svg = QSvgRenderer(QByteArray(result["svg"].encode("utf-8")))
            if not svg.isValid():
                raise ValueError("公式 SVG 无效。")
            width = max(1, float(result["width"]))
            height = max(1, float(result["height"]))
            scale = min(1, 4096 / max(width, height))
            image = QImage(max(1, round(width * scale)), max(1, round(height * scale)), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            svg.render(painter)
            painter.end()
            if len(self._images) >= 256:
                self._images.pop(next(iter(self._images)))
            self._images[key] = image
        return key, self._images[key]

    def set_source(self, content: str) -> None:
        resources = []
        self.rendered_formulas = self.failed_formulas = 0

        def replace(match):
            display = match.group(2) is not None
            tex = html.unescape(match.group(2) if display else match.group(1))
            try:
                key, image = self._math_image(tex, display)
                url = QUrl("mathjax:" + key)
                resources.append((url, image))
                scale = min(1, max(80, self.viewport().width() - 32) / image.width())
                width, height = max(1, round(image.width() * scale)), max(1, round(image.height() * scale))
                self.rendered_formulas += 1
                tag = f'<img src="{url.toString()}" width="{width}" height="{height}" style="vertical-align: middle;" alt="{html.escape(tex, quote=True)}">'
                return f'<div align="center">{tag}</div>' if display else tag
            except Exception as exc:
                self.failed_formulas += 1
                logging.getLogger(__name__).warning("Native formula rendering failed: %s", exc)
                return match.group(0)

        rendered = MATH_PATTERN.sub(replace, content)
        self.setHtml(rendered)
        for url, image in resources:
            self.document().addResource(QTextDocument.ResourceType.ImageResource, url, image)
        self.document().markContentsDirty(0, self.document().characterCount())
        self.viewport().update()

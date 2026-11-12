"""Exercise native MathJax rendering and the real Qt editor event loop."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


class PreviewRuntimeTests(unittest.TestCase):
    def test_offline_rendering_and_live_updates(self):
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--runtime"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=environment, timeout=70,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OFFLINE PREVIEW PASS", result.stdout)


def run_runtime_checks():
    from dataclasses import replace
    from decimal import Decimal
    import tempfile
    import time

    from PySide6.QtWidgets import QApplication
    from deepseek_question_generator import GeneratedQuestion, QuestionBatch, QuestionSpec, save_question_batch
    from question_preview import QuestionPreview
    from question_review_gui_with_generation import QuestionReviewWindow, build_question_preview_html

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    view = QuestionPreview(build_question_preview_html)
    view.resize(580, 900)
    view.show()
    messages = []
    view.status_changed.connect(messages.append)

    def wait_for(condition, description):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError(description + ": " + repr(messages[-3:]))

    def ready():
        wait_for(lambda: bool(messages) and messages[-1].startswith("预览已更新"), "Preview did not finish")

    def text(widget):
        return widget.toPlainText()

    question = GeneratedQuestion(
        local_id="Q001", question_type="single_choice", chapter="第四章",
        knowledge_point="卷积性质", difficulty="medium", score=Decimal("5"),
        stem=r"原题完整 \(x(t)=1\)（\(|t|<T_0/4\)），题干末尾。[12345]",
        options={"A": r"\(a<b\)", "B": r"\(x+y\)", "C": r"\(x-y\)", "D": r"\(x/y\)"},
        answer="A", explanation=r"完整解析 \[\begin{cases}1 & t<T_0/4 \\ -1 & t>T_0/4\end{cases}\] 解析末尾。",
        identifier="[12345]",
    )
    view.set_question(question)
    ready()
    assert view.rendered_formulas >= 7
    assert view.failed_formulas == 0
    assert "题干末尾" in text(view)
    assert "解析末尾" in text(view)

    for index in range(15):
        view.set_question(replace(question, local_id=f"Q{index+2:03d}", stem=rf"快速修改{index} \(a<b\) 最新末尾"))
    ready()
    assert "快速修改14" in text(view)
    # Invalid TeX must keep its original text without dropping adjacent content.
    view.set_question(replace(question, stem=r"前文 \(\frac{1}{\) 后文末尾"))
    assert view.failed_formulas >= 1
    assert r"\frac{1}{" in text(view) and "后文末尾" in text(view)

    with tempfile.TemporaryDirectory() as directory:
        batch_path = Path(directory) / "questions.json"
        batch = QuestionBatch(
            batch_id="preview-test", provider="test", created_at="2026-11-12",
            spec=QuestionSpec(course_name="test", chapter="第四章", knowledge_point="卷积性质"),
            questions=[question], raw_response="",
        )
        save_question_batch(batch_path, batch)
        window = QuestionReviewWindow(batch_path)
        window.show()
        wait_for(lambda: window.preview_status.text().startswith("预览已更新"), "Review initial preview")
        window.auto_preview.setChecked(False)
        window.stem_edit.setPlainText(r"关闭自动预览后编辑 \(a<b\) 完整末尾")
        assert not window.preview_timer.isActive()
        window.auto_preview.setChecked(True)
        wait_for(lambda: "关闭自动预览后编辑" in text(window.preview), "Re-enabling preview")
        window.stem_edit.setPlainText(r"自动刷新检查 \(x<y\) 自动末尾")
        wait_for(lambda: "自动刷新检查" in text(window.preview), "Latest editor text")
        window.auto_preview.setChecked(False)
        window.stem_edit.setPlainText(r"立即刷新检查 \(x<y\) 手动末尾")
        window.preview_button.click()
        wait_for(lambda: "立即刷新检查" in text(window.preview), "Manual refresh")
        from unittest.mock import patch
        with patch("question_review_gui_with_generation.QDesktopServices.openUrl", return_value=True) as opened:
            window.open_browser_preview()
            opened.assert_called_once()
            exported = batch_path.with_name("questions.preview.html").read_text(encoding="utf-8")
            assert "立即刷新检查" in exported
            assert "file:///" in exported and 'src="https://' not in exported
        window.dirty = False
        window.close()
        app.processEvents()

    view.clear_question()
    assert "当前没有题目" in view.toPlainText()
    view.close()
    print("OFFLINE PREVIEW PASS: native MathJax SVG, invalid TeX preservation, 15 rapid updates, auto/manual refresh, browser export; no Chromium process required.")



if __name__ == "__main__":
    if "--runtime" in sys.argv:
        run_runtime_checks()
    else:
        unittest.main()

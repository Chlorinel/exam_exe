from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import ElementClickInterceptedException

from ai_question_locator import _open_chapter


class OpenChapterTests(unittest.TestCase):
    def open_chapter(self, *, intercepted=False, expected_count=2):
        state = {"opened": False}

        def open_content():
            if intercepted:
                raise ElementClickInterceptedException("overlay receives click")
            state["opened"] = True

        content = Mock()
        content.is_displayed.return_value = True
        content.is_enabled.return_value = True
        content.click.side_effect = open_content
        chapter = Mock()
        chapter.text = f"第四章（{expected_count}）"
        chapter.find_element.return_value = content
        rows = [Mock(), Mock()]

        def execute_script(script, element):
            if script == "arguments[0].click();":
                self.assertIs(element, content)
                state["opened"] = True

        driver = SimpleNamespace(
            current_url="about:blank",
            execute_script=Mock(side_effect=execute_script),
        )
        uploader = SimpleNamespace(
            driver=driver,
            config=SimpleNamespace(page_timeout=0),
            open_question_bank=Mock(),
        )
        with (
            patch("ai_question_locator.chapter_rows", side_effect=lambda d: [] if state["opened"] else [chapter]),
            patch("ai_question_locator.question_rows", side_effect=lambda d: rows if state["opened"] else []),
        ):
            result = _open_chapter(uploader, "第四章")
        uploader.open_question_bank.assert_called_once_with()
        self.assertEqual(result, rows)
        content.click.assert_called_once_with()
        fallback_calls = [
            call for call in driver.execute_script.call_args_list
            if call.args[0] == "arguments[0].click();"
        ]
        self.assertEqual(len(fallback_calls), int(intercepted))

    def test_opens_chapter_normally(self):
        self.open_chapter()

    def test_recovers_when_overlay_intercepts_chapter_click(self):
        self.open_chapter(intercepted=True)

    def test_incomplete_question_list_still_fails_validation(self):
        with self.assertRaisesRegex(RuntimeError, "期望 3 道，当前 2 道"):
            self.open_chapter(intercepted=True, expected_count=3)


if __name__ == "__main__":
    unittest.main()

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import TimeoutException

import create_signal_exam as exam

EXAM_NAME = "周期方波信号傅里叶变换+对偶性质叠加频移"
PAGE_TEXT_WITH_SIDEBAR = (
    "全部班级 课程主页 教学活动 课程资源库 2026年秋冬学期 陈霖 "
    "创建活动 最新 作业 考试 课堂 考勤 讨论 通知 学习资源 "
    "隐藏AI翻转任务 暂无数据 即将截止 第三章作业 11月9日 11:59"
)


def card(text, displayed=True):
    element = Mock()
    element.text = text
    element.is_displayed.return_value = displayed
    return element


class FakeWait:
    """Poll a predicate immediately instead of waiting on the real clock."""

    def __init__(self, driver, timeout):
        self.driver = driver
        self.timeout = timeout

    def until(self, method):
        for _ in range(50):
            result = method(self.driver)
            if result:
                return result
        raise TimeoutException("fake wait timed out")


class FindActivityTests(unittest.TestCase):
    def find(self, *, names, body, empty_state, course_id="course"):
        driver = Mock()
        search_field = Mock()
        search_field.get_attribute.side_effect = lambda attribute: {
            "type": "text",
            "value": EXAM_NAME,
        }.get(attribute)

        def find_elements(by, selector):
            if selector in exam.ACTIVITY_LIST_EMPTY_SELECTORS:
                return [card("暂无数据")] if empty_state else []
            return []

        def visible(driver_arg, selector):
            if selector == "input":
                return [search_field]
            if selector == "p.name":
                return [card(name) for name in names]
            if selector == ".route-loading":
                return []
            raise AssertionError(f"unexpected selector: {selector}")

        driver.find_elements.side_effect = find_elements
        config = SimpleNamespace(exam_name=EXAM_NAME, course_id=course_id)
        with (
            patch.object(exam, "open_activity_page"),
            patch.object(exam, "set_input"),
            patch.object(exam, "responsive_sleep"),
            patch.object(exam, "visible", side_effect=visible),
            patch.object(exam, "body_text", side_effect=lambda _driver: body),
            patch.object(exam, "WebDriverWait", FakeWait),
            patch.object(exam, "ACTIVITY_EMPTY_STATE_SETTLE_SECONDS", 0.0),
        ):
            return exam.find_activity(driver, config)

    def test_new_exam_name_with_single_empty_state_is_not_a_duplicate(self):
        # Regression: the platform renders exactly one "暂无数据" block when a
        # search returns nothing, and drops the "共有 N 个教学活动" counter.
        self.assertEqual(
            self.find(names=[], body=PAGE_TEXT_WITH_SIDEBAR, empty_state=True),
            [],
        )

    def test_existing_exam_is_returned(self):
        body = "共有 1 个教学活动 隐藏AI翻转任务 选题考试 " + EXAM_NAME
        matches = self.find(names=[EXAM_NAME], body=body, empty_state=False)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].text, EXAM_NAME)

    def test_sidebar_empty_text_alone_does_not_finish_the_wait(self):
        # "暂无数据" outside the activity list must not be read as an empty
        # search result, otherwise duplicate exams could be overlooked.
        with self.assertRaisesRegex(RuntimeError, "搜索列表未就绪"):
            self.find(names=[], body=PAGE_TEXT_WITH_SIDEBAR, empty_state=False)

    def test_diagnostic_reports_empty_state_on_timeout(self):
        try:
            self.find(names=[], body=PAGE_TEXT_WITH_SIDEBAR, empty_state=False)
        except RuntimeError as exc:
            self.assertIn('"empty_state": false', str(exc))
            self.assertIn(EXAM_NAME, str(exc))
        else:  # pragma: no cover - the call above must fail
            self.fail("expected RuntimeError")


if __name__ == "__main__":
    unittest.main()

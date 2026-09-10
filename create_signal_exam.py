from __future__ import annotations

"""Single-file SYSU exam automation, configured by 考试配置表.xlsx.

Default/--validate/--dry-run only read local configuration.
--prepare writes an unpublished draft. Opening 配置试题 itself auto-saves a draft.
--run resumes from a durable receipt and schedules the configured grade-release time.
--publish-exam explicitly permits publishing the exam to its configured class.
No automatic grading, account scraping, or credential export is performed.
"""

import argparse
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

from exam_session import (
    load_exam_state,
    load_session,
    save_exam_state,
    save_session,
    update_status as update_session_status,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_DIR = ROOT / "work" / "edge-automation-profile"

BASE_URL = "https://aic.sysu.edu.cn"
DEFAULT_COURSE_ID = "GEaZpQk9q4izv12ZBxjM"
DEFAULT_TERM_ID = "20271"
DEFAULT_COURSE_NAME = "信号与系统"
DEFAULT_CLASS_CODE = "202613475"
DEFAULT_EXAM_NAME = "第一次随堂测试"
DEFAULT_EXAM_DESCRIPTION = "随堂小测"
DEFAULT_PAPER_NAME = "随堂测试1"

# Edit this list before running. Numbers are 1-based positions in the visible
# question-bank list; any number of questions may be provided, for example:
# QUESTION_NUMBERS = [1, 3, 5]
# Leave it empty to keep the original paper-library flow using DEFAULT_PAPER_NAME.
QUESTION_NUMBERS: list[int] = [1, 2]


def norm(text: str | None) -> str:
    return " ".join((text or "").split())


def _is_displayed(element: WebElement) -> bool:
    try:
        return element.is_displayed()
    except StaleElementReferenceException:
        return False


def wait_until_ready(driver: webdriver.Edge, timeout: int = 40) -> None:
    """Wait for the document itself; the site's route overlay may stay mounted."""
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )


def wait_for(driver: webdriver.Edge, selector: str, timeout: int = 40) -> WebElement:
    return WebDriverWait(driver, timeout).until(
        lambda d: next((e for e in d.find_elements(By.CSS_SELECTOR, selector) if e.is_displayed()), False)
    )


def click_safely(driver: webdriver.Edge, element: WebElement) -> None:
    """Click a visible element, falling back to DOM click for overlay/race issues."""
    try:
        WebDriverWait(driver, 10).until(lambda d: element.is_displayed() and element.is_enabled())
        element.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", element)


def exact_text_elements(root: webdriver.Edge | WebElement, text: str, selectors: str) -> list[WebElement]:
    target = norm(text)
    found: list[WebElement] = []
    for element in root.find_elements(By.CSS_SELECTOR, selectors):
        try:
            if element.is_displayed() and norm(element.text) == target:
                found.append(element)
        except StaleElementReferenceException:
            continue
    return found


def click_exact_text(
    driver: webdriver.Edge,
    text: str,
    selectors: str = "button,a,[role=button],[role=menuitem],li,.select-item,.activity-item",
    timeout: int = 20,
) -> WebElement:
    def find(d: webdriver.Edge) -> WebElement | bool:
        candidates = exact_text_elements(d, text, selectors)
        return candidates[-1] if candidates else False

    element = WebDriverWait(driver, timeout).until(find)
    click_safely(driver, element)
    return element


def click_visible_exact(
    driver: webdriver.Edge,
    text: str,
    selectors: str = ".base-button-component,button,a,[role=button],[role=menuitem]",
    timeout: int = 20,
) -> WebElement:
    """Click an exact-text control, including the div-based controls in question-bank hub."""
    return click_exact_text(driver, text, selectors=selectors, timeout=timeout)


def wait_route_idle(driver: webdriver.Edge, timeout: int = 30) -> None:
    """Wait for the site's full-screen route-loading overlay to disappear."""
    WebDriverWait(driver, timeout).until(
        lambda d: not any(_is_displayed(e) for e in d.find_elements(By.CSS_SELECTOR, ".route-loading"))
    )


def body_text(driver: webdriver.Edge) -> str:
    return norm(driver.find_element(By.TAG_NAME, "body").text)


def is_login_page(driver: webdriver.Edge) -> bool:
    url = (driver.current_url or "").lower()
    text = body_text(driver).lower()
    url_markers = ("login", "auth", "cas", "sso")
    text_markers = ("统一身份认证", "用户名", "密码", "验证码", "登录")
    return any(x in url for x in url_markers) or any(x.lower() in text for x in text_markers)


def wait_for_login_if_needed(
    driver: webdriver.Edge,
    timeout: int = 300,
) -> None:
    """
    若当前是登录页，只等待用户在 Edge 中完成登录。

    不依赖终端按 Enter；适用于 GUI / pythonw.exe。
    """
    if not is_login_page(driver):
        return

    print(
        "当前 Edge 配置需要登录。"
        "请在打开的 Edge 窗口中完成登录；"
        "登录成功后脚本会自动继续。",
        flush=True,
    )

    WebDriverWait(driver, timeout).until(
        lambda d: not is_login_page(d)
    )
    wait_until_ready(driver, 40)




def activity_url(course_id: str) -> str:
    return f"{BASE_URL}/aic/{course_id}/teaching-act"


def create_url(course_id: str, term_id: str) -> str:
    return f"{BASE_URL}/aic/exam-hub/teach-exam/create/{course_id}/0/{term_id}?from=agentCourse"


def launch_driver(profile_dir: Path, headless: bool) -> webdriver.Edge:
    profile_dir.mkdir(parents=True, exist_ok=True)
    options = Options()
    options.binary_location = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--disable-notifications")
    options.add_argument("--start-maximized")
    options.page_load_strategy = "normal"
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1600,1200")
    return webdriver.Edge(options=options)


def open_activity_page(driver: webdriver.Edge, course_id: str, timeout: int = 600) -> None:
    """Open the activity page, allowing an interactive SSO login to finish first."""
    target = activity_url(course_id)
    driver.get(target)
    deadline = time.monotonic() + timeout
    login_seen = False
    while time.monotonic() < deadline:
        try:
            wait_until_ready(driver, 15)
        except TimeoutException:
            pass
        if is_login_page(driver):
            if not login_seen:
                print('检测到登录或统一认证页面；请完成登录，脚本会自动继续。', flush=True)
                login_seen = True
            remaining = max(1, int(deadline - time.monotonic()))
            wait_for_login_if_needed(driver, timeout=remaining)
            # Time spent by the user logging in must not consume the page-load budget.
            deadline = time.monotonic() + timeout
            # SSO may return to a portal or course home instead of the original URL.
            if '/teaching-act' not in (driver.current_url or ''):
                driver.get(target)
            continue
        if '/teaching-act' in (driver.current_url or ''):
            if '创建活动' in body_text(driver) and not visible(driver, '.route-loading'):
                return
        else:
            # Authentication has completed but its return address was not preserved.
            driver.get(target)
        time.sleep(1)
    raise RuntimeError(
        f'登录或教学活动页在 {timeout} 秒内仍未准备完成，当前 URL 为 {driver.current_url}'
    )


def resolve_exam_name(driver: webdriver.Edge, requested_name: str | None) -> str:
    """Use the configured default name when no name was supplied, then prevent duplicates."""
    exam_name = norm(requested_name) or DEFAULT_EXAM_NAME
    if exam_name in body_text(driver):
        raise RuntimeError(
            f"教学活动列表中已经出现“{exam_name}”，脚本为避免重复创建已停止；未打开保存操作。"
        )
    print(f"未找到同名考试，将新建考试：{exam_name}")
    return exam_name


def open_create_exam_page(driver: webdriver.Edge, course_id: str, term_id: str) -> None:
    # Use the page menu instead of guessing a POST endpoint. The site opens the
    # create form in a new tab during the observed manual flow.
    before = set(driver.window_handles)
    click_exact_text(driver, "创建活动", selectors="button")
    click_exact_text(driver, "创建考试", selectors=".option-item", timeout=20)
    try:
        WebDriverWait(driver, 20).until(
            lambda d: len(set(d.window_handles) - before) > 0
            or "/teach-exam/create/" in d.current_url
        )
        new_handles = list(set(driver.window_handles) - before)
        if new_handles:
            driver.switch_to.window(new_handles[-1])
        if "/teach-exam/create/" not in driver.current_url:
            raise TimeoutException("创建考试菜单未打开创建页")
    except TimeoutException:
        # A direct route is the documented route observed during the manual flow.
        driver.get(create_url(course_id, term_id))
    wait_for_login_if_needed(driver)
    wait_for(driver, 'input[placeholder="请输入考试名称"]', 40)
    wait_until_ready(driver)


def fill_exam_name(driver: webdriver.Edge, name: str) -> None:
    field = wait_for(driver, 'input[placeholder="请输入考试名称"]')
    field.clear()
    field.send_keys(name)


def select_class(driver: webdriver.Edge, class_code: str) -> None:
    try:
        buttons = WebDriverWait(driver, 30).until(
            lambda d: exact_text_elements(d, class_code, "button")
        )
    except TimeoutException as exc:
        raise RuntimeError(f"考试页面没有找到参与班级“{class_code}”。") from exc
    button = buttons[-1]
    # The page marks a selected class with the primary button class.
    if "pl-button--primary" not in (button.get_attribute("class") or ""):
        click_safely(driver, button)

    def selected(d: webdriver.Edge) -> bool:
        current = exact_text_elements(d, class_code, "button")
        return bool(current and "pl-button--primary" in (current[-1].get_attribute("class") or ""))

    WebDriverWait(driver, 10).until(selected)


def resource_titles(dialog: WebElement) -> list[str]:
    titles = []
    for item in dialog.find_elements(By.CSS_SELECTOR, ".homework-item"):
        title_nodes = item.find_elements(By.CSS_SELECTOR, ".title")
        title = norm(title_nodes[0].text if title_nodes else item.text)
        if title and title not in titles:
            titles.append(title)
    return titles


def find_resource(dialog: WebElement, paper_name: str) -> WebElement | None:
    for item in dialog.find_elements(By.CSS_SELECTOR, ".homework-item"):
        title_nodes = item.find_elements(By.CSS_SELECTOR, ".title")
        title = norm(title_nodes[0].text if title_nodes else item.text)
        if title == paper_name:
            return item
    return None


def wait_resource_list(driver: webdriver.Edge, dialog: WebElement, timeout: int = 15) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: bool(dialog.find_elements(By.CSS_SELECTOR, ".homework-item"))
        or "暂无数据" in norm(dialog.text)
    )


def choose_paper(driver: webdriver.Edge, course_name: str, paper_name: str) -> None:
    click_safely(driver, wait_for(driver, "button.btn-choose"))
    dialog = wait_for(driver, ".resource-dialog", 20)
    observed: list[str] = []

    def inspect_current_view() -> WebElement | None:
        time.sleep(0.8)
        observed.extend(resource_titles(dialog))
        return find_resource(dialog, paper_name)

    def confirm_item(item: WebElement) -> None:
        click_safely(driver, item)
        confirm = wait_for(driver, ".resource-dialog .primary.normal", 10)
        click_safely(driver, confirm)
        WebDriverWait(driver, 15).until(
            lambda d: not any(_is_displayed(e) for e in d.find_elements(By.CSS_SELECTOR, ".resource-dialog"))
        )

    # The manual flow uses 课程资源库 -> 信号与系统. Reacquire elements after
    # each click because the Vue list is rerendered.
    libraries = WebDriverWait(driver, 15).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, ".resource-dialog .library-item")
    )
    if len(libraries) >= 2:
        course_library = libraries[1]
        content = course_library.find_element(By.CSS_SELECTOR, ".library-item-content")
        click_safely(driver, content)
        child = WebDriverWait(driver, 10).until(
            lambda d: next(
                (
                    x
                    for x in d.find_elements(By.CSS_SELECTOR, ".resource-dialog .library-item-child-content")
                    if _is_displayed(x) and norm(x.text) == course_name
                ),
                False,
            )
        )
        click_safely(driver, child)
        try:
            wait_resource_list(driver, dialog, 20)
        except TimeoutException:
            pass
        item = inspect_current_view()
        if item is not None:
            confirm_item(item)
            return

    # Also inspect the recent/personal libraries in case the paper was saved
    # there rather than into the course library.
    for index in (0, 2):
        current = driver.find_elements(By.CSS_SELECTOR, ".resource-dialog .library-item")
        if index >= len(current):
            continue
        click_safely(driver, current[index].find_element(By.CSS_SELECTOR, ".library-item-content"))
        item = inspect_current_view()
        if item is not None:
            confirm_item(item)
            return

    raise RuntimeError(
        f"资源库中没有找到试卷“{paper_name}”。当前可见资源：{sorted(set(observed)) or ['无']}；"
        "脚本已停止，未保存、未发布。请确认登录账号和试卷所在资源库。"
    )


def normalize_question_numbers(values: list[int] | None) -> list[int]:
    """Validate and de-duplicate 1-based question positions while preserving order."""
    raw = QUESTION_NUMBERS if values is None else values
    result: list[int] = []
    for value in raw:
        number = int(value)
        if number <= 0:
            raise RuntimeError(f"题目编号必须是正整数，收到：{number}")
        if number not in result:
            result.append(number)
    return result


def open_question_config_page(driver: webdriver.Edge) -> None:
    """Switch the create page to 选题考试 and open its question configurator."""
    wait_route_idle(driver)
    mode = exact_text_elements(driver, "选题考试", "button")
    if not mode:
        raise RuntimeError("创建考试页面没有找到“选题考试”按钮。")
    if "pl-button--primary" not in (mode[-1].get_attribute("class") or ""):
        click_safely(driver, mode[-1])
    WebDriverWait(driver, 20).until(
        lambda d: bool(exact_text_elements(d, "配置试题", "button"))
    )
    wait_route_idle(driver)
    click_exact_text(driver, "配置试题", selectors="button", timeout=20)
    WebDriverWait(driver, 30).until(lambda d: "/questionbank-hub/config-question/" in d.current_url)
    wait_route_idle(driver, 30)


def select_questions_from_question_bank(driver: webdriver.Edge, question_numbers: list[int]) -> None:
    """Select arbitrary 1-based question positions from the visible course question bank."""
    open_question_config_page(driver)
    click_visible_exact(driver, "题库选择", selectors=".base-button-component", timeout=20)
    WebDriverWait(driver, 30).until(
        lambda d: bool(exact_text_elements(d, "完成选题", ".base-button-component"))
        and bool(d.find_elements(By.CSS_SELECTOR, ".question-item-container"))
    )
    wait_route_idle(driver, 30)

    rows: list[WebElement] = []
    for row in driver.find_elements(By.CSS_SELECTOR, ".question-item-container"):
        if not _is_displayed(row):
            continue
        # Chapter entries use the same container class but do not have a question type.
        if row.find_elements(By.CSS_SELECTOR, ".question-type"):
            rows.append(row)

    missing = [number for number in question_numbers if number > len(rows)]
    if missing:
        raise RuntimeError(
            f"题库当前可见题目只有 {len(rows)} 道，无法选择编号：{missing}。"
            "请检查题库筛选、章节或分页后再运行。"
        )

    for number in question_numbers:
        row = rows[number - 1]
        click_safely(driver, row.find_element(By.CSS_SELECTOR, ".select"))

    WebDriverWait(driver, 10).until(
        lambda d: f"已选中 {len(question_numbers)} 道题" in body_text(d)
    )
    click_visible_exact(driver, "完成选题", selectors=".base-button-component", timeout=20)
    WebDriverWait(driver, 30).until(
        lambda d: "/questionbank-hub/config-question/" in d.current_url
        and not exact_text_elements(d, "完成选题", ".base-button-component")
    )
    wait_route_idle(driver, 30)
    click_visible_exact(driver, "确定", selectors=".base-button-component", timeout=20)
    WebDriverWait(driver, 30).until(lambda d: "/teach-exam/create/" in d.current_url)
    wait_for(driver, "input[placeholder=\"请输入考试名称\"]", 30)
    wait_route_idle(driver, 30)



from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile, BadZipFile

BEIJING = timezone(timedelta(hours=8), 'Asia/Shanghai')
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


class ConfigError(ValueError):
    pass


class LoginRequired(RuntimeError):
    pass


def text(value) -> str:
    return '' if value is None else ' '.join(str(value).split())


def read_xlsx(path: Path) -> tuple[dict[str, list[list]], bool]:
    """Read shared/inline strings, numeric and ISO date cells (Excel/WPS)."""
    try:
        with ZipFile(path) as z:
            book = ET.fromstring(z.read('xl/workbook.xml'))
            props = book.find('s:workbookPr', NS)
            date1904 = props is not None and props.get('date1904') in ('1', 'true')
            rels = {e.get('Id'): e.get('Target') for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
            shared = []
            if 'xl/sharedStrings.xml' in z.namelist():
                shared = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
            result = {}
            for s in book.findall('s:sheets/s:sheet', NS):
                name = s.get('name')
                if name not in ('考试设置', '选题明细'):
                    continue
                target = rels[s.get(REL)]
                part = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
                data = ET.fromstring(z.read(part))
                rows = []
                for row in data.findall('s:sheetData/s:row', NS):
                    number = int(row.get('r'))
                    if number > 10000:
                        raise ConfigError(f'{name} 超过 10000 行，请缩小配置表。')
                    while len(rows) < number:
                        rows.append([])
                    values = rows[number - 1]
                    for cell in row.findall('s:c', NS):
                        ref = cell.get('r')
                        col = 0
                        for ch in re.match(r'[A-Z]+', ref).group():
                            col = col * 26 + ord(ch) - 64
                        if col > 20:
                            continue
                        if cell.find('s:f', NS) is not None:
                            raise ConfigError(f'{name}!{ref} 含公式，请填写实际值。')
                        while len(values) < col:
                            values.append(None)
                        kind = cell.get('t', 'n')
                        raw = cell.findtext('s:v', default='', namespaces=NS)
                        if kind == 'inlineStr':
                            value = ''.join(cell.find('s:is', NS).itertext())
                        elif kind == 's':
                            value = shared[int(raw)]
                        elif kind == 'e':
                            raise ConfigError(f'{name}!{ref} 存在 Excel 错误：{raw}')
                        elif kind == 'b':
                            value = raw == '1'
                        elif kind == 'n' and raw:
                            value = Decimal(raw)
                        else:
                            value = raw or None
                        values[col - 1] = value
                result[name] = rows
            return result, date1904
    except (BadZipFile, KeyError, ET.ParseError, OSError, InvalidOperation) as exc:
        raise ConfigError(f'无法读取配置表 {path.name}：{exc}') from exc


def parse_time(value, label: str, date1904: bool = False) -> datetime:
    if value is None or text(value) == '':
        raise ConfigError(f'请填写“{label}”。')
    if isinstance(value, bool):
        raise ConfigError(f'“{label}”不是有效时间。')
    if isinstance(value, (Decimal, int, float)):
        base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
        value = base + timedelta(seconds=round(float(value) * 86400))
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(text(value).replace('/', '-'))
        except ValueError as exc:
            raise ConfigError(f'“{label}”格式应为 2026-09-10 11:45:00。') from exc
    value = value.replace(tzinfo=BEIJING) if value.tzinfo is None else value.astimezone(BEIJING)
    if value.year < 2020 or value.year > 2100:
        raise ConfigError(f'“{label}”年份不合理，请核对。')
    # The observed platform date picker has minute precision. Never silently truncate.
    if value.second or value.microsecond:
        raise ConfigError(f'“{label}”请精确到分钟，秒数填写 00。')
    return value


def positive_int(value, label):
    try:
        number = Decimal(text(value))
    except InvalidOperation as exc:
        raise ConfigError(f'{label} 必须是正整数。') from exc
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ConfigError(f'{label} 必须是正整数。')
    return int(number)


def chapter_title(number: int) -> str:
    digits = '零一二三四五六七八九'
    if not 1 <= number <= 99:
        raise ConfigError('章节编号须在 1 到 99 之间；更复杂的目录需要单独适配。')
    cn = digits[number] if number < 10 else (('' if number < 20 else digits[number // 10]) + '十' + (digits[number % 10] if number % 10 else ''))
    return f'第{cn}章'


@dataclass(frozen=True)
class Question:
    chapter: int
    chapter_name: str
    number: int
    keyword: str
    score: Decimal | None


@dataclass(frozen=True)
class ExamConfig:
    exam_name: str
    course_name: str
    course_id: str
    term_id: str
    class_code: str
    start: datetime
    end: datetime
    release_at: datetime | None
    release_method: str
    questions: tuple[Question, ...]

    def summary(self):
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=str)


def load_config(path: Path, *, require_questions: bool = True) -> ExamConfig:
    sheets, date1904 = read_xlsx(path)
    if set(sheets) != {'考试设置', '选题明细'}:
        raise ConfigError('配置表必须包含“考试设置”和“选题明细”两个工作表。')
    settings = {}
    for row in sheets['考试设置'][1:]:
        row = row + [None, None]
        key = text(row[0])
        if not key:
            continue
        if key in settings:
            raise ConfigError(f'配置项重复：{key}')
        settings[key] = row[1]
    required = ['考试名称', '课程名称', '课程ID', '学期ID', '参与班级', '时区', '考试开始时间', '考试结束时间', '平台成绩发布方式', '考后成绩执行方式']
    missing = [key for key in required if not text(settings.get(key))]
    if missing:
        raise ConfigError('缺少配置：' + '、'.join(missing))
    if settings['时区'] != 'Asia/Shanghai':
        raise ConfigError('当前平台适配仅支持 Asia/Shanghai（北京时间）。')
    if settings['平台成绩发布方式'] != '手动发布':
        raise ConfigError('平台成绩发布方式必须设置为“手动发布”，避免提前公开成绩。')
    method = text(settings['考后成绩执行方式'])
    if method not in ('定时脚本发布', '人工发布'):
        raise ConfigError('考后成绩执行方式只能为“定时脚本发布”或“人工发布”。')
    start = parse_time(settings['考试开始时间'], '考试开始时间', date1904)
    end = parse_time(settings['考试结束时间'], '考试结束时间', date1904)
    if end <= start:
        raise ConfigError('考试结束时间必须晚于开始时间。')
    release = None
    if method == '定时脚本发布' or text(settings.get('计划成绩发布时间')):
        release = parse_time(settings.get('计划成绩发布时间'), '计划成绩发布时间', date1904)
        if release <= end:
            raise ConfigError('计划成绩发布时间必须晚于考试结束时间。')
    for key in ('课程ID', '学期ID', '参与班级'):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', text(settings[key])):
            raise ConfigError(f'“{key}”格式无效；当前每份配置仅支持一个班级。')
    questions = []
    seen = set()
    notes = {'填写规则', '编号含义', '原脚本限制'}
    for index, row in enumerate(sheets['选题明细'][1:], start=2):
        row = (row + [None] * 5)[:5]
        if not any(text(x) for x in row) or text(row[0]) in notes:
            continue
        chapter = positive_int(row[0], f'选题明细第 {index} 行章节')
        default_title = chapter_title(chapter)
        name = text(row[1]) or default_title
        if not (name.startswith(default_title) or re.match(rf'^第\s*{chapter}\s*章', name)):
            raise ConfigError(f'第 {index} 行章节名称与“第几章”不一致。')
        number = positive_int(row[2], f'选题明细第 {index} 行题号')
        key = chapter, number
        if key in seen:
            raise ConfigError(f'重复题目：第 {chapter} 章第 {number} 题。')
        seen.add(key)
        keyword = text(row[3])
        if not keyword:
            raise ConfigError(f'第 {index} 行请填写题干关键词，防止章内顺序变化后选错题。')
        score = None
        if text(row[4]):
            try:
                score = Decimal(text(row[4]))
            except InvalidOperation as exc:
                raise ConfigError(f'第 {index} 行分值不是数字。') from exc
            if not score.is_finite() or not 0 < score <= 1000 or score * 10 != (score * 10).to_integral_value():
                raise ConfigError(f'第 {index} 行分值须大于 0、不超过 1000，最多一位小数。')
        questions.append(Question(chapter, name, number, keyword, score))
    if require_questions and not questions:
        raise ConfigError('选题明细尚未填写：请逐行填写章节、章内顺序号和题干关键词。')
    return ExamConfig(text(settings['考试名称']), text(settings['课程名称']), text(settings['课程ID']), text(settings['学期ID']), text(settings['参与班级']), start, end, release, method, tuple(questions))


def _session_chapter_number(value) -> int:
    raw = re.sub(r'\s+', '', str(value or ''))
    match = re.search(r'第?(\d+)章', raw)
    if match:
        return positive_int(match.group(1), 'Session 章节')
    for number in range(1, 100):
        if raw.startswith(chapter_title(number)):
            return number
    raise ConfigError(f'Session 章节格式无效：{value}')


def config_from_session(base: ExamConfig, session) -> ExamConfig:
    questions = []
    seen = set()
    for index, record in enumerate(session.questions, start=1):
        chapter = _session_chapter_number(getattr(record, 'chapter', ''))
        number = getattr(record, 'question_number', None)
        keyword = text(getattr(record, 'keyword', ''))
        raw_score = getattr(record, 'score', 0)
        if number is None or not keyword:
            raise ConfigError(f'Session 第 {index} 题缺少题库编号或关键词。')
        number = positive_int(number, f'Session 第 {index} 题章内编号')
        key = chapter, number
        if key in seen:
            raise ConfigError(f'Session 包含重复题目：第 {chapter} 章第 {number} 题。')
        seen.add(key)
        try:
            score = Decimal(str(raw_score))
        except InvalidOperation as exc:
            raise ConfigError(f'Session 第 {index} 题分值无效。') from exc
        if not score.is_finite() or score <= 0:
            raise ConfigError(f'Session 第 {index} 题分值必须大于 0。')
        chapter_name = text(getattr(record, 'chapter', '')) or chapter_title(chapter)
        questions.append(Question(chapter, chapter_name, number, keyword, score))
    if not questions:
        raise ConfigError('Session 中没有可用于创建考试的题目。')
    return ExamConfig(
        text(session.exam_name) or base.exam_name,
        base.course_name,
        base.course_id,
        base.term_id,
        base.class_code,
        base.start,
        base.end,
        base.release_at,
        base.release_method,
        tuple(questions),
    )


def _set_session_status(session, session_path, status):
    if session is None or session_path is None:
        return
    update_session_status(session, status)
    save_session(session, session_path)


def _exam_state_path(config_path: Path, session_path: Path | None) -> Path:
    if session_path is not None:
        return session_path.with_name('exam_state.json')
    return config_path.resolve().parent / 'work' / 'exam_state.json'


def _write_exam_state(path: Path, config: ExamConfig, state: dict, status: str) -> None:
    exam_id = state.get('exam_id')
    if not exam_id:
        raise RuntimeError('运行记录中没有考试编号，无法保存 exam_state.json。')
    save_exam_state(
        path,
        exam_id=str(exam_id),
        status=status,
        end_time=config.end,
        answer_release_time=config.release_at,
    )


def release_gate(config: ExamConfig, *, now: datetime, actual_end: datetime) -> tuple[bool, str]:
    if now.tzinfo is None or actual_end.tzinfo is None:
        raise ConfigError('发布时间检查必须使用带时区的时间。')
    if config.release_method != '定时脚本发布' or config.release_at is None:
        return False, '配置为人工发布。'
    if actual_end != config.end:
        return False, '平台截止时间与配置不一致，请核对是否延长考试。'
    if now < config.release_at or now <= actual_end:
        return False, '尚未到达计划成绩发布时间，或考试仍未结束。'
    return True, '考试时间检查通过；尚未发布成绩。'




# Configuration-driven workflow. These helpers are bundled into the single script.
import hashlib
import os
import tempfile
from urllib.parse import urlparse, parse_qs
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys


def visible(driver, selector):
    return [e for e in driver.find_elements(By.CSS_SELECTOR, selector) if _is_displayed(e)]


def unique(driver, selector):
    elements = visible(driver, selector)
    if len(elements) != 1:
        raise RuntimeError(f'页面控件不唯一或未加载：{selector}（{len(elements)} 个）')
    return elements[0]


def set_input(driver, element, value):
    click_safely(driver, element)
    driver.execute_script('arguments[0].focus();', element)
    element.send_keys(Keys.CONTROL, 'a')
    element.send_keys(str(value))
    element.send_keys(Keys.TAB)


def fill_exam_name(driver, name):
    field = unique(driver, 'input[placeholder="请输入考试名称"]')
    set_input(driver, field, name)
    try:
        WebDriverWait(driver, 10).until(
            lambda d: unique(d, 'input[placeholder="请输入考试名称"]').get_attribute('value') == name
        )
    except TimeoutException as exc:
        actual = unique(driver, 'input[placeholder="请输入考试名称"]').get_attribute('value')
        raise RuntimeError(f'考试名称写入失败：当前“{actual}”。') from exc


def fill_exam_description(driver, description=DEFAULT_EXAM_DESCRIPTION):
    selector = 'textarea[placeholder="请输入考试相关的要求和信息"]'
    field = unique(driver, selector)
    if field.get_attribute('value') == description:
        return False
    set_input(driver, field, description)
    try:
        WebDriverWait(driver, 10).until(
            lambda d: unique(d, selector).get_attribute('value') == description
        )
    except TimeoutException as exc:
        actual = unique(driver, selector).get_attribute('value')
        raise RuntimeError(f'考试描述写入失败：当前“{actual}”。') from exc
    return True


def exact_button(driver, label):
    found = exact_text_elements(driver, label, 'button')
    if len(found) != 1:
        raise RuntimeError(f'按钮“{label}”不唯一或不可见。')
    return found[0]


def select_mode(driver, label):
    button = exact_button(driver, label)
    if 'pl-button--primary' not in (button.get_attribute('class') or ''):
        button.click()
    WebDriverWait(driver, 10).until(lambda d: 'pl-button--primary' in exact_button(d, label).get_attribute('class'))


def activate_question_exam_mode(driver):
    """Explicitly select 选题考试 and verify its unique configuration control."""
    question_mode = WebDriverWait(driver, 30).until(
        lambda d: next(iter(exact_text_elements(d, '选题考试', 'button')), False)
    )
    # pl-button--primary is also used as a visual style on this page, so it is
    # not reliable before interaction. Always perform the click, then compare
    # both mutually exclusive mode buttons and require a unique edit control.
    click_safely(driver, question_mode)

    def selected_question_control(current):
        question = exact_text_elements(current, '选题考试', 'button')
        custom = exact_text_elements(current, '自定义考试', 'button')
        if len(question) != 1 or len(custom) != 1:
            return False
        question_class = (question[0].get_attribute('class') or '').split()
        custom_class = (custom[0].get_attribute('class') or '').split()
        if 'pl-button--primary' not in question_class or 'pl-button--primary' in custom_class:
            return False
        configure = exact_text_elements(current, '配置试题', 'button')
        edit = exact_text_elements(current, '编辑', 'button')
        controls = configure if len(configure) == 1 else edit if len(edit) == 1 else []
        return controls[0] if controls else False

    try:
        return WebDriverWait(driver, 20).until(selected_question_control)
    except TimeoutException as exc:
        question = exact_text_elements(driver, '选题考试', 'button')
        custom = exact_text_elements(driver, '自定义考试', 'button')
        diagnostic = {
            'question_mode_class': question[0].get_attribute('class') if len(question) == 1 else None,
            'custom_mode_class': custom[0].get_attribute('class') if len(custom) == 1 else None,
            'configure_count': len(exact_text_elements(driver, '配置试题', 'button')),
            'edit_count': len(exact_text_elements(driver, '编辑', 'button')),
            'page_text': body_text(driver)[:600],
        }
        raise RuntimeError(
            '无法同时确认“选题考试”已选中以及唯一的试题编辑入口。'
            + json.dumps(diagnostic, ensure_ascii=False)
        ) from exc


def open_question_config_page(driver):
    # This site can keep a transparent .route-loading element mounted indefinitely.
    # Wait for the actual controls/navigation instead of that overlay's existence.
    button = activate_question_exam_mode(driver)
    click_safely(driver, button)
    WebDriverWait(driver, 30).until(lambda d: '/questionbank-hub/config-question/' in d.current_url)
    WebDriverWait(driver, 30).until(lambda d: exact_text_elements(d, '题库选择', '.base-button-component'))


def fill_schedule(driver, config):
    select_class(driver, config.class_code)
    select_mode(driver, '手动发布')
    start = unique(driver, 'input.el-range-input[placeholder="开始时间"]')
    end = unique(driver, 'input.el-range-input[placeholder="结束时间"]')
    if start.get_attribute('value') == config.start.strftime('%Y-%m-%d %H:%M') and end.get_attribute('value') == config.end.strftime('%Y-%m-%d %H:%M'):
        verify_schedule(driver, config)
        return
    # Element Plus range fields commit together on Enter/blur. Do not edit Vue state.
    start.click()
    start.send_keys(Keys.CONTROL, 'a')
    start.send_keys(config.start.strftime('%Y-%m-%d %H:%M'))
    end.click()
    end.send_keys(Keys.CONTROL, 'a')
    end.send_keys(config.end.strftime('%Y-%m-%d %H:%M'))
    end.send_keys(Keys.ENTER)
    end.send_keys(Keys.TAB)
    unique(driver, 'input[placeholder="请输入考试名称"]').click()
    verify_schedule(driver, config)


def verify_schedule(driver, config):
    for label, expected in [('开始时间', config.start), ('结束时间', config.end)]:
        field = unique(driver, f'input.el-range-input[placeholder="{label}"]')
        actual = parse_time(field.get_attribute('value'), label)
        if actual != expected:
            raise RuntimeError(f'{label}未正确写入：{actual}，预期 {expected}')
    if 'pl-button--primary' not in exact_button(driver, '手动发布').get_attribute('class'):
        raise RuntimeError('成绩发布方式不是“手动发布”。')
    if 'pl-button--primary' not in exact_button(driver, config.class_code).get_attribute('class'):
        raise RuntimeError('参与班级未正确选中。')


def configuration_exam_id(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    value = (query.get('examId') or [''])[0]
    if value.isdigit() and value != '0':
        return value
    match = re.search(r'/teach-exam/(?:create|examList)/[^/]+/(\d+)(?:/|$)', parsed.path)
    return match.group(1) if match and match.group(1) != '0' else None


def state_fingerprint(config):
    return hashlib.sha256(config.summary().encode('utf-8')).hexdigest()


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    state['updated_at'] = datetime.now(BEIJING).isoformat()
    handle, tmp = tempfile.mkstemp(prefix=path.stem + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_state(path, config):
    if not path.exists():
        return {'version': 1, 'config_fingerprint': state_fingerprint(config), 'exam_name': config.exam_name, 'status': 'new'}
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('config_fingerprint') != state_fingerprint(config):
        raise RuntimeError('配置表已改变，与运行记录不一致。请核对旧考试后使用新的 --state 文件，不会自动重复创建。')
    return state


class ProcessLock:
    """OS releases this lock on process exit, including crashes. No stale PID lock."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b'0')
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise RuntimeError('已有脚本正在处理同一份运行记录。') from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


def chapter_rows(driver):
    return [e for e in visible(driver, '.question-item-container') if not e.find_elements(By.CSS_SELECTOR, '.question-type')]


def question_rows(driver):
    return [e for e in visible(driver, '.question-item-container') if e.find_elements(By.CSS_SELECTOR, '.question-type')]


def count_badge(text_value):
    match = re.search(r'[（(]\s*(\d+)\s*[)）]\s*$', norm(text_value))
    return int(match.group(1)) if match else None


def course_path_element(driver, config, selector):
    """Resolve duplicate rendered breadcrumb copies for the current course."""
    def matching(elements):
        return [
            element
            for element in elements
            if re.sub(r'\s*[（(]\d+[)）]\s*$', '', norm(element.text)) == config.course_name
        ]

    matches = matching(visible(driver, selector))
    if not matches and selector == '.AGENT_COURSE-driver-anchor .title':
        # This release renders the breadcrumb class on a parent while the exact
        # title is a sibling. The course id in the route remains authoritative.
        matches = matching(visible(driver, '.title'))
    if not matches:
        visible_titles = [norm(e.text) for e in visible(driver, '.title') if norm(e.text)]
        raise RuntimeError(
            f'题库中没有找到当前课程路径“{config.course_name}”：'
            + json.dumps(
                {'selector': selector, 'url': driver.current_url, 'visible_titles': visible_titles[:30], 'page_text': body_text(driver)[:800]},
                ensure_ascii=False,
            )
        )
    if config.course_id not in (driver.current_url or ''):
        raise RuntimeError('题库地址中的课程编号与配置不一致。')
    # The responsive layout may render duplicate visible breadcrumb labels. They
    # carry the same exact course name while the URL supplies the stable identity.
    return matches[-1]


def select_configured_questions(driver, config):
    click_visible_exact(driver, '题库选择', selectors='.base-button-component')
    WebDriverWait(driver, 30).until(lambda d: len(chapter_rows(d)) > 0)
    course_path_element(driver, config, '.AGENT_COURSE-driver-anchor .title')
    selected = 0
    grouped = {}
    for q in config.questions:
        grouped.setdefault(q.chapter_name, []).append(q)
    for chapter_name, questions in grouped.items():
        if selected:
            course_link = course_path_element(driver, config, '.title.clickable')
            course_link.click()
            WebDriverWait(driver, 30).until(lambda d: len(chapter_rows(d)) > 0)
        matches = [row for row in chapter_rows(driver) if re.sub(r'\s*[（(]\d+[)）]\s*$', '', norm(row.text)) == chapter_name]
        if len(matches) != 1:
            raise RuntimeError(f'找不到唯一章节：{chapter_name}')
        total = count_badge(matches[0].text)
        if total is None or total == 0:
            raise RuntimeError(f'{chapter_name} 没有可核对的题目数量。')
        matches[0].find_element(By.CSS_SELECTOR, '.content').click()
        # Fail closed if folders, pagination or lazy loading mean the chapter is incomplete.
        WebDriverWait(driver, 30).until(lambda d: len(question_rows(d)) == total and not chapter_rows(d))
        rows = question_rows(driver)
        for q in questions:
            if q.number > len(rows):
                raise RuntimeError(f'{chapter_name} 只有 {len(rows)} 道题，无法选择第 {q.number} 题。')
            row = question_rows(driver)[q.number - 1]
            if q.keyword not in norm(row.text):
                raise RuntimeError(f'{chapter_name} 第 {q.number} 题与关键词“{q.keyword}”不符，停止选题。')
            if sum(q.keyword in norm(r.text) for r in question_rows(driver)) != 1:
                raise RuntimeError(f'关键词“{q.keyword}”匹配多道题，请填写更完整的题干片段。')
            control = row.find_element(By.CSS_SELECTOR, '.select')
            if 'active' not in control.get_attribute('class').split():
                ActionChains(driver).move_to_element(row).perform()
                WebDriverWait(driver, 10).until(lambda d: control.is_displayed())
                control.click()
            WebDriverWait(driver, 10).until(lambda d: 'active' in control.get_attribute('class').split())
            selected += 1
            WebDriverWait(driver, 10).until(lambda d: re.search(rf'已选中\s*{selected}\s*道题', body_text(d)))
    click_visible_exact(driver, '完成选题', selectors='.base-button-component')
    WebDriverWait(driver, 30).until(lambda d: len(visible(d, '.preview-question-content-item')) == len(config.questions))
    finish_question_config(driver, config)


def finish_question_config(driver, config):
    cards = visible(driver, '.preview-question-content-item')
    if len(cards) != len(config.questions):
        raise RuntimeError('草稿试题数量与配置不一致，停止自动修改。')
    for position, (q, card) in enumerate(zip(config.questions, cards), start=1):
        if q.keyword not in norm(card.text):
            raise RuntimeError(f'组卷后第 {position} 题与关键词“{q.keyword}”不符。')
        field = card.find_element(By.CSS_SELECTOR, 'input[placeholder="输入分值"]')
        if q.score is not None:
            set_input(driver, field, str(q.score))
        raw = field.get_attribute('value')
        if not raw or Decimal(raw) <= 0:
            raise RuntimeError(f'题目“{q.keyword}”没有正分值，请在配置表填写分值。')
        if q.score is not None and Decimal(raw) != q.score:
            raise RuntimeError(f'题目“{q.keyword}”分值未正确写入。')
    click_visible_exact(driver, '确定', selectors='.base-button-component')
    WebDriverWait(driver, 30).until(lambda d: '/teach-exam/create/' in d.current_url)
    wait_for(driver, 'input[placeholder="请输入考试名称"]')


def find_activity(driver, config):
    print('步骤：打开教学活动页。', flush=True)
    open_activity_page(driver, config.course_id)
    # Observed activity cards use p.name. Search the unique visible text field.
    fields = [e for e in visible(driver, 'input') if e.get_attribute('type') in ('text', 'search')]
    if len(fields) != 1:
        raise RuntimeError('活动搜索框不唯一，无法可靠检查同名考试。')
    set_input(driver, fields[0], config.exam_name)
    fields[0].send_keys(Keys.ENTER)
    print('步骤：搜索并核对考试名称。', flush=True)
    def loaded(d):
        names = visible(d, 'p.name')
        summary = body_text(d)
        count = re.search(r'共有\s*(\d+)\s*个教学活动', summary)
        if not count:
            if not names and summary.count('暂无数据') >= 2 and not visible(d, '.route-loading'):
                return ('loaded', [])
            return False
        n = int(count.group(1))
        if n == 0 and not names:
            return ('loaded', [])
        if n == len(names) and all(config.exam_name in norm(e.text) for e in names):
            return ('loaded', names)
        return False
    # Debounced search must settle before using zero or stale counts.
    time.sleep(1)
    try:
        _, names = WebDriverWait(driver, 30).until(loaded)
    except TimeoutException as exc:
        summary = body_text(driver)
        count = re.search(r'共有\s*(\d+)\s*个教学活动', summary)
        diagnostic = {'count': count.group(1) if count else None, 'names': [norm(e.text) for e in visible(driver, 'p.name')], 'search_value': fields[0].get_attribute('value'), 'page_text': summary[:750]}
        raise RuntimeError('搜索列表未就绪：' + json.dumps(diagnostic, ensure_ascii=False)) from exc
    return [e for e in names if norm(e.text) == config.exam_name]


def open_existing(driver, config, expected_id=None):
    matches = find_activity(driver, config)
    if len(matches) != 1:
        raise RuntimeError(f'找到 {len(matches)} 个同名考试，无法自动恢复或发布。')
    before = set(driver.window_handles)
    matches[0].click()
    WebDriverWait(driver, 30).until(lambda d: set(d.window_handles) - before or '/teach-exam/' in d.current_url)
    added = list(set(driver.window_handles) - before)
    if added:
        driver.switch_to.window(added[-1])
    WebDriverWait(driver, 30).until(lambda d: configuration_exam_id(d.current_url))
    actual_id = configuration_exam_id(driver.current_url)
    if expected_id and actual_id != expected_id:
        raise RuntimeError('考试编号与运行记录不一致，停止操作。')
    if '/create/' in driver.current_url:
        wait_for(driver, 'input[placeholder="请输入考试名称"]', 40)
        WebDriverWait(driver, 30).until(
            lambda d: unique(d, 'input[placeholder="请输入考试名称"]').get_attribute('value')
        )
    return actual_id


def open_recorded_draft(driver, config, exam_id):
    if not str(exam_id).isdigit():
        raise RuntimeError('运行记录中的考试编号无效。')
    url = f'{BASE_URL}/aic/exam-hub/teach-exam/create/{config.course_id}/{exam_id}/{config.term_id}?from=agentCourse'
    driver.get(url)
    wait_for_login_if_needed(driver)
    wait_for(driver, 'input[placeholder="请输入考试名称"]', 40)
    if configuration_exam_id(driver.current_url) != str(exam_id):
        raise RuntimeError('恢复后的考试编号不一致。')


def verify_create_form(driver, config):
    try:
        WebDriverWait(driver, 30).until(
            lambda d: unique(d, 'input[placeholder="请输入考试名称"]').get_attribute('value') == config.exam_name
        )
    except TimeoutException as exc:
        actual = unique(driver, 'input[placeholder="请输入考试名称"]').get_attribute('value')
        raise RuntimeError(f'考试名称未正确保留：当前“{actual}”。') from exc
    description = unique(driver, 'textarea[placeholder="请输入考试相关的要求和信息"]').get_attribute('value')
    if description != DEFAULT_EXAM_DESCRIPTION:
        raise RuntimeError(f'考试描述未正确保留：当前“{description}”。')
    verify_schedule(driver, config)
    expected_total = sum((q.score or Decimal('0')) for q in config.questions)
    full_score = unique(driver, 'input[placeholder="请输入满分值"]').get_attribute('value')
    if not full_score or Decimal(full_score) != expected_total:
        raise RuntimeError(f'平台满分值不是配置总分 {expected_total}。')
    selected = re.search(r'已选题目[:：]\s*(\d+)\s*道', body_text(driver))
    if not selected or int(selected.group(1)) != len(config.questions):
        raise RuntimeError('平台已选题目数量与配置不一致。')


def prepare_exam(driver, config, state, state_path):
    resume_empty = state.get('exam_id') and state.get('status') in ('draft_created', 'creating')
    if state.get('exam_id') and not resume_empty:
        raise RuntimeError('已有考试编号。为避免重复选题，请检查既有草稿；可用 --check 检查或 --run 恢复已保存考试。')
    if state.get('status') != 'new' and not resume_empty:
        raise RuntimeError('上次创建结果不确定，请先在平台核对草稿；本次不会重复创建。')
    if datetime.now(BEIJING) >= config.start:
        raise RuntimeError('考试开始时间已到，停止创建。')
    if resume_empty:
        open_recorded_draft(driver, config, state['exam_id'])
        if '/create/' not in driver.current_url:
            raise RuntimeError('已发布考试不能继续配置草稿。')
        wait_for(driver, 'input[placeholder="请输入考试名称"]')
    else:
        if find_activity(driver, config):
            raise RuntimeError('平台已有同名考试，停止重复创建。')
        print('步骤：打开创建考试页。', flush=True)
        open_create_exam_page(driver, config.course_id, config.term_id)
    fill_exam_name(driver, config.exam_name)
    fill_exam_description(driver)
    print('步骤：填写考试时间和成绩发布方式。', flush=True)
    fill_schedule(driver, config)
    # Configure questions auto-creates a server-side draft. Persist intent first.
    state['status'] = 'draft_created' if resume_empty else 'creating'
    save_state(state_path, state)
    print('步骤：打开配置试题（平台会创建草稿）。', flush=True)
    open_question_config_page(driver)
    state['exam_id'] = configuration_exam_id(driver.current_url)
    state['status'] = 'draft_created'
    save_state(state_path, state)
    if not state['exam_id']:
        raise RuntimeError('平台未返回考试编号，停止后续操作。')
    WebDriverWait(driver, 30).until(lambda d: re.search(r'题目数[:：]\s*\d+\s*道', body_text(d)))
    print('步骤：按章节、顺序号和关键词选题并设置分值。', flush=True)
    if re.search(r'题目数[:：]\s*0\s*道', body_text(driver)):
        select_configured_questions(driver, config)
    else:
        finish_question_config(driver, config)
    # Returning from the question bank can reload stale form values.
    fill_schedule(driver, config)
    fill_exam_name(driver, config.exam_name)
    fill_exam_description(driver)
    duration = visible(driver, 'input[placeholder="请设置考试时长（如：60分）"]')
    if len(duration) == 1:
        # Limit duration to the configured exam window, rounded down to whole minutes.
        minutes = int((config.end - config.start).total_seconds() // 60)
        set_input(driver, duration[0], minutes)
        if Decimal(duration[0].get_attribute('value')) != minutes:
            raise RuntimeError('考试时长写入失败。')
    verify_create_form(driver, config)
    state['edit_url'] = driver.current_url
    state['exam_mode'] = 'question_bank'
    state['status'] = 'prepared'
    save_state(state_path, state)


def repair_legacy_question_exam(driver, config, state, state_path):
    """Repair drafts saved before exam-mode verification was introduced."""
    expected_id = str(state.get('exam_id') or '')
    if not expected_id.isdigit():
        raise RuntimeError('运行记录中的考试编号无效，不能修复考试类型。')
    print('步骤：重新选择“选题考试”并核对题目配置。', flush=True)
    state['status'] = 'draft_created'
    save_state(state_path, state)
    open_question_config_page(driver)
    if configuration_exam_id(driver.current_url) != expected_id:
        raise RuntimeError('切换考试类型后的考试编号与运行记录不一致。')
    WebDriverWait(driver, 30).until(lambda d: re.search(r'题目数[:：]\s*\d+\s*道', body_text(d)))
    if re.search(r'题目数[:：]\s*0\s*道', body_text(driver)):
        select_configured_questions(driver, config)
    else:
        finish_question_config(driver, config)
    fill_schedule(driver, config)
    fill_exam_name(driver, config.exam_name)
    fill_exam_description(driver)
    duration = visible(driver, 'input[placeholder="请设置考试时长（如：60分）"]')
    if len(duration) == 1:
        minutes = int((config.end - config.start).total_seconds() // 60)
        set_input(driver, duration[0], minutes)
        if Decimal(duration[0].get_attribute('value')) != minutes:
            raise RuntimeError('考试时长写入失败。')
    verify_create_form(driver, config)
    state['exam_mode'] = 'question_bank'
    state['edit_url'] = driver.current_url
    state['status'] = 'prepared'
    save_state(state_path, state)
    save_prepared(driver, config, state, state_path)


def save_prepared(driver, config, state, state_path):
    verify_create_form(driver, config)
    state['status'] = 'saving'
    save_state(state_path, state)
    exact_button(driver, '保存').click()
    # Confirm persisted settings by reopening the exact saved activity.
    time.sleep(1)
    open_existing(driver, config, state['exam_id'])
    if '/create/' not in driver.current_url:
        raise RuntimeError('保存后未进入预期草稿编辑页，需核对考试状态。')
    wait_for(driver, 'input[placeholder="请输入考试名称"]')
    verify_create_form(driver, config)
    state['status'] = 'saved'
    state['edit_url'] = driver.current_url
    state.pop('last_error', None)
    state.pop('error_trace', None)
    state.pop('last_page', None)
    save_state(state_path, state)
    print(f'已保存并重新读取验证：{config.exam_name}，考试编号 {state["exam_id"]}。')


def recover_saving(driver, config, state, state_path):
    open_existing(driver, config, state.get('exam_id'))
    if '/create/' not in driver.current_url:
        raise RuntimeError('保存恢复时考试已不是草稿，请人工核对。')
    verify_create_form(driver, config)
    state['status'] = 'saved'
    state['edit_url'] = driver.current_url
    save_state(state_path, state)
    print('已从平台重新读取并确认上次保存成功。')


def confirm_known_dialog(driver, words):
    dialogs = [e for e in visible(driver, '[role="dialog"]') if any(w in norm(e.text) for w in words)]
    if not dialogs:
        return False
    if len(dialogs) != 1:
        raise RuntimeError('出现多个确认框，停止操作。')
    candidates = []
    selected_label = None
    for label in ('确定', '确认发布', '发布成绩', '确认', '发布'):
        candidates = driver.execute_script(
            """
            const root = arguments[0], target = arguments[1];
            const norm = value => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = element => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
            return [...root.querySelectorAll('*')].filter(element =>
                visible(element) && norm(element.innerText) === target &&
                ![...element.children].some(child => visible(child) && norm(child.innerText) === target)
            );
            """,
            dialogs[0],
            label,
        )
        if candidates:
            selected_label = label
            break
    if len(candidates) != 1:
        raise RuntimeError(f'发布确认控件“{selected_label or "确定"}”不唯一（{len(candidates)} 个），停止操作。')
    driver.execute_script('arguments[0].click();', candidates[0])
    return True


def publish_exam(driver, config, state, state_path):
    if datetime.now(BEIJING) >= config.start:
        raise RuntimeError('考试开始时间已到，停止自动发布考试。')
    WebDriverWait(driver, 60).until(
        lambda d: not any(_is_displayed(e) for e in d.find_elements(By.CSS_SELECTOR, '.route-loading'))
    )
    verify_create_form(driver, config)
    state['status'] = 'publishing_exam'
    save_state(state_path, state)
    publish_button = exact_button(driver, '发布')
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", publish_button)
    driver.execute_script('arguments[0].click();', publish_button)

    def publish_response(current):
        dialogs = [e for e in visible(current, '[role="dialog"]') if '发布' in norm(e.text)]
        if dialogs:
            return 'dialog'
        if '/create/' not in (current.current_url or ''):
            return 'navigated'
        return False

    try:
        response = WebDriverWait(driver, 15).until(publish_response)
    except TimeoutException as exc:
        errors = [norm(e.text) for e in visible(driver, '.el-form-item__error,.el-message,.el-notification') if norm(e.text)]
        raise RuntimeError(
            '点击发布后页面没有出现确认框或跳转：'
            + json.dumps({'errors': errors, 'page_text': body_text(driver)[-900:]}, ensure_ascii=False)
        ) from exc
    if response == 'dialog':
        if not confirm_known_dialog(driver, ('发布考试', '发布')):
            raise RuntimeError('检测到发布确认框，但无法确认唯一的确认按钮。')
        try:
            WebDriverWait(driver, 20).until(lambda d: '/create/' not in (d.current_url or ''))
        except TimeoutException:
            # Some releases keep the edit route after success; the activity list
            # below is the authoritative read-back.
            pass
    open_existing(driver, config, state['exam_id'])
    if '/examList/' not in driver.current_url:
        raise RuntimeError('无法确认考试已发布，停止自动重试。')
    state['detail_url'] = driver.current_url
    state['status'] = 'waiting'
    save_state(state_path, state)
    print('考试已发布，后续仅在配置的成绩发布时间到达后检查成绩。')


def grade_snapshot(driver, config):
    # Read only the configured class and its actual exam window.
    WebDriverWait(driver, 30).until(lambda d: exact_text_elements(d, '发布成绩', 'button'))
    links = exact_text_elements(driver, '查看考试截止时间', 'span,div')
    # Parents with identical text can repeat: choose the smallest leaf element.
    if not links:
        raise RuntimeError('无法找到考试截止时间入口。')
    links[-1].click()
    dialog = WebDriverWait(driver, 15).until(lambda d: next((e for e in visible(d, '[role="dialog"]') if '各个班级考试截止时间' in e.text), False))
    classes = dialog.find_elements(By.CSS_SELECTOR, '.class-item')
    # Publishing affects the whole exam. Do not accidentally publish other classes.
    if len(classes) != 1 or norm(classes[0].find_element(By.CSS_SELECTOR, 'h5').text) != config.class_code:
        raise RuntimeError('平台参与班级与单班配置不一致。')
    actual_start = parse_time(classes[0].find_element(By.CSS_SELECTOR, 'input[placeholder="开始时间"]').get_attribute('value'), '平台开始时间')
    actual_end = parse_time(classes[0].find_element(By.CSS_SELECTOR, 'input[placeholder="结束时间"]').get_attribute('value'), '平台结束时间')
    dialog.find_element(By.CSS_SELECTOR, '.close-btn').click()
    if actual_start != config.start:
        raise RuntimeError('平台开始时间与配置不一致。')
    return {'actual_end': actual_end}


def publication_counts(driver):
    """Scan all result pages without relying on visually hidden table headers."""

    def page_button(selector):
        return next((element for element in visible(driver, selector)), None)

    def enabled(element):
        if element is None:
            return False
        classes = element.get_attribute('class') or ''
        return (
            element.is_enabled()
            and element.get_attribute('disabled') is None
            and 'is-disabled' not in classes
        )

    WebDriverWait(driver, 60).until(
        lambda d: visible(d, '.el-table__body-wrapper tbody tr')
    )
    previous = page_button('button.btn-prev')
    while enabled(previous):
        previous.click()
        time.sleep(0.5)
        previous = page_button('button.btn-prev')
    total, published, unpublished = 0, 0, 0
    page = 0
    while True:
        page += 1
        if page > 200:
            raise RuntimeError('成绩分页超过检查上限。')
        rows = visible(driver, '.el-table__body-wrapper tbody tr')
        row_texts = [norm(row.text) for row in rows]
        if not row_texts:
            raise RuntimeError('成绩列表为空，不能确认发布结果。')
        if any('未发布' not in text and '已发布' not in text for text in row_texts):
            raise RuntimeError('成绩行缺少可识别的发布状态。')
        total += len(row_texts)
        unpublished += sum('未发布' in text for text in row_texts)
        published += sum('未发布' not in text and '已发布' in text for text in row_texts)
        nxt = page_button('button.btn-next')
        if not enabled(nxt):
            break
        first_row = row_texts[0]
        nxt.click()
        WebDriverWait(driver, 30).until(
            lambda d: (
                visible(d, '.el-table__body-wrapper tbody tr')
                and norm(visible(d, '.el-table__body-wrapper tbody tr')[0].text) != first_row
            )
        )
        time.sleep(0.4)
    return {'total': total, 'published': published, 'unpublished': unpublished}


def publish_exam_answers(driver, config, state, state_path):
    """Publish the paper and answers to every configured class after grades."""
    open_existing(driver, config, state['exam_id'])
    WebDriverWait(driver, 60).until(
        lambda d: exact_text_elements(d, '发布成绩', 'button')
    )
    more = WebDriverWait(driver, 30).until(
        lambda d: next((element for element in visible(d, '.handle-more')), False)
    )
    more.click()
    menu_item = WebDriverWait(driver, 15).until(
        lambda d: next(
            (
                element
                for element in visible(d, '[role="menuitem"]')
                if norm(element.text) == '公布试卷及答案'
            ),
            False,
        )
    )
    menu_item.click()
    dialog = WebDriverWait(driver, 15).until(
        lambda d: next(
            (
                element
                for element in visible(d, '[role="dialog"]')
                if '公布试卷及答案' in norm(element.text)
            ),
            False,
        )
    )
    class_names = WebDriverWait(driver, 15).until(
        lambda d: [
            norm(element.text)
            for element in d.find_elements(
                By.CSS_SELECTOR,
                '.dialog-polymas-exam-publish li.clearfloat p[classid]',
            )
        ]
        or False
    )
    if class_names != [config.class_code]:
        raise RuntimeError('公布答案弹窗中的班级与配置不一致。')
    all_checkbox = WebDriverWait(driver, 15).until(
        lambda d: next(
            iter(
                d.find_elements(
                    By.CSS_SELECTOR,
                    '.dialog-polymas-exam-publish .all input.el-checkbox__original',
                )
            ),
            False,
        )
    )
    switches = WebDriverWait(driver, 15).until(
        lambda d: d.find_elements(
            By.CSS_SELECTOR,
            '.dialog-polymas-exam-publish input[role="switch"]',
        )
        or False
    )
    if len(switches) != 1:
        raise RuntimeError('公布答案弹窗中的班级开关数量异常。')

    if all_checkbox.is_selected() and all(
        element.get_attribute('aria-checked') == 'true'
        for element in switches
    ):
        cancel = next(
            element
            for element in visible(dialog, 'button')
            if norm(element.text) == '取消'
        )
        cancel.click()
        state['answers_published'] = True
        state['answers_published_at'] = datetime.now(BEIJING).isoformat()
        save_state(state_path, state)
        return '试卷及答案已经全部公布，无需重复操作。'

    all_checkbox.find_element(By.XPATH, 'ancestor::label[1]').click()
    WebDriverWait(driver, 15).until(
        lambda d: all_checkbox.is_selected()
        and all(element.get_attribute('aria-checked') == 'true' for element in switches)
    )
    state['answers_publication_status'] = 'publishing'
    save_state(state_path, state)
    confirm = next(
        (
            element
            for element in visible(dialog, 'button')
            if norm(element.text) == '确定'
        ),
        None,
    )
    if confirm is None:
        raise RuntimeError('公布答案弹窗中找不到确定按钮。')
    confirm.click()
    WebDriverWait(driver, 30).until(
        lambda d: not any(
            '公布试卷及答案' in norm(element.text)
            for element in visible(d, '[role="dialog"]')
        )
    )

    time.sleep(2)
    driver.refresh()
    time.sleep(2)
    WebDriverWait(driver, 60).until(
        lambda d: exact_text_elements(d, '发布成绩', 'button')
    )
    more = WebDriverWait(driver, 30).until(
        lambda d: next((element for element in visible(d, '.handle-more')), False)
    )
    more.click()
    menu_item = WebDriverWait(driver, 15).until(
        lambda d: next(
            (
                element
                for element in visible(d, '[role="menuitem"]')
                if norm(element.text) == '公布试卷及答案'
            ),
            False,
        )
    )
    menu_item.click()
    verify_dialog = WebDriverWait(driver, 15).until(
        lambda d: next(
            (
                element
                for element in visible(d, '[role="dialog"]')
                if '公布试卷及答案' in norm(element.text)
            ),
            False,
        )
    )
    verify_checkbox = WebDriverWait(driver, 15).until(
        lambda d: next(
            iter(
                d.find_elements(
                    By.CSS_SELECTOR,
                    '.dialog-polymas-exam-publish .all input.el-checkbox__original',
                )
            ),
            False,
        )
    )
    verify_switches = WebDriverWait(driver, 15).until(
        lambda d: d.find_elements(
            By.CSS_SELECTOR,
            '.dialog-polymas-exam-publish input[role="switch"]',
        )
        or False
    )
    if (
        not verify_checkbox.is_selected()
        or len(verify_switches) != 1
        or any(
            element.get_attribute('aria-checked') != 'true'
            for element in verify_switches
        )
    ):
        raise RuntimeError('公布答案后回读失败：全部公布或班级开关未保持开启。')
    cancel = next(
        element
        for element in visible(verify_dialog, 'button')
        if norm(element.text) == '取消'
    )
    cancel.click()
    state['answers_published'] = True
    state['answers_published_at'] = datetime.now(BEIJING).isoformat()
    state.pop('answers_publication_status', None)
    save_state(state_path, state)
    return '试卷及答案已全部公布。'


def check_or_release(driver, config, state, state_path, *, commit=False, before_release=None):
    open_existing(driver, config, state.get('exam_id'))
    if '/examList/' not in driver.current_url:
        return False, '考试尚未发布，仍是草稿。'
    if not state.get('exam_id'):
        # Read-only checks may inspect an existing exam; release needs an existing receipt.
        if commit:
            raise RuntimeError('没有本脚本创建并记录的考试编号，拒绝自动发布成绩。')
    snapshot = grade_snapshot(driver, config)
    ready, message = release_gate(config, now=datetime.now(BEIJING), actual_end=snapshot['actual_end'])
    state['last_check'] = {**snapshot, 'actual_end': snapshot['actual_end'].isoformat(), 'ready': ready, 'message': message}
    save_state(state_path, state)
    if not ready or not commit:
        return False, message
    counts = publication_counts(driver)
    if counts['unpublished'] == 0:
        state['status'] = 'grades_published'
        save_state(state_path, state)
        return True, '所有成绩已发布，无需重复操作。'
    if state.get('status') == 'releasing_grades':
        raise RuntimeError('上次成绩发布结果不确定；当前仍有未发布记录，请人工核对，脚本不会重复点击。')
    # Re-read the cutoff and all-review counts immediately before committing.
    snapshot = grade_snapshot(driver, config)
    ready, message = release_gate(config, now=datetime.now(BEIJING), actual_end=snapshot['actual_end'])
    if not ready:
        return False, message
    if before_release is not None:
        before_release()
    state['status'] = 'releasing_grades'
    save_state(state_path, state)
    exact_button(driver, '发布成绩').click()
    time.sleep(1)
    confirm_known_dialog(driver, ('发布成绩', '成绩'))
    driver.refresh()
    snapshot = grade_snapshot(driver, config)
    counts = publication_counts(driver)
    if counts['unpublished']:
        raise RuntimeError('尚未确认所有成绩发布成功，请核对运行记录和平台。')
    state['status'] = 'grades_published'
    save_state(state_path, state)
    return True, '已确认所有成绩发布成功。'


def launch_for_config(args):
    driver = launch_driver(args.profile_dir, args.headless)
    driver._exam_headless = args.headless
    if args.headless:
        base_config = load_config(Path(args.config), require_questions=False)
        target = activity_url(base_config.course_id)
        driver.get(target)
        wait_until_ready(driver, 40)
        if is_login_page(driver):
            print('教学平台登录已失效，正在弹出 Edge 登录窗口。', flush=True)
            driver.quit()
            driver = launch_driver(args.profile_dir, False)
            driver._exam_headless = False
            open_activity_page(driver, base_config.course_id)
            print('教学平台登录完成，正在恢复后台运行。', flush=True)
            driver.quit()
            driver = launch_driver(args.profile_dir, True)
            driver._exam_headless = True
    return driver


def open_visible_publish_review(driver, args, config, state):
    """Show the exact saved draft before the GUI asks for final publication."""
    if not getattr(args, 'headless', False):
        return driver
    print('发布前正在弹出考试页面，供用户核对当前考试信息。', flush=True)
    driver.quit()
    visible_driver = launch_driver(args.profile_dir, False)
    visible_driver._exam_headless = False
    open_existing(visible_driver, config, state['exam_id'])
    if '/create/' not in visible_driver.current_url:
        visible_driver.quit()
        raise RuntimeError('发布前无法打开考试编辑页供用户核对。')
    verify_create_form(visible_driver, config)
    return visible_driver


def wait_for_login_if_needed(
    driver: webdriver.Edge,
    timeout: int = 300,
) -> None:
    """
    若当前是登录页，只等待用户在 Edge 中完成登录。

    不依赖终端按 Enter；适用于 GUI / pythonw.exe。
    """
    if not is_login_page(driver):
        return

    print(
        "当前 Edge 配置需要登录。"
        "请在打开的 Edge 窗口中完成登录；"
        "登录成功后脚本会自动继续。",
        flush=True,
    )

    while is_login_page(driver):
        try:
            WebDriverWait(driver, 60).until(
                lambda d: not is_login_page(d)
            )
        except TimeoutException:
            print('仍在等待教学平台登录；完成后程序会自动继续。', flush=True)
    wait_until_ready(driver, 40)




def _scheduler_functions():
    from scheduler.windows_task import (
        create_grade_release_task,
        delete_grade_release_task,
        task_exists,
        task_name,
    )
    return create_grade_release_task, delete_grade_release_task, task_exists, task_name


def ensure_grade_release_task(
    config,
    state,
    config_path,
    state_path,
    profile_dir,
    *,
    exam_state_path=None,
    session_path=None,
):
    if config.release_method != '定时脚本发布' or config.release_at is None:
        raise RuntimeError('当前配置不是定时脚本发布，不能创建成绩发布任务。')
    if not state.get('exam_id'):
        raise RuntimeError('运行记录中没有考试编号，不能创建成绩发布任务。')
    if state.get('status') == 'grades_published':
        raise RuntimeError('成绩已经发布，不再创建计划任务。')
    if state.get('status') != 'waiting':
        raise RuntimeError('尚未确认考试已经发布，不能创建成绩发布任务。')
    exam_state_path = Path(exam_state_path or _exam_state_path(Path(config_path), session_path))
    exam_state = load_exam_state(exam_state_path)
    if exam_state.get('status') != 'published':
        raise RuntimeError('exam_state.status 不是 published，不能创建计划任务。')
    if str(exam_state.get('exam_id') or '') != str(state.get('exam_id') or ''):
        raise RuntimeError('exam_state.json 与运行记录中的考试编号不一致。')
    if config.release_at <= datetime.now(BEIJING):
        raise RuntimeError('计划成绩发布时间已经到达；请直接运行 --release-grades。')
    create_task, _, exists, get_name = _scheduler_functions()
    create_task(
        config_path,
        state_path,
        config.release_at,
        profile_dir=profile_dir,
        session_path=session_path,
    )
    name = get_name(config, state)
    if not exists(name):
        raise RuntimeError('schtasks.exe 返回成功，但重新查询不到成绩发布任务。')
    state['scheduled_grade_release'] = {
        'enabled': True,
        'task_name': name,
        'release_at': config.release_at.isoformat(),
        'created_at': datetime.now(BEIJING).isoformat(),
    }
    state.pop('last_error', None)
    state.pop('error_trace', None)
    save_state(state_path, state)
    print(f'成绩发布计划任务已创建：{name}，执行时间 {config.release_at:%Y-%m-%d %H:%M}（北京时间）。')


def delete_scheduled_grade_release(config, state, state_path):
    _, delete_task, _, _ = _scheduler_functions()
    delete_task(config, state)
    scheduled = state.get('scheduled_grade_release')
    if isinstance(scheduled, dict):
        scheduled['enabled'] = False
        scheduled['deleted_at'] = datetime.now(BEIJING).isoformat()
    save_state(state_path, state)


def verify_published_exam(driver, config, state):
    if not state.get('exam_id'):
        raise RuntimeError('运行记录中没有考试编号。')
    open_existing(driver, config, state['exam_id'])
    if '/examList/' not in driver.current_url:
        raise RuntimeError('考试尚未发布，不能创建成绩发布计划任务。')
    state['detail_url'] = driver.current_url
    state['status'] = 'waiting'


def confirm_exam_publish(
    args,
    config,
    *,
    confirmer=None,
) -> bool:
    """
    考试发布的最终人工门禁。

    GUI 模式：
        必须由 confirmer(config) 明确返回 True。

    CLI 备用模式：
        必须是交互终端，并由人工输入 YES。

    headless / 无人值守环境：
        禁止发布考试。

    注意：
    --publish-exam 只是 CLI 入口参数，不承担“人工认证”职责；
    真正的安全门始终是这里的人工作为确认。
    """
    if confirmer is not None:
        return bool(confirmer(config))

    if args.headless or not sys.stdin.isatty():
        raise RuntimeError(
            "当前环境无法进行终端确认。"
            "考试发布必须由人工确认；"
            "headless/无人值守环境禁止发布考试。"
        )

    print(
        "\n考试草稿已准备完成，"
        "请人工核对后决定是否发布："
    )
    print(f"  考试：{config.exam_name}")
    print(f"  班级：{config.class_code}")
    print(
        f"  时间："
        f"{config.start:%Y-%m-%d %H:%M} 至 "
        f"{config.end:%Y-%m-%d %H:%M}"
        "（北京时间）"
    )
    print(f"  题目：{len(config.questions)} 道")

    answer = input(
        "确认立即发布考试？"
        "输入 YES 发布，"
        "其他输入保留草稿并退出："
    ).strip()

    return answer == "YES"






def release_grades_once(args, config, state, state_path):
    if not state.get('exam_id'):
        raise RuntimeError('运行记录中没有考试编号，拒绝发布成绩。')
    if config.release_method != '定时脚本发布' or config.release_at is None:
        raise RuntimeError('当前配置不是定时脚本发布，拒绝发布成绩。')
    if state.get('status') == 'grades_published' and state.get('answers_published'):
        delete_scheduled_grade_release(config, state, state_path)
        print('成绩、试卷及答案已经发布；残留的 Windows 计划任务已清理。')
        return 0
    if datetime.now(BEIJING) < config.release_at:
        raise RuntimeError(f'尚未到计划成绩发布时间 {config.release_at:%Y-%m-%d %H:%M}。')
    if state.get('status') not in ('waiting', 'releasing_grades', 'grades_published'):
        raise RuntimeError(f'当前状态为 {state.get("status")}，不能执行独立成绩发布。')
    driver = launch_for_config(args)
    driver._exam_unattended = bool(args.headless)
    try:
        if state.get('status') != 'grades_published':
            done, message = check_or_release(
                driver,
                config,
                state,
                state_path,
                commit=True,
            )
            print(message)
            if not done:
                raise RuntimeError('本次未发布成绩；计划任务保留，请核对运行记录。')
        answer_message = publish_exam_answers(
            driver,
            config,
            state,
            state_path,
        )
        print(answer_message)
    finally:
        driver.quit()
    delete_scheduled_grade_release(config, state, state_path)
    print('成绩、试卷及答案发布成功，Windows 计划任务已删除。')
    return 0


def run_configuration(args, *, publish_confirmer=None):
    session_path = getattr(args, 'session', None)
    session_path = Path(session_path).resolve() if session_path else None
    session = load_session(session_path) if session_path else None
    config = load_config(args.config, require_questions=session is None)
    if session is not None:
        config = config_from_session(config, session)
    exam_state_path = _exam_state_path(Path(args.config), session_path)
    print(config.summary())
    if args.validate or args.dry_run or not (
        args.prepare or args.run or args.check or args.release_grades or args.schedule_grades
    ):
        print('配置校验通过。仅本地检查，没有打开浏览器、创建考试或发布成绩。')
        return 0

    state_path = args.state or args.config.with_suffix('.state.json')
    with ProcessLock(state_path.with_suffix('.lock')):
        state = load_state(state_path, config)
        if args.release_grades:
            return release_grades_once(args, config, state, state_path)
        if state.get('status') == 'grades_published':
            print('这场考试的成绩已发布，任务完成。')
            return 0

        driver = None
        try:
            if args.check:
                driver = launch_for_config(args)
                done, message = check_or_release(driver, config, state, state_path, commit=False)
                print(message)
                return 0

            if args.schedule_grades:
                driver = launch_for_config(args)
                verify_published_exam(driver, config, state)
                save_state(state_path, state)
                _write_exam_state(exam_state_path, config, state, 'published')
                _set_session_status(session, session_path, 'published')
                driver.quit()
                driver = None
                ensure_grade_release_task(
                    config,
                    state,
                    args.config,
                    state_path,
                    args.profile_dir,
                    exam_state_path=exam_state_path,
                    session_path=session_path,
                )
                _set_session_status(session, session_path, 'scheduled')
                return 0

            if state['status'] in ('new', 'draft_created') or (
                state['status'] == 'creating' and state.get('exam_id')
            ):
                if session is not None and session.status != 'uploaded':
                    raise RuntimeError(
                        f'Session 状态为 {session.status}，只有 uploaded 状态可以创建考试。'
                    )
                driver = launch_for_config(args)
                prepare_exam(driver, config, state, state_path)
                save_prepared(driver, config, state, state_path)
            elif state['status'] == 'saving':
                driver = launch_for_config(args)
                recover_saving(driver, config, state, state_path)
            elif state['status'] in ('saved', 'waiting', 'releasing_grades', 'publishing_exam'):
                print(f'恢复考试 {state.get("exam_id")}，状态：{state["status"]}')
            else:
                raise RuntimeError(
                    f'上次停在 {state["status"]}，可能存在未完成草稿。'
                    '请检查运行记录，避免重复创建。'
                )

            if args.prepare:
                if state.get('status') == 'saved':
                    _write_exam_state(exam_state_path, config, state, 'exam_created')
                    _set_session_status(session, session_path, 'exam_created')
                print('考试草稿已保存，未发布考试，未启动成绩定时发布。')
                return 0

            if state['status'] in ('saved', 'publishing_exam'):
                if driver is None:
                    driver = launch_for_config(args)
                open_existing(driver, config, state['exam_id'])
                if '/create/' in driver.current_url:
                    if state.get('exam_mode') != 'question_bank':
                        repair_legacy_question_exam(driver, config, state, state_path)
                        open_existing(driver, config, state['exam_id'])
                        if '/create/' not in driver.current_url:
                            raise RuntimeError('修复考试类型后无法重新打开草稿。')
                    description_changed = fill_exam_description(driver)
                    verify_create_form(driver, config)
                    if description_changed:
                        state['status'] = 'prepared'
                        save_state(state_path, state)
                        save_prepared(driver, config, state, state_path)
                        open_existing(driver, config, state['exam_id'])
                        if '/create/' not in driver.current_url:
                            raise RuntimeError('保存考试描述后无法重新打开草稿。')
                        verify_create_form(driver, config)
                    if state.get('status') == 'publishing_exam':
                        state['status'] = 'saved'
                    state.pop('last_error', None)
                    state.pop('error_trace', None)
                    state.pop('last_page', None)
                    save_state(state_path, state)
                    _write_exam_state(exam_state_path, config, state, 'exam_created')
                    driver = open_visible_publish_review(driver, args, config, state)
                    _set_session_status(session, session_path, 'waiting_publish_confirm')
                    if not confirm_exam_publish(args, config, confirmer=publish_confirmer):
                        print('已取消发布，考试继续保留为草稿。')
                        return 0
                    publish_exam(driver, config, state, state_path)
                    _write_exam_state(exam_state_path, config, state, 'published')
                    _set_session_status(session, session_path, 'published')
                else:
                    state['status'] = 'waiting'
                    state['detail_url'] = driver.current_url
                    save_state(state_path, state)
                    _write_exam_state(exam_state_path, config, state, 'published')
                    _set_session_status(session, session_path, 'published')

            if state.get('status') == 'waiting':
                _write_exam_state(exam_state_path, config, state, 'published')
            if config.release_method == '人工发布':
                print('配置为人工发布成绩，脚本结束。')
                return 0
            if driver:
                driver.quit()
                driver = None
            ensure_grade_release_task(
                config,
                state,
                args.config,
                state_path,
                args.profile_dir,
                exam_state_path=exam_state_path,
                session_path=session_path,
            )
            _set_session_status(session, session_path, 'scheduled')
            return 0
        except Exception as exc:
            state['last_error'] = (
                'LOGIN_REQUIRED' if isinstance(exc, LoginRequired) else f'{type(exc).__name__}: {exc}'
            )
            import traceback
            state['error_trace'] = traceback.format_exc()
            if driver:
                try:
                    state['last_page'] = driver.current_url.split('?')[0]
                except WebDriverException:
                    pass
            save_state(state_path, state)
            raise
        finally:
            if driver:
                driver.quit()


def parse_args():
    parser = argparse.ArgumentParser(description='读取 ExamSession 或 Excel 配置，创建考试并安排考后发布')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('考试配置表.xlsx'))
    parser.add_argument(
        '--session',
        type=Path,
        help='使用 ExamSession 中的本次题目映射；Excel 仍提供课程、班级和时间设置',
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--validate', action='store_true', help='只检查配置，不打开浏览器（默认）')
    group.add_argument('--dry-run', action='store_true', help='等同 --validate，绝不访问平台')
    group.add_argument('--prepare', action='store_true', help='创建并保存考试草稿，不发布考试、不等待成绩')
    group.add_argument('--run', action='store_true', help='创建/恢复考试，并创建成绩发布计划任务后退出')
    group.add_argument('--check', action='store_true', help='只读检查已存在考试的截止时间和发布状态')
    group.add_argument('--release-grades', action='store_true', help='计划任务入口：仅对已发布考试执行一次成绩发布')
    group.add_argument('--schedule-grades', action='store_true', help='为已发布考试创建或修复 Windows 成绩发布任务')
    parser.add_argument('--publish-exam', action='store_true', help='与 --run 配合，允许把考试草稿发布给配置班级')
    parser.add_argument('--state', type=Path, help='运行记录文件，默认与配置表同名的 .state.json')
    parser.add_argument('--profile-dir', type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument('--headless', action='store_true', help='无窗口运行；首次登录请不要使用')
    args = parser.parse_args()
    if args.publish_exam and not args.run:
        parser.error('--publish-exam 必须配合 --run')
    return args


def main():
    try:
        return run_configuration(parse_args())
    except KeyboardInterrupt:
        print('已停止。运行记录已保留，可用相同配置和 --run 恢复。')
        return 130
    except (ConfigError, RuntimeError, ValueError, TimeoutException, WebDriverException) as exc:
        print(f'脚本停止：{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

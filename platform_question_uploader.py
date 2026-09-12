from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from selenium import webdriver
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.options import Options
from selenium.webdriver.remote.webelement import WebElement
from responsive_wait import WebDriverWait
from platform_guidance import dismiss_platform_guidance, install_guidance_hook

from deepseek_question_generator import GeneratedQuestion, load_question_batch
from exam_session import load_session, save_session, update_question_upload
from question_review_gui_with_generation import question_batch_file_is_approved


QUESTION_BANK_BASE_URL = "https://aic.sysu.edu.cn/aic/questionbank-hub/"

QUESTION_TYPE_LABELS = {
    "single_choice": "单选题",
    "single-choice": "单选题",
    "单选": "单选题",
    "单选题": "单选题",
    "单项选择题": "单选题",
    "multiple_choice": "多选题",
    "multiple-choice": "多选题",
    "多选": "多选题",
    "多选题": "多选题",
    "true_false": "判断题",
    "true-false": "判断题",
    "判断": "判断题",
    "判断题": "判断题",
    "short_answer": "问答题",
    "short-answer": "问答题",
    "简答题": "问答题",
    "问答题": "问答题",
    "calculation": "计算题",
    "计算题": "计算题",
}

DIFFICULTY_LABELS = {
    "easy": "简单",
    "简单": "简单",
    "medium": "适中",
    "中等": "适中",
    "适中": "适中",
    "hard": "困难",
    "困难": "困难",
}

MATH_PATTERN = re.compile(r"(\\\[(?:.|\n)*?\\\]|\\\((?:.|\n)*?\\\))", re.DOTALL)


@dataclass(frozen=True)
class RichSegment:
    kind: Literal["text", "math"]
    value: str
    display: bool = False


@dataclass(frozen=True)
class UploadConfig:
    course_id: str
    term_id: str
    course_name: str
    profile_dir: Path
    edge_binary: str | None = None
    headless: bool = False
    login_timeout: int = 360
    page_timeout: int = 90

    def question_bank_url(self) -> str:
        return (
            f"{QUESTION_BANK_BASE_URL}?source=1&questionBankType=course"
            f"&type=question&courseId={self.course_id}&termId={self.term_id}"
        )


@dataclass
class UploadRecord:
    fingerprint: str
    status: Literal["pending", "saving", "uploaded", "uncertain"] = "pending"
    platform_question_id: str | None = None
    updated_at: float = field(default_factory=time.time)


@dataclass
class UploadState:
    batch_id: str
    questions: dict[str, UploadRecord] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "batch_id": self.batch_id,
            "questions": {key: asdict(value) for key, value in self.questions.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UploadState":
        return cls(
            batch_id=str(data["batch_id"]),
            questions={key: UploadRecord(**value) for key, value in data.get("questions", {}).items()},
        )


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_markdown_latex(value: str) -> list[RichSegment]:
    """Split reviewed Markdown text into plain text and platform formula inputs."""
    result: list[RichSegment] = []
    cursor = 0
    for match in MATH_PATTERN.finditer(value or ""):
        if match.start() > cursor:
            result.append(RichSegment("text", value[cursor:match.start()]))
        token = match.group(0)
        display = token.startswith(r"\[")
        result.append(RichSegment("math", token[2:-2].strip(), display=display))
        cursor = match.end()
    if cursor < len(value or ""):
        result.append(RichSegment("text", value[cursor:]))
    return result


def question_fingerprint(question: GeneratedQuestion) -> str:
    payload = {
        "question_type": normalize_text(question.question_type).casefold(),
        "chapter": normalize_text(question.chapter),
        "knowledge_point": normalize_text(question.knowledge_point),
        "difficulty": normalize_text(question.difficulty).casefold(),
        "stem": normalize_text(question.stem),
        "options": {
            str(key).upper(): normalize_text(value)
            for key, value in sorted((question.options or {}).items())
        },
        "answer": normalize_text(question.answer).upper(),
        "explanation": normalize_text(question.explanation),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_upload_state(
    path: Path,
    batch_id: str,
    *,
    reset_on_batch_mismatch: bool = False,
) -> UploadState:
    if not path.exists():
        return UploadState(batch_id=batch_id)
    state = UploadState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if state.batch_id != batch_id:
        if reset_on_batch_mismatch:
            previous_batch_id = state.batch_id
            state = UploadState(batch_id=batch_id)
            save_upload_state(path, state)
            print(
                "检测到上一批题目的上传状态，已自动重置："
                f"{previous_batch_id} → {batch_id}",
                flush=True,
            )
            return state
        raise RuntimeError("上传状态文件属于另一批题目，请更换状态文件或删除旧状态文件。")
    return state


def save_upload_state(path: Path, state: UploadState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _visible(elements: Iterable[WebElement]) -> list[WebElement]:
    result = []
    for element in elements:
        try:
            if element.is_displayed():
                result.append(element)
        except WebDriverException:
            continue
    return result


def _exact_visible_text(driver: webdriver.Edge, selector: str, text: str) -> WebElement | bool:
    wanted = normalize_text(text)
    for element in _visible(driver.find_elements(By.CSS_SELECTOR, selector)):
        try:
            if normalize_text(element.text) == wanted:
                return element
        except StaleElementReferenceException:
            # Vue/Element Plus may replace a button between is_displayed()
            # and .text while the question-bank page is still rendering.
            # Returning False lets WebDriverWait locate the replacement node.
            continue
    return False


def click_safely(driver: webdriver.Edge, element: WebElement) -> None:
    """Click a control even when a transient page layer intercepts WebDriver."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center',inline:'nearest'});",
        element,
    )
    try:
        WebDriverWait(driver, 5).until(
            lambda _: element.is_displayed() and element.is_enabled()
        )
        element.click()
    except WebDriverException:
        # The platform keeps some transparent/popover layers mounted after
        # their animation. A DOM click is stable across window sizes and does
        # not depend on screen coordinates.
        driver.execute_script("arguments[0].click();", element)


class QuestionBankUploader:
    def __init__(self, config: UploadConfig, *, driver: webdriver.Edge | None = None):
        self.config = config
        self.driver = driver
        self._owns_driver = driver is None
        self._active_headless = bool(config.headless and driver is None)
        if driver is not None:
            install_guidance_hook(driver)

    def launch(self, *, headless: bool | None = None) -> webdriver.Edge:
        if self.driver is not None:
            return self.driver
        if headless is None:
            headless = self.config.headless
        options = Options()
        if self.config.edge_binary:
            options.binary_location = self.config.edge_binary
        options.add_argument(f"--user-data-dir={self.config.profile_dir.resolve()}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.page_load_strategy = "eager"
        if headless:
            options.add_argument("--headless=new")
        self.driver = webdriver.Edge(options=options)
        install_guidance_hook(self.driver)
        self.driver.set_page_load_timeout(self.config.page_timeout)
        self._active_headless = bool(headless)
        return self.driver

    def close(self) -> None:
        if self.driver is not None and self._owns_driver:
            self.driver.quit()
            self.driver = None

    def _restart_browser(self, *, headless: bool) -> webdriver.Edge:
        if not self._owns_driver:
            raise RuntimeError("外部浏览器实例无法自动切换登录模式。")
        self.close()
        return self.launch(headless=headless)

    @property
    def wait(self) -> WebDriverWait:
        assert self.driver is not None
        return WebDriverWait(self.driver, self.config.page_timeout)

    def _login_required(self) -> bool:
        assert self.driver is not None
        url = self.driver.current_url.casefold()
        try:
            body = self.driver.find_element(By.TAG_NAME, "body").text
        except StaleElementReferenceException:
            # A successful login redirects and replaces the whole document.
            # Treat that transient state as "still waiting" so the next poll
            # reads the new page instead of aborting the upload.
            return True
        return "login" in url or "登录" in body and "课程题库" not in body

    def _navigate_to_question_bank(
        self,
        driver: webdriver.Edge,
        target: str,
    ) -> None:
        """Open a fresh question-bank document and wait until it is usable."""
        driver.get(target)
        WebDriverWait(driver, self.config.page_timeout).until(
            lambda current: current.execute_script(
                "return document.readyState"
            )
            in ("interactive", "complete")
        )
        dismiss_platform_guidance(driver)

    def open_question_bank(self) -> None:
        target = self.config.question_bank_url()
        driver = self.launch()
        self._navigate_to_question_bank(driver, target)
        if self._login_required():
            return_to_background = self._active_headless
            if return_to_background:
                print("教学平台登录已失效，正在弹出 Edge 登录窗口。", flush=True)
                driver = self._restart_browser(headless=False)
                self._navigate_to_question_bank(driver, target)
            print(
                "页面正在等待登录，请在打开的 Edge 中完成登录；"
                "登录后脚本会自动继续，关闭 Edge 可停止流程。",
                flush=True,
            )
            deadline = time.monotonic() + self.config.login_timeout
            while self._login_required():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("等待教学平台登录超过 6 分钟，已停止。")
                try:
                    WebDriverWait(driver, min(60, remaining)).until(
                        lambda _: not self._login_required()
                    )
                except TimeoutException:
                    print("仍在等待教学平台登录；完成后程序会自动继续。", flush=True)
            if return_to_background:
                print("教学平台登录完成，正在恢复后台运行。", flush=True)
                driver = self._restart_browser(headless=True)
            # 登录完成后必须重新访问目标 URL。即使登录跳转已经回到
            # 同一个地址，也不复用登录期间的文档或任何旧 DOM 元素。
            self._navigate_to_question_bank(driver, target)
        self.wait.until(lambda d: _exact_visible_text(d, ".base-button-component,button", "新增试题"))

    def open_manual_create(self) -> None:
        assert self.driver is not None
        add = self.wait.until(lambda d: _exact_visible_text(d, ".base-button-component,button", "新增试题"))
        click_safely(self.driver, add)
        manual = self.wait.until(
            lambda d: _exact_visible_text(
                d,
                ".el-dropdown-menu__item,[role='menuitem'],.cascade-operator .option-item",
                "手动新增",
            )
        )
        click_safely(self.driver, manual)
        self.wait.until(lambda d: len(_visible(d.find_elements(By.CSS_SELECTOR, ".tiptap.ProseMirror"))) >= 2)
        self.wait.until(lambda d: _exact_visible_text(d, "button,.base-button-component", "保存"))

    def select_question_type(self, question_type: str) -> str:
        assert self.driver is not None
        label = QUESTION_TYPE_LABELS.get(normalize_text(question_type).casefold())
        if not label:
            raise ValueError(f"暂不支持上传题型：{question_type}")
        select = self.wait.until(
            lambda d: next(iter(_visible(d.find_elements(By.CSS_SELECTOR, ".question-type-select"))), False)
        )
        click_safely(self.driver, select)
        option = self.wait.until(lambda d: _exact_visible_text(d, ".el-select-dropdown__item,[role='option']", label))
        click_safely(self.driver, option)
        self.wait.until(lambda _: label in normalize_text(select.text))
        return label

    def _editor_components(self) -> list[WebElement]:
        assert self.driver is not None
        return _visible(self.driver.find_elements(By.CSS_SELECTOR, ".polymas-editor-component"))

    def _move_caret_to_editor_end(self, editor: WebElement) -> None:
        """Place the ProseMirror selection at the end without a mouse click."""
        assert self.driver is not None
        self.driver.execute_script(
            "const editor=arguments[0];"
            "const doc=editor.ownerDocument;"
            "const selection=doc.defaultView.getSelection();"
            "const range=doc.createRange();"
            "editor.focus();"
            "range.selectNodeContents(editor);"
            "range.collapse(false);"
            "selection.removeAllRanges();"
            "selection.addRange(range);"
            "doc.dispatchEvent(new Event('selectionchange',{bubbles:true}));",
            editor,
        )

    def _verify_trailing_identifier(
        self,
        component: WebElement,
        identifier: str,
    ) -> None:
        editor = component.find_element(By.CSS_SELECTOR, ".tiptap.ProseMirror")
        rendered = normalize_text(editor.text)
        if not rendered.endswith(identifier):
            raise RuntimeError(
                f"平台题干写入后末尾缺少唯一标识 {identifier}，"
                "已停止保存，请重新运行上传。"
            )

    def fill_rich_content(self, component: WebElement, value: str) -> None:
        assert self.driver is not None
        editor = component.find_element(By.CSS_SELECTOR, ".tiptap.ProseMirror")
        click_safely(self.driver, editor)
        editor.send_keys(Keys.CONTROL, "a")
        editor.send_keys(Keys.BACKSPACE)
        for segment in parse_markdown_latex(value):
            if segment.kind == "text":
                if segment.value:
                    self._move_caret_to_editor_end(editor)
                    editor.send_keys(segment.value)
                continue
            # The toolbar click moves focus away from ProseMirror. Store an
            # explicit end-of-document selection first so the formula plugin
            # inserts at the same append position as the local source.
            self._move_caret_to_editor_end(editor)
            click_safely(
                self.driver,
                component.find_element(By.CSS_SELECTOR, '[data-menu-type="math"]'),
            )
            dialog = self.wait.until(
                lambda d: next(
                    (x for x in _visible(d.find_elements(By.CSS_SELECTOR, ".el-dialog")) if "数学公式编辑器" in x.text),
                    False,
                )
            )
            math_field = dialog.find_element(By.CSS_SELECTOR, "math-field")
            self.driver.execute_script(
                "arguments[0].value=arguments[1];"
                "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                math_field,
                segment.value,
            )
            confirm = next(
                x for x in _visible(dialog.find_elements(By.CSS_SELECTOR, "button,.base-button-component"))
                if normalize_text(x.text) == "确认"
            )
            click_safely(self.driver, confirm)
            self.wait.until(lambda d: dialog not in _visible(d.find_elements(By.CSS_SELECTOR, ".el-dialog")))
            editor = component.find_element(By.CSS_SELECTOR, ".tiptap.ProseMirror")
            self._move_caret_to_editor_end(editor)

    def _choose_answers(self, label: str, answer: str) -> None:
        assert self.driver is not None
        normalized = normalize_text(answer).upper()
        if label == "判断题":
            if normalized in {"A", "TRUE", "T", "对", "正确", "是"}:
                indexes = [0]
            elif normalized in {"B", "FALSE", "F", "错", "错误", "否"}:
                indexes = [1]
            else:
                raise ValueError(f"无法识别判断题答案：{answer}")
            selector = "input.el-radio__original"
        elif label == "单选题":
            if normalized not in {"A", "B", "C", "D"}:
                raise ValueError(f"单选题答案必须是 A/B/C/D：{answer}")
            indexes = [ord(normalized) - ord("A")]
            selector = "input.el-radio__original"
        else:
            letters = [x for x in re.findall(r"[A-D]", normalized)]
            indexes = sorted({ord(x) - ord("A") for x in letters})
            if not indexes:
                raise ValueError(f"多选题答案必须包含 A/B/C/D：{answer}")
            selector = "input.el-checkbox__original"
        inputs = self.driver.find_elements(By.CSS_SELECTOR, selector)
        if len(inputs) < max(indexes) + 1:
            raise RuntimeError(f"{label}答案控件数量异常。")
        for index in indexes:
            self.driver.execute_script("arguments[0].click();", inputs[index])

    def _select_difficulty(self, difficulty: str) -> None:
        assert self.driver is not None
        label = DIFFICULTY_LABELS.get(normalize_text(difficulty).casefold())
        if not label:
            return
        candidates = [
            element for element in _visible(self.driver.find_elements(By.CSS_SELECTOR, ".el-select"))
            if "question-type-select" not in (element.get_attribute("class") or "")
        ]
        for select in candidates:
            current = normalize_text(select.text)
            if current in set(DIFFICULTY_LABELS.values()):
                if current == label:
                    # The manual-create form already defaults to “适中”.
                    # Avoid reopening the dropdown after rich-text editors
                    # have triggered several asynchronous page updates.
                    return
                click_safely(self.driver, select)
                option = self.wait.until(
                    lambda d: _exact_visible_text(d, ".el-select-dropdown__item,[role='option']", label)
                )
                click_safely(self.driver, option)
                return

    def fill_question(self, question: GeneratedQuestion) -> None:
        label = self.select_question_type(question.question_type)
        components = self._editor_components()
        if label in {"单选题", "多选题"}:
            if len(components) < 6 or not question.options:
                raise RuntimeError(f"{label}编辑区域或本地选项数据不完整。")
            self.fill_rich_content(components[0], question.stem)
            for index, key in enumerate(("A", "B", "C", "D"), start=1):
                self.fill_rich_content(components[index], question.options[key])
            self._choose_answers(label, question.answer)
            self.fill_rich_content(components[-1], question.explanation)
        elif label == "判断题":
            if len(components) < 2:
                raise RuntimeError("判断题编辑区域数量异常。")
            self.fill_rich_content(components[0], question.stem)
            self._choose_answers(label, question.answer)
            self.fill_rich_content(components[-1], question.explanation)
        else:
            if len(components) < 3:
                raise RuntimeError(f"{label}编辑区域数量异常。")
            self.fill_rich_content(components[0], question.stem)
            self.fill_rich_content(components[1], question.answer)
            self.fill_rich_content(components[-1], question.explanation)
        current_components = self._editor_components()
        if not current_components:
            raise RuntimeError("题干编辑区域在填写后被页面替换，无法执行保存前校验。")
        self._verify_trailing_identifier(
            current_components[0],
            question.identifier,
        )
        self._select_difficulty(question.difficulty)

    def select_chapter(self, chapter: str) -> str:
        assert self.driver is not None
        modify = self.wait.until(
            lambda d: next(iter(_visible(d.find_elements(By.CSS_SELECTOR, ".save-region .modify-icon"))), False)
        )
        click_safely(self.driver, modify)
        dialog = self.wait.until(
            lambda d: next(
                (x for x in _visible(d.find_elements(By.CSS_SELECTOR, ".el-dialog")) if "保存至" in x.text),
                False,
            )
        )
        course_root = self.wait.until(
            lambda _: next(iter(_visible(dialog.find_elements(By.CSS_SELECTOR, '.el-tree-node[data-key="course"]'))), False)
        )
        click_safely(
            self.driver,
            course_root.find_element(By.CSS_SELECTOR, ":scope > .el-tree-node__content"),
        )

        course_title = self.wait.until(
            lambda _: next(
                (
                    x for x in _visible(dialog.find_elements(By.CSS_SELECTOR, ".custom-tree-node .title"))
                    if normalize_text(x.text).startswith(normalize_text(self.config.course_name))
                ),
                False,
            )
        )
        course_node = course_title.find_element(
            By.XPATH,
            "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' el-tree-node ')][1]",
        )
        click_safely(
            self.driver,
            course_node.find_element(
                By.CSS_SELECTOR,
                ":scope > .el-tree-node__content .expand-icon-wrapper",
            ),
        )

        wanted = normalize_text(chapter)
        chapter_node = self.wait.until(
            lambda _: self._unique_chapter_node(dialog, wanted)
        )
        chapter_choice = chapter_node.find_element(
            By.CSS_SELECTOR,
            ":scope > .el-tree-node__content .custom-tree-node",
        )
        click_safely(self.driver, chapter_choice)
        self.wait.until(
            lambda _: "is-current" in (chapter_node.get_attribute("class") or "")
            and "active" in (chapter_choice.get_attribute("class") or "")
        )
        save_here = next(
            x for x in _visible(dialog.find_elements(By.CSS_SELECTOR, "button,.base-button-component"))
            if normalize_text(x.text) == "保存到此处"
        )
        click_safely(self.driver, save_here)
        self.wait.until(lambda d: dialog not in _visible(d.find_elements(By.CSS_SELECTOR, ".el-dialog")))
        location = normalize_text(self.driver.find_element(By.CSS_SELECTOR, ".save-region-content").text)
        if wanted not in location:
            raise RuntimeError(f"保存位置核对失败：期望 {wanted}，实际 {location}")
        return location

    @staticmethod
    def _unique_chapter_node(dialog: WebElement, wanted: str) -> WebElement | bool:
        matches: list[WebElement] = []
        for title in _visible(dialog.find_elements(By.CSS_SELECTOR, ".custom-tree-node")):
            if normalize_text(title.text) == wanted:
                matches.append(
                    title.find_element(
                        By.XPATH,
                        "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' el-tree-node ')][1]",
                    )
                )
        if len(matches) > 1:
            raise RuntimeError(f"章节名称不唯一：{wanted}")
        return matches[0] if matches else False

    def _manual_form_is_blank(self) -> bool:
        """Return True only after the platform has created a fresh empty form."""
        components = self._editor_components()
        if len(components) < 2:
            return False
        try:
            editors = [
                component.find_element(
                    By.CSS_SELECTOR,
                    ".tiptap.ProseMirror",
                )
                for component in components
            ]
            return all(not normalize_text(editor.text) for editor in editors)
        except WebDriverException:
            return False

    def save_current_question(
        self,
        *,
        create_next: bool = False,
    ) -> str | None:
        assert self.driver is not None
        button_text = "保存并创建下一题" if create_next else "保存"
        save = self.wait.until(
            lambda d: _exact_visible_text(
                d,
                "button,.base-button-component",
                button_text,
            )
        )
        before_url = self.driver.current_url
        click_safely(self.driver, save)

        def saved(current: webdriver.Edge) -> bool:
            if current.current_url != before_url:
                return True
            for message in _visible(
                current.find_elements(
                    By.CSS_SELECTOR,
                    ".el-message,.el-notification",
                )
            ):
                try:
                    message_text = normalize_text(message.text)
                except StaleElementReferenceException:
                    continue
                if any(word in message_text for word in ("成功", "已保存")):
                    return True
            return create_next and self._manual_form_is_blank()

        try:
            self.wait.until(saved)
            if create_next:
                # A success toast can appear before the editor is replaced.
                # Wait for the new empty form before the caller fills the next
                # question, so no DOM element from the saved question is reused.
                self.wait.until(lambda _: self._manual_form_is_blank())
        except TimeoutException as exc:
            raise RuntimeError(
                f"点击“{button_text}”后未确认平台是否成功，"
                "状态已标记为 uncertain，请人工核对后再运行。"
            ) from exc
        match = re.search(r"/(?:question|detail)/(\w+)", self.driver.current_url)
        return match.group(1) if match else None


def upload_batch(
    config: UploadConfig,
    batch_path: Path,
    state_path: Path,
    *,
    commit: bool = False,
    assume_yes: bool = False,
    session_path: Path | None = None,
) -> UploadState:
    batch_path = batch_path.resolve()
    if not question_batch_file_is_approved(batch_path):
        raise RuntimeError("题目批次未全部通过人工审核，禁止进入平台填写或上传。")
    batch = load_question_batch(batch_path)
    state = load_upload_state(
        state_path,
        batch.batch_id,
        reset_on_batch_mismatch=True,
    )
    session = None
    if session_path is not None:
        session_path = Path(session_path).resolve()
        session = load_session(session_path)
        if session.status != "reviewed":
            raise RuntimeError(
                f"Session 状态为 {session.status}，只有 reviewed 状态允许上传题库。"
            )
    uploader = QuestionBankUploader(config)
    manual_form_open = False
    try:
        uploader.open_question_bank()
        for index, question in enumerate(batch.questions, start=1):
            fingerprint = question_fingerprint(question)
            record = state.questions.get(question.local_id)
            if record and record.fingerprint == fingerprint and record.status == "uploaded":
                if session is not None:
                    update_question_upload(
                        session,
                        question.local_id,
                        upload_status="uploaded",
                        platform_id=record.platform_question_id,
                    )
                    save_session(session, session_path)
                print(f"[{index}/{len(batch.questions)}] 已上传，跳过：{question.local_id}")
                continue
            if record and record.status in {"saving", "uncertain"}:
                raise RuntimeError(
                    f"题目 {question.local_id} 上次保存结果不确定。请先在课程题库核对，避免重复上传。"
                )
            if record and record.fingerprint != fingerprint and record.status == "uploaded":
                raise RuntimeError(f"题目 {question.local_id} 上传后又被修改，程序不会自动重复创建。")

            if not manual_form_open:
                uploader.open_manual_create()
            uploader.fill_question(question)
            location = uploader.select_chapter(question.chapter)
            print(f"[{index}/{len(batch.questions)}] 已填写：{question.local_id} → {location}", flush=True)

            if not commit:
                input("当前为核对模式，未点击保存。请在浏览器核对，按 Enter 关闭程序：")
                return state
            if not assume_yes:
                answer = input("确认保存这一题？输入 YES 后回车：").strip()
                if answer != "YES":
                    print("已停止，当前题未保存。")
                    return state

            state.questions[question.local_id] = UploadRecord(fingerprint=fingerprint, status="saving")
            save_upload_state(state_path, state)
            if session is not None:
                update_question_upload(session, question.local_id, upload_status="saving")
                save_session(session, session_path)
            create_next = any(
                not (
                    (later_record := state.questions.get(later.local_id))
                    and later_record.fingerprint == question_fingerprint(later)
                    and later_record.status == "uploaded"
                )
                for later in batch.questions[index:]
            )
            try:
                platform_id = uploader.save_current_question(
                    create_next=create_next,
                )
            except Exception:
                state.questions[question.local_id].status = "uncertain"
                state.questions[question.local_id].updated_at = time.time()
                save_upload_state(state_path, state)
                if session is not None:
                    update_question_upload(session, question.local_id, upload_status="uncertain")
                    save_session(session, session_path)
                raise
            state.questions[question.local_id] = UploadRecord(
                fingerprint=fingerprint,
                status="uploaded",
                platform_question_id=platform_id,
            )
            save_upload_state(state_path, state)
            if session is not None:
                update_question_upload(
                    session,
                    question.local_id,
                    upload_status="uploaded",
                    platform_id=platform_id,
                )
                save_session(session, session_path)
            print(f"[{index}/{len(batch.questions)}] 保存成功：{question.local_id}", flush=True)
            manual_form_open = create_next
    finally:
        uploader.close()

    return state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将人工审核通过的本地题目填写到课程题库。")
    parser.add_argument("--batch", type=Path, required=True, help="人工审核后的题目 JSON")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--term-id", required=True)
    parser.add_argument("--course-name", required=True)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path, help="上传状态文件，默认与题目 JSON 放在一起")
    parser.add_argument("--session", type=Path, help="同步更新的 ExamSession JSON")
    parser.add_argument("--edge-binary")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--commit", action="store_true", help="允许点击保存；仍会逐题要求终端确认")
    parser.add_argument("--yes", action="store_true", help="与 --commit 同用，跳过逐题终端确认")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.yes and not args.commit:
        raise SystemExit("--yes 必须与 --commit 一起使用。")
    state_path = args.state or args.batch.with_suffix(".upload-state.json")
    config = UploadConfig(
        course_id=args.course_id,
        term_id=args.term_id,
        course_name=args.course_name,
        profile_dir=args.profile_dir,
        edge_binary=args.edge_binary,
        headless=args.headless,
    )
    upload_batch(
        config,
        args.batch,
        state_path,
        commit=args.commit,
        assume_yes=args.yes,
        session_path=args.session,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

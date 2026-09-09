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
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.options import Options
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

from deepseek_question_generator import GeneratedQuestion, load_question_batch
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
    login_timeout: int = 600
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


def load_upload_state(path: Path, batch_id: str) -> UploadState:
    if not path.exists():
        return UploadState(batch_id=batch_id)
    state = UploadState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if state.batch_id != batch_id:
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
        if normalize_text(element.text) == wanted:
            return element
    return False


class QuestionBankUploader:
    def __init__(self, config: UploadConfig, *, driver: webdriver.Edge | None = None):
        self.config = config
        self.driver = driver
        self._owns_driver = driver is None

    def launch(self) -> webdriver.Edge:
        if self.driver is not None:
            return self.driver
        options = Options()
        if self.config.edge_binary:
            options.binary_location = self.config.edge_binary
        options.add_argument(f"--user-data-dir={self.config.profile_dir.resolve()}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--disable-blink-features=AutomationControlled")
        if self.config.headless:
            options.add_argument("--headless=new")
        self.driver = webdriver.Edge(options=options)
        return self.driver

    def close(self) -> None:
        if self.driver is not None and self._owns_driver:
            self.driver.quit()
            self.driver = None

    @property
    def wait(self) -> WebDriverWait:
        assert self.driver is not None
        return WebDriverWait(self.driver, self.config.page_timeout)

    def _login_required(self) -> bool:
        assert self.driver is not None
        url = self.driver.current_url.casefold()
        body = self.driver.find_element(By.TAG_NAME, "body").text
        return "login" in url or "登录" in body and "课程题库" not in body

    def open_question_bank(self) -> None:
        driver = self.launch()
        driver.get(self.config.question_bank_url())
        if self._login_required():
            print("页面正在等待登录，请在打开的 Edge 中完成登录；登录后脚本会自动继续。", flush=True)
            WebDriverWait(driver, self.config.login_timeout).until(lambda _: not self._login_required())
        self.wait.until(lambda d: _exact_visible_text(d, ".base-button-component,button", "新增试题"))

    def open_manual_create(self) -> None:
        assert self.driver is not None
        add = self.wait.until(lambda d: _exact_visible_text(d, ".base-button-component,button", "新增试题"))
        add.click()
        manual = self.wait.until(
            lambda d: _exact_visible_text(
                d,
                ".el-dropdown-menu__item,[role='menuitem'],.cascade-operator .option-item",
                "手动新增",
            )
        )
        self.driver.execute_script("arguments[0].click();", manual)
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
        select.click()
        option = self.wait.until(lambda d: _exact_visible_text(d, ".el-select-dropdown__item,[role='option']", label))
        self.driver.execute_script("arguments[0].scrollIntoView({block:'nearest'}); arguments[0].click();", option)
        self.wait.until(lambda _: label in normalize_text(select.text))
        return label

    def _editor_components(self) -> list[WebElement]:
        assert self.driver is not None
        return _visible(self.driver.find_elements(By.CSS_SELECTOR, ".polymas-editor-component"))

    def fill_rich_content(self, component: WebElement, value: str) -> None:
        assert self.driver is not None
        editor = component.find_element(By.CSS_SELECTOR, ".tiptap.ProseMirror")
        editor.click()
        editor.send_keys(Keys.CONTROL, "a")
        editor.send_keys(Keys.BACKSPACE)
        for segment in parse_markdown_latex(value):
            if segment.kind == "text":
                if segment.value:
                    editor.send_keys(segment.value)
                continue
            component.find_element(By.CSS_SELECTOR, '[data-menu-type="math"]').click()
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
            confirm.click()
            self.wait.until(lambda d: dialog not in _visible(d.find_elements(By.CSS_SELECTOR, ".el-dialog")))
            editor = component.find_element(By.CSS_SELECTOR, ".tiptap.ProseMirror")
            editor.click()

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
                select.click()
                option = self.wait.until(
                    lambda d: _exact_visible_text(d, ".el-select-dropdown__item,[role='option']", label)
                )
                self.driver.execute_script("arguments[0].click();", option)
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
        self._select_difficulty(question.difficulty)

    def select_chapter(self, chapter: str) -> str:
        assert self.driver is not None
        modify = self.wait.until(
            lambda d: next(iter(_visible(d.find_elements(By.CSS_SELECTOR, ".save-region .modify-icon"))), False)
        )
        modify.click()
        dialog = self.wait.until(
            lambda d: next(
                (x for x in _visible(d.find_elements(By.CSS_SELECTOR, ".el-dialog")) if "保存至" in x.text),
                False,
            )
        )
        course_root = self.wait.until(
            lambda _: next(iter(_visible(dialog.find_elements(By.CSS_SELECTOR, '.el-tree-node[data-key="course"]'))), False)
        )
        course_root.find_element(By.CSS_SELECTOR, ":scope > .el-tree-node__content").click()

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
        course_node.find_element(By.CSS_SELECTOR, ":scope > .el-tree-node__content .expand-icon-wrapper").click()

        wanted = normalize_text(chapter)
        chapter_node = self.wait.until(
            lambda _: self._unique_chapter_node(dialog, wanted)
        )
        chapter_choice = chapter_node.find_element(
            By.CSS_SELECTOR,
            ":scope > .el-tree-node__content .custom-tree-node",
        )
        self.driver.execute_script(
            "arguments[0].scrollIntoView({block:'nearest'}); arguments[0].click();",
            chapter_choice,
        )
        self.wait.until(
            lambda _: "is-current" in (chapter_node.get_attribute("class") or "")
            and "active" in (chapter_choice.get_attribute("class") or "")
        )
        save_here = next(
            x for x in _visible(dialog.find_elements(By.CSS_SELECTOR, "button,.base-button-component"))
            if normalize_text(x.text) == "保存到此处"
        )
        save_here.click()
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

    def save_current_question(self) -> str | None:
        assert self.driver is not None
        save = self.wait.until(lambda d: _exact_visible_text(d, "button,.base-button-component", "保存"))
        before_url = self.driver.current_url
        save.click()
        try:
            self.wait.until(
                lambda d: d.current_url != before_url
                or any(
                    word in normalize_text(x.text)
                    for x in _visible(d.find_elements(By.CSS_SELECTOR, ".el-message,.el-notification"))
                    for word in ("成功", "已保存")
                )
            )
        except TimeoutException as exc:
            raise RuntimeError("点击保存后未确认平台是否成功，状态已标记为 uncertain，请人工核对后再运行。") from exc
        match = re.search(r"/(?:question|detail)/(\w+)", self.driver.current_url)
        return match.group(1) if match else None


def upload_batch(
    config: UploadConfig,
    batch_path: Path,
    state_path: Path,
    *,
    commit: bool = False,
    assume_yes: bool = False,
) -> None:
    batch_path = batch_path.resolve()
    if not question_batch_file_is_approved(batch_path):
        raise RuntimeError("题目批次未全部通过人工审核，禁止进入平台填写或上传。")
    batch = load_question_batch(batch_path)
    state = load_upload_state(state_path, batch.batch_id)
    uploader = QuestionBankUploader(config)
    try:
        uploader.open_question_bank()
        for index, question in enumerate(batch.questions, start=1):
            fingerprint = question_fingerprint(question)
            record = state.questions.get(question.local_id)
            if record and record.fingerprint == fingerprint and record.status == "uploaded":
                print(f"[{index}/{len(batch.questions)}] 已上传，跳过：{question.local_id}")
                continue
            if record and record.status in {"saving", "uncertain"}:
                raise RuntimeError(
                    f"题目 {question.local_id} 上次保存结果不确定。请先在课程题库核对，避免重复上传。"
                )
            if record and record.fingerprint != fingerprint and record.status == "uploaded":
                raise RuntimeError(f"题目 {question.local_id} 上传后又被修改，程序不会自动重复创建。")

            uploader.open_manual_create()
            uploader.fill_question(question)
            location = uploader.select_chapter(question.chapter)
            print(f"[{index}/{len(batch.questions)}] 已填写：{question.local_id} → {location}", flush=True)

            if not commit:
                input("当前为核对模式，未点击保存。请在浏览器核对，按 Enter 关闭程序：")
                return
            if not assume_yes:
                answer = input("确认保存这一题？输入 YES 后回车：").strip()
                if answer != "YES":
                    print("已停止，当前题未保存。")
                    return

            state.questions[question.local_id] = UploadRecord(fingerprint=fingerprint, status="saving")
            save_upload_state(state_path, state)
            try:
                platform_id = uploader.save_current_question()
            except Exception:
                state.questions[question.local_id].status = "uncertain"
                state.questions[question.local_id].updated_at = time.time()
                save_upload_state(state_path, state)
                raise
            state.questions[question.local_id] = UploadRecord(
                fingerprint=fingerprint,
                status="uploaded",
                platform_question_id=platform_id,
            )
            save_upload_state(state_path, state)
            print(f"[{index}/{len(batch.questions)}] 保存成功：{question.local_id}", flush=True)
            if index < len(batch.questions):
                uploader.open_question_bank()
    finally:
        uploader.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将人工审核通过的本地题目填写到课程题库。")
    parser.add_argument("--batch", type=Path, required=True, help="人工审核后的题目 JSON")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--term-id", required=True)
    parser.add_argument("--course-name", required=True)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path, help="上传状态文件，默认与题目 JSON 放在一起")
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
    upload_batch(config, args.batch, state_path, commit=args.commit, assume_yes=args.yes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

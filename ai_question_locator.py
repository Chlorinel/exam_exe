from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from responsive_wait import WebDriverWait

from deepseek_question_generator import (
    GeneratedQuestion,
    QuestionBatch,
    extract_question_identifier,
)
from exam_session import (
    all_uploaded as session_all_uploaded,
    load_session,
    save_session,
    update_question_location,
    update_status,
)
from platform_question_uploader import (
    QuestionBankUploader,
    UploadConfig,
    UploadState,
    normalize_text,
    question_fingerprint,
)


@dataclass(frozen=True)
class LocatedQuestion:
    """
    AI 题上传到课程题库后，转换成现有考试配置表需要的信息。
    """

    local_id: str
    chapter: int
    chapter_name: str
    number: int
    identifier: str
    score: Decimal
    platform_id: str | None = None


def _visible(elements: Iterable[WebElement]) -> list[WebElement]:
    result: list[WebElement] = []
    for element in elements:
        try:
            if element.is_displayed():
                result.append(element)
        except WebDriverException:
            continue
    return result


def chapter_rows(driver) -> list[WebElement]:
    """
    与 create_signal_exam.py 当前题库选择页保持同一 DOM 规则：
    没有 .question-type 的 .question-item-container 是章节行。
    """
    return [
        element
        for element in _visible(
            driver.find_elements(
                By.CSS_SELECTOR,
                ".question-item-container",
            )
        )
        if not element.find_elements(
            By.CSS_SELECTOR,
            ".question-type",
        )
    ]


def question_rows(driver) -> list[WebElement]:
    """
    有 .question-type 的 .question-item-container 是题目行。
    """
    return [
        element
        for element in _visible(
            driver.find_elements(
                By.CSS_SELECTOR,
                ".question-item-container",
            )
        )
        if element.find_elements(
            By.CSS_SELECTOR,
            ".question-type",
        )
    ]


def count_badge(text: str) -> int | None:
    match = re.search(
        r"[（(]\s*(\d+)\s*[)）]\s*$",
        normalize_text(text),
    )
    return int(match.group(1)) if match else None


def strip_count_badge(text: str) -> str:
    return re.sub(
        r"\s*[（(]\s*\d+\s*[)）]\s*$",
        "",
        normalize_text(text),
    ).strip()


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _parse_chinese_integer(value: str) -> int:
    """
    足够处理课程章节常见的 一~九十九。
    """
    value = value.strip()

    if value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]

    if "十" in value:
        left, right = value.split("十", 1)

        tens = (
            1
            if not left
            else _CHINESE_DIGITS.get(left)
        )
        ones = (
            0
            if not right
            else _CHINESE_DIGITS.get(right)
        )

        if tens is None or ones is None:
            raise ValueError(
                f"无法识别中文章节数字：{value}"
            )

        return tens * 10 + ones

    raise ValueError(
        f"无法识别中文章节数字：{value}"
    )


def parse_chapter_number(chapter_name: str) -> int:
    """
    支持：
        第一章
        第二章
        第十二章
        第12章
        12章
        12
    """
    text = normalize_text(chapter_name)

    arabic = re.fullmatch(
        r"(?:第\s*)?(\d+)\s*(?:章)?",
        text,
    )
    if arabic:
        number = int(arabic.group(1))
        if number <= 0:
            raise ValueError(
                f"章节编号必须大于 0：{chapter_name}"
            )
        return number

    chinese = re.fullmatch(
        r"(?:第\s*)?([零〇一二两三四五六七八九十]+)\s*(?:章)?",
        text,
    )
    if chinese:
        number = _parse_chinese_integer(
            chinese.group(1)
        )
        if number <= 0:
            raise ValueError(
                f"章节编号必须大于 0：{chapter_name}"
            )
        return number

    raise ValueError(
        f"无法从章节名称识别章节编号：{chapter_name}"
    )


def locate_row_by_identifier(
    stem: str,
    row_texts: list[str],
) -> tuple[int, str]:
    """Locate one uploaded question by its exact trailing identifier."""
    normalized_rows = [
        normalize_text(text)
        for text in row_texts
    ]

    if not normalized_rows:
        raise ValueError(
            "当前章节没有可定位的题目。"
        )

    identifier = extract_question_identifier(stem)
    if not identifier:
        raise ValueError(
            "题干末尾没有五位唯一标识，无法定位题目。"
        )

    matches = [
        index
        for index, row_text in enumerate(normalized_rows)
        if identifier in row_text
    ]
    if len(matches) == 1:
        return matches[0], identifier

    raise ValueError(
        f"五位唯一标识 {identifier} 在当前章节匹配到 "
        f"{len(matches)} 道题，无法唯一定位。"
    )


def ensure_all_uploaded(
    batch: QuestionBatch,
    upload_state: UploadState,
) -> None:
    """
    Locator 只处理已经被 uploader 明确记为 uploaded 的题目。
    """
    if upload_state.batch_id != batch.batch_id:
        raise RuntimeError(
            "上传状态文件与当前 AI 题目批次不一致。"
        )

    for question in batch.questions:
        record = upload_state.questions.get(
            question.local_id
        )

        if record is None:
            raise RuntimeError(
                f"{question.local_id} 没有上传状态记录。"
            )

        if record.status != "uploaded":
            raise RuntimeError(
                f"{question.local_id} 上传状态为 "
                f"{record.status}，不是 uploaded。"
            )

        expected = question_fingerprint(
            question
        )
        if record.fingerprint != expected:
            raise RuntimeError(
                f"{question.local_id} 的内容与上传时不一致。"
            )


def _open_chapter(
    uploader: QuestionBankUploader,
    chapter_name: str,
) -> list[WebElement]:
    """
    每个章节都从课程题库根页面重新进入。

    这里故意保持简单：不维护复杂的浏览器导航状态。
    """
    uploader.open_question_bank()

    driver = uploader.driver
    if driver is None:
        raise RuntimeError(
            "课程题库浏览器没有启动。"
        )

    wait = WebDriverWait(
        driver,
        uploader.config.page_timeout,
    )

    try:
        wait.until(
            lambda d: len(chapter_rows(d)) > 0
        )
    except TimeoutException as exc:
        raise RuntimeError(
            "课程题库没有加载出章节列表。"
        ) from exc

    wanted = normalize_text(chapter_name)

    matches = [
        row
        for row in chapter_rows(driver)
        if strip_count_badge(row.text) == wanted
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"找不到唯一章节：{chapter_name}"
        )

    total = count_badge(matches[0].text)

    if total is None or total <= 0:
        raise RuntimeError(
            f"{chapter_name} 没有可核对的题目数量。"
        )

    content = matches[0].find_element(
        By.CSS_SELECTOR,
        ".content",
    )
    content.click()

    try:
        wait.until(
            lambda d: (
                len(question_rows(d)) == total
                and not chapter_rows(d)
            )
        )
    except TimeoutException as exc:
        raise RuntimeError(
            f"{chapter_name} 的题目没有完整加载："
            f"期望 {total} 道，"
            f"当前 {len(question_rows(driver))} 道。"
        ) from exc

    return question_rows(driver)


def locate_uploaded_questions(
    config: UploadConfig,
    batch: QuestionBatch,
    upload_state: UploadState,
    *,
    session_path: Path | None = None,
) -> list[LocatedQuestion]:
    """
    上传完成后立即定位 AI 题。

    设计前提：
    从 AI 上传题库到生成最终考试配置期间，
    不考虑其他人同时增删同一课程题库。

    因此这里只定位一次，得到：
        章节 + 当前章内题号 + 五位唯一标识 + 分值
    """
    ensure_all_uploaded(
        batch,
        upload_state,
    )

    uploader = QuestionBankUploader(config)

    located_by_id: dict[
        str,
        LocatedQuestion,
    ] = {}

    try:
        grouped: dict[
            str,
            list[GeneratedQuestion],
        ] = {}

        for question in batch.questions:
            grouped.setdefault(
                question.chapter,
                [],
            ).append(question)

        for chapter_name, questions in grouped.items():
            rows = _open_chapter(
                uploader,
                chapter_name,
            )

            row_texts = [
                normalize_text(row.text)
                for row in rows
            ]

            used_indexes: set[int] = set()

            for question in questions:
                row_index, identifier = (
                    locate_row_by_identifier(
                        question.stem,
                        row_texts,
                    )
                )

                if row_index in used_indexes:
                    raise RuntimeError(
                        f"{chapter_name} 中有多道 AI 题"
                        "被定位到同一个题目行。"
                    )

                used_indexes.add(row_index)

                located_by_id[
                    question.local_id
                ] = LocatedQuestion(
                    local_id=question.local_id,
                    chapter=parse_chapter_number(
                        chapter_name
                    ),
                    chapter_name=chapter_name,
                    number=row_index + 1,
                    identifier=identifier,
                    score=question.score,
                    platform_id=upload_state.questions[
                        question.local_id
                    ].platform_question_id,
                )

    finally:
        uploader.close()

    located = [
        located_by_id[question.local_id]
        for question in batch.questions
    ]
    if session_path is not None:
        session_path = Path(session_path).resolve()
        session = load_session(session_path)
        if session.status != "reviewed":
            raise RuntimeError(
                f"Session 状态为 {session.status}，无法写入题库定位结果。"
            )
        for question in located:
            update_question_location(
                session,
                question.local_id,
                chapter=question.chapter_name,
                question_number=question.number,
                identifier=question.identifier,
                platform_id=question.platform_id,
            )
        if not session_all_uploaded(session):
            raise RuntimeError("Session 题目映射不完整，未进入 uploaded 状态。")
        update_status(session, "uploaded")
        save_session(session, session_path)
    return located

from __future__ import annotations

"""
DeepSeek 网页出题子程序。

用途：
- Selenium + Edge 操作 DeepSeek 免费聊天网页；
- 输入 QuestionSpec，返回结构化 GeneratedQuestion；
- 内部统一使用 Markdown + LaTeX；
- AI 生成结果默认 pending，必须由人工审核后才能上传题库；
- 不读取、保存或导出 Cookie / Token / Authorization / 密码。

主程序典型调用：
    from deepseek_question_generator import (
        QuestionSpec,
        DeepSeekWebConfig,
        DeepSeekWebGenerator,
    )

    spec = QuestionSpec(
        course_name="信号与系统",
        chapter="第一章",
        knowledge_point="周期信号",
        question_type="single_choice",
        difficulty="medium",
        count=2,
        score="5",
    )

    cfg = DeepSeekWebConfig(
        profile_dir=ROOT / "work" / "edge-deepseek-profile"
    )

    with DeepSeekWebGenerator(cfg) as ai:
        batch = ai.generate_questions(
            spec,
            output_path=ROOT / "work" / "generated_questions.json",
        )
"""

import argparse
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable

from selenium import webdriver
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from responsive_wait import WebDriverWait, responsive_sleep


CONTENT_FORMAT = "markdown_latex"
DEFAULT_CHAT_URL = "https://chat.deepseek.com/"


# ============================================================================
# 数据模型
# ============================================================================

@dataclass(frozen=True)
class QuestionSpec:
    course_name: str
    chapter: str
    knowledge_point: str
    question_type: str = "single_choice"
    difficulty: str = "medium"
    count: int = 1
    score: Decimal | int | float | str = Decimal("5")
    requirements: str = ""
    language: str = "zh-CN"

    def normalized_score(self) -> Decimal:
        try:
            value = Decimal(str(self.score))
        except InvalidOperation as exc:
            raise ValueError(f"题目分值不是有效数字：{self.score}") from exc
        if not value.is_finite() or value <= 0:
            raise ValueError("题目分值必须大于 0。")
        return value

    def validate(self) -> None:
        for label, value in (
            ("course_name", self.course_name),
            ("chapter", self.chapter),
            ("knowledge_point", self.knowledge_point),
            ("question_type", self.question_type),
            ("difficulty", self.difficulty),
        ):
            if not str(value).strip():
                raise ValueError(f"{label} 不能为空。")
        if not 1 <= int(self.count) <= 100:
            raise ValueError("count 必须在 1~100 之间。")
        self.normalized_score()


@dataclass
class GeneratedQuestion:
    local_id: str
    question_type: str
    chapter: str
    knowledge_point: str
    difficulty: str
    stem: str
    options: dict[str, str] | None
    answer: str
    explanation: str
    score: Decimal

    content_format: str = CONTENT_FORMAT
    review_status: str = "pending"
    edited_by_user: bool = False
    platform_question_id: str | None = None
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["score"] = str(self.score)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeneratedQuestion":
        value = dict(data)
        value["score"] = Decimal(str(value["score"]))
        return cls(**value)


@dataclass
class QuestionBatch:
    batch_id: str
    provider: str
    created_at: str
    spec: QuestionSpec
    questions: list[GeneratedQuestion]
    raw_response: str
    specs: list[QuestionSpec] = field(default_factory=list)

    def all_specs(self) -> list[QuestionSpec]:
        return list(self.specs) if self.specs else [self.spec]

    def expected_question_count(self) -> int:
        return sum(int(item.count) for item in self.all_specs())

    def to_dict(self) -> dict[str, Any]:
        spec = asdict(self.spec)
        spec["score"] = str(self.spec.normalized_score())
        specs = []
        for item in self.all_specs():
            value = asdict(item)
            value["score"] = str(item.normalized_score())
            specs.append(value)
        return {
            "version": 1,
            "batch_id": self.batch_id,
            "provider": self.provider,
            "created_at": self.created_at,
            "spec": spec,
            "specs": specs,
            "raw_response": self.raw_response,
            "questions": [q.to_dict() for q in self.questions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuestionBatch":
        spec_data = dict(data["spec"])
        spec_data["score"] = Decimal(str(spec_data["score"]))
        specs: list[QuestionSpec] = []
        for raw_spec in data.get("specs", []):
            item = dict(raw_spec)
            item["score"] = Decimal(str(item["score"]))
            specs.append(QuestionSpec(**item))
        return cls(
            batch_id=data["batch_id"],
            provider=data["provider"],
            created_at=data["created_at"],
            spec=QuestionSpec(**spec_data),
            questions=[
                GeneratedQuestion.from_dict(x)
                for x in data["questions"]
            ],
            raw_response=data.get("raw_response", ""),
            specs=specs,
        )


# ============================================================================
# DeepSeek 页面适配
# ============================================================================

@dataclass(frozen=True)
class DeepSeekSelectors:
    """
    DeepSeek 网页改版时优先只修改这里。
    CSS 选择器按优先级尝试。
    """
    input_selectors: tuple[str, ...] = (
        'textarea[placeholder*="DeepSeek"]',
        'textarea[placeholder*="发送"]',
        'textarea[placeholder*="发消息"]',
        'textarea[placeholder*="Send"]',
        "textarea",
        '[contenteditable="true"][role="textbox"]',
        '[contenteditable="true"]',
    )

    answer_selectors: tuple[str, ...] = (
        ".ds-message .ds-markdown",
        ".ds-markdown",
    )

    stop_selectors: tuple[str, ...] = (
        '[aria-label*="停止"]',
        '[aria-label*="Stop"]',
        '[title*="停止"]',
        '[title*="Stop"]',
    )

    send_selectors: tuple[str, ...] = (
        'button[aria-label*="发送"]',
        'button[aria-label*="Send"]',
        '[role="button"][aria-label*="发送"]',
        '[role="button"][aria-label*="Send"]',
        ".ds-button--primary.ds-button--filled.ds-button--circle",
    )

    # “新对话”按钮文本。只用于 DeepSeek 首页仍恢复到旧会话时的兜底。
    new_chat_texts: tuple[str, ...] = (
        "新对话",
        "开启新对话",
        "新建对话",
        "New chat",
        "New Chat",
    )


@dataclass
class DeepSeekWebConfig:
    profile_dir: Path = field(
        default_factory=lambda: (
            Path(__file__).resolve().parent
            / "work"
            / "edge-deepseek-profile"
        )
    )
    chat_url: str = DEFAULT_CHAT_URL
    edge_binary: str | None = None
    headless: bool = False

    login_timeout: int = 360
    page_timeout: int = 60
    generation_timeout: int = 360

    minimum_generation_seconds: float = 4.0
    stable_seconds: float = 6.0
    poll_interval: float = 0.5

    selectors: DeepSeekSelectors = field(default_factory=DeepSeekSelectors)


# ============================================================================
# Prompt
# ============================================================================

def build_generation_prompt(spec: QuestionSpec) -> str:
    spec.validate()
    score = spec.normalized_score()

    return f"""你是一名严谨的大学课程教师。请严格按照以下要求生成考试题。

课程：{spec.course_name}
章节：{spec.chapter}
知识点：{spec.knowledge_point}
题型：{spec.question_type}
难度：{spec.difficulty}
数量：{spec.count}
每题分值：{score}
额外要求：{spec.requirements or "无"}

要求：
1. 题目必须围绕指定章节和知识点。
2. 题意完整、无歧义、答案确定。
3. 解析必须说明答案依据。
4. 所有数学公式必须使用 LaTeX。
5. 行内公式统一使用 \\( ... \\)。
6. 独立公式统一使用 \\[ ... \\]。
7. 禁止使用 $$...$$。
8. 禁止将公式转为图片。
9. 禁止用 Unicode 上标/下标替代 LaTeX。
10. 单选题必须有 A/B/C/D 四个不同选项且只有一个正确答案。
11. 同一批题目不得完全重复。
12. 只能输出合法 JSON；禁止 Markdown 代码块、前言、结尾说明。
13. JSON 字符串里的 LaTeX 反斜杠必须正确转义。

严格返回：

{{
  "questions": [
    {{
      "type": "{spec.question_type}",
      "chapter": "{spec.chapter}",
      "knowledge_point": "{spec.knowledge_point}",
      "difficulty": "{spec.difficulty}",
      "question": "题干，可含 Markdown 和 LaTeX",
      "options": {{
        "A": "选项A",
        "B": "选项B",
        "C": "选项C",
        "D": "选项D"
      }},
      "answer": "A",
      "explanation": "解析，可含 Markdown 和 LaTeX",
      "score": {score}
    }}
  ]
}}

如果题型不需要选项，options 必须为 null。
questions 数组必须恰好包含 {spec.count} 道题。"""


def build_regeneration_prompt(
    original: GeneratedQuestion,
    feedback: str = "",
) -> str:
    original_json = json.dumps(
        original.to_dict(),
        ensure_ascii=False,
        indent=2,
    )

    return f"""请重新生成下面这一道考试题。

原题：
{original_json}

人工意见：
{feedback.strip() or "保持知识点、题型、难度和分值，但重新设计一道实质不同的题。"}

要求：
1. 保持原章节、知识点、题型、难度和分值。
2. 新题不能只是改数字或同义改写。
3. 题意完整、答案唯一、解析正确。
4. 公式使用 LaTeX。
5. 行内公式使用 \\( ... \\)，独立公式使用 \\[ ... \\]。
6. 禁止 $$...$$ 和公式图片。
7. 只能输出合法 JSON，不要 Markdown 代码块或其他文字。
8. JSON 字符串中的反斜杠必须正确转义。

严格返回：

{{
  "question": {{
    "type": "{original.question_type}",
    "chapter": "{original.chapter}",
    "knowledge_point": "{original.knowledge_point}",
    "difficulty": "{original.difficulty}",
    "question": "...",
    "options": null,
    "answer": "...",
    "explanation": "...",
    "score": {original.score}
  }}
}}

选择题的 options 必须返回 A/B/C/D 对象。"""


# ============================================================================
# JSON 解析
# ============================================================================

_JSON_FENCE = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _strip_json_fence(text: str) -> str:
    match = _JSON_FENCE.match(text)
    return match.group(1).strip() if match else text.strip()


def _looks_like_raw_latex_json(text: str) -> bool:
    r"""
    DeepSeek 网页有时返回“看起来像 JSON，但 LaTeX 未按 JSON 转义”的文本。

    正确 JSON 源文本中的行内公式类似：
        "\\(x(t)\\)"

    常见错误输出则类似：
        \(x(t)\)

    后者如果直接 json.loads：
    - \(、\[ 等会导致 invalid escape；
    - \t、\b、\f 等还可能被误当成 JSON 控制字符，
      使 \tau、\begin、\frac 等 LaTeX 被静默破坏。
    """
    return bool(
        re.search(r'(?<!\\)\\(?:\(|\[)', text)
    )


def _escape_raw_latex_json_strings(text: str) -> str:
    r"""
    将 DeepSeek 的 raw-LaTeX JSON 转为合法 JSON。

    仅在 JSON 字符串内部工作：
    - 除了 \"（JSON 字符串中的双引号转义）之外，
      每一个原始反斜杠都逐个再转义一次。
    - 单个 LaTeX 反斜杠会在 json.loads 后恢复为单个。
    - 原本两个反斜杠（例如 cases 中的 \\ 换行）
      会在 json.loads 后仍恢复为两个。
    """
    out: list[str] = []
    in_string = False
    i = 0

    while i < len(text):
        ch = text[i]

        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue

        if ch == '"':
            out.append(ch)
            in_string = False
            i += 1
            continue

        if ch != "\\":
            out.append(ch)
            i += 1
            continue

        # 保留真正的 JSON 双引号转义。
        if i + 1 < len(text) and text[i + 1] == '"':
            out.append('\\"')
            i += 2
            continue

        # 逐个保护 LaTeX 反斜杠。
        out.append("\\\\")
        i += 1

    return "".join(out)


def _prepare_json_source(text: str) -> str:
    stripped = _strip_json_fence(text)

    if _looks_like_raw_latex_json(stripped):
        return _escape_raw_latex_json_strings(stripped)

    return stripped


def _json_candidate_score(value: Any, consumed: int) -> tuple[int, int]:
    """
    给解析到的 JSON 候选打分，避免把 options 等局部对象
    错当成整份 DeepSeek 输出。
    """
    score = 0

    if isinstance(value, dict):
        if isinstance(value.get("questions"), list):
            score += 1000
        elif isinstance(value.get("questions"), dict):
            score += 900

        if isinstance(value.get("question"), dict):
            score += 800

        # 一道题本身也可能是候选，但优先级低于顶层 questions。
        question_keys = {
            "type",
            "chapter",
            "knowledge_point",
            "question",
            "stem",
            "answer",
            "explanation",
            "score",
        }
        score += len(question_keys & set(value.keys())) * 20

        # 只有 A/B/C/D 的对象往往只是 options，降低优先级。
        keys = {str(k).upper() for k in value.keys()}
        if keys and keys <= {"A", "B", "C", "D"}:
            score -= 300

    elif isinstance(value, list):
        if value and all(isinstance(x, dict) for x in value):
            score += 700
        else:
            score += 50

    return score, consumed


def _decode_first_json(text: str) -> Any:
    """
    先修复 raw-LaTeX JSON，再从回复里寻找最可信的完整 JSON。
    """
    decoder = json.JSONDecoder()
    prepared = _prepare_json_source(text)

    candidates = [prepared]
    original = _strip_json_fence(text)

    # 原文仅作为次级候选；raw-LaTeX 时优先使用 repaired 版本。
    if original != prepared:
        candidates.append(original)

    found: list[tuple[tuple[int, int], Any]] = []

    for candidate in candidates:
        try:
            value = json.loads(candidate)
            found.append(
                (_json_candidate_score(value, len(candidate)), value)
            )
        except json.JSONDecodeError:
            pass

        for index, ch in enumerate(candidate):
            if ch not in "[{":
                continue

            try:
                value, consumed = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue

            found.append(
                (
                    _json_candidate_score(value, consumed),
                    value,
                )
            )

    if not found:
        raise ValueError(
            "DeepSeek 回复中没有找到可解析的 JSON。"
        )

    found.sort(key=lambda item: item[0], reverse=True)
    return found[0][1]


def _normalize_options(value: Any) -> dict[str, str] | None:
    if value is None:
        return None

    if isinstance(value, list):
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return {
            letters[i]: str(item).strip()
            for i, item in enumerate(value)
            if i < len(letters)
        }

    if isinstance(value, dict):
        return {
            str(key).strip().upper(): str(item).strip()
            for key, item in value.items()
        }

    raise ValueError("options 必须为对象、数组或 null。")


def _question_from_mapping(
    data: dict[str, Any],
    *,
    fallback_spec: QuestionSpec | None = None,
    local_id: str | None = None,
) -> GeneratedQuestion:
    if not isinstance(data, dict):
        raise ValueError("题目不是 JSON 对象。")

    fallback_score = (
        fallback_spec.normalized_score()
        if fallback_spec is not None
        else None
    )

    score_raw = data.get("score", fallback_score)
    if score_raw is None:
        raise ValueError("题目缺少 score。")

    try:
        score = Decimal(str(score_raw))
    except InvalidOperation as exc:
        raise ValueError(f"题目 score 无效：{score_raw}") from exc

    question = GeneratedQuestion(
        local_id=local_id or f"Q-{uuid.uuid4().hex[:10]}",
        question_type=str(
            data.get(
                "type",
                fallback_spec.question_type if fallback_spec else "",
            )
        ).strip(),
        chapter=str(
            data.get(
                "chapter",
                fallback_spec.chapter if fallback_spec else "",
            )
        ).strip(),
        knowledge_point=str(
            data.get(
                "knowledge_point",
                fallback_spec.knowledge_point if fallback_spec else "",
            )
        ).strip(),
        difficulty=str(
            data.get(
                "difficulty",
                fallback_spec.difficulty if fallback_spec else "",
            )
        ).strip(),
        stem=str(data.get("question", data.get("stem", ""))).strip(),
        options=_normalize_options(data.get("options")),
        answer=str(data.get("answer", "")).strip(),
        explanation=str(data.get("explanation", "")).strip(),
        score=score,
    )
    question.validation_errors = validate_question(question)
    return question


def _normalize_raw_questions(payload: Any) -> list[Any]:
    """
    兼容 DeepSeek 偶尔产生的轻微结构变体：
      {"questions": [...]}
      {"questions": {"1": {...}, "2": {...}}}
      [{...}, {...}]
    """
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        raise ValueError(
            f"DeepSeek 顶层 JSON 类型为 {type(payload).__name__}，"
            '预期对象 {"questions": [...]}。'
        )

    raw_questions = payload.get("questions")

    if isinstance(raw_questions, list):
        return raw_questions

    if isinstance(raw_questions, dict):
        # 有些模型会把题号作为 key。
        return list(raw_questions.values())

    # 若顶层本身就是一道题，给出更明确诊断。
    if any(
        key in payload
        for key in ("question", "stem", "answer", "options")
    ):
        return [payload]

    raise ValueError(
        'DeepSeek JSON 没有有效的 "questions" 数组/对象；'
        f"顶层字段为：{list(payload.keys())[:20]}"
    )


def _normalize_question_item(
    item: Any,
    *,
    index: int,
) -> dict[str, Any]:
    if isinstance(item, dict):
        return item

    # 偶尔模型会把每道题再次编码成 JSON 字符串。
    if isinstance(item, str):
        stripped = item.strip()
        if stripped.startswith("{"):
            try:
                decoded = _decode_first_json(stripped)
            except ValueError:
                decoded = None
            if isinstance(decoded, dict):
                return decoded

        preview = stripped.replace("\\n", " ")[:180]
        raise ValueError(
            f"第 {index} 道题是字符串而不是 JSON 对象：{preview!r}"
        )

    raise ValueError(
        f"第 {index} 道题类型为 {type(item).__name__}，"
        "预期 JSON 对象。"
    )


def parse_generated_questions(
    response_text: str,
    spec: QuestionSpec,
) -> list[GeneratedQuestion]:
    payload = _decode_first_json(response_text)
    raw_questions = _normalize_raw_questions(payload)

    normalized_items = [
        _normalize_question_item(item, index=index)
        for index, item in enumerate(raw_questions, start=1)
    ]

    questions = [
        _question_from_mapping(
            item,
            fallback_spec=spec,
            local_id=f"Q{index:03d}",
        )
        for index, item in enumerate(normalized_items, start=1)
    ]

    if len(questions) != spec.count:
        raise ValueError(
            f"DeepSeek 返回 {len(questions)} 道题，"
            f"但要求 {spec.count} 道。"
        )

    for index, message in validate_batch_duplicates(questions):
        questions[index].validation_errors.append(message)

    return questions


def parse_regenerated_question(
    response_text: str,
    original: GeneratedQuestion,
) -> GeneratedQuestion:
    payload = _decode_first_json(response_text)

    if isinstance(payload, dict) and isinstance(
        payload.get("question"),
        dict,
    ):
        payload = payload["question"]

    if not isinstance(payload, dict):
        raise ValueError(
            '单题重生成结果必须为 {"question": {...}}。'
        )

    question = _question_from_mapping(
        payload,
        local_id=original.local_id,
    )
    question.review_status = "pending"
    question.edited_by_user = False
    question.platform_question_id = None
    return question


# ============================================================================
# 自动校验
# ============================================================================

_SINGLE_CHOICE_TYPES = {
    "single_choice",
    "single-choice",
    "单选",
    "单选题",
    "单项选择题",
}


def _latex_delimiter_errors(text: str, label: str) -> list[str]:
    errors: list[str] = []

    if text.count(r"\(") != text.count(r"\)"):
        errors.append(f"{label} 的 \\(...\\) 分隔符不成对。")

    if text.count(r"\[") != text.count(r"\]"):
        errors.append(f"{label} 的 \\[...\\] 分隔符不成对。")

    if "$$" in text:
        errors.append(
            f"{label} 使用了 $$...$$，应改为 \\[...\\]。"
        )

    return errors


def validate_question(question: GeneratedQuestion) -> list[str]:
    errors: list[str] = []

    for label, value in (
        ("题型", question.question_type),
        ("章节", question.chapter),
        ("知识点", question.knowledge_point),
        ("难度", question.difficulty),
        ("题干", question.stem),
        ("答案", question.answer),
        ("解析", question.explanation),
    ):
        if not str(value).strip():
            errors.append(f"{label}为空。")

    if not question.score.is_finite() or question.score <= 0:
        errors.append("分值必须大于 0。")

    errors.extend(_latex_delimiter_errors(question.stem, "题干"))
    errors.extend(
        _latex_delimiter_errors(
            question.explanation,
            "解析",
        )
    )

    if question.options:
        for label, value in question.options.items():
            errors.extend(
                _latex_delimiter_errors(
                    value,
                    f"选项 {label}",
                )
            )

    if question.question_type.lower() in _SINGLE_CHOICE_TYPES:
        required = {"A", "B", "C", "D"}

        if question.options is None:
            errors.append("单选题缺少选项。")
        else:
            missing = required - set(question.options)
            if missing:
                errors.append(
                    "单选题缺少选项："
                    + "、".join(sorted(missing))
                )

            values = [
                re.sub(r"\s+", " ", question.options[key]).strip()
                for key in sorted(required & set(question.options))
            ]
            if len(values) != len(set(values)):
                errors.append("单选题存在完全重复的选项。")

        if question.answer.upper() not in required:
            errors.append("单选题答案必须是 A/B/C/D。")

    return errors


def validate_batch_duplicates(
    questions: Iterable[GeneratedQuestion],
) -> list[tuple[int, str]]:
    seen: dict[str, int] = {}
    errors: list[tuple[int, str]] = []

    for index, question in enumerate(questions):
        key = re.sub(
            r"\s+",
            " ",
            question.stem,
        ).strip().casefold()

        if not key:
            continue

        if key in seen:
            errors.append(
                (
                    index,
                    f"题干与 Q{seen[key] + 1:03d} 完全重复。",
                )
            )
        else:
            seen[key] = index

    return errors


# ============================================================================
# 本地 JSON
# ============================================================================

def save_question_batch(
    path: Path,
    batch: QuestionBatch,
) -> None:
    """原子保存，避免异常中断留下半个 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    handle, tmp = tempfile.mkstemp(
        prefix=path.stem + ".",
        suffix=".tmp",
        dir=path.parent,
    )

    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                batch.to_dict(),
                stream,
                ensure_ascii=False,
                indent=2,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_question_batch(path: Path) -> QuestionBatch:
    data = json.loads(
        Path(path).read_text(encoding="utf-8")
    )
    return QuestionBatch.from_dict(data)


def _raw_response_debug_path(output_path: Path) -> Path:
    output_path = Path(output_path)
    return output_path.with_name(
        output_path.stem + ".raw.txt"
    )


def save_raw_response_debug(
    output_path: Path,
    raw_response: str,
) -> Path:
    """
    仅在解析失败时保存原始 DeepSeek 回复，便于定位网页输出格式问题。
    """
    debug_path = _raw_response_debug_path(output_path)
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(raw_response, encoding="utf-8")
    return debug_path


# ============================================================================
# Selenium DeepSeek
# ============================================================================

def _is_displayed(element: WebElement) -> bool:
    try:
        return element.is_displayed()
    except StaleElementReferenceException:
        return False


class DeepSeekWebGenerator:
    provider_name = "deepseek_web"

    def __init__(
        self,
        config: DeepSeekWebConfig | None = None,
        *,
        driver: webdriver.Edge | None = None,
        logger: Callable[[str], None] | None = None,
    ):
        self.config = config or DeepSeekWebConfig()
        self.driver = driver
        self._owns_driver = driver is None
        self._active_headless = bool(self.config.headless and driver is None)
        self.log = logger or (
            lambda message: print(message, flush=True)
        )

    def __enter__(self) -> "DeepSeekWebGenerator":
        self.launch()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def launch(self, *, headless: bool | None = None) -> webdriver.Edge:
        if self.driver is not None:
            return self.driver

        if headless is None:
            headless = self.config.headless

        profile_dir = Path(
            self.config.profile_dir
        ).resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)

        options = Options()

        if self.config.edge_binary:
            options.binary_location = self.config.edge_binary

        options.add_argument(
            f"--user-data-dir={profile_dir}"
        )
        options.add_argument("--profile-directory=Default")
        options.add_argument("--disable-notifications")
        options.add_argument("--start-maximized")
        options.page_load_strategy = "eager"

        if headless:
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1600,1200")

        self.driver = webdriver.Edge(options=options)
        self.driver.set_page_load_timeout(self.config.page_timeout)
        self._active_headless = bool(headless)
        return self.driver

    def close(self) -> None:
        if self.driver is not None and self._owns_driver:
            try:
                self.driver.quit()
            finally:
                self.driver = None

    def _restart_browser(self, *, headless: bool) -> webdriver.Edge:
        if not self._owns_driver:
            raise RuntimeError("外部浏览器实例无法自动切换登录模式。")
        self.close()
        return self.launch(headless=headless)

    def _wait_for_login(self, input_ready) -> None:
        """Wait for the human login; closing the browser interrupts the flow."""
        deadline = time.monotonic() + self.config.login_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("等待 DeepSeek 登录超过 6 分钟，已停止。")
            try:
                WebDriverWait(self.driver, min(60, remaining)).until(input_ready)
                return
            except TimeoutException:
                self.log("仍在等待 DeepSeek 登录；完成后程序会自动继续。")

    def _find_first_visible(
        self,
        selectors: Iterable[str],
    ) -> WebElement | None:
        assert self.driver is not None

        for selector in selectors:
            for element in self.driver.find_elements(
                By.CSS_SELECTOR,
                selector,
            ):
                if _is_displayed(element):
                    return element

        return None

    def _find_all_visible(
        self,
        selectors: Iterable[str],
    ) -> list[WebElement]:
        assert self.driver is not None

        result: list[WebElement] = []
        seen: set[str] = set()

        for selector in selectors:
            for element in self.driver.find_elements(
                By.CSS_SELECTOR,
                selector,
            ):
                if not _is_displayed(element):
                    continue
                try:
                    key = element.id
                except StaleElementReferenceException:
                    continue
                if key not in seen:
                    seen.add(key)
                    result.append(element)

        return result

    def ensure_logged_in(self) -> None:
        assert self.driver is not None

        def input_ready(_):
            return self._find_first_visible(
                self.config.selectors.input_selectors
            ) or False

        try:
            WebDriverWait(self.driver, 8).until(input_ready)
            return
        except TimeoutException:
            pass

        return_to_background = self._active_headless
        if return_to_background:
            self.log("DeepSeek 登录已失效，正在弹出 Edge 登录窗口。")
            driver = self._restart_browser(headless=False)
            driver.get(self.config.chat_url)
            WebDriverWait(driver, self.config.page_timeout).until(
                lambda d: d.execute_script("return document.readyState")
                in ("interactive", "complete")
            )

        self.log(
            "请在自动打开的 Edge 中手动完成 "
            "DeepSeek 登录/人机验证；关闭 Edge 可停止流程。"
        )

        self._wait_for_login(input_ready)

        if return_to_background:
            self.log("DeepSeek 登录完成，正在恢复后台运行。")
            driver = self._restart_browser(headless=True)
            driver.get(self.config.chat_url)
            WebDriverWait(driver, self.config.page_timeout).until(
                lambda d: d.execute_script("return document.readyState")
                in ("interactive", "complete")
            )
            WebDriverWait(driver, self.config.page_timeout).until(input_ready)

    def open_chat(self) -> None:
        driver = self.launch()
        driver.get(self.config.chat_url)

        WebDriverWait(
            driver,
            self.config.page_timeout,
        ).until(
            lambda d: d.execute_script(
                "return document.readyState"
            )
            in ("interactive", "complete")
        )
        self.ensure_logged_in()

    def _click_new_chat_control(self) -> bool:
        """
        DeepSeek 首页若仍恢复旧会话，则尝试点击“新对话”。

        不依赖一个固定 CSS class，而是按可见文本寻找可点击元素。
        """
        assert self.driver is not None

        for label in self.config.selectors.new_chat_texts:
            xpath = (
                "//*[self::button or self::a or @role='button']"
                f"[normalize-space(.)={json.dumps(label, ensure_ascii=False)}]"
            )

            elements = self.driver.find_elements(
                By.XPATH,
                xpath,
            )

            for element in elements:
                if not _is_displayed(element):
                    continue

                try:
                    element.click()
                except WebDriverException:
                    try:
                        self.driver.execute_script(
                            "arguments[0].click();",
                            element,
                        )
                    except WebDriverException:
                        continue

                return True

        return False

    def _conversation_is_fresh(self) -> bool:
        """
        新对话必须没有任何 AI 历史回答。

        这是比单纯检查 URL 更可靠的条件：
        DeepSeek 的路由结构以后可能改变，但新会话不应带旧回答。
        """
        return len(self._answers()) == 0

    def new_chat(self) -> None:
        """
        强制进入全新对话。

        每次批量出题、每次单题重生成都必须调用这里。
        如果直接打开聊天首页后仍恢复到旧会话，则点击“新对话”；
        最终仍检测到历史 AI 回复时，fail closed，不发送 Prompt。
        """
        assert self.driver is not None

        self.log("DeepSeek：创建全新对话。")

        # 第一层：直接进入聊天首页。
        self.driver.get(self.config.chat_url)
        self.ensure_logged_in()
        self.wait_for_input()

        # 给前端一点时间恢复路由/历史会话。
        responsive_sleep(0.8)

        if self._conversation_is_fresh():
            return

        # 第二层：如果首页恢复了旧对话，主动点击“新对话”。
        clicked = self._click_new_chat_control()

        if clicked:
            WebDriverWait(
                self.driver,
                self.config.page_timeout,
            ).until(
                lambda d: (
                    self._find_first_visible(
                        self.config.selectors.input_selectors
                    )
                    is not None
                )
            )
            responsive_sleep(0.8)

        if not self._conversation_is_fresh():
            raise RuntimeError(
                "DeepSeek 未能确认进入全新对话。"
                "为避免沿用旧上下文，本次不会发送出题 Prompt。"
            )

    def wait_for_input(self) -> WebElement:
        assert self.driver is not None

        return WebDriverWait(
            self.driver,
            self.config.page_timeout,
        ).until(
            lambda d: self._find_first_visible(
                self.config.selectors.input_selectors
            )
            or False
        )

    def _answers(self) -> list[WebElement]:
        return self._find_all_visible(
            self.config.selectors.answer_selectors
        )

    def _extract_answer_text(
        self,
        element: WebElement,
    ) -> str:
        """
        尽量把网页已经渲染的公式恢复为 LaTeX。

        如果 DeepSeek 使用 KaTeX/MathML：
        <annotation encoding="application/x-tex">
        中通常仍保留 TeX 源码。
        """
        assert self.driver is not None

        script = r"""
const root = arguments[0];
const clone = root.cloneNode(true);

/* 去除可能的思考过程，只保留最终回答。 */
clone.querySelectorAll(
    '.ds-think-content, [class*="think-content"]'
).forEach(node => node.remove());

/* 先恢复块公式。 */
clone.querySelectorAll('.katex-display').forEach(node => {
    const tex = node.querySelector(
        'annotation[encoding="application/x-tex"]'
    );
    if (!tex) return;
    node.replaceWith(
        document.createTextNode(
            '\n\\\\[' + tex.textContent + '\\\\]\n'
        )
    );
});

/* 再恢复行内公式。 */
clone.querySelectorAll('.katex, math').forEach(node => {
    const tex = node.querySelector(
        'annotation[encoding="application/x-tex"]'
    );
    if (!tex) return;
    node.replaceWith(
        document.createTextNode(
            '\\\\(' + tex.textContent + '\\\\)'
        )
    );
});

return (
    clone.innerText ||
    clone.textContent ||
    ''
).trim();
"""
        value = self.driver.execute_script(
            script,
            element,
        )
        return str(value or "").strip()

    def _last_answer_text(self) -> str:
        answers = self._answers()
        if not answers:
            return ""
        return self._extract_answer_text(answers[-1])

    def _stop_visible(self) -> bool:
        return (
            self._find_first_visible(
                self.config.selectors.stop_selectors
            )
            is not None
        )

    def _set_prompt_text(
        self,
        input_box: WebElement,
        prompt: str,
    ) -> None:
        """
        一次性写入完整 Prompt，不逐字符模拟键盘。

        重要原因：
        DeepSeek 聊天框中 Enter=发送；如果对多行字符串使用
        send_keys(prompt)，字符串里的换行可能被解释成真实 Enter，
        从而把一个 Prompt 拆成多条消息。

        这里把完整文本一次性写入 DOM，并触发 input 事件；
        最后 submit_prompt() 再单独点击一次发送按钮。
        """
        assert self.driver is not None

        tag = (input_box.tag_name or "").lower()
        is_contenteditable = (
            input_box.get_attribute("contenteditable")
            or ""
        ).lower() == "true"

        if tag in ("textarea", "input"):
            self.driver.execute_script(
                """
                const el = arguments[0];
                const value = arguments[1];

                const proto = (
                    el.tagName.toLowerCase() === 'textarea'
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype
                );
                const descriptor = Object.getOwnPropertyDescriptor(
                    proto,
                    'value'
                );

                el.focus();

                if (descriptor && descriptor.set) {
                    descriptor.set.call(el, value);
                } else {
                    el.value = value;
                }

                el.dispatchEvent(
                    new InputEvent(
                        'input',
                        {
                            bubbles: true,
                            composed: true,
                            inputType: 'insertText',
                            data: value
                        }
                    )
                );
                """,
                input_box,
                prompt,
            )

        elif is_contenteditable:
            inserted = self.driver.execute_script(
                """
                const el = arguments[0];
                const value = arguments[1];

                el.focus();

                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(el);
                selection.removeAllRanges();
                selection.addRange(range);

                let ok = false;
                try {
                    ok = document.execCommand(
                        'insertText',
                        false,
                        value
                    );
                } catch (_) {
                    ok = false;
                }

                if (!ok) {
                    el.textContent = value;
                    el.dispatchEvent(
                        new InputEvent(
                            'input',
                            {
                                bubbles: true,
                                composed: true,
                                inputType: 'insertText',
                                data: value
                            }
                        )
                    );
                }

                return true;
                """,
                input_box,
                prompt,
            )
            if not inserted:
                raise RuntimeError(
                    "无法向 DeepSeek contenteditable 输入框写入 Prompt。"
                )

        else:
            # 非预期输入控件仍使用 JS 写值，避免多行 send_keys。
            self.driver.execute_script(
                """
                const el = arguments[0];
                const value = arguments[1];
                el.focus();
                if ('value' in el) {
                    el.value = value;
                } else {
                    el.textContent = value;
                }
                el.dispatchEvent(
                    new InputEvent(
                        'input',
                        {
                            bubbles: true,
                            composed: true,
                            inputType: 'insertText',
                            data: value
                        }
                    )
                );
                """,
                input_box,
                prompt,
            )

        # 写入后立即校验：只比较规范化换行，不允许静默截断。
        actual = self.driver.execute_script(
            """
            const el = arguments[0];
            if ('value' in el && typeof el.value === 'string') {
                return el.value;
            }
            return el.innerText || el.textContent || '';
            """,
            input_box,
        )
        actual = str(actual or "").replace("\\r\\n", "\\n")
        expected = prompt.replace("\\r\\n", "\\n")

        if actual != expected:
            raise RuntimeError(
                "DeepSeek Prompt 写入后内容与原文不一致，"
                "已停止发送，避免把不完整要求发给模型。"
            )

    def submit_prompt(self, prompt: str) -> int:
        assert self.driver is not None

        input_box = self.wait_for_input()
        before_count = len(self._answers())

        # 一次性写入完整多行 Prompt，禁止 send_keys(prompt)。
        self._set_prompt_text(input_box, prompt)

        # 等页面前端完成状态更新后再找发送按钮。
        responsive_sleep(0.2)

        send_button = self._find_first_visible(
            self.config.selectors.send_selectors
        )

        if send_button is not None:
            try:
                WebDriverWait(
                    self.driver,
                    5,
                ).until(
                    lambda d: (
                        send_button.is_displayed()
                        and send_button.is_enabled()
                    )
                )
                send_button.click()
            except WebDriverException:
                self.driver.execute_script(
                    "arguments[0].click();",
                    send_button,
                )
        else:
            # 此时完整 Prompt 已经一次性写入；
            # 这里只按一次 Enter，因此不会被内部换行拆分。
            input_box.send_keys(Keys.ENTER)

        return before_count

    def wait_for_answer(
        self,
        before_count: int,
    ) -> str:
        assert self.driver is not None

        started = time.monotonic()

        try:
            WebDriverWait(
                self.driver,
                self.config.generation_timeout,
            ).until(
                lambda d: (
                    len(self._answers()) > before_count
                    and bool(self._last_answer_text())
                )
            )
        except TimeoutException as exc:
            raise RuntimeError(
                "DeepSeek 没有产生新的回答。"
            ) from exc

        last_text = ""
        last_change = time.monotonic()

        while True:
            now = time.monotonic()

            if (
                now - started
                > self.config.generation_timeout
            ):
                raise RuntimeError(
                    "等待 DeepSeek 回答完成超时。"
                )

            current = self._last_answer_text()

            if current and current != last_text:
                last_text = current
                last_change = now

            enough_time = (
                now - started
                >= self.config.minimum_generation_seconds
            )
            stable = (
                bool(last_text)
                and now - last_change
                >= self.config.stable_seconds
            )

            if (
                enough_time
                and stable
                and not self._stop_visible()
            ):
                return last_text

            responsive_sleep(self.config.poll_interval)

    @staticmethod
    def _is_conversation_limit_message(text: str) -> bool:
        normalized = re.sub(r"\s+", "", text or "").lower()

        markers = (
            "达到对话长度上限",
            "对话长度已达上限",
            "当前对话已达到长度上限",
            "请开启新对话",
            "请开始新对话",
            "conversationlengthlimit",
            "conversationlimit",
            "startanewchat",
            "newconversation",
        )
        return any(
            marker.replace(" ", "").lower() in normalized
            for marker in markers
        )

    def ask(
        self,
        prompt: str,
        *,
        new_chat: bool = True,
    ) -> str:
        """
        发送 AI 请求。

        出题系统的默认且推荐行为始终是 new_chat=True。
        如果 DeepSeek 仍返回“达到对话长度上限”，自动再新建一次
        对话并重试一次。
        """
        self.launch()

        if new_chat:
            self.new_chat()
        else:
            self.ensure_logged_in()

        before_count = self.submit_prompt(prompt)
        response = self.wait_for_answer(before_count)

        if (
            new_chat
            and self._is_conversation_limit_message(response)
        ):
            self.log(
                "DeepSeek 提示当前对话达到长度上限；"
                "正在自动开启新的对话并重试。"
            )

            self.new_chat()
            before_count = self.submit_prompt(prompt)
            response = self.wait_for_answer(before_count)

            if self._is_conversation_limit_message(response):
                raise RuntimeError(
                    "DeepSeek 在全新对话中仍提示对话长度上限，"
                    "已停止，避免继续重复发送。"
                )

        return response

    # ------------------------------------------------------------------
    # 主程序核心接口
    # ------------------------------------------------------------------

    def generate_questions(
        self,
        spec: QuestionSpec,
        *,
        output_path: Path | None = None,
    ) -> QuestionBatch:
        spec.validate()

        self.log(
            f"DeepSeek 出题："
            f"{spec.chapter} / "
            f"{spec.knowledge_point} / "
            f"{spec.count} 道"
        )

        # 每一次出题都必须使用全新 DeepSeek 对话。
        raw_response = self.ask(
            build_generation_prompt(spec),
            new_chat=True,
        )

        try:
            questions = parse_generated_questions(
                raw_response,
                spec,
            )
        except Exception as exc:
            if output_path is not None:
                debug_path = save_raw_response_debug(
                    output_path,
                    raw_response,
                )
                raise ValueError(
                    f"{exc}；DeepSeek 原始回复已保存到："
                    f"{debug_path}"
                ) from exc
            raise

        batch = QuestionBatch(
            batch_id=f"B-{uuid.uuid4().hex[:12]}",
            provider=self.provider_name,
            created_at=datetime.now(
                timezone.utc
            ).isoformat(),
            spec=spec,
            questions=questions,
            raw_response=raw_response,
        )

        if output_path is not None:
            save_question_batch(output_path, batch)

        bad = sum(
            bool(q.validation_errors)
            for q in questions
        )

        self.log(
            f"DeepSeek 出题完成："
            f"{len(questions)} 道；"
            f"自动校验异常 {bad} 道。"
        )

        return batch

    def generate_question_groups(
        self,
        specs: Iterable[QuestionSpec],
        *,
        output_path: Path | None = None,
    ) -> QuestionBatch:
        """Generate heterogeneous question groups and save one combined batch."""
        groups = list(specs)
        if not groups:
            raise ValueError("至少需要一组 AI 出题要求。")
        for spec in groups:
            spec.validate()

        if len(groups) == 1:
            batch = self.generate_questions(groups[0], output_path=output_path)
            batch.specs = groups
            if output_path is not None:
                save_question_batch(output_path, batch)
            return batch

        questions: list[GeneratedQuestion] = []
        responses: list[str] = []
        for group_index, spec in enumerate(groups, start=1):
            self.log(
                f"正在生成第 {group_index}/{len(groups)} 组："
                f"{spec.chapter} / {spec.knowledge_point} / {spec.count} 道"
            )
            result = self.generate_questions(spec)
            responses.append(result.raw_response)
            for question in result.questions:
                question.local_id = f"Q{len(questions) + 1:03d}"
                questions.append(question)

        batch = QuestionBatch(
            batch_id=f"B-{uuid.uuid4().hex[:12]}",
            provider=self.provider_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            spec=groups[0],
            specs=groups,
            questions=questions,
            raw_response="\n\n".join(responses),
        )
        if output_path is not None:
            save_question_batch(output_path, batch)
        self.log(
            f"全部出题组完成：{len(groups)} 组，共 {len(questions)} 道。"
        )
        return batch

    def regenerate_question(
        self,
        original: GeneratedQuestion,
        *,
        feedback: str = "",
    ) -> GeneratedQuestion:
        """
        供人工审核界面的“重新生成当前题”按钮调用。
        重生成后仍然是 pending。
        """
        # 单题重新生成也使用全新 DeepSeek 对话，
        # 不继承此前任何出题上下文。
        raw_response = self.ask(
            build_regeneration_prompt(
                original,
                feedback,
            ),
            new_chat=True,
        )

        try:
            return parse_regenerated_question(
                raw_response,
                original,
            )
        except Exception as exc:
            raise ValueError(
                f"单题重新生成解析失败：{exc}"
            ) from exc


# ============================================================================
# 便捷接口
# ============================================================================

def generate_questions(
    spec: QuestionSpec,
    *,
    profile_dir: Path,
    output_path: Path | None = None,
    headless: bool = False,
    edge_binary: str | None = None,
    logger: Callable[[str], None] | None = None,
) -> QuestionBatch:
    """
    主程序不需要保持 DeepSeek 浏览器时可直接调用。
    """
    config = DeepSeekWebConfig(
        profile_dir=Path(profile_dir),
        headless=headless,
        edge_binary=edge_binary,
    )

    with DeepSeekWebGenerator(
        config,
        logger=logger,
    ) as generator:
        return generator.generate_questions(
            spec,
            output_path=output_path,
        )


# ============================================================================
# 独立 CLI 测试入口
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "DeepSeek 网页出题测试程序；"
            "只生成本地 pending 题目，不上传教学平台。"
        )
    )

    parser.add_argument("--course", required=True)
    parser.add_argument("--chapter", required=True)
    parser.add_argument(
        "--knowledge-point",
        required=True,
    )
    parser.add_argument(
        "--type",
        dest="question_type",
        default="single_choice",
    )
    parser.add_argument(
        "--difficulty",
        default="medium",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
    )
    parser.add_argument(
        "--score",
        default="5",
    )
    parser.add_argument(
        "--requirements",
        default="",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=(
            Path(__file__).resolve().parent
            / "work"
            / "edge-deepseek-profile"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated_questions.json"),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
    )
    parser.add_argument("--edge-binary")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    spec = QuestionSpec(
        course_name=args.course,
        chapter=args.chapter,
        knowledge_point=args.knowledge_point,
        question_type=args.question_type,
        difficulty=args.difficulty,
        count=args.count,
        score=Decimal(str(args.score)),
        requirements=args.requirements,
    )

    try:
        batch = generate_questions(
            spec,
            profile_dir=args.profile_dir,
            output_path=args.output,
            headless=args.headless,
            edge_binary=args.edge_binary,
        )
    except (
        ValueError,
        RuntimeError,
        TimeoutException,
        WebDriverException,
    ) as exc:
        print(
            f"DeepSeek 出题失败："
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    print(
        json.dumps(
            {
                "batch_id": batch.batch_id,
                "output": str(args.output.resolve()),
                "question_count": len(
                    batch.questions
                ),
                "pending_review": sum(
                    q.review_status == "pending"
                    for q in batch.questions
                ),
                "validation_error_count": sum(
                    bool(q.validation_errors)
                    for q in batch.questions
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

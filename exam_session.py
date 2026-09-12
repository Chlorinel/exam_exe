from __future__ import annotations

"""Durable state for one AI-generated exam workflow."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable


SESSION_VERSION = 1
SESSION_STATUSES = (
    "created",
    "generated",
    "reviewed",
    "uploaded",
    "exam_created",
    "waiting_publish_confirm",
    "published",
    "scheduled",
)


@dataclass
class ExamQuestionRecord:
    local_id: str
    chapter: str = ""
    knowledge_point: str = ""
    question_type: str = ""
    score: float = 0.0
    identifier: str = ""
    review_status: str = "pending"
    upload_status: str = "pending"
    question_number: int | None = None
    platform_id: str | None = None

    @classmethod
    def from_object(cls, question: Any, *, index: int = 0) -> "ExamQuestionRecord":
        """Read generated questions and old test doubles without requiring new fields."""
        raw_score = getattr(question, "score", 0)
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0
        return cls(
            local_id=str(getattr(question, "local_id", "") or f"Q{index + 1:03d}"),
            chapter=str(getattr(question, "chapter", "") or ""),
            knowledge_point=str(getattr(question, "knowledge_point", "") or ""),
            question_type=str(getattr(question, "question_type", "") or ""),
            score=score,
            identifier=str(getattr(question, "identifier", "") or ""),
            review_status=str(getattr(question, "review_status", "pending") or "pending"),
        )


@dataclass
class ExamSession:
    session_id: str
    exam_name: str
    status: str
    questions: list[ExamQuestionRecord] = field(default_factory=list)
    created_time: str = ""


def create_session(
    exam_name: str,
    questions: Iterable[ExamQuestionRecord] | None = None,
) -> ExamSession:
    now = datetime.now()
    # Keep the session-id format introduced with the original Session work.
    session_id = now.strftime("%Y%m%d_%H%M%S")
    return ExamSession(
        session_id=session_id,
        exam_name=str(exam_name),
        status="created",
        created_time=now.strftime("%Y-%m-%d %H:%M:%S"),
        questions=list(questions or ()),
    )


def create_session_from_batch(exam_name: str, batch: Any) -> ExamSession:
    questions = [
        ExamQuestionRecord.from_object(question, index=index)
        for index, question in enumerate(getattr(batch, "questions", ()) or ())
    ]
    session = create_session(exam_name, questions)
    update_status(session, "generated")
    return session


def update_status(session: ExamSession, status: str) -> None:
    if status not in SESSION_STATUSES:
        raise ValueError(f"未知 ExamSession 状态：{status}")
    session.status = status


def add_question(session: ExamSession, question: ExamQuestionRecord) -> None:
    if any(item.local_id == question.local_id for item in session.questions):
        raise ValueError(f"Session 中存在重复 local_id：{question.local_id}")
    session.questions.append(question)


def find_question(session: ExamSession, local_id: str) -> ExamQuestionRecord:
    matches = [question for question in session.questions if question.local_id == local_id]
    if len(matches) != 1:
        raise RuntimeError(f"Session 中找不到唯一题目：{local_id}")
    return matches[0]


def sync_review_from_batch(session: ExamSession, batch: Any) -> bool:
    """Copy reviewed metadata and advance only when every item is approved."""
    incoming = list(getattr(batch, "questions", ()) or ())
    existing = {question.local_id: question for question in session.questions}
    synced: list[ExamQuestionRecord] = []
    for index, source in enumerate(incoming):
        fresh = ExamQuestionRecord.from_object(source, index=index)
        old = existing.get(fresh.local_id)
        if old is not None:
            fresh.upload_status = old.upload_status
            fresh.question_number = old.question_number
            fresh.platform_id = old.platform_id
            fresh.identifier = old.identifier or fresh.identifier
        synced.append(fresh)
    session.questions = synced
    approved = bool(synced) and all(question.review_status == "approved" for question in synced)
    if approved:
        update_status(session, "reviewed")
    elif session.status in {"reviewed", "uploaded"}:
        update_status(session, "generated")
    return approved


def update_question_upload(
    session: ExamSession,
    local_id: str,
    *,
    upload_status: str,
    platform_id: str | None = None,
) -> None:
    question = find_question(session, local_id)
    question.upload_status = upload_status
    if platform_id is not None:
        question.platform_id = str(platform_id)


def update_question_location(
    session: ExamSession,
    local_id: str,
    *,
    chapter: str,
    question_number: int,
    identifier: str,
    platform_id: str | None = None,
) -> None:
    question = find_question(session, local_id)
    question.chapter = str(chapter)
    question.question_number = int(question_number)
    question.identifier = str(identifier)
    question.upload_status = "uploaded"
    if platform_id is not None:
        question.platform_id = str(platform_id)


def all_uploaded(session: ExamSession) -> bool:
    return bool(session.questions) and all(
        question.upload_status == "uploaded"
        and question.question_number is not None
        and bool(question.identifier)
        for question in session.questions
    )


def session_to_dict(session: ExamSession) -> dict[str, Any]:
    return {
        "version": SESSION_VERSION,
        "session_id": session.session_id,
        "exam_name": session.exam_name,
        "status": session.status,
        "created_time": session.created_time,
        "questions": [asdict(question) for question in session.questions],
    }


def _atomic_json_write(path: Path, data: dict[str, Any]) -> Path:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
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
    return path


def save_session(session: ExamSession, path: Path) -> Path:
    return _atomic_json_write(Path(path), session_to_dict(session))


def load_session(path: Path) -> ExamSession:
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    # Read the first draft format as well as the flat documented format.
    source = data.get("session", data)
    questions = []
    for item in source.get("questions", []):
        value = dict(item)
        if "platform_question_id" in value and "platform_id" not in value:
            value["platform_id"] = value.pop("platform_question_id")
        allowed = ExamQuestionRecord.__dataclass_fields__
        questions.append(ExamQuestionRecord(**{key: value[key] for key in allowed if key in value}))
    session = ExamSession(
        session_id=str(source["session_id"]),
        exam_name=str(source["exam_name"]),
        status=str(source["status"]),
        created_time=str(source.get("created_time", "")),
        questions=questions,
    )
    if session.status not in SESSION_STATUSES:
        raise RuntimeError(f"Session 状态无效：{session.status}")
    return session


def save_exam_state(
    path: Path,
    *,
    exam_id: str,
    status: str,
    end_time: Any,
    answer_release_time: Any,
) -> Path:
    value = {
        "exam_id": str(exam_id),
        "status": str(status),
        "end_time": end_time.isoformat() if hasattr(end_time, "isoformat") else str(end_time or ""),
        "answer_release_time": (
            answer_release_time.isoformat()
            if hasattr(answer_release_time, "isoformat")
            else str(answer_release_time or "")
        ),
    }
    return _atomic_json_write(Path(path), value)


def load_exam_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取考试状态：{path}") from exc

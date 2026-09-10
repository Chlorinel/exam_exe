from __future__ import annotations

from pathlib import Path

from ai_question_locator import (
    ensure_all_uploaded,
    locate_uploaded_questions,
)
from config_writer import create_ai_exam_config
from create_signal_exam import load_config
from deepseek_question_generator import load_question_batch
from platform_question_uploader import (
    UploadConfig,
    upload_batch,
)
from question_review_gui_with_generation import (
    generate_and_review_questions,
    question_batch_file_is_approved,
)


def prepare_ai_exam_config(
    source_config_path: Path,
    *,
    deepseek_profile_dir: Path,
    platform_profile_dir: Path,
    output_config_path: Path | None = None,
    batch_path: Path | None = None,
    upload_state_path: Path | None = None,
    platform_edge_binary: str | None = None,
    platform_headless: bool = False,
) -> Path | None:
    """
    把“AI 出题”转换成现有考试程序可直接读取的最终配置表。

    流程：
        原考试配置
        -> AI 生成 + 人工审核
        -> 审核门禁复核
        -> 上传课程题库
        -> 确认全部 uploaded
        -> 定位章内题号和关键词
        -> 生成 *_AI完成.xlsx
        -> 再次调用现有 load_config() 验证

    本函数不会创建考试、不会发布考试、不会安排成绩发布。
    """

    source_config_path = Path(
        source_config_path
    ).resolve()
    deepseek_profile_dir = Path(
        deepseek_profile_dir
    ).resolve()
    platform_profile_dir = Path(
        platform_profile_dir
    ).resolve()

    if not source_config_path.exists():
        raise FileNotFoundError(
            source_config_path
        )

    # AI 准备阶段允许原“选题明细”为空。
    base_config = load_config(
        source_config_path,
        require_questions=False,
    )

    work_dir = (
        source_config_path.parent
        / "work"
    )
    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if batch_path is None:
        batch_path = (
            work_dir
            / (
                source_config_path.stem
                + "_AI题目.json"
            )
        )
    else:
        batch_path = Path(
            batch_path
        ).resolve()

    if output_config_path is None:
        output_config_path = (
            work_dir
            / (
                source_config_path.stem
                + "_AI完成.xlsx"
            )
        )
    else:
        output_config_path = Path(
            output_config_path
        ).resolve()

    batch_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    output_config_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # 生成窗口负责收集 QuestionSpec；
    # 审核窗口负责逐题人工确认。
    reviewed_batch_path, approved = (
        generate_and_review_questions(
            default_profile_dir=(
                deepseek_profile_dir
            ),
            default_output_path=(
                batch_path
            ),
        )
    )

    if reviewed_batch_path is None:
        # 用户在生成/审核 GUI 中取消。
        return None

    reviewed_batch_path = Path(
        reviewed_batch_path
    ).resolve()

    # GUI 返回值不是最终业务门禁。
    if not approved:
        raise RuntimeError(
            "AI 题目尚未全部人工审核通过，"
            "不会上传课程题库。"
        )

    if not question_batch_file_is_approved(
        reviewed_batch_path
    ):
        raise RuntimeError(
            "AI 题目未通过最终审核门禁，"
            "不会上传课程题库。"
        )

    batch = load_question_batch(
        reviewed_batch_path
    )

    upload_config = UploadConfig(
        course_id=base_config.course_id,
        term_id=base_config.term_id,
        course_name=base_config.course_name,
        profile_dir=platform_profile_dir,
        edge_binary=platform_edge_binary,
        headless=platform_headless,
    )

    if upload_state_path is None:
        upload_state_path = (
            reviewed_batch_path.with_suffix(
                ".upload-state.json"
            )
        )
    else:
        upload_state_path = Path(
            upload_state_path
        ).resolve()

    upload_state_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # 本地人工审核通过后，允许直接上传题库。
    # 不再增加第二次终端 YES 确认。
    upload_state = upload_batch(
        upload_config,
        reviewed_batch_path,
        upload_state_path,
        commit=True,
        assume_yes=True,
    )

    # 如果用户取消某一道题保存，或状态不是 uploaded，
    # 在这里停止，不进入定位和配置表写入。
    ensure_all_uploaded(
        batch,
        upload_state,
    )

    located_questions = (
        locate_uploaded_questions(
            upload_config,
            batch,
            upload_state,
        )
    )

    final_path = (
        create_ai_exam_config(
            source_config_path,
            output_config_path,
            located_questions,
        )
    )

    # ConfigWriter 内部已经验证一次；
    # 编排层再按普通考试完整配置读取一次，
    # 确保返回给后续 --prepare 的路径可直接使用。
    load_config(
        final_path,
        require_questions=True,
    )

    return final_path

from __future__ import annotations

import os
import posixpath
import re
import shutil
import sys
import tempfile
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import xml.etree.ElementTree as ET

from ai_question_locator import LocatedQuestion
from create_signal_exam import load_config


MAIN_NS = (
    "http://schemas.openxmlformats.org/"
    "spreadsheetml/2006/main"
)
OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/"
    "officeDocument/2006/relationships"
)
PACKAGE_REL_NS = (
    "http://schemas.openxmlformats.org/"
    "package/2006/relationships"
)
XML_NS = "http://www.w3.org/XML/1998/namespace"

ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", OFFICE_REL_NS)

SHEET_NAME = "选题明细"
QUESTION_COLUMNS = ("A", "B", "C", "D", "E")


class ConfigWriteError(RuntimeError):
    pass


def prepare_user_installation(root: Path) -> Path:
    """Create writable user files without relying on CMD's filename encoding."""
    root = Path(root).resolve()
    template = root / "考试配置模板.xlsx"
    config = root / "考试配置表.xlsx"
    if not config.exists():
        if not template.is_file():
            raise ConfigWriteError(f"找不到考试配置模板：{template}")
        shutil.copy2(template, config)
    (root / "work").mkdir(parents=True, exist_ok=True)
    return config


def _qname(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _validate_located_question(
    question: LocatedQuestion,
) -> tuple[int, str, int, str, Decimal]:
    try:
        chapter = int(question.chapter)
    except (TypeError, ValueError) as exc:
        raise ConfigWriteError(
            f"{question.local_id} 的章节编号无效："
            f"{question.chapter}"
        ) from exc

    try:
        number = int(question.number)
    except (TypeError, ValueError) as exc:
        raise ConfigWriteError(
            f"{question.local_id} 的章内题号无效："
            f"{question.number}"
        ) from exc

    chapter_name = _clean_text(
        question.chapter_name
    )
    identifier = _clean_text(
        question.identifier
    )

    try:
        score = Decimal(str(question.score))
    except (InvalidOperation, TypeError) as exc:
        raise ConfigWriteError(
            f"{question.local_id} 的分值无效："
            f"{question.score}"
        ) from exc

    if chapter <= 0:
        raise ConfigWriteError(
            f"{question.local_id} 的章节编号必须大于 0。"
        )

    if number <= 0:
        raise ConfigWriteError(
            f"{question.local_id} 的章内题号必须大于 0。"
        )

    if not chapter_name:
        raise ConfigWriteError(
            f"{question.local_id} 缺少章节名称。"
        )

    if not re.fullmatch(r"\[\d{5}\]", identifier):
        raise ConfigWriteError(
            f"{question.local_id} 缺少有效的五位唯一标识。"
        )

    # 与 create_signal_exam.load_config() 当前分值规则保持一致：
    # > 0、<= 1000、最多一位小数。
    if (
        not score.is_finite()
        or score <= 0
        or score > 1000
        or score * 10
        != (score * 10).to_integral_value()
    ):
        raise ConfigWriteError(
            f"{question.local_id} 的分值 {score} "
            "不符合考试配置要求："
            "须大于 0、不超过 1000，最多一位小数。"
        )

    return (
        chapter,
        chapter_name,
        number,
        identifier,
        score,
    )


def _worksheet_member(
    archive: ZipFile,
    sheet_name: str,
) -> str:
    try:
        workbook_root = ET.fromstring(
            archive.read("xl/workbook.xml")
        )
        rels_root = ET.fromstring(
            archive.read(
                "xl/_rels/workbook.xml.rels"
            )
        )
    except KeyError as exc:
        raise ConfigWriteError(
            "配置表不是完整的 XLSX 工作簿。"
        ) from exc
    except ET.ParseError as exc:
        raise ConfigWriteError(
            "配置表的 workbook XML 无法解析。"
        ) from exc

    sheet = None

    for candidate in workbook_root.findall(
        f".//{_qname(MAIN_NS, 'sheet')}"
    ):
        if candidate.get("name") == sheet_name:
            sheet = candidate
            break

    if sheet is None:
        raise ConfigWriteError(
            f"配置表没有工作表“{sheet_name}”。"
        )

    relationship_id = sheet.get(
        _qname(OFFICE_REL_NS, "id")
    )
    if not relationship_id:
        raise ConfigWriteError(
            f"工作表“{sheet_name}”没有关系 ID。"
        )

    target = None

    for relation in rels_root.findall(
        _qname(
            PACKAGE_REL_NS,
            "Relationship",
        )
    ):
        if relation.get("Id") == relationship_id:
            target = relation.get("Target")
            break

    if not target:
        raise ConfigWriteError(
            f"无法解析工作表“{sheet_name}”的 XML 路径。"
        )

    if target.startswith("/"):
        member = target.lstrip("/")
    else:
        member = posixpath.normpath(
            posixpath.join(
                "xl",
                target,
            )
        )

    if member not in archive.namelist():
        raise ConfigWriteError(
            f"工作表 XML 不存在：{member}"
        )

    return member


_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


def _row_number(row: ET.Element) -> int:
    raw = row.get("r")

    if raw and raw.isdigit():
        return int(raw)

    numbers: list[int] = []

    for cell in row.findall(
        _qname(MAIN_NS, "c")
    ):
        ref = cell.get("r", "")
        match = _CELL_REF.fullmatch(ref)

        if match:
            numbers.append(
                int(match.group(2))
            )

    return max(numbers, default=0)


def _column_styles(
    sheet_data: ET.Element,
) -> dict[str, str]:
    """
    尽量沿用现有数据行的单元格 style id。
    如果选题明细目前只有表头，则不复制表头样式。
    """
    styles: dict[str, str] = {}

    rows = sorted(
        sheet_data.findall(
            _qname(MAIN_NS, "row")
        ),
        key=_row_number,
        reverse=True,
    )

    for row in rows:
        if _row_number(row) <= 1:
            continue

        for cell in row.findall(
            _qname(MAIN_NS, "c")
        ):
            ref = cell.get("r", "")
            match = _CELL_REF.fullmatch(ref)

            if not match:
                continue

            column = match.group(1)

            if (
                column in QUESTION_COLUMNS
                and column not in styles
                and cell.get("s") is not None
            ):
                styles[column] = cell.get("s", "")

        if len(styles) == len(
            QUESTION_COLUMNS
        ):
            break

    return styles


def _add_string_cell(
    row: ET.Element,
    reference: str,
    value: str,
    *,
    style: str | None = None,
) -> None:
    attributes = {
        "r": reference,
        "t": "inlineStr",
    }

    if style is not None:
        attributes["s"] = style

    cell = ET.SubElement(
        row,
        _qname(MAIN_NS, "c"),
        attributes,
    )
    inline = ET.SubElement(
        cell,
        _qname(MAIN_NS, "is"),
    )
    text_node = ET.SubElement(
        inline,
        _qname(MAIN_NS, "t"),
    )

    if (
        value.startswith(" ")
        or value.endswith(" ")
    ):
        text_node.set(
            _qname(XML_NS, "space"),
            "preserve",
        )

    text_node.text = value


def _set_inline_string_cell(
    sheet_data: ET.Element,
    reference: str,
    value: str,
) -> None:
    for row in sheet_data.findall(_qname(MAIN_NS, "row")):
        for cell in row.findall(_qname(MAIN_NS, "c")):
            if cell.get("r") != reference:
                continue
            style = cell.get("s")
            cell.clear()
            cell.set("r", reference)
            cell.set("t", "inlineStr")
            if style is not None:
                cell.set("s", style)
            inline = ET.SubElement(cell, _qname(MAIN_NS, "is"))
            text_node = ET.SubElement(inline, _qname(MAIN_NS, "t"))
            text_node.text = value
            return
    raise ConfigWriteError(f"“选题明细”缺少单元格 {reference}。")


def _add_number_cell(
    row: ET.Element,
    reference: str,
    value: str,
    *,
    style: str | None = None,
) -> None:
    attributes = {
        "r": reference,
    }

    if style is not None:
        attributes["s"] = style

    cell = ET.SubElement(
        row,
        _qname(MAIN_NS, "c"),
        attributes,
    )
    value_node = ET.SubElement(
        cell,
        _qname(MAIN_NS, "v"),
    )
    value_node.text = value


def _column_number(column: str) -> int:
    result = 0

    for char in column:
        result = (
            result * 26
            + ord(char)
            - ord("A")
            + 1
        )

    return result


def _column_name(number: int) -> str:
    chars: list[str] = []

    while number:
        number, remainder = divmod(
            number - 1,
            26,
        )
        chars.append(
            chr(
                ord("A")
                + remainder
            )
        )

    return "".join(reversed(chars))


def _update_dimension(
    root: ET.Element,
    final_row: int,
) -> None:
    dimension = root.find(
        _qname(MAIN_NS, "dimension")
    )

    if dimension is None:
        return

    current = dimension.get(
        "ref",
        "A1",
    )

    end = (
        current.split(":", 1)[-1]
    )
    match = _CELL_REF.fullmatch(end)

    current_end_column = (
        match.group(1)
        if match
        else "A"
    )

    final_column = _column_name(
        max(
            _column_number(
                current_end_column
            ),
            _column_number("E"),
        )
    )

    dimension.set(
        "ref",
        f"A1:{final_column}{final_row}",
    )


def _append_questions_xml(
    raw_xml: bytes,
    questions: list[
        tuple[
            int,
            str,
            int,
            str,
            Decimal,
        ]
    ],
) -> bytes:
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise ConfigWriteError(
            "“选题明细”的 worksheet XML 无法解析。"
        ) from exc

    sheet_data = root.find(
        _qname(MAIN_NS, "sheetData")
    )

    if sheet_data is None:
        raise ConfigWriteError(
            "“选题明细”没有 sheetData。"
        )

    existing_rows = sheet_data.findall(
        _qname(MAIN_NS, "row")
    )

    last_row = max(
        (
            _row_number(row)
            for row in existing_rows
        ),
        default=0,
    )

    if last_row < 1:
        raise ConfigWriteError(
            "“选题明细”缺少表头行。"
        )

    _set_inline_string_cell(
        sheet_data,
        "D1",
        "五位唯一标识（核对用）",
    )

    styles = _column_styles(
        sheet_data
    )

    next_row = last_row + 1

    for (
        chapter,
        chapter_name,
        number,
        identifier,
        score,
    ) in questions:
        row = ET.SubElement(
            sheet_data,
            _qname(MAIN_NS, "row"),
            {"r": str(next_row)},
        )

        _add_number_cell(
            row,
            f"A{next_row}",
            str(chapter),
            style=styles.get("A"),
        )
        _add_string_cell(
            row,
            f"B{next_row}",
            chapter_name,
            style=styles.get("B"),
        )
        _add_number_cell(
            row,
            f"C{next_row}",
            str(number),
            style=styles.get("C"),
        )
        _add_string_cell(
            row,
            f"D{next_row}",
            identifier,
            style=styles.get("D"),
        )
        _add_number_cell(
            row,
            f"E{next_row}",
            _decimal_text(score),
            style=styles.get("E"),
        )

        next_row += 1

    _update_dimension(
        root,
        next_row - 1,
    )

    return ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )


def _base_config_dict(
    config,
) -> dict:
    data = asdict(config)
    data.pop("questions", None)
    return data


def _verify_output(
    source_path: Path,
    candidate_path: Path,
    questions: list[
        tuple[
            int,
            str,
            int,
            str,
            Decimal,
        ]
    ],
) -> None:
    before = load_config(
        source_path,
        require_questions=False,
    )
    after = load_config(
        candidate_path,
        require_questions=bool(
            before.questions
            or questions
        ),
    )

    if (
        _base_config_dict(before)
        != _base_config_dict(after)
    ):
        raise ConfigWriteError(
            "写入 AI 题后考试基础设置发生变化，"
            "已拒绝生成最终配置表。"
        )

    original_count = len(
        before.questions
    )

    if (
        tuple(
            after.questions[
                :original_count
            ]
        )
        != tuple(before.questions)
    ):
        raise ConfigWriteError(
            "写入 AI 题后原有选题发生变化，"
            "已拒绝生成最终配置表。"
        )

    appended = after.questions[
        original_count:
    ]

    if len(appended) != len(
        questions
    ):
        raise ConfigWriteError(
            "最终配置表中的 AI 题数量"
            "与待写入数量不一致。"
        )

    for actual, expected in zip(
        appended,
        questions,
    ):
        (
            chapter,
            chapter_name,
            number,
            identifier,
            score,
        ) = expected

        if (
            actual.chapter
            != chapter
            or actual.chapter_name
            != chapter_name
            or actual.number
            != number
            or actual.identifier
            != identifier
            or actual.score
            != score
        ):
            raise ConfigWriteError(
                "最终配置表中的 AI 题"
                "与定位结果不一致。"
            )


def create_ai_exam_config(
    source_path: Path,
    output_path: Path,
    ai_questions: list[
        LocatedQuestion
    ],
) -> Path:
    """
    从原考试配置表生成 AI 完成版配置表。

    - 不覆盖 source_path；
    - 保留原有考试设置和原有选题；
    - 只在“选题明细”末尾追加 AI 题五列；
    - 写入临时文件后调用现有 load_config() 自验证；
    - 验证成功后才原子替换 output_path。
    """
    source_path = Path(
        source_path
    ).resolve()
    output_path = Path(
        output_path
    ).resolve()

    if not source_path.exists():
        raise FileNotFoundError(
            source_path
        )

    if (
        source_path.suffix.lower()
        != ".xlsx"
    ):
        raise ConfigWriteError(
            "源配置必须是 .xlsx 文件。"
        )

    if (
        output_path.suffix.lower()
        != ".xlsx"
    ):
        raise ConfigWriteError(
            "输出配置必须是 .xlsx 文件。"
        )

    if source_path == output_path:
        raise ConfigWriteError(
            "不会覆盖原考试配置表，"
            "请指定新的输出路径。"
        )

    if not ai_questions:
        raise ConfigWriteError(
            "没有可写入的 AI 题目。"
        )

    # 先用现有配置解析器确认源文件本身有效。
    base_config = load_config(
        source_path,
        require_questions=False,
    )

    prepared: list[
        tuple[
            int,
            str,
            int,
            str,
            Decimal,
        ]
    ] = []

    occupied = {
        (
            question.chapter,
            question.number,
        )
        for question
        in base_config.questions
    }

    local_ids: set[str] = set()

    for question in ai_questions:
        local_id = _clean_text(
            question.local_id
        )

        if not local_id:
            raise ConfigWriteError(
                "存在缺少 local_id 的 AI 题目。"
            )

        if local_id in local_ids:
            raise ConfigWriteError(
                f"重复的 AI local_id：{local_id}"
            )

        local_ids.add(local_id)

        row = _validate_located_question(
            question
        )

        key = (
            row[0],
            row[2],
        )

        if key in occupied:
            raise ConfigWriteError(
                f"第 {row[0]} 章第 {row[2]} 题"
                "已经存在于选题明细中。"
            )

        occupied.add(key)
        prepared.append(row)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    handle, temporary_name = (
        tempfile.mkstemp(
            prefix=(
                "."
                + output_path.stem
                + "."
            ),
            suffix=".xlsx",
            dir=output_path.parent,
        )
    )
    os.close(handle)

    temporary_path = Path(
        temporary_name
    )

    try:
        with ZipFile(
            source_path,
            "r",
        ) as source_archive:
            worksheet_member = (
                _worksheet_member(
                    source_archive,
                    SHEET_NAME,
                )
            )

            modified_worksheet = (
                _append_questions_xml(
                    source_archive.read(
                        worksheet_member
                    ),
                    prepared,
                )
            )

            with ZipFile(
                temporary_path,
                "w",
                compression=ZIP_DEFLATED,
            ) as output_archive:
                for info in (
                    source_archive.infolist()
                ):
                    data = (
                        modified_worksheet
                        if info.filename
                        == worksheet_member
                        else source_archive.read(
                            info.filename
                        )
                    )

                    output_archive.writestr(
                        info,
                        data,
                    )

        _verify_output(
            source_path,
            temporary_path,
            prepared,
        )

        os.replace(
            temporary_path,
            output_path,
        )

    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise

    return output_path


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2 and arguments[0] == "--prepare-install":
        prepare_user_installation(Path(arguments[1]))
        return 0
    print("用法：config_writer.py --prepare-install <项目目录>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

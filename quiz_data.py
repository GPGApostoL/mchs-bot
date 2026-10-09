"""Load questions from attached_assets/questions.docx and build answer choices.

Distractors are selected by answer type first (ratio, measurement, period, legal
reference, etc.), then by section and wording. Source answers are never rewritten.
"""

from collections import defaultdict
from itertools import combinations
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

SOURCE_DOCUMENT = Path(__file__).resolve().parent / "attached_assets" / "questions.docx"
OPTION_LABELS = ("A", "B", "C", "D")
MAX_TELEGRAM_MESSAGE_LENGTH = 3900
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": WORD_NAMESPACE}

_INFERRED_ANSWERS = {
    "Боевая готовность пожарной аварийно-спасательной техники определяется:": (
        "Технической исправностью и полной укомплектованностью пожарно-спасательной "
        "техники, обеспечивающими ее готовность к выполнению задач."
    ),
    "Перечислите составляющие элементы организационной структуры ГСЧС Республики Беларусь?": (
        "Территориальные и функциональные подсистемы ГСЧС Республики Беларусь."
    ),
    "С какой периодичностью Министерство по чрезвычайным ситуациям представляет "
    "в Совет Министров Республики Беларусь доклад о состоянии защиты населения "
    "и территорий от чрезвычайных ситуаций?": "Ежегодно.",
}
_INFERRED_ANSWER_NOTE = (
    "В документе ответ отсутствует или отмечен вопросительным знаком; "
    "этот вариант ответа подготовлен предположительно."
)

# Explicit distractors for questions where the source only gives one correct answer.
# These are intentionally type-matched and do not change the source answer.
_MANUAL_DISTRACTORS = {
    "Какое соотношение масла и топлива необходимо соблюдать при приготовлении топливной смеси для бензопил STIHL?": [
        "1:25", "1:75", "1:100"
    ],
}


def _norm(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split()).strip()


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts = []
    for element in paragraph.iter():
        if element.tag == f"{{{WORD_NAMESPACE}}}t":
            parts.append(element.text or "")
        elif element.tag == f"{{{WORD_NAMESPACE}}}tab":
            parts.append("\t")
        elif element.tag in (f"{{{WORD_NAMESPACE}}}br", f"{{{WORD_NAMESPACE}}}cr"):
            parts.append("\n")
    return "".join(parts)


def _cell_text(cell: ElementTree.Element) -> str:
    return "\n".join(_paragraph_text(p) for p in cell.findall(".//w:p", NS)).strip()


def _draft_answer(question: str) -> str | None:
    normalized = _norm(question)
    for source_question, answer in _INFERRED_ANSWERS.items():
        if normalized == _norm(source_question):
            return answer
    return None


def _load_source_questions() -> list[dict]:
    if not SOURCE_DOCUMENT.is_file():
        raise FileNotFoundError(f"Question source document was not found: {SOURCE_DOCUMENT}")
    try:
        with ZipFile(SOURCE_DOCUMENT) as document:
            xml = ElementTree.fromstring(document.read("word/document.xml"))
    except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise RuntimeError(f"Could not read the question tables in {SOURCE_DOCUMENT.name}.") from exc

    tables = xml.findall(".//w:tbl", NS)
    if not tables:
        raise ValueError(f"No question tables were found in {SOURCE_DOCUMENT.name}.")
    records = []
    for group, table in enumerate(tables):
        rows = []
        for row_number, row in enumerate(table.findall("./w:tr", NS), start=1):
            cells = row.findall("./w:tc", NS)
            if len(cells) < 3:
                raise ValueError(f"Unexpected table layout in table {group + 1}, row {row_number}.")
            rows.append((_cell_text(cells[1]), _cell_text(cells[2])))
        i = 0
        while i < len(rows):
            question, source_answer = rows[i]

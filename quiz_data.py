"""Load the complete question bank from the uploaded DOCX and build choices.

The source document contains each question and its answer, but not four options.
Distractors are selected from other answers in the same section where possible.
"""

from collections import defaultdict
from itertools import combinations
from pathlib import Path
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


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for element in paragraph.iter():
        if element.tag == f"{{{WORD_NAMESPACE}}}t":
            parts.append(element.text or "")
        elif element.tag == f"{{{WORD_NAMESPACE}}}tab":
            parts.append("\t")
        elif element.tag in (
            f"{{{WORD_NAMESPACE}}}br",
            f"{{{WORD_NAMESPACE}}}cr",
        ):
            parts.append("\n")
    return "".join(parts)


def _cell_text(cell: ElementTree.Element) -> str:
    paragraphs = cell.findall(".//w:p", NS)
    return "\n".join(_paragraph_text(paragraph) for paragraph in paragraphs).strip()


def _draft_answer(question: str) -> str | None:
    answer = _INFERRED_ANSWERS.get(question)
    if answer is not None:
        return answer

    # The source uses a line break in this question on some Word versions.
    normalized = " ".join(question.split())
    for source_question, drafted_answer in _INFERRED_ANSWERS.items():
        if normalized == " ".join(source_question.split()):
            return drafted_answer
    return None


def _load_source_questions() -> list[dict]:
    if not SOURCE_DOCUMENT.is_file():
        raise FileNotFoundError(
            f"Question source document was not found: {SOURCE_DOCUMENT}"
        )

    try:
        with ZipFile(SOURCE_DOCUMENT) as document:
            xml = ElementTree.fromstring(document.read("word/document.xml"))
    except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise RuntimeError(
            f"Could not read the question tables in {SOURCE_DOCUMENT.name}."
        ) from exc

    tables = xml.findall(".//w:tbl", NS)
    if not tables:
        raise ValueError(f"No question tables were found in {SOURCE_DOCUMENT.name}.")

    records: list[dict] = []
    for group, table in enumerate(tables):
        table_rows: list[tuple[str, str]] = []
        for row_number, row in enumerate(table.findall("./w:tr", NS), start=1):
            cells = row.findall("./w:tc", NS)
            if len(cells) < 3:
                raise ValueError(
                    f"Unexpected table layout in table {group + 1}, row {row_number}."
                )
            table_rows.append((_cell_text(cells[1]), _cell_text(cells[2])))

        row_index = 0
        while row_index < len(table_rows):
            question, source_answer = table_rows[row_index]

            # A down arrow marks a question continued in the following row.
            if source_answer == "↓":
                if row_index + 1 >= len(table_rows):
                    raise ValueError(
                        f"Question continuation at the end of table {group + 1}."
                    )
                continued_question, source_answer = table_rows[row_index + 1]
                question = f"{question.rstrip()} {continued_question.lstrip()}".strip()
                row_index += 1

            if not question:
                raise ValueError(
                    f"An empty question was found in table {group + 1}, "
                    f"row {row_index + 1}."
                )

            answer = source_answer
            answer_inferred = False
            if not answer.strip() or answer.strip() == "?":
                answer = _draft_answer(question) or ""
                if not answer:
                    raise ValueError(
                        "A question has no answer in the source and no approved "
                        f"draft is available: {question}"
                    )
                answer_inferred = True

            records.append(
                {
                    "group": group,
                    "question": question,
                    "answer": answer,
                    "source_answer": source_answer,
                    "answer_inferred": answer_inferred,
                }
            )
            row_index += 1

    return records


def _format_length(question: str, options: list[str], answer_inferred: bool) -> int:
    heading = "⚖️ ВОПРОС [50/50]\n\n"
    body = heading + question
    if answer_inferred:
        body += f"\n\n⚠️ {_INFERRED_ANSWER_NOTE}"
    body += "\n\n" + "\n\n".join(
        f"{label}) {option}" for label, option in zip(OPTION_LABELS, options)
    )
    return len(body)


def _unique_answers(records: list[dict]) -> list[str]:
    return list(
        dict.fromkeys(
            record["answer"]
            for record in records
            if record["answer"] and not record["answer_inferred"]
        )
    )


def _find_distractors(
    question: str,
    correct_answer: str,
    answer_inferred: bool,
    candidates: list[str],
) -> list[str] | None:
    unique_candidates = list(
        dict.fromkeys(
            answer
            for answer in candidates
            if answer and answer != correct_answer
        )
    )
    if len(unique_candidates) < 3:
        return None

    by_similarity = sorted(
        unique_candidates,
        key=lambda answer: (abs(len(answer) - len(correct_answer)), len(answer)),
    )
    by_length = sorted(unique_candidates, key=len)
    search_space = list(dict.fromkeys(by_similarity[:32] + by_length[:32]))

    best: tuple[int, list[str]] | None = None
    for trio in combinations(search_space, 3):
        options = [correct_answer, *trio]
        if _format_length(question, options, answer_inferred) > MAX_TELEGRAM_MESSAGE_LENGTH:
            continue
        score = sum(abs(len(option) - len(correct_answer)) for option in trio)
        if best is None or score < best[0]:
            best = (score, list(trio))

    return best[1] if best else None


def _build_quiz_questions(records: list[dict]) -> list[dict]:
    answers_by_group: dict[int, list[str]] = defaultdict(list)
    for record in records:
        if record["answer"] and not record["answer_inferred"]:
            answers_by_group[record["group"]].append(record["answer"])

    all_answers = _unique_answers(records)
    quiz_questions: list[dict] = []

    for record in records:
        question = record["question"]
        answer = record["answer"]
        inferred = record["answer_inferred"]

        distractors = _find_distractors(
            question,
            answer,
            inferred,
            answers_by_group[record["group"]],
        )
        if distractors is None:
            distractors = _find_distractors(
                question,
                answer,
                inferred,
                all_answers,
            )
        if distractors is None:
            raise ValueError(
                f"Could not create three distinct answer choices for: {question}"
            )

        options = [answer, *distractors]
        quiz_questions.append(
            {
                "question": question,
                "answer": answer,
                "options": options,
                "correct": 0,
                "answer_inferred": inferred,
                "source_answer": record["source_answer"],
                "source_group": record["group"],
            }
        )

    return quiz_questions


SOURCE_QUESTIONS = _load_source_questions()
QUIZ_QUESTIONS = _build_quiz_questions(SOURCE_QUESTIONS)

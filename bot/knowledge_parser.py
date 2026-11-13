"""Parse pasted knowledge outlines into validated knowledge documents.

P18 can call :func:`parse_knowledge_text` with a topic name and the pasted body.
It returns a ``KnowledgeDocument``; call ``model_dump()`` for a JSON-compatible
tree. Parse failures are ``KnowledgeParseError`` instances with ``line_number``
and ``message`` attributes, or use ``as_dict()`` for ``{"line": n,
"message": text}``.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import ValidationError

from bot.knowledge_schema import MAX_PATH_ID_LENGTH, KnowledgeDocument


class KnowledgeParseError(ValueError):
    """A parse or schema error tied to a one-based source line."""

    def __init__(self, line_number: int, message: str) -> None:
        self.line_number = line_number
        self.message = message
        super().__init__(f"Line {line_number}: {message}")

    def as_dict(self) -> dict[str, int | str]:
        """Return the stable error shape expected by UI callers."""
        return {"line": self.line_number, "message": self.message}


_BULLET_RE = re.compile(r"^( *)(?:- )(.*)$")
MAX_OUTLINE_CHARACTERS = 100_000
MAX_OUTLINE_ITEMS = 2_000


class _SiblingIds:
    def __init__(self, max_length: int | None = None) -> None:
        self._max_length = max_length
        self._used: set[str] = set()
        self._next_suffix: dict[str, int] = {}

    def allocate(self, text: str) -> str:
        base = _make_id(text)[:self._max_length].rstrip("-")
        identifier = base
        if base in self._used:
            suffix = self._next_suffix.get(base, 2)
            while identifier in self._used:
                ending = f"-{suffix}"
                stem_length = None if self._max_length is None else self._max_length - len(ending)
                identifier = base[:stem_length].rstrip("-") + ending
                suffix += 1
            self._next_suffix[base] = suffix
        self._used.add(identifier)
        return identifier


def parse_knowledge_text(topic_name: str, body: str) -> KnowledgeDocument:
    """Parse a topic name and outline body into the existing knowledge schema.

    A unit's first concept starts at column one or one level deeper. Each
    subsequent level adds two spaces or one tab. Blank lines are preserved
    inside an answer when followed by an answer continuation.
    """
    if len(topic_name) + len(body) > MAX_OUTLINE_CHARACTERS:
        raise KnowledgeParseError(1, "주제명과 본문은 합쳐서 최대 100,000자까지 입력할 수 있습니다.")
    lines = [re.sub(r"^[ \t]+", lambda match: match[0].replace("\t", "  "), line)
             for line in body.splitlines()]
    topic_name = topic_name.strip()
    if not topic_name:
        raise KnowledgeParseError(1, "주제명을 입력해 주세요.")

    units: list[dict] = []
    unit_ids = _SiblingIds(MAX_PATH_ID_LENGTH)
    concept_ids = _SiblingIds(MAX_PATH_ID_LENGTH)
    question_ids = _SiblingIds()
    line_locations: dict[tuple, int] = {}
    current_unit: dict | None = None
    current_concept: dict | None = None
    current_question: dict | None = None
    current_unit_index: int | None = None
    current_concept_index: int | None = None
    answer_lines: list[str] | None = None
    concept_indent = 0

    index = 0
    item_count = 0
    while index < len(lines):
        raw_line = lines[index]
        line_number = index + 1
        index += 1

        if not raw_line.strip():
            if answer_lines is not None:
                blank_count = 1
                while index < len(lines) and not lines[index].strip():
                    blank_count += 1
                    index += 1
                if index < len(lines) and _is_continuation(lines[index], concept_indent + 6):
                    answer_lines.extend([""] * blank_count)
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == concept_indent + 6:
            if answer_lines is None:
                raise KnowledgeParseError(line_number, "여섯 칸 들여쓰기 줄은 예시 답안의 후속 줄이어야 합니다.")
            answer_lines.append(raw_line[indent:])
            continue

        if answer_lines is not None:
            current_question["example_answer"] = "\n".join(answer_lines)
            answer_lines = None

        bullet = _BULLET_RE.match(raw_line)
        if bullet or indent == 0:
            item_count += 1
            if item_count > MAX_OUTLINE_ITEMS:
                raise KnowledgeParseError(line_number, "단원, 개념, 질문, 예시 답안은 합쳐서 최대 2,000개까지 입력할 수 있습니다.")
        if bullet:
            if current_unit is not None and not current_unit["concepts"] and indent in (0, 2):
                concept_indent = indent
            level_indent = indent - concept_indent
            text = bullet.group(2).strip()
            if not text:
                raise KnowledgeParseError(line_number, "글머리표 뒤에 내용을 입력해 주세요.")
            if level_indent == 0:
                if current_unit is None:
                    raise KnowledgeParseError(line_number, "개념을 추가하기 전에 단원을 입력해 주세요.")
                current_concept = _new_node(
                    "concept", text, current_unit["concepts"], concept_ids, line_number, line_locations,
                    parent_indices=(current_unit_index,),
                )
                question_ids = _SiblingIds()
                current_concept_index = len(current_unit["concepts"]) - 1
                current_question = None
            elif level_indent == 2:
                if current_concept is None:
                    raise KnowledgeParseError(line_number, "질문을 추가하기 전에 개념을 입력해 주세요.")
                if len(current_concept["questions"]) >= 2:
                    raise KnowledgeParseError(line_number, "개념당 질문은 최대 2개까지 입력할 수 있습니다.")
                current_question = _new_node(
                    "question", text, current_concept["questions"], question_ids, line_number, line_locations,
                    parent_indices=(current_unit_index, current_concept_index),
                )
            elif level_indent == 4:
                if current_question is None:
                    raise KnowledgeParseError(line_number, "예시 답안을 추가하기 전에 질문을 입력해 주세요.")
                if current_question.get("example_answer") is not None:
                    raise KnowledgeParseError(line_number, "질문당 예시 답안은 최대 1개까지 입력할 수 있습니다.")
                answer_lines = [text]
            else:
                raise KnowledgeParseError(line_number, "개념 들여쓰기를 기준으로 질문과 예시 답안을 한 단계씩 들여써 주세요.")
            continue

        if raw_line.lstrip().startswith("-"):
            raise KnowledgeParseError(line_number, "글머리표는 하이픈 뒤에 공백을 넣어 입력해 주세요.")

        if indent == 0:
            text = raw_line.strip()
            current_unit = _new_node("unit", text, units, unit_ids, line_number, line_locations)
            concept_ids = _SiblingIds(MAX_PATH_ID_LENGTH)
            current_unit_index = len(units) - 1
            concept_indent = 0
            current_concept = None
            current_question = None
            current_concept_index = None
            continue

        raise KnowledgeParseError(line_number, "허용되지 않은 들여쓰기입니다.")

    if answer_lines is not None:
        current_question["example_answer"] = "\n".join(answer_lines)

    if not units:
        raise KnowledgeParseError(1, "단원과 내용을 입력해 주세요.")
    for unit_index, unit in enumerate(units):
        if not unit["concepts"]:
            raise KnowledgeParseError(
                line_locations[("unit", unit_index)], "단원에 개념을 하나 이상 입력해 주세요."
            )

    topic = {
        "id": _make_id(topic_name)[:MAX_PATH_ID_LENGTH].rstrip("-"),
        "name": topic_name,
        "units": units,
    }
    try:
        return KnowledgeDocument.model_validate({"schema_version": "1.0", "topic": topic})
    except ValidationError as exc:
        error = exc.errors(include_input=False, include_url=False)[0]
        location = tuple(error["loc"])
        line_number = _line_for_location(location, line_locations)
        raise KnowledgeParseError(line_number, error["msg"]) from exc


def _new_node(
    kind: str,
    text: str,
    siblings: list[dict],
    sibling_ids: _SiblingIds,
    line_number: int,
    line_locations: dict[tuple, int],
    *,
    parent_indices: tuple[int | None, ...] = (),
) -> dict:
    index = len(siblings)
    identifier = sibling_ids.allocate(text)

    if kind == "unit":
        node = {"id": identifier, "name": text, "concepts": []}
        line_locations[("unit", index)] = line_number
    elif kind == "concept":
        node = {"id": identifier, "name": text, "questions": []}
        unit_index = parent_indices[0]
        line_locations[("concept", unit_index, index)] = line_number
    else:
        node = {"id": identifier, "prompt": text}
        unit_index, concept_index = parent_indices
        line_locations[("question", unit_index, concept_index, index)] = line_number
    siblings.append(node)
    return node


def _make_id(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    parts: list[str] = []
    separator = False
    for character in normalized:
        if character.isalnum():
            parts.append(character)
            separator = False
        elif not separator and parts:
            parts.append("-")
            separator = True
    identifier = "".join(parts).strip("-")
    return identifier or "item"


def _is_continuation(line: str, indent: int) -> bool:
    return len(line) - len(line.lstrip(" ")) == indent and bool(line.strip())


def _line_for_location(location: tuple, line_locations: dict[tuple, int]) -> int:
    if len(location) >= 3 and location[0] == "topic" and location[1] == "units":
        unit_index = location[2]
        if len(location) >= 5 and location[3] == "concepts":
            concept_index = location[4]
            if len(location) >= 7 and location[5] == "questions":
                question_index = location[6]
                return line_locations.get(
                    ("question", unit_index, concept_index, question_index),
                    line_locations.get(("concept", unit_index, concept_index), 1),
                )
            return line_locations.get(("concept", unit_index, concept_index), 1)
        return line_locations.get(("unit", unit_index), 1)
    return 1

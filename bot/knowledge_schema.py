from typing import Annotated, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MAX_DOCUMENT_VALUES = 20_000
MAX_DOCUMENT_DEPTH = 16
MAX_DOCUMENT_TEXT_CHARACTERS = 2 * 1024 * 1024
MAX_PATH_ID_LENGTH = 128


class KnowledgeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonBlankText


def reject_path_separator(value: str) -> str:
    if "/" in value:
        raise ValueError("ID must not contain '/'")
    return value


class KnowledgePathItem(KnowledgeItem):
    id: Annotated[
        NonBlankText, StringConstraints(max_length=MAX_PATH_ID_LENGTH), AfterValidator(reject_path_separator)
    ]


Item = TypeVar("Item", bound=KnowledgeItem)


def require_unique_ids(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            preview = item.id if len(item.id) <= 80 else item.id[:79] + "…"
            raise ValueError(f"Duplicate sibling ID: {preview!r}")
        seen.add(item.id)
    return items


class Question(KnowledgeItem):
    prompt: NonBlankText
    example_answer: NonBlankText | None = None


class Concept(KnowledgePathItem):
    name: NonBlankText
    questions: Annotated[
        list[Question], Field(max_length=2), AfterValidator(require_unique_ids)
    ] = Field(default_factory=list)


class Unit(KnowledgePathItem):
    name: NonBlankText
    concepts: Annotated[list[Concept], Field(min_length=1), AfterValidator(require_unique_ids)]


class Topic(KnowledgePathItem):
    name: NonBlankText
    units: Annotated[list[Unit], Field(min_length=1), AfterValidator(require_unique_ids)]


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    topic: Topic

    @model_validator(mode="before")
    @classmethod
    def limit_input_structure(cls, data: object) -> object:
        # Bound the raw tree before field validation can accumulate per-entry errors.
        values = 0
        characters = 0

        def visit(value: object, depth: int) -> None:
            nonlocal values, characters
            values += 1
            if depth > MAX_DOCUMENT_DEPTH or values > MAX_DOCUMENT_VALUES:
                raise ValueError("문서 구조 한도(값·키 20,000개, 깊이 16단계)를 초과합니다.")
            if isinstance(value, dict):
                for key, child in value.items():
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child, depth + 1)
            elif isinstance(value, str):
                characters += len(value)
                if characters > MAX_DOCUMENT_TEXT_CHARACTERS:
                    raise ValueError("문서 텍스트는 최대 2,097,152자까지 입력할 수 있습니다.")

        visit(data, 1)
        return data

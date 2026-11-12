from typing import Annotated, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class KnowledgeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonBlankText


def reject_path_separator(value: str) -> str:
    if "/" in value:
        raise ValueError("ID must not contain '/'")
    return value


class KnowledgePathItem(KnowledgeItem):
    id: Annotated[NonBlankText, AfterValidator(reject_path_separator)]


Item = TypeVar("Item", bound=KnowledgeItem)


def require_unique_ids(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"Duplicate sibling ID: {item.id!r}")
        seen.add(item.id)
    return items


class Question(KnowledgeItem):
    prompt: NonBlankText
    example_answer: NonBlankText


class Concept(KnowledgePathItem):
    name: NonBlankText
    questions: Annotated[
        list[Question], Field(min_length=1, max_length=2), AfterValidator(require_unique_ids)
    ]


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

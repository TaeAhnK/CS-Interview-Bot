"""UI-independent interview question and session state machine."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field
from typing import Any

from bot.evaluation import EvaluationProvider, EvaluationRequest, EvaluationResponse, QuestionAnswer
from bot.knowledge_schema import Concept, KnowledgeDocument


class SessionError(RuntimeError):
    """Base error for an invalid session operation."""


class SessionBusyError(SessionError):
    """An evaluation is in progress and the requested operation is locked."""


class SessionStateError(SessionError):
    """The operation is not valid in the current session state."""


@dataclass(frozen=True)
class QuestionRef:
    id: str
    prompt: str


@dataclass(frozen=True)
class ConceptRef:
    id: str
    name: str
    questions: tuple[QuestionRef, ...]


@dataclass
class AnswerRecord:
    question: str
    answer: str
    evaluation: EvaluationResponse | None = None
    evaluation_published: bool = False


@dataclass
class ConceptRecord:
    concept_id: str
    concept_name: str
    turns: list[AnswerRecord] = field(default_factory=list)
    unanswered_questions: list[str] = field(default_factory=list)
    skipped: bool = False


@dataclass(frozen=True)
class SessionSnapshot:
    status: str
    question: str | None
    concept_id: str | None
    concept_name: str | None
    provider_id: str | None
    busy: bool
    error: str | None
    records: tuple[dict[str, Any], ...]


def _concepts_from_documents(documents: list[KnowledgeDocument] | tuple[KnowledgeDocument, ...]) -> list[ConceptRef]:
    concepts: list[ConceptRef] = []
    for document in documents:
        for unit in document.topic.units:
            for concept in unit.concepts:
                concepts.append(_concept_ref(concept, f"{document.topic.id}/{unit.id}/{concept.id}"))
    return concepts


def _concept_ref(concept: Concept, concept_id: str | None = None) -> ConceptRef:
    return ConceptRef(
        id=concept_id or concept.id,
        name=concept.name,
        questions=tuple(QuestionRef(id=q.id, prompt=q.prompt) for q in concept.questions),
    )


class InterviewSessionEngine:
    """Owns question selection, answer history, evaluation, and session transitions.

    Use ``from_documents`` to build the session from validated knowledge documents.
    Concept IDs are expected to be unique in the supplied
    collection; callers with multiple topics should pass path-qualified IDs.
    """

    MAX_FOLLOW_UPS = 2

    def __init__(
        self,
        concepts: list[ConceptRef] | tuple[ConceptRef, ...],
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._concepts = list(concepts)
        ids = [concept.id for concept in self._concepts]
        if len(ids) != len(set(ids)):
            raise ValueError("Concept IDs must be unique in the session engine")
        self._by_id = {concept.id: concept for concept in self._concepts}
        self._rng = rng or random.Random()
        self._lock = threading.RLock()
        self._reset_question_pool(ids)
        self._question: str | None = None
        self._current_concept: ConceptRef | None = None
        self._status = "ready" if ids else "empty"
        self._provider_id: str | None = None
        self._provider: EvaluationProvider | None = None
        self._busy = False
        self._error: str | None = None
        self._records: list[ConceptRecord] = []
        self._record_by_id: dict[str, ConceptRecord] = {}
        self._follow_up_count = 0
        self._generation = 0
        self._pending_answer: str | None = None
        self._draw_initial_question()

    @classmethod
    def from_documents(
        cls,
        documents: list[KnowledgeDocument] | tuple[KnowledgeDocument, ...],
        *,
        rng: random.Random | None = None,
    ) -> "InterviewSessionEngine":
        return cls(_concepts_from_documents(documents), rng=rng)

    def set_scope(self, concept_ids: list[str] | tuple[str, ...]) -> SessionSnapshot:
        with self._lock:
            if self._status not in {"ready", "empty"}:
                raise SessionStateError("The interview scope is fixed after the first submission")
            if self._busy:
                raise SessionBusyError("An evaluation is in progress")
            unknown = set(concept_ids) - self._by_id.keys()
            if unknown:
                raise ValueError(f"Unknown concept IDs: {', '.join(sorted(unknown))}")
            if len(set(concept_ids)) != len(concept_ids):
                raise ValueError("Scope contains duplicate concept IDs")
            self._reset_question_pool(concept_ids)
            self._current_concept = None
            self._question = None
            self._status = "ready" if concept_ids else "empty"
            self._draw_initial_question()
            return self.snapshot()

    def redraw_question(self) -> str:
        """Choose from all basic questions in the pre-session scope, including current."""
        with self._lock:
            self._ensure_pre_session()
            if not self._initial_questions:
                raise SessionStateError("No concepts are selected")
            self._current_concept, self._question = self._rng.choice(self._initial_questions)
            return self._question

    def submit(
        self, answer: str, provider_id: str, provider: EvaluationProvider | None = None, *, record_start: int = 0,
    ) -> SessionSnapshot:
        """Submit using the provider fixed on the first answer. Retain failed drafts for ``retry``."""
        with self._lock:
            self._ensure_submittable()
            if not answer.strip():
                raise ValueError("Answer must not be blank")
            if self._status == "ready":
                if not self._remaining_concepts:
                    raise SessionStateError("No concepts are selected")
                if provider is None:
                    raise SessionStateError("No evaluation provider is configured")
                self._provider_id = provider_id
                self._provider = provider
                self._status = "active"
                self._touch_current_concept()
            elif provider_id != self._provider_id:
                raise SessionStateError("The evaluation provider is fixed for this interview")
            chosen_provider = self._provider
            if chosen_provider is None:
                raise SessionStateError("No evaluation provider is configured")
            self._pending_answer = answer
            prepared = self._prepare_evaluation()
        return self._run_evaluation(prepared, chosen_provider, record_start)

    def retry(self, provider: EvaluationProvider | None = None, *, record_start: int = 0) -> SessionSnapshot:
        """Retry the retained answer using the fixed provider identity."""
        with self._lock:
            if self._status != "error" or self._pending_answer is None:
                raise SessionStateError("There is no failed answer to retry")
            if self._busy:
                raise SessionBusyError("An evaluation is in progress")
            chosen_provider = provider or self._provider
            if chosen_provider is None:
                raise SessionStateError("No evaluation provider is configured")
            self._provider = chosen_provider
            self._error = None
            self._status = "active"
            prepared = self._prepare_evaluation()
        return self._run_evaluation(prepared, chosen_provider, record_start)

    def skip_concept(self, *, record_start: int = 0) -> SessionSnapshot:
        """Finish the current concept, mark a visible unanswered follow-up, and advance."""
        with self._lock:
            if self._busy:
                raise SessionBusyError("Cannot move while an evaluation is in progress")
            self._ensure_active()
            if self._status == "error":
                raise SessionStateError("Retry the failed answer or end the interview")
            self._touch_current_concept()
            self._finish_current(skipped=True)
            self._advance_or_finish()
            return self.snapshot(record_start=record_start)

    def end(self, *, record_start: int = 0) -> SessionSnapshot:
        """End immediately; an in-flight provider response is invalidated and discarded."""
        with self._lock:
            if self._status in {"finished", "empty"}:
                return self.snapshot(record_start=record_start)
            self._generation += 1
            self._busy = False
            self._pending_answer = None
            self._error = None
            if self._current_concept is not None and self._status != "ready":
                self._finish_current(skipped=True)
            self._status = "finished"
            self._question = None
            return self.snapshot(record_start=record_start)

    @property
    def provider_id(self) -> str | None:
        with self._lock:
            return self._provider_id

    def snapshot(self, *, record_start: int = 0) -> SessionSnapshot:
        """Return state and the record suffix after a caller's retained prefix."""
        with self._lock:
            records = tuple(self._serialize_record(record) for record in self._records[record_start:])
            return SessionSnapshot(
                status=self._status,
                question=self._question,
                concept_id=self._current_concept.id if self._current_concept else None,
                concept_name=self._current_concept.name if self._current_concept else None,
                provider_id=self._provider_id,
                busy=self._busy,
                error=self._error,
                records=records,
            )

    def _reset_question_pool(self, concept_ids: list[str] | tuple[str, ...]) -> None:
        self._remaining_concepts = [self._by_id[cid] for cid in concept_ids]
        self._remaining_indices = {concept.id: index for index, concept in enumerate(self._remaining_concepts)}
        self._initial_questions = [
            (concept, question.prompt)
            for concept in self._remaining_concepts
            for question in concept.questions
        ]

    def _draw_initial_question(self) -> None:
        if self._remaining_concepts:
            self.redraw_question()

    def _ensure_pre_session(self) -> None:
        if self._status not in {"ready", "empty"}:
            raise SessionStateError("A new question can only be drawn before the first submission")
        if self._busy:
            raise SessionBusyError("An evaluation is in progress")

    def _ensure_submittable(self) -> None:
        if self._busy:
            raise SessionBusyError("An evaluation is in progress")
        if self._status not in {"ready", "active", "error"}:
            raise SessionStateError("The interview is not accepting answers")
        if self._status == "error":
            raise SessionStateError("Retry the failed answer or end the interview")
        if self._question is None or self._current_concept is None:
            raise SessionStateError("No question is available")

    def _ensure_active(self) -> None:
        if self._status not in {"active", "error"} or self._current_concept is None:
            raise SessionStateError("There is no active concept")

    def _touch_current_concept(self) -> None:
        concept = self._current_concept
        if concept is not None and concept.id not in self._record_by_id:
            record = ConceptRecord(concept_id=concept.id, concept_name=concept.name)
            self._record_by_id[concept.id] = record
            self._records.append(record)

    def _prepare_evaluation(self) -> tuple[int, ConceptRef, str, str, EvaluationRequest]:
        concept = self._current_concept
        question = self._question
        answer = self._pending_answer
        if concept is None or question is None or answer is None:
            raise SessionStateError("No pending answer is available")
        record = self._record_by_id[concept.id]
        history = [QuestionAnswer(question=turn.question, answer=turn.answer) for turn in record.turns]
        request = EvaluationRequest(history=history, question=question, submitted_answer=answer)
        self._busy = True
        self._status = "evaluating"
        self._generation += 1
        return self._generation, concept, question, answer, request

    def _run_evaluation(
        self,
        prepared: tuple[int, ConceptRef, str, str, EvaluationRequest],
        provider: EvaluationProvider,
        record_start: int,
    ) -> SessionSnapshot:
        generation, concept, question, answer, request = prepared
        try:
            response = provider.evaluate(request)
            if not isinstance(response, EvaluationResponse):
                response = EvaluationResponse.model_validate(response)
        except Exception as error:
            with self._lock:
                if generation != self._generation or self._status == "finished":
                    return self.snapshot(record_start=record_start)
                self._busy = False
                self._status = "error"
                self._error = str(error) or type(error).__name__
                return self.snapshot(record_start=record_start)

        with self._lock:
            if generation != self._generation or self._status == "finished":
                return self.snapshot(record_start=record_start)
            record = self._record_by_id[concept.id]
            record.turns.append(AnswerRecord(question=question, answer=answer, evaluation=response))
            self._pending_answer = None
            self._busy = False
            self._error = None
            if response.follow_up_question and self._follow_up_count < self.MAX_FOLLOW_UPS:
                self._follow_up_count += 1
                self._question = response.follow_up_question
                self._status = "active"
            else:
                self._finish_current(skipped=False)
                self._advance_or_finish()
            return self.snapshot(record_start=record_start)

    def _finish_current(self, *, skipped: bool) -> None:
        concept = self._current_concept
        if concept is None:
            return
        record = self._record_by_id.get(concept.id)
        if record is not None:
            if (
                skipped
                and self._question
                and self._follow_up_count > 0
                and self._pending_answer is None
            ):
                record.unanswered_questions.append(self._question)
            record.skipped = skipped
            for turn in record.turns:
                turn.evaluation_published = turn.evaluation is not None
        # Swap the last candidate into the gap. Uniform choice only needs the remaining members.
        index = self._remaining_indices.pop(concept.id)
        last = self._remaining_concepts.pop()
        if index < len(self._remaining_concepts):
            self._remaining_concepts[index] = last
            self._remaining_indices[last.id] = index

    def _advance_or_finish(self) -> None:
        self._follow_up_count = 0
        if not self._remaining_concepts:
            self._current_concept = None
            self._question = None
            self._status = "finished"
            return
        concept = self._rng.choice(self._remaining_concepts)
        self._current_concept = concept
        self._question = self._rng.choice(concept.questions).prompt
        self._touch_current_concept()
        self._status = "active"

    @staticmethod
    def _serialize_record(record: ConceptRecord) -> dict[str, Any]:
        return {
            "concept_id": record.concept_id,
            "concept_name": record.concept_name,
            "skipped": record.skipped,
            "unanswered_questions": list(record.unanswered_questions),
            "turns": [
                {
                    "question": turn.question,
                    "answer": turn.answer,
                    "evaluation": turn.evaluation.model_dump() if turn.evaluation else None,
                    "evaluation_published": turn.evaluation_published,
                }
                for turn in record.turns
            ],
        }


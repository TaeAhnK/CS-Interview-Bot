"""UI-independent interview question and session state machine."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field, replace
from typing import Any

from bot.evaluation import (
    EvaluationConnectionError,
    EvaluationError,
    EvaluationProvider,
    EvaluationRequest,
    EvaluationResponse,
    InitialQuestionProvider,
    QuestionAnswer,
    QuestionGenerationUnavailableError,
    generate_initial_question,
)
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
    topic_name: str = ""
    unit_name: str = ""


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
    scope_ids: tuple[str, ...] | None = None
    question_version: int = 0
    question_changed: bool = False
    submission_blocked: str | None = None


def _concepts_from_documents(documents: list[KnowledgeDocument] | tuple[KnowledgeDocument, ...]) -> list[ConceptRef]:
    concepts: list[ConceptRef] = []
    for document in documents:
        for unit in document.topic.units:
            for concept in unit.concepts:
                concepts.append(_concept_ref(
                    concept, f"{document.topic.id}/{unit.id}/{concept.id}", document.topic.name, unit.name,
                ))
    return concepts


def _concept_ref(concept: Concept, concept_id: str, topic_name: str, unit_name: str) -> ConceptRef:
    return ConceptRef(
        id=concept_id,
        name=concept.name,
        questions=tuple(QuestionRef(id=q.id, prompt=q.prompt) for q in concept.questions),
        topic_name=topic_name,
        unit_name=unit_name,
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
        self._by_id = {concept.id: concept for concept in concepts}
        if len(self._by_id) != len(concepts):
            raise ValueError("Concept IDs must be unique in the session engine")
        ids = list(self._by_id)
        self._rng = rng or random.Random()
        self._lock = threading.RLock()
        self._reset_question_pool(ids)
        self._question: str | None = None
        self._question_version = 0
        self._current_concept: ConceptRef | None = None
        self._status = "ready" if ids else "empty"
        self._provider_id: str | None = None
        self._provider: EvaluationProvider | None = None
        self._question_provider: EvaluationProvider | InitialQuestionProvider | None = None
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
            if self._status not in {"ready", "empty", "finished"}:
                raise SessionStateError("The interview scope is fixed after the first submission")
            if self._busy:
                raise SessionBusyError("An evaluation is in progress")
            unknown = set(concept_ids) - self._by_id.keys()
            if unknown:
                raise ValueError(f"Unknown concept IDs: {', '.join(sorted(unknown))}")
            if len(set(concept_ids)) != len(concept_ids):
                raise ValueError("Scope contains duplicate concept IDs")
            self._reset_question_pool(concept_ids)
            if self._status == "finished":
                return self.snapshot()
            if not concept_ids and self._current_concept is not None:
                self._question_version += 1
            self._current_concept = None
            self._question = None
            self._status = "ready" if concept_ids else "empty"
            self._draw_initial_question()
            return self.snapshot()

    def restart(self) -> SessionSnapshot:
        """Start a fresh interview with the selected scope after completion."""
        with self._lock:
            if self._status != "finished":
                raise SessionStateError("The interview must finish before restarting")
            self._generation += 1
            self._reset_question_pool([cid for cid in self._scope_ids if cid in self._by_id])
            self._current_concept = None
            self._question = None
            self._provider_id = None
            self._provider = None
            self._question_provider = None
            self._busy = False
            self._error = None
            self._records.clear()
            self._record_by_id.clear()
            self._follow_up_count = 0
            self._pending_answer = None
            self._status = "ready" if self._scope_ids else "empty"
            self._draw_initial_question()
            return self.snapshot(include_scope=True)

    def reload_documents(self, documents: tuple[KnowledgeDocument, ...]) -> SessionSnapshot:
        """Refresh the catalog while keeping an active interview's question pool."""
        concepts = _concepts_from_documents(documents)
        with self._lock:
            selected_all = self._by_id.keys() == set(self._scope_ids)
            self._by_id = {concept.id: concept for concept in concepts}
            if self._status in {"ready", "empty", "finished"}:
                scope = [cid for cid in self._scope_ids if cid in self._by_id]
                if selected_all and self._status != "finished":
                    scope = list(self._by_id)
                current = self._current_concept
                if self._status == "ready" and current and current.id in scope and self._by_id[current.id] == current:
                    self._reset_question_pool(scope)
                else:
                    self.set_scope(scope)
            return self.snapshot(include_scope=True)

    def redraw_question(self) -> str | None:
        """Choose a concept from the pre-session scope, including the current one."""
        with self._lock:
            self._ensure_pre_session()
            if not self._remaining_concepts:
                raise SessionStateError("No concepts in the selected scope")
            self._draw_concept()
            return self._question

    def submit(
        self, answer: str, provider_id: str, provider: EvaluationProvider | None = None, *,
        question_provider: EvaluationProvider | InitialQuestionProvider | None = None,
        record_start: int = 0,
        expected_question_version: int | None = None,
        expected_mode: str | None = None,
    ) -> SessionSnapshot:
        """Start a questionless interview or evaluate an answer using the session's fixed provider."""
        generation = None
        with self._lock:
            if expected_question_version is not None and expected_question_version != self._question_version:
                return replace(
                    self.snapshot(include_scope=True),
                    question_changed=True,
                    submission_blocked="질문이 바뀌었습니다. 새 질문을 확인해 주세요.",
                )
            if expected_mode is not None and expected_mode != (self._provider_id or provider_id):
                return replace(
                    self.snapshot(record_start=record_start),
                    submission_blocked="평가 방식이 바뀌었습니다. 안내를 확인하고 다시 제출해 주세요.",
                )
            self._ensure_submittable()
            if not answer.strip():
                raise ValueError("Answer must not be blank")
            if self._status == "ready":
                if provider is None:
                    raise SessionStateError("No evaluation provider is configured")
                self._provider_id = provider_id
                self._provider = provider
                self._question_provider = question_provider if question_provider is not None else provider
                if self._question is None:
                    generation = self._prepare_question_generation()
                else:
                    self._status = "active"
                    self._touch_current_concept()
            elif provider_id != self._provider_id:
                raise SessionStateError("The evaluation provider is fixed for this interview")
            if generation is None:
                chosen_provider = self._provider
                if chosen_provider is None:
                    raise SessionStateError("No evaluation provider is configured")
                self._pending_answer = answer
                prepared = self._prepare_evaluation()
        if generation is not None:
            return self._run_question_generation(generation, record_start)
        return self._run_evaluation(prepared, chosen_provider, record_start)

    def retry(self, provider: EvaluationProvider | None = None, *, record_start: int = 0) -> SessionSnapshot:
        """Retry the retained answer using the fixed provider identity."""
        with self._lock:
            if self._status == "question_error":
                generation = self._prepare_question_generation()
            else:
                generation = None
                if self._status != "error" or self._pending_answer is None:
                    raise SessionStateError("There is no failed submission to retry")
                if self._busy:
                    raise SessionBusyError("An evaluation is in progress")
                chosen_provider = provider or self._provider
                if chosen_provider is None:
                    raise SessionStateError("No evaluation provider is configured")
                self._provider = chosen_provider
                self._error = None
                self._status = "active"
                prepared = self._prepare_evaluation()
        if generation is not None:
            return self._run_question_generation(generation, record_start)
        return self._run_evaluation(prepared, chosen_provider, record_start)

    def skip_concept(self, *, record_start: int = 0, expected_generation: int | None = None) -> SessionSnapshot:
        """Finish the current concept, mark a visible unanswered follow-up, and advance."""
        with self._lock:
            if expected_generation is not None and (
                expected_generation != self._generation
                or self._status not in {"generating_question", "question_error", "question_unavailable", "active"}
            ):
                return self.snapshot(record_start=record_start)
            if self._busy and self._status != "generating_question":
                raise SessionBusyError("Cannot move while an evaluation is in progress")
            self._ensure_active()
            if self._status == "error":
                raise SessionStateError("Retry the failed answer or end the interview")
            self._finish_current(skipped=True)
            generation = self._advance_or_finish()
            if generation is None:
                return self.snapshot(record_start=record_start)
        return self._run_question_generation(generation, record_start)

    def question_generation(self) -> dict[str, int | str] | None:
        """Expose the in-flight concept and token without serializing interview records."""
        with self._lock:
            if self._status != "generating_question" or self._current_concept is None:
                return None
            return {"token": self._generation, "concept_name": self._current_concept.name}

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
            self._question_version += 1
            return self.snapshot(record_start=record_start)

    @property
    def provider_id(self) -> str | None:
        with self._lock:
            return self._provider_id

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def snapshot(self, *, record_start: int = 0, include_scope: bool = False) -> SessionSnapshot:
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
                scope_ids=self._scope_ids if include_scope else None,
                question_version=self._question_version,
            )

    def _reset_question_pool(self, concept_ids: list[str] | tuple[str, ...]) -> None:
        self._scope_ids = tuple(concept_ids)
        self._remaining_concepts = [self._by_id[cid] for cid in concept_ids]
        self._remaining_indices = {concept.id: index for index, concept in enumerate(self._remaining_concepts)}

    def _draw_initial_question(self) -> None:
        if self._remaining_concepts:
            self._draw_concept()

    def _draw_concept(self) -> None:
        concept = self._rng.choice(self._remaining_concepts)
        self._current_concept = concept
        self._question_version += 1
        self._question = self._rng.choice(concept.questions).prompt if concept.questions else None

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
        if self._current_concept is None or (self._question is None and self._status != "ready"):
            raise SessionStateError("No question is available")

    def _ensure_active(self) -> None:
        if self._status not in {"active", "error", "generating_question", "question_error", "question_unavailable"} or self._current_concept is None:
            raise SessionStateError("There is no active concept")

    def _touch_current_concept(self) -> ConceptRecord | None:
        concept = self._current_concept
        if concept is None:
            return None
        record = self._record_by_id.get(concept.id)
        if record is None:
            record = ConceptRecord(concept_id=concept.id, concept_name=concept.name)
            self._record_by_id[concept.id] = record
            self._records.append(record)
        return record

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
            response = EvaluationResponse.model_validate(provider.evaluate(request))
        except Exception as error:
            with self._lock:
                if generation != self._generation or self._status == "finished":
                    return self.snapshot(record_start=record_start)
                self._busy = False
                self._status = "error"
                fallback = (
                    "평가 제공자에 연결하지 못했습니다."
                    if isinstance(error, EvaluationConnectionError)
                    else "평가 결과를 처리하지 못했습니다."
                )
                self._error = (str(error) if isinstance(error, EvaluationError) else "") or fallback
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
                self._question_version += 1
                self._question = response.follow_up_question
                self._status = "active"
                return self.snapshot(record_start=record_start)
            self._finish_current(skipped=False)
            generation = self._advance_or_finish()
            if generation is None:
                return self.snapshot(record_start=record_start)
        return self._run_question_generation(generation, record_start)

    def _prepare_question_generation(self) -> int:
        self._generation += 1
        self._status = "generating_question"
        self._busy = True
        self._error = None
        return self._generation

    def _run_question_generation(self, generation: int, record_start: int) -> SessionSnapshot:
        with self._lock:
            concept = self._current_concept
            provider = self._question_provider
            if generation != self._generation or concept is None:
                return self.snapshot(record_start=record_start)
        try:
            question = generate_initial_question(provider, concept.topic_name, concept.unit_name, concept.name)
        except Exception as error:
            with self._lock:
                if generation != self._generation:
                    return self.snapshot(record_start=record_start)
                self._busy = False
                self._status = "question_unavailable" if isinstance(error, QuestionGenerationUnavailableError) else "question_error"
                self._error = (str(error) if isinstance(error, EvaluationError) else "") or "첫 질문을 생성하지 못했습니다."
                return self.snapshot(record_start=record_start)
        with self._lock:
            if generation != self._generation:
                return self.snapshot(record_start=record_start)
            self._busy = False
            self._question_version += 1
            self._question = question
            self._status = "active"
            self._touch_current_concept()
            return self.snapshot(record_start=record_start)

    def _finish_current(self, *, skipped: bool) -> None:
        concept = self._current_concept
        if concept is None:
            return
        record = self._touch_current_concept()
        if record is not None:
            if (
                skipped
                and self._question
                and (self._follow_up_count > 0 or not concept.questions)
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

    def _advance_or_finish(self) -> int | None:
        self._follow_up_count = 0
        self._generation += 1
        self._busy = False
        self._error = None
        if not self._remaining_concepts:
            self._current_concept = None
            self._question = None
            self._status = "finished"
            self._question_version += 1
            return None
        self._draw_concept()
        if self._question is None:
            return self._prepare_question_generation()
        self._touch_current_concept()
        self._status = "active"
        return None

    @staticmethod
    def _serialize_record(record: ConceptRecord) -> dict[str, Any]:
        turns = []
        for turn in record.turns:
            serialized: dict[str, Any] = {"question": turn.question, "answer": turn.answer}
            if turn.evaluation_published and turn.evaluation is not None:
                serialized["evaluation"] = turn.evaluation.model_dump(exclude={"follow_up_question"})
            turns.append(serialized)
        return {
            "concept_id": record.concept_id,
            "concept_name": record.concept_name,
            "skipped": record.skipped,
            "unanswered_questions": list(record.unanswered_questions),
            "turns": turns,
        }


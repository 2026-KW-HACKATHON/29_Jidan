"""Domain-oriented AI/STT operations on top of a pluggable model backend.

`AiProvider` implements every operation once: it builds the data message, asks the backend for
structured output, parses it with the strict schema's pydantic model, re-validates it against
the request (`app.ai.validation`) and returns a typed result from `app.ai.contracts`. Backends
(`OpenAiProvider`, `FakeAiProvider` in `app.ai.fake`) only implement `_complete` (raw JSON text
candidates) and `_transcribe` (raw text), so the fake exercises exactly the production parsing
and validation path.

All methods are blocking and must be called outside a DB transaction (from the task runner).
Failures are `AiError`s; nothing else escapes (provider exceptions are classified and chained
`from None`, so their messages cannot reach logs or responses).
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.ai.contracts import (
    CallMeta,
    DraftComposition,
    DraftRequest,
    GeneratedQuestion,
    ImageInput,
    IntentSummary,
    IntentSummaryRequest,
    QaAnswer,
    QaRequest,
    QuestionRequest,
    StructureRevision,
    StructureRevisionRequest,
    StructureSnapshot,
    SufficiencyJudgement,
    SufficiencyRequest,
    Transcript,
    TranscriptionRequest,
    clean_text,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.prompts import INSTRUCTIONS, PROMPT_VERSION, data_message
from app.ai.schemas import (
    OUTPUTS,
    RawDraft,
    RawJudgement,
    RawQa,
    RawQuestion,
    RawRevision,
    RawSummary,
)
from app.ai.validation import (
    build_citations,
    check_revision_scope,
    dangling_shift_references,
    invalid,
    known_ids,
    materialize_structure,
    same_content,
)

logger = logging.getLogger("jidan.ai")


@dataclass(frozen=True)
class RawTranscript:
    """What a backend's `_transcribe` may return instead of plain text.

    `speech_detected`: False when the backend itself reports that the audio holds no speech
    (no sound at all, or the model found no language it could recognise); the text, which a
    model can invent from noise, is then not used. None means the backend gave no such signal.
    """

    text: str
    speech_detected: bool | None = None


def _structure_payload(snapshot: StructureSnapshot) -> dict[str, Any]:
    return snapshot.model_dump(mode="json")


class AiProvider(ABC):
    """Every AI/STT operation the manual and Q&A features use. See module docstring."""

    provider_name: str = "abstract"
    model: str = ""
    transcribe_model: str = ""

    @property
    def config_version(self) -> str:
        return f"{self.provider_name}:{self.model}:{PROMPT_VERSION}"

    @property
    def max_call_seconds(self) -> float:
        """Upper bound of one operation's wall time (its timeout); 0 when nothing is called.
        The task runner checks leases against it (`app.tasks.validate_task_leases`)."""
        return 0.0

    def meta(self, *, transcription: bool = False) -> CallMeta:
        model = self.transcribe_model if transcription else self.model
        return CallMeta(
            provider=self.provider_name, model=model,
            config_version=f"{self.provider_name}:{model}:{PROMPT_VERSION}",
        )

    # --- backend hooks ----------------------------------------------------------------------

    @abstractmethod
    def _complete(
        self, operation: str, instructions: str, message: str, images: Sequence[ImageInput],
    ) -> list[str]:
        """Raw JSON text candidates for `operation` (several when a backend returns several
        message items); raise AiError on failure."""

    @abstractmethod
    def _transcribe(self, request: TranscriptionRequest) -> "str | RawTranscript":
        """Raw recognized text (or a RawTranscript with the backend's speech signal); raise
        AiError on failure."""

    # --- shared plumbing --------------------------------------------------------------------

    def _structured(self, operation: str, payload: dict[str, Any], images: Sequence[ImageInput] = ()):
        _name, _schema, parser = OUTPUTS[operation]
        started = time.monotonic()
        outcome = "ok"
        try:
            candidates = self._complete(operation, INSTRUCTIONS[operation], data_message(payload), images)
            for candidate in candidates:
                try:
                    return parser.model_validate(json.loads(candidate))
                except (ValueError, ValidationError):
                    continue  # malformed or schema-violating item: try the next one
            raise invalid("unparseable_output")
        except AiError as error:
            outcome = error.code.value
            raise
        finally:
            # Operation name, model and outcome code only: never prompts, answers or keys.
            logger.info(
                "ai call op=%s model=%s outcome=%s ms=%d", operation, self.model, outcome,
                int((time.monotonic() - started) * 1000),
            )

    # --- operations -------------------------------------------------------------------------

    def judge_sufficiency(self, request: SufficiencyRequest) -> SufficiencyJudgement:
        # The probe count stays with the server (it ends an intent at depth 5): the model judges
        # the dialogue alone, so "asked often enough" can never read as "known".
        payload = request.model_dump(mode="json", exclude={"depth"})
        raw: RawJudgement = self._structured("judge_sufficiency", payload)
        aspects = tuple(dict.fromkeys(a for a in (clean_text(x) for x in raw.missing_aspects) if a))
        if (raw.probability >= 0.5) != raw.sufficient:
            raise invalid("inconsistent_sufficiency_probability")
        if raw.sufficient and aspects:
            raise invalid("sufficient_with_missing_aspects")
        if not raw.sufficient and not aspects:
            raise invalid("insufficient_without_aspects")
        return SufficiencyJudgement(
            sufficient=raw.sufficient, probability=raw.probability,
            missing_aspects=() if raw.sufficient else aspects, meta=self.meta(),
        )

    def generate_question(self, request: QuestionRequest) -> GeneratedQuestion:
        if request.kind == "PROBE" and not request.missing_aspects:
            raise ValueError("a PROBE question needs the missing aspects from the judgement")
        raw: RawQuestion = self._structured("generate_question", request.model_dump(mode="json"))
        text = clean_text(raw.question)
        if not text:
            raise invalid("blank_question")
        return GeneratedQuestion(text=text, meta=self.meta())

    def summarize_intent(self, request: IntentSummaryRequest) -> IntentSummary:
        raw: RawSummary = self._structured("summarize_intent", request.model_dump(mode="json"))
        structure = materialize_structure(
            raw.structure,
            known_shift_ids=[shift.id for shift in request.available_shifts],
            external_shifts=request.available_shifts,
        )
        summary = clean_text(raw.summary)
        if not summary:
            raise invalid("blank_summary")
        return IntentSummary(summary=summary, structure=structure, meta=self.meta())

    def revise_structure(self, request: StructureRevisionRequest) -> StructureRevision:
        target = request.target
        if (target.kind == "MANUAL") != (target.target_id is None):
            raise ValueError("MANUAL targets have no id; SHIFT/SECTION targets need one")
        current = request.current
        ids = known_ids(current)
        if target.kind == "SHIFT" and target.target_id not in ids["shift"]:
            raise ValueError("target shift is not part of the current content")
        if target.kind == "SECTION" and target.target_id not in ids["section"]:
            raise ValueError("target section is not part of the current content")
        raw: RawRevision = self._structured("revise_structure", request.model_dump(mode="json"))
        meta = self.meta()
        if raw.outcome in ("CLARIFICATION_REQUIRED", "REFERENCE_CONFLICT", "NO_CHANGE"):
            return StructureRevision(outcome=raw.outcome, meta=meta)
        external_ids = {shift.id for shift in request.external_shifts}
        try:
            structure = materialize_structure(
                raw.structure,
                known_shift_ids=ids["shift"] | external_ids,
                known_section_ids=ids["section"], known_step_ids=ids["step"],
                external_shifts=request.external_shifts,
                previous_missing=current.missing_information,
                require_manual_level=request.require_manual_level,
            )
        except AiError as error:
            if error.detail == "unresolved_shift_ref":
                # A section still points at a shift the correction removed: the instruction
                # broke a reference. Report it instead of guessing a replacement.
                return StructureRevision(outcome="REFERENCE_CONFLICT", meta=meta)
            raise
        check_revision_scope(current, structure, target.kind, target.target_id)
        if dangling_shift_references(structure, external_ids):
            return StructureRevision(outcome="REFERENCE_CONFLICT", meta=meta)
        summary = None
        if request.summary is not None:
            summary = clean_text(raw.summary or "")
            if not summary:
                raise invalid("revision_without_summary")
        if same_content(current, structure) and summary in (None, request.summary):
            return StructureRevision(outcome="NO_CHANGE", meta=meta)
        return StructureRevision(outcome="APPLIED", structure=structure, summary=summary, meta=meta)

    def compose_draft(self, request: DraftRequest) -> DraftComposition:
        raw: RawDraft = self._structured("compose_draft", request.model_dump(mode="json"))
        shift_ids: set[str] = set()
        section_ids: set[str] = set()
        step_ids: set[str] = set()
        previous = []
        for review in request.reviews:
            ids = known_ids(review.structure)
            shift_ids |= ids["shift"]
            section_ids |= ids["section"]
            step_ids |= ids["step"]
            previous.extend(review.structure.missing_information)
        structure = materialize_structure(
            raw.structure, known_shift_ids=shift_ids, known_section_ids=section_ids,
            known_step_ids=step_ids, previous_missing=previous, require_manual_level=True,
        )
        # Photos hang on section IDs, so every reviewed shift and section must survive.
        if not shift_ids <= {s.id for s in structure.shifts} or not section_ids <= {
            s.id for s in structure.sections
        }:
            raise invalid("draft_dropped_reviewed_item")
        return DraftComposition(structure=structure, meta=self.meta())

    def answer_question(self, request: QaRequest) -> QaAnswer:
        payload = request.model_dump(mode="json", exclude={"images"})
        payload["image_count"] = len(request.images)
        raw: RawQa = self._structured("answer_question", payload, request.images)
        text = clean_text(raw.answer)
        if not text:
            raise invalid("blank_answer")
        citations = build_citations(raw.citations, request.manual)
        if raw.outcome == "ANSWERED" and not citations:
            raise invalid("answered_without_citation")
        if raw.outcome == "NEEDS_OWNER":
            citations = ()
        return QaAnswer(outcome=raw.outcome, text=text, citations=citations, meta=self.meta())

    def transcribe(self, request: TranscriptionRequest) -> Transcript:
        started = time.monotonic()
        outcome = "ok"
        try:
            raw = self._transcribe(request)
            if isinstance(raw, RawTranscript):
                if raw.speech_detected is False:
                    raise AiError(AiErrorCode.EMPTY_TRANSCRIPT, detail="no_speech")
                raw = raw.text
            text = clean_text(raw or "")
            if not text:
                raise AiError(AiErrorCode.EMPTY_TRANSCRIPT)
            if len(text) > 10000:
                raise AiError(AiErrorCode.INVALID_OUTPUT, detail="transcript_too_long")
            return Transcript(text=text, meta=self.meta(transcription=True))
        except AiError as error:
            outcome = error.code.value
            raise
        finally:
            logger.info(
                "stt call model=%s outcome=%s ms=%d", self.transcribe_model, outcome,
                int((time.monotonic() - started) * 1000),
            )


class FallbackAiProvider(AiProvider):
    """Try `primary`, then `fallback` when the primary fails with a retryable error.

    The result's `meta` names the backend that produced it, so evaluation rows record the
    fallback's provider/config. A non-retryable primary failure (e.g. input rejected) is not
    retried on the fallback, because the same input would be rejected again.
    """

    def __init__(self, primary: AiProvider, fallback: AiProvider):
        self.primary = primary
        self.fallback = fallback
        self.provider_name = primary.provider_name
        self.model = primary.model
        self.transcribe_model = primary.transcribe_model

    @property
    def max_call_seconds(self) -> float:
        # A retryable primary failure (e.g. its timeout) is followed by a full fallback call.
        return self.primary.max_call_seconds + self.fallback.max_call_seconds

    def _complete(self, *args, **kwargs):  # pragma: no cover - operations are delegated
        raise NotImplementedError

    def _transcribe(self, request):  # pragma: no cover - operations are delegated
        raise NotImplementedError

    def _delegate(self, name: str, request):
        try:
            return getattr(self.primary, name)(request)
        except AiError as error:
            if not error.retryable:
                raise
            logger.info("ai fallback op=%s after=%s", name, error.code.value)
            return getattr(self.fallback, name)(request)

    def judge_sufficiency(self, request):
        return self._delegate("judge_sufficiency", request)

    def generate_question(self, request):
        return self._delegate("generate_question", request)

    def summarize_intent(self, request):
        return self._delegate("summarize_intent", request)

    def revise_structure(self, request):
        return self._delegate("revise_structure", request)

    def compose_draft(self, request):
        return self._delegate("compose_draft", request)

    def answer_question(self, request):
        return self._delegate("answer_question", request)

    def transcribe(self, request):
        return self._delegate("transcribe", request)


class UnconfiguredAiProvider(AiProvider):
    """Used when no provider is configured: every call fails as NOT_CONFIGURED (not retryable)."""

    provider_name = "unconfigured"

    def _complete(self, *_args, **_kwargs):
        raise AiError(AiErrorCode.NOT_CONFIGURED)

    def _transcribe(self, _request):
        raise AiError(AiErrorCode.NOT_CONFIGURED)

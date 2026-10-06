"""Domain-level inputs and results of every AI/STT operation.

Requests are built by the interview, draft and Q&A services from stored rows; results are what
`app.ai` returns after the provider output passed structured-output parsing *and* server-side
re-validation (`app.ai.validation`). Callers never see raw provider output.

Identifiers. The model never invents UUIDs. Inputs carry real IDs; outputs either reuse an ID
that was present in the input or use a placeholder `new-<n>`. `app.ai.validation` checks both
and replaces placeholders with fresh UUIDs, so a result's `StructureSnapshot` always holds real,
unique UUIDs whose references resolve.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

STAGES = ("WORK_STRUCTURE", "COMMON_TASKS", "SHIFT_TASKS", "COMPLEMENTS")
SECTION_CATEGORIES = ("COMMON_TASK", "SHIFT_TASK", "RULE", "EQUIPMENT")
MISSING_TARGETS = ("MANUAL", "SHIFT", "SECTION")
MISSING_FIELDS = ("shifts", "sections", "startTime", "endTime", "endsNextDay", "steps")
REVISION_OUTCOMES = ("APPLIED", "NO_CHANGE", "CLARIFICATION_REQUIRED", "REFERENCE_CONFLICT")
QA_OUTCOMES = ("ANSWERED", "NEEDS_OWNER")

Stage = Literal["WORK_STRUCTURE", "COMMON_TASKS", "SHIFT_TASKS", "COMPLEMENTS"]
SectionCategory = Literal["COMMON_TASK", "SHIFT_TASK", "RULE", "EQUIPMENT"]
MissingTarget = Literal["MANUAL", "SHIFT", "SECTION"]
MissingField = Literal["shifts", "sections", "startTime", "endTime", "endsNextDay", "steps"]
RevisionOutcome = Literal["APPLIED", "NO_CHANGE", "CLARIFICATION_REQUIRED", "REFERENCE_CONFLICT"]
QaOutcome = Literal["ANSWERED", "NEEDS_OWNER"]

# Limits mirror openapi.yaml (ManualContent, ManualInterviewReview, QAAnswer, ...).
MAX_SHIFTS = 20
MAX_SECTIONS = 200
MAX_STEPS = 100
MAX_MISSING = 200
MAX_CITATIONS = 10
MAX_QA_IMAGES = 3
MAX_DIALOGUE_TURNS = 40
MAX_CONTEXT_NOTES = 50
MAX_ASPECTS = 5


def clean_text(value: str) -> str:
    """Drop control characters (keeping newlines and tabs) and surrounding whitespace."""
    return "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32 and ord(ch) != 127).strip()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CallMeta(_Model):
    """Which provider configuration produced a result (stored as provider/config_version)."""

    provider: str
    model: str
    config_version: str


# --- structures with real IDs (inputs and validated outputs) -----------------------------------


class ShiftItem(_Model):
    id: str
    name: str = Field(min_length=1, max_length=50)
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    ends_next_day: bool | None = None


class StepItem(_Model):
    id: str
    instruction: str = Field(min_length=1, max_length=3000)
    checklist_item: bool = False


class SectionItem(_Model):
    id: str
    category: SectionCategory
    shift_id: str | None = None
    title: str = Field(min_length=1, max_length=100)
    steps: tuple[StepItem, ...] = Field(default=(), max_length=MAX_STEPS)


class MissingItem(_Model):
    id: str
    target: MissingTarget
    target_id: str | None
    field: MissingField
    description: str = Field(min_length=1, max_length=300)


class StructureSnapshot(_Model):
    """Shifts, sections (with steps) and missing-information entries of a review or manual.

    Photos are not part of it: they stay attached to section IDs and are re-joined by the
    caller, so the model cannot drop, invent or move a photo.
    """

    shifts: tuple[ShiftItem, ...] = Field(default=(), max_length=MAX_SHIFTS)
    sections: tuple[SectionItem, ...] = Field(default=(), max_length=MAX_SECTIONS)
    missing_information: tuple[MissingItem, ...] = Field(default=(), max_length=MAX_MISSING)


# --- shared request parts ---------------------------------------------------------------------


class StoreContext(_Model):
    """Who the interview is for. Used to word questions naturally (e.g. "카페에서는"), never as a
    source of facts: the model must not fill in what such stores usually do."""

    name: str = Field(min_length=1, max_length=100)
    industry: str = Field(min_length=1, max_length=50)  # Korean label, e.g. 카페


class IntentBrief(_Model):
    key: str = Field(min_length=1, max_length=100)
    stage: Stage
    base_question: str = Field(min_length=1, max_length=2000)
    coverage_criteria: str = Field(min_length=1, max_length=4000)


class DialogueTurn(_Model):
    """One question and the owner's answer to it, in order (the answer is stored text)."""

    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=10000)
    depth: int = Field(ge=0, le=5)


class ContextNote(_Model):
    """A summary of another intent of the same interview (never another store's data)."""

    intent_key: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=10000)


# --- requests ---------------------------------------------------------------------------------


class SufficiencyRequest(_Model):
    """Jev: is the accumulated dialogue of the current intent enough to write the manual part?"""

    intent: IntentBrief
    dialogue: tuple[DialogueTurn, ...] = Field(min_length=1, max_length=MAX_DIALOGUE_TURNS)
    depth: int = Field(ge=0, le=5)
    context: tuple[ContextNote, ...] = Field(default=(), max_length=MAX_CONTEXT_NOTES)
    store: StoreContext | None = None


class QuestionRequest(_Model):
    """Word the next question: BASE adapts the intent's base question, PROBE asks one follow-up."""

    kind: Literal["BASE", "PROBE"]
    intent: IntentBrief
    depth: int = Field(ge=0, le=5)
    dialogue: tuple[DialogueTurn, ...] = Field(default=(), max_length=MAX_DIALOGUE_TURNS)
    missing_aspects: tuple[str, ...] = Field(default=(), max_length=MAX_ASPECTS)
    context: tuple[ContextNote, ...] = Field(default=(), max_length=MAX_CONTEXT_NOTES)
    store: StoreContext | None = None


class IntentSummaryRequest(_Model):
    """Summarize a finished intent into structure. `available_shifts` are read-only shifts from
    other reviews of the session that a SHIFT_TASK section may reference."""

    intent: IntentBrief
    dialogue: tuple[DialogueTurn, ...] = Field(min_length=1, max_length=MAX_DIALOGUE_TURNS)
    needs_detail: bool
    available_shifts: tuple[ShiftItem, ...] = Field(default=(), max_length=MAX_SHIFTS)
    store: StoreContext | None = None


class RevisionTarget(_Model):
    kind: Literal["MANUAL", "SHIFT", "SECTION"]
    target_id: str | None = None


class StructureRevisionRequest(_Model):
    """Apply a spoken/typed correction to a review or a draft.

    `current` is the content being corrected; `summary` is the review summary (None for a
    draft). `external_shifts` are read-only shifts that sections may reference. Only the target
    may change; other existing items must come back unchanged (enforced by validation).
    """

    current: StructureSnapshot
    summary: str | None = Field(default=None, max_length=10000)
    target: RevisionTarget = RevisionTarget(kind="MANUAL")
    instruction: str = Field(min_length=1, max_length=10000)
    external_shifts: tuple[ShiftItem, ...] = Field(default=(), max_length=MAX_SHIFTS)
    require_manual_level: bool = False  # True for drafts (ManualContent rules)
    store: StoreContext | None = None


class ReviewForDraft(_Model):
    intent_key: str = Field(min_length=1, max_length=100)
    stage: Stage
    summary: str = Field(min_length=1, max_length=10000)
    needs_detail: bool
    structure: StructureSnapshot


class DraftRequest(_Model):
    """Compose the manual draft from every review (fixed generation snapshot)."""

    reviews: tuple[ReviewForDraft, ...] = Field(min_length=1, max_length=50)
    store: StoreContext | None = None


class ImageInput(_Model):
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: bytes = Field(repr=False)


class QaRequest(_Model):
    """A worker's question against the published manual of the question's fixed version."""

    question: str = Field(min_length=1, max_length=2000)
    manual: StructureSnapshot
    images: tuple[ImageInput, ...] = Field(default=(), max_length=MAX_QA_IMAGES)


class TranscriptionRequest(_Model):
    audio: bytes = Field(repr=False, min_length=1)
    mime_type: Literal["audio/mpeg", "audio/mp4", "audio/webm", "audio/wav"]
    language: str = "ko"
    keywords: tuple[str, ...] = Field(default=(), max_length=20)


# --- results ----------------------------------------------------------------------------------


class SufficiencyJudgement(_Model):
    sufficient: bool
    probability: float = Field(ge=0.0, le=1.0)  # confidence that the information is sufficient
    missing_aspects: tuple[str, ...] = Field(default=(), max_length=MAX_ASPECTS)
    meta: CallMeta

    @property
    def needs_follow_up(self) -> bool:
        return not self.sufficient


class GeneratedQuestion(_Model):
    text: str = Field(min_length=1, max_length=2000)
    meta: CallMeta


class IntentSummary(_Model):
    summary: str = Field(min_length=1, max_length=10000)
    structure: StructureSnapshot
    meta: CallMeta


class StructureRevision(_Model):
    """APPLIED carries the new structure (and summary for reviews); NO_CHANGE means the content
    is identical to the input; the other outcomes carry nothing and change nothing."""

    outcome: RevisionOutcome
    structure: StructureSnapshot | None = None
    summary: str | None = Field(default=None, max_length=10000)
    meta: CallMeta


class DraftComposition(_Model):
    structure: StructureSnapshot
    meta: CallMeta


class Citation(_Model):
    section_id: str
    section_title: str = Field(min_length=1, max_length=100)
    step_ids: tuple[str, ...] = ()
    excerpt: str = Field(min_length=1, max_length=1000)  # built by the server from the steps


class QaAnswer(_Model):
    outcome: QaOutcome
    text: str = Field(min_length=1, max_length=3000)
    citations: tuple[Citation, ...] = Field(default=(), max_length=MAX_CITATIONS)
    meta: CallMeta


class Transcript(_Model):
    text: str = Field(min_length=1, max_length=10000)
    meta: CallMeta

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank transcript")
        return value

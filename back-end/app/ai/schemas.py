"""Raw structured-output shapes: the strict JSON Schema sent to the model and the pydantic model
that parses what comes back.

The JSON Schemas use only the keywords OpenAI documents for strict Structured Outputs (`type`
including `[T, "null"]`, `properties`, `required` listing every property,
`additionalProperties: false`, `items`, `enum`, `description`). Length limits are not
expressible there, so the pydantic models below enforce them on the way back in; failing that
re-validation is an `invalid_output` (retryable) error, never a silently truncated value.
`tests/test_ai_schemas.py` keeps the two in sync.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.contracts import (
    MAX_ASPECTS,
    MAX_CITATIONS,
    MAX_EVIDENCE_IDS,
    MAX_MISSING,
    MAX_SECTIONS,
    MAX_SHIFTS,
    MAX_STEPS,
    MISSING_FIELDS,
    MISSING_TARGETS,
    QA_OUTCOMES,
    REVISION_OUTCOMES,
    SECTION_CATEGORIES,
    MissingField,
    MissingTarget,
    QaOutcome,
    RevisionOutcome,
    SectionCategory,
)

# --- tiny strict-schema builder -----------------------------------------------------------------


def _string(description: str, *, nullable: bool = False) -> dict[str, Any]:
    return {"type": ["string", "null"] if nullable else "string", "description": description}


def _enum(values: tuple[str, ...], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values), "description": description}


def _array(items: dict[str, Any], description: str) -> dict[str, Any]:
    return {"type": "array", "items": items, "description": description}


def _object(properties: dict[str, dict[str, Any]], description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object", "properties": properties, "required": list(properties),
        "additionalProperties": False,
    }
    if description:
        schema["description"] = description
    return schema


_REF = "기존 항목이면 입력에 있던 id를 그대로, 새 항목이면 new-1, new-2 같은 임시 참조"
_EVIDENCE = ("근거가 된 evidence 조각의 id 목록(입력 evidence에 있는 값만, 최대 20개). "
             "evidence가 비어 있거나 입력의 기존 항목을 내용 그대로 유지했으면 빈 배열")


def _structure_schema(evidence: str) -> dict[str, Any]:
    return _object({
        "shifts": _array(_object({
            "ref": _string(_REF),
            "name": _string("근무조 이름 (50자 이내)"),
            "start_time": _string("HH:MM 24시간제. 답변에 근거가 없으면 null", nullable=True),
            "end_time": _string("HH:MM 24시간제. 답변에 근거가 없으면 null", nullable=True),
            "ends_next_day": {"type": ["boolean", "null"], "description": "종료가 다음 날이면 true. 모르면 null"},
            "evidence_ids": _array(_string("evidence id"), "시간 값(start_time/end_time/ends_next_day)의 " + evidence),
        }), "근무조 목록 (최대 20개)"),
        "sections": _array(_object({
            "ref": _string(_REF),
            "category": _enum(SECTION_CATEGORIES, "업무 분류"),
            "shift_ref": _string("SHIFT_TASK일 때만 근무조 ref, 나머지는 null", nullable=True),
            "title": _string("업무·규정·설비 제목 (100자 이내)"),
            "steps": _array(_object({
                "ref": _string(_REF),
                "instruction": _string("근무자가 따라 할 지시문 한 단계"),
                "checklist_item": {"type": "boolean", "description": "체크리스트로 확인할 만한 단계인지"},
                "evidence_ids": _array(_string("evidence id"), "이 단계 내용의 " + evidence),
            }), "순서대로의 단계. 근거가 없으면 빈 배열과 missing_information 항목"),
        }), "업무 섹션 목록 (최대 200개)"),
        "missing_information": _array(_object({
            "target": _enum(MISSING_TARGETS, "미확정 대상 종류"),
            "target_ref": _string("SHIFT/SECTION이면 대상 ref, MANUAL이면 null", nullable=True),
            "field": _enum(MISSING_FIELDS, "미확정 필드"),
            "description": _string("점주·근무자에게 보여 줄 미확정 설명 (300자 이내)"),
        }), "답변으로 확정되지 않은 값의 목록"),
    }, "매뉴얼 구조")


STRUCTURE_SCHEMA = _structure_schema(_EVIDENCE)

JUDGE_SCHEMA = _object({
    "sufficient": {"type": "boolean", "description": "이 인텐트의 매뉴얼을 쓰기에 정보가 충분한가"},
    "probability": {"type": "number", "description": "정보가 충분할 확률 0~1; sufficient=true는 0.5 이상, false는 0.5 미만"},
    "missing_aspects": _array(
        _string("부족한 정보 한 가지: 업무 하나의 측면 하나를 '<대상>의 <측면>' 형식으로 (200자 이내)"),
        "부족한 측면 (최대 5개, 중요한 것부터, 한 항목에 여러 측면을 묶지 않음, 충분하면 빈 배열)",
    ),
    "not_applicable_probability": {
        "type": ["number", "null"],
        "description": "not_applicable_rule이 있으면 그 명제가 참일 확률 0~1, 없으면 null",
    },
    "not_applicable_confirmed_probability": {
        "type": ["number", "null"],
        "description": "not_applicable_confirmation_rule이 있으면 재확인 명제가 참일 확률 0~1, 없으면 null",
    },
})

QUESTION_ITEM_SCHEMA = _object({
    "id": _string("입력의 기존 item ID 또는 새 항목이면 null", nullable=True),
    "label": _string("항목 이름 (200자 이내)"),
    "description": _string("보조 설명 (1000자 이내)", nullable=True),
    "status": {"type": ["string", "null"], "enum": ["PENDING", "CURRENT", "COMPLETED", "NEEDS_DETAIL", None]},
})
QUESTION_CARD_SCHEMA = _object({
    "type": _enum(("LIST", "PROGRESS_CHECKLIST"), "질문 안내 유형"),
    "title": _string("카드 제목 (200자 이내)"),
    "items": _array(QUESTION_ITEM_SCHEMA, "1~50개 항목"),
    "footer": _string("하단 안내 (1000자 이내)", nullable=True),
})
QUESTION_SCHEMA = _object({
    "question": _string("점주에게 할 질문 한 개 (2000자 이내)"),
    "guidance": _string("답변 안내 (2000자 이내)", nullable=True),
    "guidanceCards": _array(QUESTION_CARD_SCHEMA, "질문 안내 카드 최대 5개, 필요 없으면 빈 배열"),
})
PHOTO_SUGGESTIONS_SCHEMA = _object({
    "suggestions": _array(_object({
        "sectionId": _string("저장된 요약에 있는 section id"),
        "title": _string("카드 제목"),
        "items": _array(_object({
            "label": _string("사진 대상"),
            "description": _string("촬영 안내", nullable=True),
        }), "사진 추천 목록"),
        "footer": _string("하단 안내", nullable=True),
    }), "도움이 되는 사진이 없으면 빈 배열"),
})

SUMMARY_SCHEMA = _object({
    "summary": _string("점주가 확인할 이해 요약 (해요체)"),
    "structure": STRUCTURE_SCHEMA,
})

REVISION_SCHEMA = _object({
    "outcome": _enum(REVISION_OUTCOMES, "정정 결과"),
    "summary": _string("APPLIED이고 요약이 있는 대상이면 새 요약, 아니면 null", nullable=True),
    "structure": STRUCTURE_SCHEMA,
})

DRAFT_SCHEMA = _object({"structure": STRUCTURE_SCHEMA})

_MEDIA_EVIDENCE = ("근거 id 목록: 입력 evidence 조각의 id 또는 media 항목의 id(사진·영상 프레임·영상 음성 전사), "
                   "입력에 있는 값만, 최대 20개. 입력의 기존 항목을 내용 그대로 유지했으면 빈 배열")
MEDIA_WRITING_OUTCOMES = ("APPLIED", "NO_CHANGE")

MEDIA_WRITING_SCHEMA = _object({
    "outcome": _enum(MEDIA_WRITING_OUTCOMES, "사진·영상으로 대상 섹션을 작성·보강했으면 APPLIED, 바꿀 것이 없으면 NO_CHANGE"),
    "structure": _structure_schema(_MEDIA_EVIDENCE),
    "removed_steps": _array(_object({
        "ref": _string("대상 섹션에서 뺀 기존 단계의 id"),
        "evidence_ids": _array(_string("evidence id 또는 media id"),
                               "그 단계를 빼야 하는 근거 id(입력에 있는 값만, 1개 이상)"),
    }), "근거가 있어 대상 섹션에서 뺀 기존 단계 (대부분 빈 배열)"),
})

QA_SCHEMA = _object({
    "outcome": _enum(QA_OUTCOMES, "ANSWERED는 매뉴얼 근거가 있을 때만"),
    "answer": _string("근무자에게 줄 답변 (3000자 이내, 해요체)"),
    "citations": _array(_object({
        "section_id": _string("근거 섹션 id (입력에 있는 값)"),
        "step_ids": _array(_string("근거 단계 id"), "근거가 된 단계 id 목록"),
    }), "근거 (ANSWERED면 1~10개, NEEDS_OWNER면 빈 배열)"),
})


# --- pydantic parsers (lengths enforced here) --------------------------------------------------


class _Raw(BaseModel):
    # strict: "yes" is not a boolean and "1" is not a number, whatever lax parsing would allow.
    model_config = ConfigDict(extra="forbid", strict=True)


class RawShift(_Raw):
    ref: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=50)
    start_time: str | None
    end_time: str | None
    ends_next_day: bool | None
    # Required by the strict schema; defaults only so outputs captured before grounding (eval
    # fixtures, scripted test outputs) still parse. Missing = no citation.
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=MAX_EVIDENCE_IDS)


class RawStep(_Raw):
    ref: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=3000)
    checklist_item: bool
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=MAX_EVIDENCE_IDS)


class RawSection(_Raw):
    ref: str = Field(min_length=1, max_length=64)
    category: SectionCategory
    shift_ref: str | None
    title: str = Field(min_length=1, max_length=100)
    steps: list[RawStep] = Field(max_length=MAX_STEPS)


class RawMissing(_Raw):
    target: MissingTarget
    target_ref: str | None
    field: MissingField
    description: str = Field(min_length=1, max_length=300)


class RawStructure(_Raw):
    shifts: list[RawShift] = Field(max_length=MAX_SHIFTS)
    sections: list[RawSection] = Field(max_length=MAX_SECTIONS)
    missing_information: list[RawMissing] = Field(max_length=MAX_MISSING)


class RawJudgement(_Raw):
    sufficient: bool
    probability: float = Field(ge=0.0, le=1.0)
    missing_aspects: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        max_length=MAX_ASPECTS,
    )
    # Older captures remain replayable for intents without a confirmation protocol. The
    # provider requires both probabilities for confirmation intents, including old captures.
    not_applicable_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    not_applicable_confirmed_probability: float | None = Field(default=None, ge=0.0, le=1.0)


class RawExample(_Raw):
    label: str
    description: str | None


class RawQuestionItem(_Raw):
    id: str | None
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(min_length=1, max_length=1000)
    status: Literal["PENDING", "CURRENT", "COMPLETED", "NEEDS_DETAIL"] | None


class RawQuestionCard(_Raw):
    type: Literal["LIST", "PROGRESS_CHECKLIST"]
    title: str = Field(min_length=1, max_length=200)
    items: list[RawQuestionItem] = Field(min_length=1, max_length=50)
    footer: str | None = Field(min_length=1, max_length=1000)


class RawQuestion(_Raw):
    """Question text with separately recoverable optional guidance cards."""

    question: str = Field(min_length=1, max_length=2000)
    guidance: str | None = Field(min_length=1, max_length=2000)
    guidanceCards: list[RawQuestionCard] = Field(max_length=5)


class RawPhotoItem(_Raw):
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(min_length=1, max_length=1000)


class RawPhotoSuggestion(_Raw):
    sectionId: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    items: list[RawPhotoItem] = Field(min_length=1, max_length=50)
    footer: str | None = Field(min_length=1, max_length=1000)


class RawPhotoSuggestions(_Raw):
    suggestions: list[RawPhotoSuggestion] = Field(max_length=5)


def parse_question(value: Any) -> RawQuestion:
    """Validate the question envelope, dropping only independently invalid cards."""
    if not isinstance(value, dict) or set(value) - {"question", "guidance", "guidanceCards"}:
        raise ValueError("invalid question envelope")
    guidance = value.get("guidance")
    if not isinstance(guidance, str) or not guidance.strip() or len(guidance) > 2000:
        guidance = None
    cards = value.get("guidanceCards", [])
    valid = []
    if isinstance(cards, list):
        for card in cards[:5]:
            try:
                valid.append(RawQuestionCard.model_validate(card))
            except ValueError:
                pass
    return RawQuestion(question=value.get("question"), guidance=guidance, guidanceCards=valid)


class RawSummary(_Raw):
    summary: str = Field(min_length=1, max_length=10000)
    structure: RawStructure


class RawRevision(_Raw):
    outcome: RevisionOutcome
    summary: str | None = Field(max_length=10000)
    structure: RawStructure


class RawDraft(_Raw):
    structure: RawStructure


class RawRemovedStep(_Raw):
    ref: str = Field(min_length=1, max_length=64)
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(max_length=MAX_EVIDENCE_IDS)


class RawMediaWriting(_Raw):
    outcome: Literal["APPLIED", "NO_CHANGE"]
    structure: RawStructure
    # Required by the strict schema; defaults only so scripted test outputs may omit it.
    removed_steps: list[RawRemovedStep] = Field(default_factory=list, max_length=MAX_STEPS)


class RawCitation(_Raw):
    section_id: str = Field(min_length=1, max_length=64)
    step_ids: list[str] = Field(max_length=MAX_STEPS)


class RawQa(_Raw):
    outcome: QaOutcome
    answer: str = Field(min_length=1, max_length=3000)
    citations: list[RawCitation] = Field(max_length=MAX_CITATIONS)


Operation = Literal["judge_sufficiency", "generate_question", "summarize_intent", "revise_structure",
                    "compose_draft", "answer_question", "suggest_review_photos", "write_section_from_media"]

# operation -> (schema name sent to the provider, JSON Schema, parser)
OUTPUTS: dict[str, tuple[str, dict[str, Any], type[_Raw]]] = {
    "suggest_review_photos": ("review_photo_suggestions", PHOTO_SUGGESTIONS_SCHEMA, RawPhotoSuggestions),
    "judge_sufficiency": ("sufficiency_judgement", JUDGE_SCHEMA, RawJudgement),
    "generate_question": ("interview_question", QUESTION_SCHEMA, RawQuestion),
    "summarize_intent": ("intent_summary", SUMMARY_SCHEMA, RawSummary),
    "revise_structure": ("structure_revision", REVISION_SCHEMA, RawRevision),
    "compose_draft": ("manual_draft", DRAFT_SCHEMA, RawDraft),
    "answer_question": ("manual_answer", QA_SCHEMA, RawQa),
    "write_section_from_media": ("section_from_media", MEDIA_WRITING_SCHEMA, RawMediaWriting),
}

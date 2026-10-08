"""Real OpenAI check of the draft correction call (#118). Opt-in: OPENAI_API_KEY and
JIDAN_RUN_OPENAI=1 (see conftest). It checks the contract the DRAFT_CORRECTION task relies on,
not the wording quality."""

import uuid

import pytest

from app.ai import get_ai_provider
from app.ai.contracts import (
    MissingItem,
    RevisionTarget,
    SectionItem,
    ShiftItem,
    StepItem,
    StructureRevisionRequest,
    StructureSnapshot,
)

pytestmark = pytest.mark.openai


def _id() -> str:
    return str(uuid.uuid4())


DAY, NIGHT, TASK, RULE = _id(), _id(), _id(), _id()
GAP = _id()
DRAFT = StructureSnapshot(
    shifts=(
        ShiftItem(id=DAY, name="오전조", start_time="09:00", end_time="15:00", ends_next_day=False),
        ShiftItem(id=NIGHT, name="야간조", start_time="22:00", end_time=None, ends_next_day=True),
    ),
    sections=(
        SectionItem(id=TASK, category="SHIFT_TASK", shift_id=DAY, title="오픈 준비",
                    steps=(StepItem(id=_id(), instruction="포스기를 켜세요.", checklist_item=True),)),
        SectionItem(id=RULE, category="RULE", title="복장 규정",
                    steps=(StepItem(id=_id(), instruction="앞치마를 착용하세요."),)),
    ),
    missing_information=(
        MissingItem(id=GAP, target="SHIFT", target_id=NIGHT, field="endTime",
                    description="야간조 종료 시각이 정해지지 않았어요."),
    ),
)


def test_live_draft_correction_fills_only_the_spoken_value():
    revision = get_ai_provider().revise_structure(StructureRevisionRequest(
        current=DRAFT, target=RevisionTarget(kind="SHIFT", target_id=NIGHT),
        instruction="야간조는 다음 날 아침 6시에 끝나요.", require_manual_level=True,
    ))
    assert revision.outcome == "APPLIED", revision.outcome
    night = next(shift for shift in revision.structure.shifts if shift.id == NIGHT)
    assert (night.end_time, night.ends_next_day) == ("06:00", True)
    # Everything outside the target is kept (also enforced by validation) and the gap is closed.
    assert next(s for s in revision.structure.shifts if s.id == DAY) == DRAFT.shifts[0]
    assert {s.id for s in revision.structure.sections} == {TASK, RULE}
    assert all(m.field != "endTime" for m in revision.structure.missing_information)


def test_live_vague_instruction_is_not_guessed():
    revision = get_ai_provider().revise_structure(StructureRevisionRequest(
        current=DRAFT, instruction="그거 좀 바꿔 주세요.", require_manual_level=True,
    ))
    assert revision.outcome in ("CLARIFICATION_REQUIRED", "NO_CHANGE"), revision.outcome

"""Free-form corrections pass through the normal provider validation contract."""
import pytest

from app.ai.contracts import RevisionTarget, StructureRevisionRequest
from app.ai.errors import AiError
from app.ai.fake import FakeOutcome, structure_to_raw
from tests.test_ai_provider import snapshot


@pytest.mark.parametrize("instruction", [
    "이제 오픈 업무는 하지 않으니 없애 주시겠어요?",
    "오픈 업무는 앞으로 필요 없어요. 목록에서 빼 주시면 됩니다.",
])
def test_natural_deletion_preserves_surviving_ids(fake_ai, instruction):
    current = snapshot()
    raw = structure_to_raw(current)
    raw["sections"].pop(0)
    fake_ai.script("revise_structure", FakeOutcome.ok({"outcome": "APPLIED", "summary": None, "structure": raw}))
    result = fake_ai.revise_structure(StructureRevisionRequest(
        current=current, target=RevisionTarget(kind="SECTION", target_id=current.sections[0].id),
        instruction=instruction))
    assert result.outcome == "APPLIED"
    assert result.structure.sections == current.sections[1:]
    assert result.structure.shifts == current.shifts


def test_manual_correction_can_combine_deletion_addition_and_edit(fake_ai):
    current = snapshot()
    raw = structure_to_raw(current)
    raw["sections"].pop(0)
    raw["sections"][0]["title"] = "근무 복장"
    raw["sections"].append({"ref": "new-1", "category": "COMMON_TASK", "shift_ref": None,
                            "title": "정산", "steps": [{"ref": "new-2", "instruction": "금액 확인",
                                                          "checklist_item": False}]})
    fake_ai.script("revise_structure", FakeOutcome.ok({"outcome": "APPLIED", "summary": None, "structure": raw}))
    result = fake_ai.revise_structure(StructureRevisionRequest(
        current=current, target=RevisionTarget(kind="MANUAL"),
        instruction="오픈 업무는 빼고 복장은 근무 복장으로 바꿔 주세요. 정산 업무도 추가하고 금액을 확인하게 해 주세요."))
    assert result.outcome == "APPLIED"
    assert result.structure.sections[0].id == current.sections[1].id
    assert result.structure.sections[0].title == "근무 복장"
    assert result.structure.sections[1].id not in {s.id for s in current.sections}


@pytest.mark.parametrize("violation", ["outside_target", "unknown_id"])
def test_deletion_does_not_bypass_reference_or_scope_validation(fake_ai, violation):
    current = snapshot()
    raw = structure_to_raw(current)
    raw["sections"].pop(0)
    raw["sections"][0]["title" if violation == "outside_target" else "ref"] = "unknown"
    fake_ai.script("revise_structure", FakeOutcome.ok({"outcome": "APPLIED", "summary": None, "structure": raw}))
    with pytest.raises(AiError) as error:
        fake_ai.revise_structure(StructureRevisionRequest(
            current=current, target=RevisionTarget(kind="SECTION", target_id=current.sections[0].id),
            instruction="이제 오픈 업무는 필요 없으니 없애 주세요."))
    assert error.value.detail == ("revision_outside_target" if violation == "outside_target" else "unknown_ref:section")

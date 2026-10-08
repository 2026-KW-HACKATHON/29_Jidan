
import pytest

from app.ai.contracts import (
    SectionItem,
    StructureSnapshot,
)
from app.ai.errors import AiError
from app.ai.validation import check_section_identity


def test_conservative_section_identity_guard():
    section = SectionItem(id="old", category="COMMON_TASK", title="청소")
    before = StructureSnapshot(sections=(section,))
    renamed = section.model_copy(update={"title": "마감 청소"})
    check_section_identity(before, StructureSnapshot(sections=(renamed,)), "이름 수정", "SECTION", "old")
    check_section_identity(before, StructureSnapshot(), "청소를 삭제해 주세요", "SECTION", "old")
    extra = section.model_copy(update={"id": "new"})
    check_section_identity(before, StructureSnapshot(sections=(section, extra)), "업무 추가", "MANUAL", None)
    with pytest.raises(AiError, match="invalid_output"):
        check_section_identity(before, StructureSnapshot(sections=(extra,)), "청소를 삭제해 주세요", "SECTION", "old")


@pytest.mark.parametrize("instruction", [
    "삭제하지 마", "삭제하라는 문구를 바꿔", "이름을 마감 청소로 바꿔 주세요", "정산을 삭제해 주세요",
    '"청소를 삭제해 주세요"', "청소를 삭제해 주세요 그리고 정산을 추가해 주세요",
    "청소를 삭제해 주세요. 하지만 보존해 주세요",
])
def test_omitted_section_requires_whole_unambiguous_command(instruction):
    before = StructureSnapshot(sections=(SectionItem(id="old", category="COMMON_TASK", title="청소"),))
    with pytest.raises(AiError):
        check_section_identity(before, StructureSnapshot(), instruction, "SECTION", "old")


def test_deletion_only_authorizes_exact_target_and_unique_name():
    first = SectionItem(id="one", category="COMMON_TASK", title="청소")
    second = first.model_copy(update={"id": "two"})
    before = StructureSnapshot(sections=(first, second))
    with pytest.raises(AiError):
        check_section_identity(before, StructureSnapshot(), "삭제해 주세요", "SECTION", "one")
    with pytest.raises(AiError):
        check_section_identity(before, StructureSnapshot(sections=(second,)), "청소를 삭제해 주세요", "MANUAL", None)
    check_section_identity(before, StructureSnapshot(sections=(second,)), "삭제해 주세요", "SECTION", "one")




def test_exact_named_remove_command_keeps_existing_supported_wording():
    item = SectionItem(id="one", category="COMMON_TASK", title="손님 응대")
    before = StructureSnapshot(sections=(item,))
    check_section_identity(before, StructureSnapshot(), "손님 응대는 빼 주세요.", "MANUAL", None)
    for command in ("손님 응대는 빼지 마세요.", "손님 응대는 빼 주세요. 그리고 내용을 바꿔 주세요."):
        with pytest.raises(AiError):
            check_section_identity(before, StructureSnapshot(), command, "MANUAL", None)

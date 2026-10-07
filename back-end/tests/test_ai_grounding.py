"""Grounded writing (RAG): citations of the owner's words are checked by the server.

Rules (app.ai.validation.ground_structure): cited IDs must exist in the request's evidence
(else INVALID_OUTPUT); uncited steps are removed (a section left empty gets a SECTION/steps
missing entry); uncited shift times are cleared with SHIFT missing entries; content returned
unchanged from the input is exempt; no evidence means no check (pre-grounding behaviour).
"""

import pytest

from app.ai.contracts import (
    DialogueTurn,
    DraftRequest,
    EvidenceChunk,
    IntentBrief,
    IntentSummaryRequest,
    ReviewForDraft,
    SectionItem,
    ShiftItem,
    StepItem,
    StructureRevisionRequest,
    StructureSnapshot,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, FakeOutcome, structure_to_raw

INTENT = IntentBrief(key="COMMON_TASKS", stage="COMMON_TASKS", base_question="공통 업무를 알려 주세요.",
                     coverage_criteria="작업 순서")
TURN = DialogueTurn(question="공통 업무를 알려 주세요.", answer="손님께 인사해요. 주문을 받아요.", depth=0)
EVIDENCE = (
    EvidenceChunk(id="t1#1", intent_key="COMMON_TASKS", question="공통 업무를 알려 주세요.", text="손님께 인사해요."),
    EvidenceChunk(id="t1#2", intent_key="COMMON_TASKS", text="주문을 받아요."),
    EvidenceChunk(id="t0#1", intent_key="WORK_STRUCTURE", question="근무조가 있나요?", text="오픈조는 9시부터 15시까지예요."),
)
S1, S2, T1, T2, H1 = (f"00000000-0000-4000-8000-00000000000{i}" for i in range(1, 6))


def step(ref, instruction, ids):
    return {"ref": ref, "instruction": instruction, "checklist_item": False, "evidence_ids": ids}


def section(ref, steps, title="손님 응대"):
    return {"ref": ref, "category": "COMMON_TASK", "shift_ref": None, "title": title, "steps": steps}


def shift(ref, ids, start="09:00", end="15:00", next_day=False):
    return {"ref": ref, "name": "오픈조", "start_time": start, "end_time": end, "ends_next_day": next_day,
            "evidence_ids": ids}


def summary(sections=(), shifts=(), missing=()):
    return {"summary": "정리했어요.", "structure": {"shifts": list(shifts), "sections": list(sections),
                                                   "missing_information": list(missing)}}


@pytest.fixture
def fake():
    return FakeAiProvider(auto_cite=False)


def summarize(fake, output, evidence=EVIDENCE):
    fake.script("summarize_intent", FakeOutcome.ok(output))
    return fake.summarize_intent(IntentSummaryRequest(intent=INTENT, dialogue=(TURN,), needs_detail=False,
                                                      evidence=evidence))


def test_cited_steps_are_kept_and_citations_do_not_reach_the_snapshot(fake):
    result = summarize(fake, summary([section("new-1", [step("new-2", "인사해요.", ["t1#1"]),
                                                        step("new-3", "주문을 받아요.", ["t1#2", "t1#1"])])]))
    [kept] = result.structure.sections
    assert [s.instruction for s in kept.steps] == ["인사해요.", "주문을 받아요."]
    assert "evidence" not in kept.steps[0].model_dump() and result.structure.missing_information == ()


def test_the_model_sees_evidence_instead_of_the_whole_dialogue(fake):
    summarize(fake, summary([section("new-1", [step("new-2", "인사해요.", ["t1#1"])])]))
    data = fake.calls_for("summarize_intent")[0].data
    assert "dialogue" not in data and [c["id"] for c in data["evidence"]] == ["t1#1", "t1#2", "t0#1"]
    assert "evidence_ids" in fake.calls_for("summarize_intent")[0].instructions


def test_unknown_citation_is_invalid_output(fake):
    with pytest.raises(AiError) as caught:
        summarize(fake, summary([section("new-1", [step("new-2", "인사해요.", ["t9#1"])])]))
    assert caught.value.code == AiErrorCode.INVALID_OUTPUT and caught.value.detail == "unknown_evidence_id"
    with pytest.raises(AiError):  # an invented ID on a shift too
        summarize(fake, summary(shifts=[shift("new-1", ["made-up"])]))


def test_uncited_step_is_removed_and_an_emptied_section_becomes_unknown(fake):
    result = summarize(fake, summary([
        section("new-1", [step("new-2", "인사해요.", ["t1#1"]), step("new-3", "포인트를 적립해요.", [])]),
        section("new-4", [step("new-5", "퇴근 전에 금고를 잠가요.", [])], title="마감"),
    ]))
    first, second = result.structure.sections
    assert [s.instruction for s in first.steps] == ["인사해요."]  # one supported step remains: no entry
    assert second.steps == ()
    [entry] = result.structure.missing_information
    assert (entry.target, entry.target_id, entry.field) == ("SECTION", second.id, "steps")
    assert "근거" in entry.description


def test_model_written_missing_entry_wins_over_the_fixed_text(fake):
    result = summarize(fake, summary(
        [section("new-1", [step("new-2", "금고를 잠가요.", [])])],
        missing=[{"target": "SECTION", "target_ref": "new-1", "field": "steps", "description": "마감 순서 미정"}],
    ))
    assert [m.description for m in result.structure.missing_information] == ["마감 순서 미정"]


def test_uncited_shift_times_are_cleared_with_missing_entries(fake):
    result = summarize(fake, summary(shifts=[shift("new-1", []), shift("new-2", ["t0#1"], "15:00", "22:00")]))
    cleared, cited = result.structure.shifts
    assert (cleared.start_time, cleared.end_time, cleared.ends_next_day) == (None, None, None)
    assert (cited.start_time, cited.end_time) == ("15:00", "22:00")
    assert sorted((m.target_id, m.field) for m in result.structure.missing_information) == sorted(
        (cleared.id, f) for f in ("startTime", "endTime", "endsNextDay"))
    # A shift with no time values needs no citation (its missing entries are the model's job).
    unknown = summarize(fake, summary(shifts=[shift("new-1", [], None, None, None)], missing=[
        {"target": "SHIFT", "target_ref": "new-1", "field": f, "description": "미정"}
        for f in ("startTime", "endTime", "endsNextDay")]))
    assert unknown.structure.shifts[0].start_time is None


def test_without_evidence_nothing_is_checked(fake):
    result = summarize(fake, summary([section("new-1", [step("new-2", "인사해요.", ["anything"])])]), evidence=())
    assert [s.instruction for s in result.structure.sections[0].steps] == ["인사해요."]
    assert "dialogue" in fake.calls_for("summarize_intent")[0].data


def test_legacy_output_without_evidence_ids_parses_as_uncited(fake):
    output = summary([section("new-1", [step("new-2", "인사해요.", [])])])
    del output["structure"]["sections"][0]["steps"][0]["evidence_ids"]
    result = summarize(fake, output)
    assert result.structure.sections[0].steps == ()


def test_fake_default_summary_cites_the_intents_own_answers():
    fake = FakeAiProvider()
    result = fake.summarize_intent(IntentSummaryRequest(intent=INTENT, dialogue=(TURN,), needs_detail=False,
                                                        evidence=EVIDENCE))
    [only] = result.structure.sections
    assert [s.instruction for s in only.steps] == ["손님께 인사해요. 주문을 받아요."]  # WORK_STRUCTURE's not used


def test_fake_auto_cite_keeps_legacy_scripts_but_respects_explicit_lists():
    fake = FakeAiProvider()
    output = summary([section("new-1", [step("new-2", "인사해요.", [])])])
    fake.script("summarize_intent", FakeOutcome.ok(output))
    request = IntentSummaryRequest(intent=INTENT, dialogue=(TURN,), needs_detail=False, evidence=EVIDENCE)
    assert fake.summarize_intent(request).structure.sections[0].steps == ()  # explicit [] respected
    del output["structure"]["sections"][0]["steps"][0]["evidence_ids"]
    fake.script("summarize_intent", FakeOutcome.ok(output))
    assert len(fake.summarize_intent(request).structure.sections[0].steps) == 1  # auto-cited


# --- revisions and drafts: content returned unchanged is exempt ----------------------------------------


CURRENT = StructureSnapshot(
    shifts=(ShiftItem(id=H1, name="오픈조", start_time="09:00", end_time="15:00", ends_next_day=False),),
    sections=(SectionItem(id=S1, category="COMMON_TASK", title="손님 응대", steps=(
        StepItem(id=T1, instruction="인사해요."), StepItem(id=T2, instruction="주문을 받아요."))),),
)
CORRECTION = EvidenceChunk(id="c1#1", intent_key="COMMON_TASKS", text="주문은 키오스크로 받아요.")


def revise(fake, mutate, evidence=(*EVIDENCE, CORRECTION)):
    raw = structure_to_raw(CURRENT)
    for item in raw["shifts"]:
        item["evidence_ids"] = []
    for item in raw["sections"][0]["steps"]:
        item["evidence_ids"] = []
    mutate(raw)
    fake.script("revise_structure", FakeOutcome.ok({"outcome": "APPLIED", "summary": "고쳤어요.", "structure": raw}))
    return fake.revise_structure(StructureRevisionRequest(
        current=CURRENT, summary="요약", instruction="주문은 키오스크로 받아요.", evidence=evidence))


def test_revision_keeps_unchanged_items_and_needs_a_citation_for_changes(fake):
    def change(raw):
        raw["sections"][0]["steps"][1]["instruction"] = "키오스크로 주문을 받아요."
    dropped = revise(fake, change)
    assert dropped.outcome == "APPLIED"
    assert [s.instruction for s in dropped.structure.sections[0].steps] == ["인사해요."]

    def cite(raw):
        change(raw)
        raw["sections"][0]["steps"][1]["evidence_ids"] = ["c1#1"]
        raw["shifts"][0]["name"] = "아침조"  # a label: no citation needed
    applied = revise(fake, cite)
    assert [s.instruction for s in applied.structure.sections[0].steps] == ["인사해요.", "키오스크로 주문을 받아요."]
    assert applied.structure.shifts[0].start_time == "09:00" and applied.structure.shifts[0].name == "아침조"


def test_revision_changed_shift_time_without_citation_is_cleared(fake):
    def move(raw):
        raw["shifts"][0]["start_time"] = "08:00"
    result = revise(fake, move)
    assert result.outcome == "APPLIED" and result.structure.shifts[0].start_time is None
    assert {m.field for m in result.structure.missing_information} == {"startTime", "endTime", "endsNextDay"}


def test_draft_may_reword_reviewed_steps_but_new_steps_need_citations(fake):
    review = ReviewForDraft(intent_key="COMMON_TASKS", stage="COMMON_TASKS", summary="요약", needs_detail=False,
                            structure=CURRENT)
    raw = structure_to_raw(CURRENT)
    for item in raw["shifts"]:
        item["evidence_ids"] = []
    for item in raw["sections"][0]["steps"]:
        item["evidence_ids"] = []
    raw["sections"][0]["steps"][0]["instruction"] = "손님께 밝게 인사해요."  # reworded reviewed step
    raw["sections"][0]["steps"].append(step("new-1", "포인트를 적립해요.", []))  # new, uncited
    raw["sections"][0]["steps"].append(step("new-2", "주문을 받아요.", ["t1#2"]))  # new, cited
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    result = fake.compose_draft(DraftRequest(reviews=(review,), evidence=EVIDENCE))
    assert [s.instruction for s in result.structure.sections[0].steps] == [
        "손님께 밝게 인사해요.", "주문을 받아요.", "주문을 받아요."]

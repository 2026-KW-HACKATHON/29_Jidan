"""Grounded writing (RAG): citations of the owner's words are checked by the server.

Rules (app.ai.validation.ground_structure): cited IDs must exist in the request's evidence
(else INVALID_OUTPUT); uncited steps are removed (a section left empty gets a SECTION/steps
missing entry); uncited shift times are cleared with SHIFT missing entries; content returned
unchanged from the input is exempt; unsupported replacements keep the original instruction.
With no evidence, new items keep pre-grounding behaviour but existing instructions are preserved.
"""

import pytest

from app.ai.contracts import (
    DialogueTurn,
    DraftRequest,
    EvidenceChunk,
    IntentBrief,
    IntentSummaryRequest,
    ReviewForDraft,
    RevisionTarget,
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


def test_empty_retrieval_keeps_reviewed_facts_but_rejects_uncited_additions(fake):
    """Explicit empty evidence means retrieval ran, not a legacy opt-out."""
    reviewed = StructureSnapshot(sections=(SectionItem(
        id=S1, category="COMMON_TASK", title="응대",
        steps=(StepItem(id=T1, instruction="손님께 인사해요."),)),))
    output = {"structure": {
        "shifts": [shift("new-1", [], start="07:00")],
        "sections": [section(S1, [step(T1, "보증금을 받아요.", []), step("new-2", "할인해요.", [])])],
        "missing_information": [],
    }}
    fake.script("compose_draft", FakeOutcome.ok(output))
    result = fake.compose_draft(DraftRequest(reviews=(ReviewForDraft(
        intent_key=INTENT.key, stage=INTENT.stage, summary="응대", needs_detail=False, structure=reviewed),), evidence=()))
    assert result.structure.sections[0].steps == reviewed.sections[0].steps
    [new_shift] = result.structure.shifts
    assert (new_shift.start_time, new_shift.end_time, new_shift.ends_next_day) == (None, None, None)
    assert {m.field for m in result.structure.missing_information if m.target_id == new_shift.id} == {
        "startTime", "endTime", "endsNextDay"}


def test_empty_retrieval_does_not_accept_invented_evidence_ids(fake):
    fake.script("compose_draft", FakeOutcome.ok({"structure": {
        "shifts": [], "sections": [section("new-1", [step("new-2", "할인해요.", ["missing#1"])])],
        "missing_information": []}}))
    request = DraftRequest(reviews=(ReviewForDraft(intent_key=INTENT.key, stage=INTENT.stage,
        summary="아직 없음", needs_detail=True, structure=StructureSnapshot()),), evidence=())
    with pytest.raises(AiError) as caught:
        fake.compose_draft(request)
    assert caught.value.detail == "unknown_evidence_id"


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


def revise(fake, mutate, evidence=(*EVIDENCE, CORRECTION), *, summary="요약", written="고쳤어요.", target=None):
    raw = structure_to_raw(CURRENT)
    for item in raw["shifts"]:
        item["evidence_ids"] = []
    for item in raw["sections"][0]["steps"]:
        item["evidence_ids"] = []
    mutate(raw)
    fake.script("revise_structure", FakeOutcome.ok({"outcome": "APPLIED", "summary": written, "structure": raw}))
    return fake.revise_structure(StructureRevisionRequest(
        current=CURRENT, summary=summary, instruction="주문은 키오스크로 받아요.", evidence=evidence,
        **({"target": target} if target else {})))


def change(raw):
    raw["sections"][0]["steps"][1]["instruction"] = "키오스크로 주문을 받아요."


def ungrounded(fake, mutate, **options):
    with pytest.raises(AiError) as caught:
        revise(fake, mutate, **options)
    assert caught.value.code == AiErrorCode.INVALID_OUTPUT and caught.value.retryable
    assert caught.value.detail == "ungrounded_revision"


@pytest.mark.parametrize("summary", ["요약", None], ids=["review", "draft"])
def test_revision_with_an_uncited_change_fails_instead_of_applying_a_restored_original(fake, summary):
    # Restoring the original would leave "APPLIED" (and a changed review summary) describing a
    # change that is not in the content; for a draft it would report the lost correction as
    # NO_CHANGE. The whole output is retried instead and nothing is returned to save.
    ungrounded(fake, change, summary=summary)
    ungrounded(fake, change, summary=summary, target=RevisionTarget(kind="SECTION", target_id=S1))
    ungrounded(fake, lambda raw: raw["sections"][0]["steps"].append(step("new-1", "포인트를 적립해요.", [])),
               summary=summary)  # an uncited new step would be dropped silently

    def cite(raw):
        change(raw)
        raw["sections"][0]["steps"][1]["evidence_ids"] = ["c1#1"]
        raw["shifts"][0]["name"] = "아침조"  # a label: no citation needed
    applied = revise(fake, cite, summary=summary)
    assert applied.outcome == "APPLIED"
    assert [s.instruction for s in applied.structure.sections[0].steps] == ["인사해요.", "키오스크로 주문을 받아요."]
    assert [s.id for s in applied.structure.sections[0].steps] == [T1, T2]
    assert applied.structure.shifts[0].start_time == "09:00" and applied.structure.shifts[0].name == "아침조"
    assert applied.summary == ("고쳤어요." if summary else None)


def test_revision_returned_unchanged_is_no_change_and_a_summary_only_rewrite_applies(fake):
    assert revise(fake, lambda raw: None, written="요약").outcome == "NO_CHANGE"
    assert revise(fake, lambda raw: None, summary=None, written=None).outcome == "NO_CHANGE"
    rewritten = revise(fake, lambda raw: None, written="주문 응대를 정리했어요.")
    assert rewritten.outcome == "APPLIED" and rewritten.structure == CURRENT
    assert rewritten.summary == "주문 응대를 정리했어요."


def test_revision_changed_shift_time_without_citation_fails(fake):
    def move(raw):
        raw["shifts"][0]["start_time"] = "08:00"
    ungrounded(fake, move)
    ungrounded(fake, move, summary=None)


def test_draft_preserves_uncited_reviewed_instructions_and_new_steps_need_citations(fake):
    review = ReviewForDraft(intent_key="COMMON_TASKS", stage="COMMON_TASKS", summary="요약", needs_detail=False,
                            structure=CURRENT)
    raw = structure_to_raw(CURRENT)
    for item in raw["shifts"]:
        item["evidence_ids"] = []
    for item in raw["sections"][0]["steps"]:
        item["evidence_ids"] = []
    raw["sections"][0]["steps"].append(step("new-1", "포인트를 적립해요.", []))  # new, uncited
    raw["sections"][0]["steps"].append(step("new-2", "손님께 인사해요.", ["t1#1"]))  # new, cited
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    result = fake.compose_draft(DraftRequest(reviews=(review,), evidence=EVIDENCE))
    assert [s.instruction for s in result.structure.sections[0].steps] == [
        "인사해요.", "주문을 받아요.", "손님께 인사해요."]
    assert [s.id for s in result.structure.sections[0].steps[:2]] == [T1, T2]


@pytest.mark.parametrize("citations", [([], ["split#1"]), (["split#1"], ["split#1"]), ([], [])])
def test_draft_split_requires_citations_for_both_parts_or_keeps_only_original(fake, citations):
    original = "바닥을 닦고 다 닦으면 체크리스트에 표시해요."
    current = CURRENT.model_copy(update={"sections": (CURRENT.sections[0].model_copy(update={
        "steps": (StepItem(id=T1, instruction=original),)}),)})
    raw = structure_to_raw(current)
    raw["sections"][0]["steps"] = [step(T1, "바닥을 닦아요.", citations[0]),
                                  step("new-1", "다 닦으면 체크리스트에 표시해요.", citations[1])]
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    request = DraftRequest(reviews=(ReviewForDraft(intent_key="COMMON_TASKS", stage="COMMON_TASKS",
        summary="요약", needs_detail=False, structure=current),), evidence=(
            EvidenceChunk(id="split#1", intent_key="COMMON_TASKS", text=original),))
    if not citations[0] and citations[1]:
        with pytest.raises(AiError) as caught:
            fake.compose_draft(request)
        assert caught.value.code == AiErrorCode.INVALID_OUTPUT
        assert caught.value.detail == "uncited_step_change_with_additions"
    else:
        steps = fake.compose_draft(request).structure.sections[0].steps
        assert [s.instruction for s in steps] == (["바닥을 닦아요.", "다 닦으면 체크리스트에 표시해요."]
                                                if citations[0] else [original])
        assert steps[0].id == T1


@pytest.mark.parametrize("evidence", [EVIDENCE, ()])
def test_draft_keeps_an_unrelated_cited_addition_beside_restored_polishing(fake, evidence):
    # Only a new step repeating what the uncited rewrite removed is a split; an unrelated cited
    # step next to harmless polishing is kept (the polishing itself is restored).
    review = ReviewForDraft(intent_key="COMMON_TASKS", stage="COMMON_TASKS", summary="요약", needs_detail=False,
                            structure=CURRENT)
    raw = structure_to_raw(CURRENT)
    for item in raw["sections"][0]["steps"]:
        item["evidence_ids"] = []
    raw["sections"][0]["steps"][0]["instruction"] = "손님께 밝게 인사해요."
    raw["sections"][0]["steps"].append(step("new-1", "마감 후 바닥을 닦아요.", ["t1#1"] if evidence else []))
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    steps = fake.compose_draft(DraftRequest(reviews=(review,), **({"evidence": evidence} if evidence else {}))).structure.sections[0].steps
    assert [s.instruction for s in steps] == ["인사해요.", "주문을 받아요.", "마감 후 바닥을 닦아요."]
    assert [s.id for s in steps[:2]] == [T1, T2]


def test_draft_split_of_a_restored_step_is_still_rejected_when_reworded_and_in_legacy_requests(fake):
    original = "포스기를 켜고 시재를 확인해요."
    current = CURRENT.model_copy(update={"sections": (CURRENT.sections[0].model_copy(update={
        "steps": (StepItem(id=T1, instruction=original),)}),)})
    review = ReviewForDraft(intent_key="COMMON_TASKS", stage="COMMON_TASKS", summary="요약", needs_detail=False,
                            structure=current)
    for evidence, ids in [((EvidenceChunk(id="s#1", intent_key="COMMON_TASKS", text=original),), ["s#1"]), ((), [])]:
        raw = structure_to_raw(current)
        raw["sections"][0]["steps"] = [step(T1, "포스기를 켜요.", []), step("new-1", "시재를 확인해요.", ids)]
        fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
        with pytest.raises(AiError) as caught:
            fake.compose_draft(DraftRequest(reviews=(review,), **({"evidence": evidence} if evidence else {})))
        assert caught.value.detail == "uncited_step_change_with_additions"


@pytest.mark.parametrize("instruction", ["모든 주문에 50% 할인해요.", "손님께 밝게 인사해요.",
                                         "상황에 맞게 처리해요.", "   "])
@pytest.mark.parametrize("evidence", [EVIDENCE, ()])
def test_draft_existing_id_cannot_authorize_uncited_changes(fake, instruction, evidence):
    raw = structure_to_raw(CURRENT)
    raw["sections"][0]["steps"][0].update(instruction=instruction, evidence_ids=[])
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    review = ReviewForDraft(intent_key="COMMON_TASKS", stage="COMMON_TASKS", summary="요약", needs_detail=False,
                            structure=CURRENT)
    assert fake.compose_draft(DraftRequest(reviews=(review,), evidence=evidence)).structure == CURRENT


def test_draft_changed_reviewed_instruction_requires_a_known_citation(fake):
    raw = structure_to_raw(CURRENT)
    raw["sections"][0]["steps"][1].update(instruction="키오스크로 주문을 받아요.", evidence_ids=["c1#1"])
    review = ReviewForDraft(intent_key="COMMON_TASKS", stage="COMMON_TASKS", summary="요약", needs_detail=False,
                            structure=CURRENT)
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    result = fake.compose_draft(DraftRequest(reviews=(review,), evidence=(*EVIDENCE, CORRECTION)))
    assert result.structure.sections[0].steps[1].id == T2
    assert result.structure.sections[0].steps[1].instruction == "키오스크로 주문을 받아요."
    raw["sections"][0]["steps"][1]["evidence_ids"] = ["invented"]
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    with pytest.raises(AiError, match="invalid_output") as caught:
        fake.compose_draft(DraftRequest(reviews=(review,), evidence=EVIDENCE))
    assert caught.value.detail == "unknown_evidence_id"


@pytest.mark.parametrize("instruction", ["상황에 맞게 처리해요.", "   ", "따로 정한 규칙은 없어요."])
@pytest.mark.parametrize("evidence", [EVIDENCE, ()])
def test_correction_uncited_replacement_fails_before_contentless_filtering(fake, instruction, evidence):
    # Contentless filtering never gets to erase the original: grounding rejects the change first.
    ungrounded(fake, lambda raw: raw["sections"][0]["steps"][1].update(instruction=instruction), evidence=evidence)


def test_unchanged_existing_step_does_not_hide_an_invalid_citation(fake):
    with pytest.raises(AiError) as caught:
        revise(fake, lambda raw: raw["sections"][0]["steps"][0].update(evidence_ids=["made-up"]))
    assert caught.value.detail == "unknown_evidence_id"


@pytest.mark.parametrize("output", ["not JSON", "{}", '{"structure": null}', '{"structure": {}}'])
def test_malformed_draft_is_rejected_instead_of_restoring_unparsed_content(fake, output):
    fake.script("compose_draft", FakeOutcome.ok(output))
    with pytest.raises(AiError) as caught:
        fake.compose_draft(DraftRequest(reviews=(ReviewForDraft(
            intent_key="COMMON_TASKS", stage="COMMON_TASKS", summary="요약", needs_detail=False,
            structure=CURRENT),)))
    assert caught.value.detail == "unparseable_output"


def test_empty_instruction_on_existing_id_is_still_malformed(fake):
    raw = structure_to_raw(CURRENT)
    raw["sections"][0]["steps"][0]["instruction"] = ""
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    with pytest.raises(AiError) as caught:
        fake.compose_draft(DraftRequest(reviews=(ReviewForDraft(
            intent_key="COMMON_TASKS", stage="COMMON_TASKS", summary="요약", needs_detail=False,
            structure=CURRENT),)))
    assert caught.value.detail == "unparseable_output"

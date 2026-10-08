"""Manual-writing quality rules seen in the live evaluation (eval-run1).

W1  "해당 없음" is not manual content: Jev's not_applicable reaches the summary request, the
    summary then writes no structure, and a "no rule" step is removed by the server.
W2  Vague owner statements ("상황에 맞게 처리해요") are not steps: they become missing information.
W3  Step rules (one action per step, completion criterion as its own step, checklist_item only
    for tick-off steps) are part of the writing prompts.
W4  Correction evidence is frozen at acceptance; draft corrections are grounded on the
    instruction; steps dropped from a section that kept others are counted in the log.
"""

import json
import logging
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import (
    DialogueTurn,
    DraftRequest,
    EvidenceChunk,
    IntentBrief,
    IntentSummaryRequest,
    MissingItem,
    ReviewForDraft,
    SectionItem,
    ShiftItem,
    StepItem,
    StructureRevisionRequest,
    StructureSnapshot,
    SufficiencyRequest,
)
from app.ai.fake import FakeAiProvider, FakeOutcome, structure_to_raw
from app.ai.prompts import INSTRUCTIONS
from app.ai.schemas import RawStructure
from app.ai.validation import (
    NO_RULE_STEPS,
    VAGUE_STEPS,
    contentless_kind,
    drop_contentless_steps,
    ground_structure,
)
from app.db import utcnow
from app.db.models import BackgroundTask, ManualDraftCorrection
from app.manual_corrections import instruction_evidence
from app.media.storage import LocalMediaStorage, set_media_storage
from app.tasks import drain
from tests.api_contract import login
from tests.factories import NOW, make_store, make_user
from tests.manual_factories import make_ready_draft, sample_content
from tests.test_interview_api import build_ctx
from tests.test_manual_corrections import Ctx, correct, draft, retry, snapshot

RULES = IntentBrief(key="RULES", stage="COMPLEMENTS", base_question="매장 규칙이 있나요?",
                    coverage_criteria="규칙의 내용")
EXCEPTIONS = IntentBrief(key="EXCEPTIONS", stage="COMPLEMENTS", base_question="돌발 상황은 어떻게 하나요?",
                         coverage_criteria="돌발 상황의 대응 방법")
NA_ANSWER = "따로 정해 둔 매장 규칙은 없어요. 이 항목은 해당 없음으로 해 주세요."
S1, S2, T1, T2 = (f"00000000-0000-4000-8000-00000000000{i}" for i in range(1, 5))


def step(ref, instruction, ids=("e#1",), checklist=False):
    return {"ref": ref, "instruction": instruction, "checklist_item": checklist, "evidence_ids": list(ids)}


def section(ref, steps, title="업무", category="COMMON_TASK"):
    return {"ref": ref, "category": category, "shift_ref": None, "title": title, "steps": steps}


def summary(sections=(), missing=(), text="정리했어요."):
    return {"summary": text, "structure": {"shifts": [], "sections": list(sections),
                                           "missing_information": list(missing)}}


def evidence(text, key="RULES"):
    return (EvidenceChunk(id="e#1", intent_key=key, question="질문", text=text),)


@pytest.fixture
def fake():
    return FakeAiProvider(auto_cite=False)


# --- W1: not applicable ---------------------------------------------------------------------------


def judge(fake, outcome):
    fake.script("judge_sufficiency", outcome)
    return fake.judge_sufficiency(SufficiencyRequest(
        intent=RULES, dialogue=(DialogueTurn(question="매장 규칙이 있나요?", answer=NA_ANSWER, depth=0),), depth=0))


def test_decisions_judgement_reports_not_applicable_only_at_its_threshold(fake):
    applies = judge(fake, FakeOutcome.predicates(0.1, not_applicable=0.95))
    assert (applies.sufficient, applies.not_applicable, applies.missing_aspects) == (True, True, ())
    covered = judge(fake, FakeOutcome.predicates(0.95, not_applicable=0.5))  # sufficient by aspects
    assert (covered.sufficient, covered.not_applicable) == (True, False)
    refused = judge(fake, FakeOutcome.predicates(0.95, refuse=("not_applicable",)))
    assert (refused.sufficient, refused.not_applicable) == (True, False)


def test_responses_judgement_never_reports_not_applicable():
    fake = FakeAiProvider(judge_backend="responses")
    result = judge(fake, FakeOutcome.ok({"sufficient": True, "probability": 0.9, "missing_aspects": []}))
    assert result.sufficient and not result.not_applicable


def summarize(fake, output, *, intent=RULES, text=NA_ANSWER, not_applicable=False, needs_detail=False):
    fake.script("summarize_intent", FakeOutcome.ok(output))
    return fake.summarize_intent(IntentSummaryRequest(
        intent=intent, dialogue=(DialogueTurn(question="질문", answer=text, depth=0),), needs_detail=needs_detail,
        evidence=evidence(text, intent.key), not_applicable=not_applicable))


def test_not_applicable_summary_keeps_the_text_and_writes_no_structure(fake, caplog):
    output = summary([section("new-1", [step("new-2", "직원은 매장 규칙을 지켜요.")], title="매장 규칙",
                              category="RULE")],
                     missing=[{"target": "MANUAL", "target_ref": None, "field": "sections", "description": "없음"}],
                     text="따로 정해 둔 매장 규칙은 없다고 하셨어요.")
    with caplog.at_level(logging.INFO, logger="jidan.ai"):
        result = summarize(fake, output, not_applicable=True)
    assert result.summary == "따로 정해 둔 매장 규칙은 없다고 하셨어요."
    assert result.structure == StructureSnapshot()
    assert fake.calls_for("summarize_intent")[0].data["not_applicable"] is True
    assert "removed_sections=1" in caplog.text and "직원은" not in caplog.text


def test_no_rule_section_is_removed_from_a_summary_even_without_the_judgement(fake):
    output = summary([section("new-1", [step("new-2", "따로 정해 둔 매장 규칙은 없어요.")], title="매장 규칙",
                              category="RULE"),
                      section("new-3", [step("new-4", "출근하면 손을 씻어요.")])])
    result = summarize(fake, output)
    assert [s.title for s in result.structure.sections] == ["업무"]
    assert result.structure.missing_information == ()


# --- W2: vague statements -------------------------------------------------------------------------


def test_vague_step_becomes_missing_information(fake):
    vague = "손님 불만이나 기계 문제가 생기면 상황에 맞게 처리해요."
    output = summary([section("new-1", [step("new-2", vague)], title="손님 불만·기계 문제 대응")])
    result = summarize(fake, output, intent=EXCEPTIONS, text="상황 봐서 적당히 하면 돼요.", needs_detail=True)
    [kept] = result.structure.sections
    assert kept.steps == ()
    [entry] = result.structure.missing_information
    assert (entry.target, entry.target_id, entry.field, entry.description) == (
        "SECTION", kept.id, "steps", VAGUE_STEPS)


def test_model_written_missing_entry_wins_and_concrete_steps_stay(fake):
    output = summary(
        [section("new-1", [step("new-2", "기계가 멈추면 전원을 껐다 켜요."), step("new-3", "그때그때 잘 처리하면 돼요.")]),
         section("new-4", [step("new-5", "알아서 해요.")], title="손님 불만")],
        missing=[{"target": "SECTION", "target_ref": "new-4", "field": "steps",
                  "description": "손님 불만이 생겼을 때 할 일이 아직 정해지지 않았어요."}])
    result = summarize(fake, output, intent=EXCEPTIONS, text="기계가 멈추면 전원을 껐다 켜요.")
    machine, complaint = result.structure.sections
    assert [s.instruction for s in machine.steps] == ["기계가 멈추면 전원을 껐다 켜요."]
    assert [m.description for m in result.structure.missing_information] == [
        "손님 불만이 생겼을 때 할 일이 아직 정해지지 않았어요."]
    assert complaint.steps == ()


@pytest.mark.parametrize(("text", "kind"), [
    ("손님 불만이나 기계 문제가 생기면 상황에 맞게 처리해요.", "vague"),
    ("그때그때 잘 처리하면 돼요.", "vague"),
    ("눈치껏 하면 돼요.", "vague"),
    ("따로 정해 둔 매장 규칙은 없어요.", "no_rule"),
    ("해당 없음", "no_rule"),
    ("먼저 사과하고, 나머지는 상황에 맞게 처리해요.", None),  # has a concrete action
    ("우유를 적당히 데워요.", None),
    ("상황에 따라 점주에게 연락해요.", None),
    ("정해진 자리에 컵을 둬요.", None),
    ("영수증이 출력되면 주문 응대가 끝난 거예요.", None),
    ("근무자는 음식물 쓰레기를 버리지 않아도 돼요.", None),  # Figma 업무 상세: a "don't" is guidance
    ("오후조와 야간조는 별도 업무가 없어요.", None),
    ("제빙기 안에는 손을 넣지 말고 꼭 스쿱을 사용해요.", None),
    ("상황에 맞게 처리해요. " * 10, None),  # long text is never judged by a pattern
    ("상황에 맞게 처리해요. 먼저 전원을 꺼요.", None),
    ("전원을 끈 다음 상황에 맞게 처리해요.", None),
    ("전원을 껐다가 상황에 맞게 처리해요.", None),
    ("환불한 뒤에는 상황에 맞게 처리해요.", None),
    ("영수증을 확인하면 상황에 맞게 처리해요.", None),
    ("정해진 규칙은 없지만 점주에게 전화해요.", None),
    ("정해진 규칙은 없어요. 점주에게 전화해요.", None),
    ("해당 없음이라고 표시한 뒤 점주에게 연락해요.", None),
    ('"상황에 맞게 처리해요"라는 안내를 읽어요.', None),
    ("점주가 상황에 맞게 처리하라고 하면 전화해요.", None),
    ("정해진 규칙은 없을 때 영수증을 확인해요.", None),
    ("", None), (" \n ", None),
    ("상황에 맞게 처리해요!", "vague"),
    ("정한 규칙이 없습니다.", "no_rule"),
    ("해당 사항 없음.", "no_rule"),
    ("정한 게 없어요.", "no_rule"),
    ("따로 정해 놓은 청소 규칙은 없어요.", "no_rule"),
    ("상황에 맞게 처리", "vague"),
    ("정해진 규칙은 따로 없어요.", "no_rule"),
    ("별도로 정해진 규칙은 없어요.", "no_rule"),
    ("정해진 방법은 없어요.", "no_rule"),
    ("점주가 따로 정한 규칙은 없어요.", "no_rule"),
    ("상황에 맞게 알아서 처리해요.", "vague"),
    ("그때그때 알아서 처리해요.", "vague"),
    ("고장이 나면 상황에 맞게 처리해요.", "vague"),
    ("정해진 규칙은 따로 없어요. 점주에게 전화해요.", None),
    ("별도로 정해진 규칙은 없어요. 영수증을 확인해요.", None),
    ("정해진 방법은 없어요. 전원을 꺼요.", None),
    ("점주가 따로 정한 규칙은 없지만 점주에게 전화해요.", None),
    ("상황에 맞게 알아서 처리해요. 전원을 꺼요.", None),
    ("영수증을 확인하고 그때그때 알아서 처리해요.", None),
    ("고장이 나면 전원을 끄고 상황에 맞게 처리해요.", None),
    # A topic/subject noun phrase before a no-content predicate, nouns outside any list.
    ("정해진 순서는 없어요.", "no_rule"),
    ("정해진 복장은 없어요.", "no_rule"),
    ("정한 시간은 따로 없어요.", "no_rule"),
    ("특별한 규칙은 없어요.", "no_rule"),
    ("규칙은 따로 없어요.", "no_rule"),
    ("복장 규정은 따로 없어요.", "no_rule"),
    ("손님 불만은 상황에 맞게 처리해요.", "vague"),
    ("청소는 상황에 맞게 해요.", "vague"),
    ("포스기 고장이 나면 알아서 처리해요.", "vague"),
    ("손님 불만이나 기계 문제가 생기면 청소는 상황에 맞게 처리해요.", "vague"),
    # The phrase carries an action, a fact or a dependent clause: kept.
    ("청소 도구는 따로 없어요.", None),  # a fact about the store, not a missing rule
    ("따로 없어요.", None),
    ("컵은 씻고 나머지는 알아서 해요.", None),
    ("청소는 직원이 알아서 해요.", None),
    ("매일 청소는 알아서 해요.", None),
    ("청소는 매일 알아서 해요.", None),
    ("주문은 키오스크로 받고 결제는 알아서 해요.", None),
    ("환불은 영수증 확인 후 상황에 맞게 처리해요.", None),
    ("매장에서는 알아서 해요.", None),
    ("컵을씻는건 알아서 해요.", None),
    ("손님이 오면 알아서 해요.", None),
    ("주문이 밀리면 알아서 처리해요.", None),
    ("2층 청소는 알아서 해요.", None),
    ("'청소'는 알아서 해요.", None),
    ("정해진 순서는 없어요. 영수증을 확인해요.", None),
    ("청소는 상황에 맞게 하고 마감 전에 점주에게 사진을 보내요.", None),
])
def test_contentless_patterns_are_narrow(text, kind):
    assert contentless_kind(text) == kind


@pytest.mark.parametrize("instruction", [
    "상황에 맞게 처리해요. 먼저 전원을 꺼요.",
    "전원을 껐다가 상황에 맞게 처리해요.",
    "영수증을 확인하면 상황에 맞게 처리해요.",
    "정해진 규칙은 없어요. 점주에게 전화해요.",
    '"상황에 맞게 처리해요"라는 안내를 읽어요.',
])
def test_mixed_and_referenced_actions_survive_summary_verbatim(fake, instruction):
    output = summary([section("new-1", [step("new-2", instruction), step("new-3", "알아서 해요.")])])
    result = summarize(fake, output, intent=EXCEPTIONS, text=instruction)
    assert [s.instruction for s in result.structure.sections[0].steps] == [instruction]
    assert result.structure.missing_information == ()


def test_step_id_without_reviewed_instruction_cannot_exempt_new_facts():
    raw = RawStructure.model_validate(summary([section(S1, [step(T1, "모든 주문을 반값에 판매해요.", ids=())])])["structure"])
    grounded, counts = ground_structure(raw, ["e#1"], grounded_steps=dict.fromkeys([T1]))
    assert grounded.sections[0].steps == []
    assert counts.dropped_steps == 1


def test_surrounding_whitespace_keeps_existing_instruction_exempt():
    current = StructureSnapshot(sections=(SectionItem(id=S1, category="COMMON_TASK", title="응대",
        steps=(StepItem(id=T1, instruction="손님께 인사해요."),)),))
    raw = structure_to_raw(current)
    raw["sections"][0]["steps"][0]["instruction"] = "  손님께 인사해요.  "
    grounded, counts = ground_structure(RawStructure.model_validate(raw), ["e#1"],
                                       grounded_steps={T1: "손님께 인사해요."})
    assert counts.restored_steps == 0 and len(grounded.sections[0].steps) == 1


def test_reviewed_section_is_kept_empty_with_a_missing_entry_in_a_draft(fake):
    reviewed = StructureSnapshot(sections=(
        SectionItem(id=S1, category="RULE", title="매장 규칙",
                    steps=(StepItem(id=T1, instruction="따로 정해 둔 매장 규칙은 없어요."),)),
        SectionItem(id=S2, category="COMMON_TASK", title="대응",
                    steps=(StepItem(id=T2, instruction="상황에 맞게 처리해요."),)),
    ))
    review = ReviewForDraft(intent_key="RULES", stage="COMPLEMENTS", summary="요약", needs_detail=False,
                            structure=reviewed)
    raw = structure_to_raw(reviewed)
    raw["missing_information"] = [{"target": "MANUAL", "target_ref": None, "field": "shifts", "description": "근무조 미정"}]
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    result = fake.compose_draft(DraftRequest(reviews=(review,), evidence=evidence("없어요.")))
    assert [(s.id, s.steps) for s in result.structure.sections] == [(S1, ()), (S2, ())]
    assert {(m.target_id, m.description) for m in result.structure.missing_information if m.target == "SECTION"} == {
        (S1, NO_RULE_STEPS), (S2, VAGUE_STEPS)}


def test_draft_keeps_reviewed_shift_times_even_against_cited_older_answers(fake, caplog):
    """Live run: the review was corrected to 23:00, the draft cited the earlier 22:30 answer."""
    reviewed = StructureSnapshot(shifts=(
        ShiftItem(id=S1, name="마감조", start_time="15:00", end_time="23:00", ends_next_day=False),
        ShiftItem(id=S2, name="야간조", start_time="23:00", end_time=None, ends_next_day=None),
    ), missing_information=(
        MissingItem(id=T1, target="SHIFT", target_id=S2, field="endTime", description="미정"),
        MissingItem(id=T2, target="SHIFT", target_id=S2, field="endsNextDay", description="미정"),
    ))
    review = ReviewForDraft(intent_key="WORK_STRUCTURE", stage="WORK_STRUCTURE", summary="요약", needs_detail=False,
                            structure=reviewed)
    raw = structure_to_raw(reviewed)
    raw["shifts"][0].update(end_time="22:30", evidence_ids=["e#1"])
    raw["shifts"][1].update(end_time="07:00", ends_next_day=True, evidence_ids=["e#1"])
    raw["missing_information"] = [m for m in raw["missing_information"] if m["target_ref"] != S2]
    raw["missing_information"].append({"target": "MANUAL", "target_ref": None, "field": "sections", "description": "미정"})
    fake.script("compose_draft", FakeOutcome.ok({"structure": raw}))
    with caplog.at_level(logging.INFO, logger="jidan.ai"):
        result = fake.compose_draft(DraftRequest(reviews=(review,), evidence=evidence("밤 10시 30분까지 일해요.")))
    assert [(s.start_time, s.end_time, s.ends_next_day) for s in result.structure.shifts] == [
        ("15:00", "23:00", False), ("23:00", None, None)]
    assert {(m.id, m.field) for m in result.structure.missing_information if m.target == "SHIFT"} == {
        (T1, "endTime"), (T2, "endsNextDay")}  # the review's own entries (ids kept) were re-added
    assert "restored=2" in caplog.text


def test_correction_leaves_unchanged_existing_steps_alone(fake):
    current = StructureSnapshot(sections=(
        SectionItem(id=S1, category="COMMON_TASK", title="대응", steps=(StepItem(id=T1, instruction="상황에 맞게 처리해요."),)),
        SectionItem(id=S2, category="COMMON_TASK", title="청소", steps=(StepItem(id=T2, instruction="바닥을 닦아요."),)),
    ))
    raw = structure_to_raw(current)
    raw["sections"][1]["steps"][0].update(instruction="바닥을 쓸고 닦아요.", evidence_ids=["c#1"])
    fake.script("revise_structure", FakeOutcome.ok({"outcome": "APPLIED", "summary": "고쳤어요.", "structure": raw}))
    result = fake.revise_structure(StructureRevisionRequest(
        current=current, summary="요약", instruction="바닥은 쓸고 닦아요.", target={"kind": "SECTION", "target_id": S2},
        evidence=(EvidenceChunk(id="c#1", intent_key="EXCEPTIONS", text="바닥은 쓸고 닦아요."),)))
    assert result.outcome == "APPLIED"
    assert [[t.instruction for t in s.steps] for s in result.structure.sections] == [
        ["상황에 맞게 처리해요."], ["바닥을 쓸고 닦아요."]]


def test_contentless_guard_without_matches_returns_the_same_object():
    raw = RawStructure.model_validate(summary([section("new-1", [step("new-2", "문을 열어요.")])])["structure"])
    cleaned, counts = drop_contentless_steps(raw)
    assert cleaned is raw and (counts.vague_steps, counts.no_rule_steps) == (0, 0)


# --- W3: prompt rules -----------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["summarize_intent", "revise_structure", "compose_draft"])
def test_writing_prompts_carry_the_step_rules(operation):
    text = INSTRUCTIONS[operation]
    assert "[단계 작성 규칙" in text
    assert "해당 없음은 매뉴얼 내용이 아니다" in text and "상황에 맞게 처리해요" in text
    assert "완료 기준(언제 끝난 것인지)을 다른 행동 문장 뒤에 덧붙이지 않는다" in text
    assert "checklist_item은 근무자가 정해진 시점" in text and "모든 단계를 true로 하지 않는다" in text


def test_only_the_summary_prompt_explains_not_applicable():
    assert "not_applicable=true" in INSTRUCTIONS["summarize_intent"]
    assert "[단계 작성 규칙" not in INSTRUCTIONS["generate_question"]
    assert "[단계 작성 규칙" not in INSTRUCTIONS["judge_sufficiency"]


# --- W4(c): steps dropped from a kept section ---------------------------------------------------


def test_steps_dropped_from_a_kept_section_are_counted(fake, caplog):
    raw = RawStructure.model_validate(summary([section("new-1", [
        step("new-2", "문을 열어요."), step("new-3", "포인트를 적립해요.", ids=())])])["structure"])
    grounded, counts = ground_structure(raw, ["e#1"])
    assert (counts.dropped_steps, counts.dropped_in_kept_sections) == (1, 1)
    assert grounded.missing_information == []
    with caplog.at_level(logging.INFO, logger="jidan.ai"):
        summarize(fake, summary([section("new-1", [step("new-2", "문을 열어요."),
                                                   step("new-3", "포인트를 적립해요.", ids=())])]),
                  intent=EXCEPTIONS, text="문을 열어요.")
    assert "dropped_steps=1 dropped_in_kept_sections=1" in caplog.text


# --- interview flow: W1 end to end, W4(a) frozen correction evidence -----------------------------


@pytest.fixture
def drv(api, db_engine, tmp_path):
    set_media_storage(LocalMediaStorage(tmp_path / "media"))
    yield build_ctx(api, db_engine)
    set_media_storage(None)


def test_not_applicable_intent_review_has_no_sections(drv, fake_ai):
    sid = drv.started()
    for text in ("오픈조만 있어요.", "손님께 인사해요.", "오픈조는 문을 열어요."):
        drv.answer_and_run(sid, text)
    fake_ai.script("judge_sufficiency", FakeOutcome.predicates(0.1, not_applicable=0.95))
    drv.answer_and_run(sid, NA_ANSWER)  # RULES: the fake summary would write the answer as a step
    content = drv.review(sid, drv.intents[3]).json()["content"]
    assert content["sections"] == [] and content["shifts"] == [] and content["missingInformation"] == []
    assert content["summary"]
    [call] = [c for c in fake_ai.calls_for("summarize_intent") if c.data["intent"]["key"] == "RULES"]
    assert call.data["not_applicable"] is True
    others = [c for c in fake_ai.calls_for("summarize_intent") if c.data["intent"]["key"] != "RULES"]
    assert others and not any(c.data["not_applicable"] for c in others)


def test_correction_evidence_is_frozen_in_the_payload_and_reused_by_a_review_retry(drv, fake_ai):
    sid = drv.started()
    drv.answer_and_run(sid, "오픈조는 9시에 시작해요.")
    revision = drv.review(sid, drv.intents[0]).json()["revision"]
    fake_ai.script("revise_structure", *[FakeOutcome.fail("timeout")] * 3)
    accepted = drv.post(drv.review_url(sid, drv.intents[0], "corrections"),
                        {"expectedRevision": revision, "input": {"method": "TEXT", "text": "오픈조는 8시에 시작해요."}})
    assert accepted.status_code == 202, accepted.text
    with Session(drv.engine) as db:
        [task] = db.scalars(select(BackgroundTask).where(BackgroundTask.kind == "REVIEW_CORRECTION"))
        frozen = task.payload["evidence"]
    assert any(chunk["text"] == "오픈조는 8시에 시작해요." for chunk in frozen)
    drv.run()
    review = drv.review(sid, drv.intents[0]).json()
    assert review["status"] == "ERROR"
    # Answers given after acceptance cannot change it: the retry re-enqueues the same payload.
    retried = drv.post(drv.review_url(sid, drv.intents[0], "retries"), {"expectedRevision": review["revision"]})
    assert retried.status_code == 202, retried.text
    drv.run()
    calls = fake_ai.calls_for("revise_structure")
    assert len(calls) == 4 and all(call.data["evidence"] == frozen for call in calls)


# --- W4(b): draft corrections are grounded on the instruction -----------------------------------


def test_instruction_evidence_is_stable_sentence_chunks():
    cid = str(uuid.uuid4())
    chunks = instruction_evidence(cid, "야간조는 6시에 끝나요. 명찰을 달아요.")
    assert [(c.id, c.intent_key, c.text) for c in chunks] == [
        (f"{cid}#1", "DRAFT_CORRECTION", "야간조는 6시에 끝나요."), (f"{cid}#2", "DRAFT_CORRECTION", "명찰을 달아요.")]
    assert instruction_evidence(cid, "야간조는 6시에 끝나요. 명찰을 달아요.") == chunks


def test_draft_correction_cites_the_instruction_and_fails_on_uncited_new_steps(api, db_engine, fake_ai):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW)
        version = make_ready_draft(db, store, sample_content())
        db.commit()
        ctx = Ctx()
        ctx.__dict__.update(api=api, engine=db_engine, store=store.id, draft=version.id, owner=owner.id)
    ctx.auth = login(api, ctx.owner)

    raw = structure_to_raw(snapshot(ctx))
    for item in raw["sections"][0]["steps"]:
        item["evidence_ids"] = []  # existing, unchanged: no citation needed
    uncited = {"ref": "new-1", "instruction": "명찰을 달아요.", "checklist_item": False, "evidence_ids": []}
    raw["sections"][0]["steps"].append({"ref": "new-2", "instruction": "앞치마를 입어요.", "checklist_item": False,
                                        "evidence_ids": ["PLACEHOLDER"]})
    before = draft(ctx)
    cited_only = False

    def respond(data):
        [chunk] = data["evidence"]
        output = json.loads(json.dumps(raw))
        output["sections"][0]["steps"][-1]["evidence_ids"] = [chunk["id"]]
        if not cited_only:  # an uncited new step would be dropped: the whole output is retried
            output["sections"][0]["steps"].insert(-1, uncited)
        return {"outcome": "APPLIED", "summary": None, "structure": output}

    fake_ai.on("revise_structure", respond)
    response = correct(ctx, text="앞치마를 입어요.")
    assert response.status_code == 202, response.text
    correction_id = response.json()["id"]
    for minutes in (0, 5, 10):
        drain(now=utcnow() + timedelta(minutes=minutes))
    calls = fake_ai.calls_for("revise_structure")
    assert len(calls) == 3  # every automatic attempt failed; none wrote anything
    assert [(c["id"], c["intent_key"], c["text"]) for c in calls[0].data["evidence"]] == [
        (f"{correction_id}#1", "DRAFT_CORRECTION", "앞치마를 입어요.")]
    with Session(db_engine) as db:
        row = db.get(ManualDraftCorrection, correction_id)
        assert (row.status, row.error_code, row.result_revision) == ("ERROR", "AI_PROCESSING_FAILED", None)
    failed = draft(ctx)
    assert failed["latestCorrection"]["status"] == "ERROR"
    assert {k: v for k, v in failed.items() if k != "latestCorrection"} == {
        k: v for k, v in before.items() if k != "latestCorrection"}  # content, revision, photos

    cited_only = True
    retried = retry(ctx, correction_id, revision=before["revision"])
    assert retried.status_code == 202, retried.text
    drain()
    with Session(db_engine) as db:
        row = db.get(ManualDraftCorrection, correction_id)
        assert (row.status, row.result_revision) == ("SUCCEEDED", before["revision"] + 1)
    after = draft(ctx)
    assert [s["instruction"] for s in after["content"]["sections"][0]["steps"]] == [
        *[s["instruction"] for s in before["content"]["sections"][0]["steps"]], "앞치마를 입어요."]
    assert after["content"]["sections"][1:] == before["content"]["sections"][1:]

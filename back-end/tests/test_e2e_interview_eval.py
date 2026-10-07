"""The manual interview evaluation (e2e.interview_eval): persona, live-op presets, call trace, report.

Offline: the persona and the fake Jev, the trace through the real provider validation (grounding
counts included), the report on synthetic records. The full run against a real uvicorn server
needs MySQL (`test_interview_evaluation_end_to_end_on_the_fake`).
"""
import json
import logging
import os
import socket
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.ai.contracts import (
    DialogueTurn,
    EvidenceChunk,
    IntentBrief,
    IntentSummaryRequest,
    QuestionRequest,
    SufficiencyRequest,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.interview.question_set import INTENTS_V1
from e2e import ai_scenario, interview_eval, interview_report, owner_persona
from e2e.demo_scenario import StepFailed

KEYS = [definition.key for definition in INTENTS_V1]
BRIEFS = {d.key: IntentBrief(key=d.key, stage=d.stage, base_question=d.base_question,
                             coverage_criteria=d.coverage_criteria) for d in INTENTS_V1}


# -- persona ---------------------------------------------------------------------------------


def test_every_question_set_intent_has_a_script():
    assert set(owner_persona.ANSWERS) == set(KEYS) == set(owner_persona.COMPLETE_FROM)


@pytest.mark.parametrize("key", KEYS)
def test_answers_are_deterministic_and_repeat_the_last_past_the_script(key):
    for depth in range(8):
        assert owner_persona.answer(key, depth) == owner_persona.answer(key, depth)
    script = owner_persona.ANSWERS[key]
    assert owner_persona.answer(key, 7) == script[-1]


@pytest.mark.parametrize("key", [k for k in KEYS if owner_persona.COMPLETE_FROM[k]])
def test_intents_start_vague_and_turn_specific(key):
    start = owner_persona.COMPLETE_FROM[key]
    assert not owner_persona.covered(key, owner_persona.answer(key, start - 1))
    assert all(owner_persona.covered(key, owner_persona.answer(key, d)) for d in range(start, 7))
    assert len(owner_persona.answer(key, start)) > len(owner_persona.answer(key, 0))


def test_rules_is_not_applicable_at_every_depth():
    assert {owner_persona.answer("RULES", d) for d in range(6)} == {owner_persona.NOT_APPLICABLE}
    assert "해당 없음" in owner_persona.NOT_APPLICABLE
    assert owner_persona.expected_depth("RULES") == 0


def test_the_vague_intent_never_covers_unless_depth_five_is_skipped():
    key = owner_persona.NEEDS_DETAIL
    vague = [owner_persona.answer(key, d) for d in range(6)]
    assert len(set(vague)) == 6 and not any(owner_persona.covered(key, text) for text in vague)
    assert owner_persona.expected_depth(key) == 5
    assert owner_persona.answer(key, 0, skip_depth_five=True) == vague[0]
    assert owner_persona.answer(key, 1, skip_depth_five=True) == owner_persona.EXCEPTIONS_SPECIFIC
    assert owner_persona.covered(key, owner_persona.EXCEPTIONS_SPECIFIC)
    assert owner_persona.expected_depth(key, skip_depth_five=True) == 1
    # skipping changes nothing for the other intents
    assert all(owner_persona.answer(k, d, skip_depth_five=True) == owner_persona.answer(k, d)
               for k in KEYS if k != key for d in range(6))


def test_unknown_intent_and_bad_depth():
    assert owner_persona.answer("NEW_INTENT", 0) == owner_persona.FALLBACK
    assert not owner_persona.covered("NEW_INTENT", owner_persona.FALLBACK)
    assert owner_persona.expected_depth("NEW_INTENT") == 5
    with pytest.raises(ValueError):
        owner_persona.answer("RULES", -1)


def test_expected_and_worst_case_call_counts():
    full = owner_persona.expected_calls(KEYS)
    # questions: WORK 2 + COMMON 3 + SHIFT 2 + RULES 1 + EQUIPMENT 3 + EXCEPTIONS 6 = 17
    assert full == {"generate_question": 17, "judge_sufficiency": 17, "summarize_intent": 6,
                    "revise_structure": 1, "compose_draft": 1}
    skipped = owner_persona.expected_calls(KEYS, skip_depth_five=True)
    assert sum(full.values()) == 42 and sum(skipped.values()) == 34
    assert owner_persona.worst_case_calls(6) == 80 == interview_eval.DEFAULT_CALL_LIMIT
    assert sum(full.values()) <= interview_eval.DEFAULT_CALL_LIMIT


def walk(fake, key, *, skip=False):
    """Depth an intent ends at under the fake Jev, as the server would (insufficient -> probe)."""
    dialogue = []
    for depth in range(6):
        dialogue.append(DialogueTurn(question="질문", answer=owner_persona.answer(key, depth, skip_depth_five=skip),
                                     depth=depth))
        judged = fake.judge_sufficiency(SufficiencyRequest(intent=BRIEFS[key], dialogue=tuple(dialogue), depth=depth))
        if judged.sufficient:
            return depth, judged
    return 5, judged


@pytest.mark.parametrize("skip", [False, True])
@pytest.mark.parametrize("key", KEYS)
def test_fake_jev_follows_the_persona_through_real_validation(key, skip):
    fake = owner_persona.install_fake(FakeAiProvider())
    depth, judged = walk(fake, key, skip=skip)
    assert depth == owner_persona.expected_depth(key, skip_depth_five=skip)
    if not owner_persona.covered(key, owner_persona.answer(key, depth, skip_depth_five=skip)):
        assert not judged.sufficient and judged.missing_aspects  # real aspect labels
        assert all("의 " in label for label in judged.missing_aspects)


# -- live ops presets ------------------------------------------------------------------------


@pytest.mark.parametrize(("spec", "expected"), [
    (None, ai_scenario.DEFAULT_LIVE_OPS),
    ("", ai_scenario.DEFAULT_LIVE_OPS),
    (" , ", ai_scenario.DEFAULT_LIVE_OPS),
    ("default", ai_scenario.DEFAULT_LIVE_OPS),
    ("interview", ai_scenario.INTERVIEW_OPS),
    ("INTERVIEW", ai_scenario.INTERVIEW_OPS),
    ("all", ai_scenario.OPERATIONS),
    ("judge_sufficiency,interview", ai_scenario.INTERVIEW_OPS),
    ("interview,answer_question,transcribe,judge_sufficiency", ai_scenario.INTERVIEW_OPS
     + ("answer_question", "transcribe")),
    ("transcribe", ("transcribe",)),
])
def test_resolve_live_ops(spec, expected):
    assert ai_scenario.resolve_live_ops(spec) == expected


def test_interview_preset_is_every_interview_operation():
    assert set(ai_scenario.INTERVIEW_OPS) == {"judge_sufficiency", "generate_question", "summarize_intent",
                                              "revise_structure", "compose_draft"}
    assert set(ai_scenario.DEFAULT_LIVE_OPS) - set(ai_scenario.INTERVIEW_OPS) == {"answer_question", "transcribe"}


def test_unknown_names_survive_resolution_and_are_refused(tmp_path):
    ops = ai_scenario.resolve_live_ops("interview,judge")
    assert "judge" in ops
    with pytest.raises(ValueError):
        ai_scenario.RoutedAiProvider(FakeAiProvider(), FakeAiProvider(), ops, ai_scenario.CallBudget(1, None))


# -- call trace ------------------------------------------------------------------------------


class Live(FakeAiProvider):
    provider_name, model = "openai", "gpt-6-luna"


def traced(tmp_path, ops=ai_scenario.INTERVIEW_OPS, limit=50, live=None):
    path = tmp_path / "trace.jsonl"
    live = live or owner_persona.install_fake(Live())
    provider = ai_scenario.RoutedAiProvider(FakeAiProvider(), live, ops, ai_scenario.CallBudget(limit, None),
                                            ai_scenario.CallTrace(str(path)))
    return provider, live, path


def test_trace_records_judgements_and_questions_without_text(tmp_path):
    provider, _live, path = traced(tmp_path)
    vague = owner_persona.answer("WORK_STRUCTURE", 0)
    turn = DialogueTurn(question="근무조는요?", answer=vague, depth=0)
    provider.judge_sufficiency(SufficiencyRequest(intent=BRIEFS["WORK_STRUCTURE"], dialogue=(turn,), depth=0))
    provider.generate_question(QuestionRequest(kind="PROBE", intent=BRIEFS["WORK_STRUCTURE"], depth=1,
                                               dialogue=(turn,), missing_aspects=("근무조의 시작 시각",)))
    judged, asked = ai_scenario.read_trace(path)
    assert judged["op"] == "judge_sufficiency" and judged["live"] is True and judged["outcome"] == "ok"
    assert (judged["intent"], judged["depth"], judged["sufficient"]) == ("WORK_STRUCTURE", 0, False)
    assert judged["probability"] == pytest.approx(0.18) and judged["missing_aspects"]
    assert judged["config"].startswith("openai:gpt-6-luna:") and judged["ms"] >= 0
    assert (asked["op"], asked["kind"], asked["depth"]) == ("generate_question", "PROBE", 1)
    raw = path.read_text(encoding="utf-8")
    assert vague not in raw and "근무조는요?" not in raw  # no owner answer or question text


def test_trace_records_failures_and_fake_calls(tmp_path):
    live = Live().script("judge_sufficiency", FakeOutcome.fail(AiErrorCode.TIMEOUT))
    provider, _live, path = traced(tmp_path, ops=("judge_sufficiency",), live=live)
    request = SufficiencyRequest(intent=BRIEFS["RULES"], dialogue=(DialogueTurn(question="q", answer="a", depth=0),),
                                 depth=0)
    with pytest.raises(AiError):
        provider.judge_sufficiency(request)
    provider.generate_question(QuestionRequest(kind="BASE", intent=BRIEFS["RULES"], depth=0))
    failed, fake = ai_scenario.read_trace(path)
    assert (failed["outcome"], failed["live"]) == ("timeout", True) and "sufficient" not in failed
    assert (fake["op"], fake["live"], fake["outcome"]) == ("generate_question", False, "ok")


def test_budget_refusal_is_not_traced_as_a_call(tmp_path):
    provider, _live, path = traced(tmp_path, ops=("generate_question",), limit=0)
    with pytest.raises(AiError) as refused:
        provider.generate_question(QuestionRequest(kind="BASE", intent=BRIEFS["RULES"], depth=0))
    assert refused.value.code == AiErrorCode.NOT_CONFIGURED
    assert ai_scenario.read_trace(path) == []


EVIDENCE = (EvidenceChunk(id="t1#1", intent_key="COMMON_TASKS", question="공통 업무는요?", text="주문을 받아요."),)


def test_trace_counts_steps_dropped_for_missing_evidence(tmp_path):
    """Real grounding (app.ai.validation) drops the uncited step; the provider logs counts only."""
    structure = {"shifts": [], "missing_information": [], "sections": [{
        "ref": "new-1", "category": "COMMON_TASK", "shift_ref": None, "title": "주문", "steps": [
            {"ref": "new-2", "instruction": "주문을 받아요.", "checklist_item": False, "evidence_ids": ["t1#1"]},
            {"ref": "new-3", "instruction": "지어낸 단계", "checklist_item": False, "evidence_ids": []}]}]}
    live = Live().script("summarize_intent", FakeOutcome.ok({"summary": "주문을 받아요.", "structure": structure}))
    provider, _live, path = traced(tmp_path, ops=("summarize_intent",), live=live)
    summary = provider.summarize_intent(IntentSummaryRequest(
        intent=BRIEFS["COMMON_TASKS"], dialogue=(DialogueTurn(question="q", answer="주문을 받아요.", depth=0),),
        needs_detail=False, evidence=EVIDENCE))
    assert [s.instruction for s in summary.structure.sections[0].steps] == ["주문을 받아요."]
    [entry] = ai_scenario.read_trace(path)
    assert (entry["dropped_steps"], entry["cleared_shifts"], entry["evidence"]) == (1, 0, 1)
    assert "지어낸 단계" not in path.read_text(encoding="utf-8")


def test_grounding_logs_outside_a_traced_call_are_ignored(tmp_path):
    traced(tmp_path)  # installs the handler
    logging.getLogger("jidan.ai").info("ai grounding op=%s dropped_steps=%d cleared_shifts=%d", "x", 3, 1)
    logging.getLogger("jidan.ai").info("ai grounding malformed")


def test_untraced_router_passes_calls_through(tmp_path):
    provider = ai_scenario.RoutedAiProvider(FakeAiProvider(), Live(), ("generate_question",),
                                            ai_scenario.CallBudget(5, None))
    assert provider.trace.path is None
    assert provider.generate_question(QuestionRequest(kind="BASE", intent=BRIEFS["RULES"], depth=0)).text
    assert list(tmp_path.iterdir()) == []


class StubClient:
    """An OpenAI client double: usage on a Responses result and on a Decisions body."""

    def __init__(self):
        self.responses = SimpleNamespace(create=lambda **_: SimpleNamespace(usage=SimpleNamespace(
            model_dump=lambda: {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140,
                                "output_tokens_details": {"reasoning_tokens": 30}, "flag": True})))
        self.audio = "audio-api"

    def post(self, *_args, **_kwargs):
        return SimpleNamespace(content=json.dumps({"answers": [], "usage": {"input_tokens": 50,
                                                                            "output_tokens": 0}}).encode())


def test_usage_client_records_token_numbers_only(tmp_path):
    backend = SimpleNamespace(_client=StubClient())
    ai_scenario.count_usage(backend)
    ai_scenario.count_usage(backend)  # idempotent: not wrapped twice
    client = backend._client
    assert isinstance(client, ai_scenario._UsageClient) and not isinstance(client._client, ai_scenario._UsageClient)
    assert client.audio == "audio-api" and "StubClient" not in repr(client)
    trace = ai_scenario.CallTrace(str(tmp_path / "t.jsonl"))

    def call():
        client.responses.create(model="m")
        client.post("/decisions")
        return SimpleNamespace(meta=None)

    trace.run("compose_draft", SimpleNamespace(), True, call)
    [entry] = ai_scenario.read_trace(tmp_path / "t.jsonl")
    assert entry["usage"] == {"input_tokens": 150, "output_tokens": 40, "total_tokens": 140,
                              "output_tokens_details.reasoning_tokens": 30}
    client.responses.create(model="m")  # outside a traced call: nothing to add to, no error


def test_usage_numbers_edge_cases():
    assert ai_scenario.usage_numbers(None) == {}
    assert ai_scenario.usage_numbers(object()) == {}
    assert ai_scenario.usage_numbers({"a": 1, "b": "x", "c": True, "d": {"e": 2, "f": None}}) == {"a": 1, "d.e": 2}


def test_build_from_env_traces_the_persona_fake(monkeypatch, tmp_path):
    for name in ("E2E_AI_LIVE_OPS", "E2E_AI_COUNTER_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("E2E_AI", "fake")
    monkeypatch.setenv("E2E_AI_SCRIPT", "persona")
    monkeypatch.setenv("E2E_AI_TRACE_FILE", str(tmp_path / "trace.jsonl"))
    provider = ai_scenario.build_from_env()
    assert isinstance(provider, ai_scenario.RoutedAiProvider) and not provider.live_ops
    depth, _ = walk(provider, "COMMON_TASKS")
    assert depth == 2
    assert [e["live"] for e in ai_scenario.read_trace(tmp_path / "trace.jsonl")] == [False] * 3
    monkeypatch.setenv("E2E_AI_SCRIPT", "other")
    with pytest.raises(ValueError):
        ai_scenario.build_from_env()
    monkeypatch.setenv("E2E_AI_SCRIPT", "")
    monkeypatch.delenv("E2E_AI_TRACE_FILE")
    assert isinstance(ai_scenario.build_from_env(), FakeAiProvider)


def test_build_from_env_live_uses_presets(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_AI", "live")
    monkeypatch.setenv("AI_PROVIDER", "fake")  # the "live" side without a key
    monkeypatch.setenv("E2E_AI_LIVE_OPS", "interview")
    monkeypatch.setenv("E2E_AI_CALL_LIMIT", "3")
    monkeypatch.setenv("E2E_AI_TRACE_FILE", str(tmp_path / "trace.jsonl"))
    monkeypatch.delenv("E2E_AI_SCRIPT", raising=False)
    provider = ai_scenario.build_from_env()
    assert provider.live_ops == frozenset(ai_scenario.INTERVIEW_OPS) and provider.budget.limit == 3


# -- report ----------------------------------------------------------------------------------


def turn(key, depth, kind=None, question=None):
    return {"intent": key, "kind": kind or ("BASE" if depth == 0 else "PROBE"), "depth": depth,
            "question": question or f"{key} 질문 {depth}", "guidance": None,
            "answer": owner_persona.answer(key, depth)}


def judge_entry(key, depth, sufficient, probability, missing=(), **extra):
    return {"op": "judge_sufficiency", "live": True, "intent": key, "depth": depth, "outcome": "ok",
            "sufficient": sufficient, "probability": probability, "missing_aspects": list(missing), "ms": 900,
            "config": "openai:gpt-6-luna:p:decisions", "usage": {"input_tokens": 1000, "output_tokens": 0}, **extra}


def sample_record(**overrides):
    content = {"summary": "두 조로 일해요.", "needsDetail": False, "structurePhotos": [], "missingInformation": [],
               "shifts": [{"id": "h1", "name": "오픈조", "startTime": "07:00", "endTime": "15:00",
                           "endsNextDay": False}],
               "sections": [{"id": "s1", "category": "SHIFT_TASK", "shiftId": "h1", "title": "오픈 | 준비",
                             "steps": [{"id": "p1", "instruction": "불을 켜요.\n머신을 켜요.", "checklistItem": True}],
                             "photos": []}]}
    record = {
        "run": "abcd1234", "mode": "live", "skip_depth_five": False, "started_at": "2026-10-08T00:00:00+00:00",
        "wall_seconds": 321.0, "live_ops": list(ai_scenario.INTERVIEW_OPS), "call_limit": 80,
        "calls": {"limit": 80, "calls": {"judge_sufficiency": 3}, "refused": 0},
        "config": {"OPENAI_MODEL": "gpt-6-luna"},
        "intents": [{"id": "i1", "key": "WORK_STRUCTURE"}, {"id": "i2", "key": "RULES"},
                    {"id": "i3", "key": "EXCEPTIONS"}],
        "turns": [turn("WORK_STRUCTURE", 0), turn("WORK_STRUCTURE", 1), turn("RULES", 0)]
        + [turn("EXCEPTIONS", d) for d in range(6)],
        "trace": [
            {"op": "generate_question", "live": True, "intent": "WORK_STRUCTURE", "depth": 0, "kind": "BASE",
             "outcome": "ok", "ms": 2500, "usage": {"input_tokens": 500, "output_tokens": 200,
                                                     "output_tokens_details.reasoning_tokens": 100}},
            judge_entry("WORK_STRUCTURE", 0, False, 0.2, ["근무조의 시작 시각"]),
            judge_entry("WORK_STRUCTURE", 1, True, 0.95),
            judge_entry("RULES", 0, True, 0.9),
            *[judge_entry("EXCEPTIONS", d, False, 0.1, ["돌발 상황의 대응 방법"]) for d in range(6)],
            {"op": "summarize_intent", "live": True, "intent": "RULES", "outcome": "ok", "ms": 30000,
             "evidence": 4, "dropped_steps": 2, "cleared_shifts": 1},
            {"op": "compose_draft", "live": True, "outcome": "invalid_output", "ms": 40000, "evidence": 10},
            {"op": "compose_draft", "live": True, "outcome": "ok", "ms": 50000, "evidence": 10},
        ],
        "reviews": {"WORK_STRUCTURE": {"status": "READY", "content": content, "error": None},
                    "EXCEPTIONS": {"status": "READY", "error": None,
                                   "content": {**content, "needsDetail": True, "sections": [], "shifts": [],
                                               "missingInformation": [{"target": "MANUAL", "field": "content",
                                                                       "description": "대응 방법 미확정"}]}}},
        "correction": {"intent": "WORK_STRUCTURE", "instruction": owner_persona.REVIEW_CORRECTION, "status": "READY",
                       "before": content["shifts"], "after": [{**content["shifts"][0], "endTime": "23:00"}]},
        "draft": {"generationStatus": "READY", "revision": 1, "content": content,
                  "issues": [{"status": "OPEN", "description": "예외 상황 정보 부족", "intentId": "i3"}]},
        "results": [("PASS", "health", "0.1s"), ("PASS", "E5 draft generation", "60.0s")],
        "price_per_1m": None,
    }
    record.update(overrides)
    return record


def test_report_has_every_section_and_the_per_intent_details():
    text = interview_report.render(sample_record())
    for heading in ("## 설정", "## 단계 결과", "## 자동 관찰 포인트", "## 인텐트별 질문·답변·판단", "### WORK_STRUCTURE",
                    "### RULES", "### EXCEPTIONS", "## 검토 요약", "## 검토 정정", "## 최종 초안",
                    "## 근거(그라운딩) 통계", "## 지연 시간·호출 수·토큰"):
        assert heading in text
    assert "결과 | PASS (2/2 단계)" in text
    assert owner_persona.answer("WORK_STRUCTURE", 0) in text and "WORK_STRUCTURE 질문 1" in text
    assert "불충분 p=0.20 · 부족: 근무조의 시작 시각" in text and "충분 p=0.95" in text
    assert "도달 깊이 **1** (페르소나 기대 1) · 결과 **충분**" in text
    assert "도달 깊이 **0** (페르소나 기대 0) · 결과 **충분**" in text  # RULES 해당 없음
    assert "도달 깊이 **5** (페르소나 기대 5) · 결과 **NEEDS_DETAIL**" in text
    assert "| summarize_intent | 1 | 4.0 | 2 | 1 |" in text
    assert "| compose_draft | 2 | 10.0 | 0 | 0 |" in text
    assert "| 오픈조 | 07:00 | 23:00 | False |" in text  # after the correction
    assert "오픈 | 준비" in text and "1. 불을 켜요." in text
    assert "[OPEN] 예외 상황 정보 부족" in text and "미확정 정보 1건" in text
    assert "실호출 합계 13회" in text and "input_tokens 9500" in text
    assert "`openai:gpt-6-luna:p:decisions`" in text


def test_report_observations_flag_judgement_problems():
    trace = sample_record()["trace"]
    trace[1] = judge_entry("WORK_STRUCTURE", 0, True, 0.8)  # vague answer accepted
    trace[3] = judge_entry("RULES", 0, False, 0.3, ["매장 규칙의 내용"])  # 해당 없음 rejected
    turns = sample_record()["turns"]
    turns[1]["question"] = turns[0]["question"]  # the probe repeats the base question
    notes = interview_report.observations(sample_record(trace=trace, turns=turns))
    joined = "\n".join(notes)
    assert "WORK_STRUCTURE depth 0: 모호하게 쓴 답을 충분으로 판단(p=0.80)" in joined
    assert "RULES depth 0: 구체적으로 쓴 답을 불충분으로 판단 (부족: 매장 규칙의 내용)" in joined
    assert "WORK_STRUCTURE: 같은 질문 문장이 반복됨" in joined
    assert "근거 없는 내용 제거: 단계 2개, 근무조 시간 1개" in joined
    assert "실패한 AI 호출: compose_draft:invalid_output x1" in joined
    assert "EXCEPTIONS 검토: 근무조·섹션이 비어 있음" in joined


def test_report_flags_a_not_applicable_intent_that_wrote_sections():
    section = {"id": "r1", "category": "RULE", "shiftId": "elsewhere", "title": "매장 규칙",
               "steps": [{"id": "x", "instruction": "따로 정해 둔 매장 규칙은 없어요.", "checklistItem": False}]}
    reviews = {"RULES": {"status": "READY", "error": None,
                         "content": {"summary": "없음", "shifts": [], "sections": [section], "needsDetail": False}}}
    record = sample_record(reviews=reviews)
    assert "RULES 검토: '해당 없음' 답인데 섹션 1개를 만듦 (초안에 그대로 실림)" in interview_report.observations(record)
    assert "(RULE · 다른 검토의 근무조)" in interview_report.render(record)
    empty = {"RULES": {**reviews["RULES"], "content": {**reviews["RULES"]["content"], "sections": [],
                                                       "missingInformation": []}}}
    assert not any("해당 없음" in n for n in interview_report.observations(sample_record(reviews=empty)))


def test_report_flags_depth_mismatch_and_review_errors():
    turns = [turn("WORK_STRUCTURE", d) for d in range(4)]
    reviews = {"WORK_STRUCTURE": {"status": "ERROR", "error": {"code": "AI_PROCESSING_FAILED"}, "content": None}}
    notes = interview_report.observations(sample_record(turns=turns, reviews=reviews, trace=[]))
    assert "WORK_STRUCTURE: 도달 깊이 3 ≠ 페르소나 기대 1 (더 캐물음)" in notes
    assert any(n.startswith("WORK_STRUCTURE 검토: ERROR") for n in notes)


def test_report_of_a_run_that_failed_early():
    record = {"run": "x", "mode": "live", "results": [("FAIL", "owner sign-up", "boom | bad\nline"),
                                                      ("SKIP", "E1 interview start", "")]}
    text = interview_report.render(record)
    assert "FAIL (0/2 단계)" in text and "boom \\| bad<br>line" in text
    assert "(초안 없음)" in text and "실호출 합계 0회" in text


def test_report_cost_estimate_only_with_a_price():
    assert "추정 비용" not in interview_report.render(sample_record())
    text = interview_report.render(sample_record(price_per_1m=(1.0, 10.0)))
    # input 9500, output 200 -> 0.0095 + 0.002
    assert "추정 비용: $0.0115" in text


def test_report_never_carries_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    scenario = interview_eval.InterviewEval("http://127.0.0.1:9", "http://localhost:5173", "admin-pass", ai="live")
    record = interview_eval.build_record(scenario, trace=[], calls={}, live_ops=[], call_limit=80,
                                         started_at="t", wall_seconds=1)
    assert record["config"] == {"OPENAI_MODEL": "gpt-6-luna"}
    text = interview_report.render(record)
    assert "sk-test-secret-value" not in text and "admin-pass" not in text


@pytest.mark.parametrize(("raw", "expected"), [("", None), ("1,2", (1.0, 2.0)), ("x,1", None), ("1", None),
                                               ("-1,2", None), ("0.5, 4", (0.5, 4.0))])
def test_price_from_env(monkeypatch, raw, expected):
    monkeypatch.setenv("E2E_AI_PRICE_PER_1M", raw)
    assert interview_eval.price_from_env() == expected


def test_cell_escapes_table_breakers():
    assert interview_report.cell("a|b\nc\r\\") == "a\\|b<br>c \\\\"
    assert interview_report.cell(None) == ""


# -- runner ----------------------------------------------------------------------------------


def scenario_with(turns):
    scenario = interview_eval.InterviewEval("http://127.0.0.1:9", "http://localhost:5173", "pw")
    scenario.record["turns"] = turns
    return scenario


ORDER = ["WORK_STRUCTURE", "COMMON_TASKS", "RULES"]


@pytest.mark.parametrize(("turns", "question"), [
    ([], {"kind": "PROBE", "depth": 1}),  # an intent must open with BASE
    ([turn("WORK_STRUCTURE", 0)], {"kind": "PROBE", "depth": 2}),  # depth skipped
    ([turn("WORK_STRUCTURE", 0)], {"kind": "BASE", "depth": 0}),  # BASE again inside an intent
])
def test_progress_check_refuses_broken_structure(turns, question):
    with pytest.raises(StepFailed):
        scenario_with(turns)._check_progress("WORK_STRUCTURE", question, ORDER)


def test_progress_check_refuses_out_of_order_and_revisited_intents():
    with pytest.raises(StepFailed):
        scenario_with([])._check_progress("COMMON_TASKS", {"kind": "BASE", "depth": 0}, ORDER)
    with pytest.raises(StepFailed):
        scenario_with([turn("WORK_STRUCTURE", 0), turn("COMMON_TASKS", 0)])._check_progress(
            "WORK_STRUCTURE", {"kind": "BASE", "depth": 0}, ORDER)
    with pytest.raises(StepFailed):
        scenario_with([turn("WORK_STRUCTURE", d) for d in range(6)])._check_progress(
            "WORK_STRUCTURE", {"kind": "PROBE", "depth": 6}, ORDER)


def test_progress_check_accepts_the_normal_path():
    scenario_with([])._check_progress("WORK_STRUCTURE", {"kind": "BASE", "depth": 0}, ORDER)
    scenario_with([turn("WORK_STRUCTURE", 0)])._check_progress("WORK_STRUCTURE", {"kind": "PROBE", "depth": 1}, ORDER)
    scenario_with([turn("WORK_STRUCTURE", 0)])._check_progress("COMMON_TASKS", {"kind": "BASE", "depth": 0}, ORDER)


def test_eval_steps_are_the_short_flow():
    methods = [m for _label, m in scenario_with([]).steps()]
    assert methods[:3] == ["step_health", "step_owner_signup", "step_admin_approval"]
    assert methods[3:] == ["step_e1_start", "step_e2_answers", "step_e3_reviews", "step_e4_correction",
                           "step_e5_draft"]


@pytest.mark.parametrize("env", [{}, {"JIDAN_E2E_OPENAI": "1"}, {"OPENAI_API_KEY": "sk-test"}])
def test_live_run_needs_the_opt_in_and_a_key(monkeypatch, capsys, env):
    for name in ("JIDAN_E2E_OPENAI", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert interview_eval.main(["--ai", "live"]) == 2
    assert "JIDAN_E2E_OPENAI=1" in capsys.readouterr().err


def test_unsafe_database_and_unknown_ops_are_refused(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan")
    assert interview_eval.main(["--ai", "fake"]) == 2
    assert "refused" in capsys.readouterr().err
    monkeypatch.setenv("DB_NAME", "jidan_e2e_test")
    monkeypatch.setenv("JIDAN_E2E_OPENAI", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert interview_eval.main(["--ai", "live", "--ai-live-ops", "interview,judge"]) == 2
    assert "unknown AI operations" in capsys.readouterr().err


def test_default_report_path_is_gitignored():
    path = interview_eval.default_report_path("abcd")
    assert path.parent == interview_eval.REPORT_DIR and path.name.endswith("-abcd.md")
    assert interview_eval.REPORT_DIR.name == ".e2e-reports"
    # The Docker test image copies the code without repository metadata, so there is nothing to
    # ignore there; in a checkout the reports must stay out of git (skips fail the E2E gate).
    gitignore = interview_eval.BACK_END / ".gitignore"
    if gitignore.exists():
        assert ".e2e-reports/" in gitignore.read_text().splitlines()


def test_env_file_loads_only_openai_names_silently(monkeypatch, tmp_path, capsys):
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_EMPTY", "DB_PASSWORD_FROM_FILE"):
        monkeypatch.setenv(name, "x")  # recorded, so the loader's values are undone afterwards
        monkeypatch.delenv(name)
    env = tmp_path / "api.env"
    env.write_text("# comment\nexport OPENAI_API_KEY='sk-file-secret'\nOPENAI_MODEL = \"gpt-6-luna\"\n"
                   "OPENAI_EMPTY=\nDB_PASSWORD_FROM_FILE=nope\nnot a line\nOPENAI_BAD-NAME=x\n", encoding="utf-8")
    assert interview_eval.load_env_file(str(env)) == ["OPENAI_API_KEY", "OPENAI_MODEL"]
    assert os.environ["OPENAI_API_KEY"] == "sk-file-secret" and os.environ["OPENAI_MODEL"] == "gpt-6-luna"
    assert "DB_PASSWORD_FROM_FILE" not in os.environ and "OPENAI_EMPTY" not in os.environ
    out = capsys.readouterr()
    assert "sk-file-secret" not in out.out + out.err


def test_unreadable_env_file_is_refused(capsys, tmp_path):
    assert interview_eval.main(["--env-file", str(tmp_path / "missing.env"), "--ai", "live"]) == 2
    assert "--env-file cannot be read: FileNotFoundError" in capsys.readouterr().err


# -- the whole run (real server, MySQL, fake AI with the persona's Jev) --------------------------


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
@pytest.mark.parametrize("skip", [False, True])
def test_interview_evaluation_end_to_end_on_the_fake(db_engine, monkeypatch, tmp_path, skip):
    from tests.interview_factories import ensure_question_set

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("KAKAO_REST_API_KEY", raising=False)
    with Session(db_engine) as db:
        ensure_question_set(db)
        db.commit()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    report = tmp_path / "report.md"
    argv = ["--ai", "fake", "--port", str(port), "--report", str(report)] + (["--skip-depth5"] if skip else [])
    assert interview_eval.main(argv) == 0
    text = report.read_text(encoding="utf-8")
    assert "결과 | PASS (8/8 단계)" in text
    for key in KEYS:
        expected = owner_persona.expected_depth(key, skip_depth_five=skip)
        assert f"도달 깊이 **{expected}** (페르소나 기대 {expected})" in text
    assert ("결과 **NEEDS_DETAIL**" in text) is (not skip)
    # The fake asks the same probe wording again (a fake artefact); judgements must all agree.
    assert not any(flag in text for flag in ("모호하게 쓴 답", "구체적으로 쓴 답", "≠ 페르소나", "실패한 AI 호출",
                                              "근거 없는 내용 제거"))

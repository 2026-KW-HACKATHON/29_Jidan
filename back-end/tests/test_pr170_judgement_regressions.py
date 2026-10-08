"""PR #170: concurrent scripts, confirmation protocol and exact configuration metadata."""
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import openai
import pytest
from jsonschema import Draft202012Validator

from app.ai import build_provider_from_env
from app.ai.aspects import aspects_for
from app.ai.contracts import (
    MAX_EVIDENCE,
    DialogueTurn,
    EvidenceChunk,
    IntentBrief,
    SufficiencyRequest,
)
from app.ai.decisions import Thresholds, build_request
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.ai.provider import FallbackAiProvider
from app.ai.retrieval import retrieve
from app.ai.schemas import JUDGE_SCHEMA, RawJudgement
from app.interview.question_set import INTENTS_V1
from e2e.ai_scenario import CallBudget, RoutedAiProvider

V1 = {d.key: IntentBrief(key=d.key, stage=d.stage, base_question=d.base_question,
                         coverage_criteria=d.coverage_criteria) for d in INTENTS_V1}


def request(key="COMMON_TASKS", turns=None):
    return SufficiencyRequest(intent=V1[key], depth=0, dialogue=turns or (
        DialogueTurn(question="질문", answer="답변", depth=0),))


def response(*, na=0.0, confirmed=0.0, sufficient=True):
    return {"sufficient": sufficient, "probability": 0.9 if sufficient else 0.2,
            "missing_aspects": [] if sufficient else ["근무조의 구성"],
            "not_applicable_probability": na, "not_applicable_confirmed_probability": confirmed}


@pytest.mark.parametrize("first", [
    FakeOutcome.ok(response(sufficient=False)),
    FakeOutcome.delay(0.01, FakeOutcome.ok(response(sufficient=False))),
    FakeOutcome.raw("not json"),
    FakeOutcome.delay(0.01, FakeOutcome.raw("not json")),
])
def test_concurrent_mixed_outcomes_keep_their_reserved_backend(monkeypatch, first):
    fake = FakeAiProvider().script("judge_sufficiency", first, FakeOutcome.predicates(aspect_2=0.31))
    barrier = threading.Barrier(2)
    original = fake._judge_backend

    def select(req):
        backend = original(req)
        barrier.wait(timeout=5)  # both calls select before either transport consumes
        return backend

    monkeypatch.setattr(fake, "_judge_backend", select)

    def call():
        try:
            return fake.judge_sufficiency(request())
        except AiError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(), range(2)))
    assert fake.pending("judge_sufficiency") == 0
    assert len(fake.calls_for("judge_sufficiency")) == 2
    decisions = [r for r in results if not isinstance(r, AiError) and ":decisions:" in r.meta.config_version]
    assert len(decisions) == 1 and decisions[0].probability == 0.31
    if first.kind == "raw" or (first.kind == "delay" and first.then.kind == "raw"):
        assert [r.code for r in results if isinstance(r, AiError)] == [AiErrorCode.INVALID_OUTPUT]
    else:
        assert len([r for r in results if ":responses:" in r.meta.config_version]) == 1


def test_failure_before_transport_returns_the_unused_reservation(monkeypatch):
    fake = FakeAiProvider().script("judge_sufficiency", FakeOutcome.predicates(aspect_2=0.21))
    original = fake._judge_backend

    def fail(_req):
        raise AiError(AiErrorCode.UNAVAILABLE)

    monkeypatch.setattr(fake, "_judge_backend", fail)
    with pytest.raises(AiError):
        fake.judge_sufficiency(request())
    assert fake.pending("judge_sufficiency") == 1 and fake.calls == []
    monkeypatch.setattr(fake, "_judge_backend", original)
    assert fake.judge_sufficiency(request()).probability == 0.21
    assert fake.pending("judge_sufficiency") == 0


def test_timed_out_reserved_response_does_not_consume_the_next_decision():
    fake = FakeAiProvider(timeout_seconds=0.01, sleep=lambda _: None).script(
        "judge_sufficiency", FakeOutcome.delay(1, FakeOutcome.ok(response())),
        FakeOutcome.predicates(aspect_1=0.23))
    with pytest.raises(AiError) as caught:
        fake.judge_sufficiency(request())
    assert caught.value.code == AiErrorCode.TIMEOUT
    assert ":responses:" in fake.judge_meta().config_version
    assert fake.pending("judge_sufficiency") == 1
    assert fake.judge_sufficiency(request()).probability == 0.23


def test_failed_mixed_backends_clean_reservations_and_refresh_metadata_on_the_reused_thread():
    fake = FakeAiProvider().script(
        "judge_sufficiency", FakeOutcome.raw("not json"),
        FakeOutcome.predicates(refuse=("aspect_1", "aspect_2", "aspect_3", "not_applicable")),
        FakeOutcome.predicates(aspect_2=0.31))

    def call():
        try:
            result = fake.judge_sufficiency(request())
            value = result.probability
        except AiError as error:
            value = error.code
        assert fake._judgement.reservation is None
        return value, fake.judge_meta().config_version

    with ThreadPoolExecutor(max_workers=1) as pool:
        first, second, third = list(pool.map(lambda _: call(), range(3)))
    assert first[0] == AiErrorCode.INVALID_OUTPUT and ":responses:" in first[1]
    assert second[0] == AiErrorCode.REFUSED and ":decisions:" in second[1]
    assert third[0] == 0.31 and ":decisions:" in third[1]
    assert len(fake.calls) == 3 and fake.pending("judge_sufficiency") == 0


def test_fake_initial_metadata_uses_the_handler_backend():
    fake = FakeAiProvider().on("judge_sufficiency", lambda _data: response())
    assert ":responses:" in fake.judge_meta().config_version
    assert fake.calls == [] and fake.pending("judge_sufficiency") == 0


@pytest.mark.parametrize("wrapper", ["direct", "routed", "routed-live", "fallback"])
def test_new_evaluation_clears_previous_backend_before_request_validation(monkeypatch, wrapper):
    from app.interview import tasks

    fake = FakeAiProvider().script("judge_sufficiency", FakeOutcome.ok(response()))
    if wrapper == "routed":
        provider = RoutedAiProvider(fake, FakeAiProvider(), (), CallBudget(5, None))
    elif wrapper == "routed-live":
        provider = RoutedAiProvider(FakeAiProvider(), fake, ("judge_sufficiency",), CallBudget(5, None))
    elif wrapper == "fallback":
        provider = FallbackAiProvider(fake, FakeAiProvider(judge_backend="responses"))
    else:
        provider = fake
    monkeypatch.setattr(tasks, "get_ai_provider", lambda: provider)
    result = tasks._evaluation_execute(SimpleNamespace(payload={"request": request().model_dump(mode="json")}))
    assert ":responses:" in result.meta.config_version
    with pytest.raises(ValueError):
        tasks._evaluation_execute(SimpleNamespace(payload={"request": {}}))
    assert ":decisions:" in provider.judge_meta().config_version  # no stale previous Responses
    fake.script("judge_sufficiency", FakeOutcome.raw("not json"))
    if wrapper == "fallback":
        provider.fallback.script("judge_sufficiency", FakeOutcome.fail("refused"))
    with pytest.raises(AiError):
        tasks._evaluation_execute(SimpleNamespace(payload={"request": request().model_dump(mode="json")}))
    assert ":responses:" in provider.judge_meta().config_version  # actual call failed
    with pytest.raises(ValueError):
        tasks._evaluation_execute(SimpleNamespace(payload={"request": {}}))
    assert ":decisions:" in provider.judge_meta().config_version


@pytest.mark.parametrize("required", [None, "TARGET"])
def test_retrieval_charges_the_deduplicated_question_once_at_exact_budget(required):
    question = "질문" * 100
    chunks = [EvidenceChunk(id=f"t#{i}", intent_key="TARGET", text="커피를 내려요.", question=question)
              for i in range(1, 5)]
    budget = len(question) + sum(len(c.text) for c in chunks)
    result = retrieve(chunks, "커피", required_intent=required, top_k=4, budget_chars=budget)
    assert [c.id for c in result] == [c.id for c in chunks]
    assert [c.question for c in result] == [question, None, None, None]
    assert sum(len(c.text) + len(c.question or "") for c in result) == budget
    assert len(retrieve(chunks, "커피", required_intent=required, top_k=4, budget_chars=budget - 1)) == 3


def test_budget_tracks_the_actual_earliest_selected_question_even_when_rank_reverses():
    chunks = [EvidenceChunk(id="t#1", intent_key="TARGET", text="커피를 내려요.", question="가" * 100),
              EvidenceChunk(id="t#2", intent_key="TARGET", text="커피 커피 커피", question="짧은 질문")]
    # #2 ranks first, but adding #1 would replace the short question with the long one.
    result = retrieve(chunks, "커피", required_intent="TARGET", budget_chars=30)
    assert [c.id for c in result] == ["t#2"]
    assert result[0].question == "짧은 질문"
    assert sum(len(c.text) + len(c.question or "") for c in result) <= 30


def test_retrieval_preserves_cap_order_and_other_turn_question_cost():
    chunks = [EvidenceChunk(id=f"t#{i}", intent_key="TARGET", text="커피", question="질문")
              for i in range(MAX_EVIDENCE + 5)]
    result = retrieve(chunks, "커피", required_intent="TARGET", top_k=0, budget_chars=10000)
    assert len(result) == MAX_EVIDENCE and [c.id for c in result] == [c.id for c in chunks[:MAX_EVIDENCE]]
    assert result == retrieve(chunks, "커피", required_intent="TARGET", top_k=0, budget_chars=10000)
    other = EvidenceChunk(id="other#1", intent_key="TARGET", text="커피", question="다른 질문")
    assert retrieve([chunks[0], other], "커피", required_intent="TARGET", budget_chars=5) == (chunks[0],)


@pytest.mark.parametrize("threshold", [0.5, math.nextafter(0.5, 1), 0.701, 0.704, 0.999, math.nextafter(1, 0)])
def test_metadata_round_trips_every_accepted_threshold_without_collisions(threshold):
    settings = Thresholds(aspect=threshold, not_applicable=threshold)
    assert tuple(map(float, settings.tag.removeprefix("t=").split("/"))) == (threshold, threshold)
    for backend in ("decisions", "responses"):
        fake = FakeAiProvider(judge_backend=backend)
        fake.judge_thresholds = settings
        assert settings.tag in fake.judge_meta().config_version
    if math.nextafter(threshold, 1) < 1:
        neighbor = Thresholds(aspect=math.nextafter(threshold, 1), not_applicable=threshold)
        assert settings.tag != neighbor.tag


@pytest.mark.parametrize("backend", ["decisions", "responses", "fallback"])
@pytest.mark.parametrize("answer", ["직원 없이 운영해요.", "매장 운영은 전부 자동이에요.", "상근 인력이 없는 형태예요."])
def test_first_no_staff_answer_always_requires_actual_reconfirmation(backend, answer):
    req = request("WORK_STRUCTURE", (DialogueTurn(question=V1["WORK_STRUCTURE"].base_question,
                                                 answer=answer, depth=0),))
    if backend == "decisions":
        fake = FakeAiProvider().script("judge_sufficiency", FakeOutcome.predicates(
            0.1, not_applicable=0.94, not_applicable_confirmed=0.1))
    else:
        fake = FakeAiProvider(judge_backend="responses").script(
            "judge_sufficiency", FakeOutcome.ok(response(na=0.94, confirmed=0.1)))
        if backend == "fallback":
            primary = FakeAiProvider().script("judge_sufficiency", FakeOutcome.fail("timeout"))
            fake = FallbackAiProvider(primary, fake)
    result = fake.judge_sufficiency(req)
    assert not result.sufficient and not result.not_applicable
    assert result.missing_aspects == (aspects_for(req.intent).confirmation_label,)


@pytest.mark.parametrize("confirmed,na,sufficient,missing", [
    (0.8, 0.8, True, ()), (0.799999, 0.9, False, ("근무가 없는 매장인지의 재확인",)),
    (0.1, 0.1, False, ("근무조의 구성",)),
])
def test_responses_confirmation_acceptance_boundary_and_owner_reversal(confirmed, na, sufficient, missing):
    turns = (DialogueTurn(question="근무 형태는요?", answer="직원 없이 운영해요.", depth=0),
             DialogueTurn(question="사람이 일하는 근무가 전혀 없다는 뜻이 맞나요?",
                          answer="네, 그 설명이 정확해요." if na >= 0.8 else "주말에는 근무자가 와요.", depth=1))
    fake = FakeAiProvider(judge_backend="responses").script(
        "judge_sufficiency", FakeOutcome.ok(response(na=na, confirmed=confirmed, sufficient=na >= 0.8)))
    result = fake.judge_sufficiency(request("WORK_STRUCTURE", turns))
    assert (result.sufficient, result.missing_aspects, result.not_applicable) == (sufficient, missing, sufficient)
    data = fake.calls_for("judge_sufficiency")[0].data
    body, _ = build_request(request("WORK_STRUCTURE", turns), model=fake.model)
    assert data["not_applicable_confirmation_rule"] in body["questions"][-2]["instructions"]
    assert data["not_applicable_rule"] in body["questions"][-1]["instructions"]


@pytest.mark.parametrize("confirmed", [0.95, 0.1])
@pytest.mark.parametrize("ordinary", [
    {"sufficient": False, "probability": 0.1, "missing_aspects": []},
    {"sufficient": True, "probability": 0.3, "missing_aspects": []},
    {"sufficient": True, "probability": 0.9, "missing_aspects": ["근무조의 구성"]},
])
def test_no_staff_gate_precedes_ordinary_sufficiency_consistency(ordinary, confirmed):
    fake = FakeAiProvider(judge_backend="responses").script("judge_sufficiency", FakeOutcome.ok({
        **ordinary, "not_applicable_probability": 0.95, "not_applicable_confirmed_probability": confirmed}))
    result = fake.judge_sufficiency(request("WORK_STRUCTURE"))
    assert result.sufficient == result.not_applicable == (confirmed >= 0.8)
    assert result.missing_aspects == (() if confirmed >= 0.8 else ("근무가 없는 매장인지의 재확인",))


@pytest.mark.parametrize("field", ["probability", "not_applicable_probability", "not_applicable_confirmed_probability"])
@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_no_staff_gate_never_bypasses_invalid_probability_schema(field, value):
    data = {**response(na=0.95, confirmed=0.95), field: value}
    fake = FakeAiProvider(judge_backend="responses").script("judge_sufficiency", FakeOutcome.raw(json.dumps(data)))
    with pytest.raises(AiError) as caught:
        fake.judge_sufficiency(request("WORK_STRUCTURE"))
    assert caught.value.code == AiErrorCode.INVALID_OUTPUT


def test_owner_reversal_still_requires_consistent_ordinary_sufficiency():
    fake = FakeAiProvider(judge_backend="responses").script("judge_sufficiency", FakeOutcome.ok({
        **response(na=0.1, confirmed=0.1, sufficient=False), "missing_aspects": []}))
    with pytest.raises(AiError) as caught:
        fake.judge_sufficiency(request("WORK_STRUCTURE"))
    assert caught.value.detail == "insufficient_without_aspects"


@pytest.mark.parametrize("changes", [
    {}, {"not_applicable_probability": 0.9},
    {"not_applicable_probability": 0.9, "not_applicable_confirmed_probability": None},
])
def test_responses_cannot_accept_missing_confirmation_protocol(changes):
    legacy = {"sufficient": True, "probability": 0.9, "missing_aspects": [], **changes}
    fake = FakeAiProvider(judge_backend="responses").script("judge_sufficiency", FakeOutcome.raw(json.dumps(legacy)))
    with pytest.raises(AiError) as caught:
        fake.judge_sufficiency(request("WORK_STRUCTURE"))
    assert caught.value.code == AiErrorCode.INVALID_OUTPUT
    assert caught.value.detail == "missing_not_applicable_confirmation"


def test_strict_confirmation_fields_and_legacy_nonconfirmation_replay():
    Draft202012Validator(JUDGE_SCHEMA).validate(response())
    RawJudgement.model_validate(response())
    legacy = {"sufficient": True, "probability": 0.9, "missing_aspects": []}
    fake = FakeAiProvider(judge_backend="responses").script("judge_sufficiency", FakeOutcome.raw(json.dumps(legacy)))
    assert fake.judge_sufficiency(request()).sufficient


@pytest.mark.parametrize("mode", ["responses", "fallback"])
def test_configured_openai_responses_and_timeout_fallback_use_the_same_confirmation_protocol(monkeypatch, mode):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "primary-model")
    monkeypatch.setenv("OPENAI_JUDGE_BACKEND", "responses" if mode == "responses" else "decisions")
    monkeypatch.setenv("OPENAI_JUDGE_ASPECT_THRESHOLD", "0.701")
    monkeypatch.setenv("OPENAI_JUDGE_NOT_APPLICABLE_THRESHOLD", "0.804")
    monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "fallback-model" if mode == "fallback" else "")
    provider = build_provider_from_env()
    selected = provider.fallback if mode == "fallback" else provider
    bodies = []
    outputs = iter([response(na=0.94, confirmed=0.1), response(na=0.94, confirmed=0.94)])

    def transport(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"id": "r", "object": "response", "status": "completed", "output": [{
            "type": "message", "id": "m", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "annotations": [], "text": json.dumps(next(outputs))}]}]})

    def timeout(_req):
        raise httpx.ReadTimeout("fixture timeout")

    selected._client.close()
    selected._client = openai.OpenAI(api_key="sk-test-not-real", max_retries=0,
                                     http_client=httpx.Client(transport=httpx.MockTransport(transport)))
    if mode == "fallback":
        provider.primary._client.close()
        provider.primary._client = openai.OpenAI(api_key="sk-test-not-real", max_retries=0,
                                                http_client=httpx.Client(transport=httpx.MockTransport(timeout)))
    try:
        first = DialogueTurn(question="근무 형태는요?", answer="사람이 하는 근무가 없어요.", depth=0)
        pending = provider.judge_sufficiency(request("WORK_STRUCTURE", (first,)))
        assert not pending.sufficient and pending.missing_aspects == ("근무가 없는 매장인지의 재확인",)
        confirmed = DialogueTurn(question="직원이 하는 근무가 전혀 없다는 뜻이 맞나요?",
                                 answer="네, 그 설명이 정확해요.", depth=1)
        accepted = provider.judge_sufficiency(request("WORK_STRUCTURE", (first, confirmed)))
        assert accepted.sufficient and accepted.not_applicable and accepted.probability == 0.94
        assert ":responses:t=0.701/0.804" in accepted.meta.config_version
        assert accepted.meta.model == ("fallback-model" if mode == "fallback" else "primary-model")
        for body in bodies:
            assert body["text"]["format"]["strict"] is True
            assert body["text"]["format"]["schema"] == JUDGE_SCHEMA
            assert "not_applicable_confirmation_rule" in body["input"][0]["content"][0]["text"]
    finally:
        selected._client.close()
        if mode == "fallback":
            provider.primary._client.close()


@pytest.mark.parametrize("live_ops", [(), ("judge_sufficiency",)])
@pytest.mark.parametrize("backend", ["decisions", "responses"])
def test_routed_metadata_delegates_the_actual_judgement_provider(live_ops, backend):
    fake = FakeAiProvider(judge_backend=backend)
    live = FakeAiProvider(judge_backend=backend)
    live.provider_name, live.model = "live-fixture", "live-model"
    live.judge_thresholds = Thresholds(0.701, 0.804)
    selected = live if live_ops else fake
    selected.script("judge_sufficiency", FakeOutcome.fail("refused"))
    routed = RoutedAiProvider(fake, live, live_ops, CallBudget(5, None))
    assert routed.judge_meta() == selected.judge_meta()
    with pytest.raises(AiError):
        routed.judge_sufficiency(request())
    assert routed.judge_meta() == selected.judge_meta()
    assert len(selected.calls_for("judge_sufficiency")) == 1

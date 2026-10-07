"""Question and judgement fixes from the live evaluation run (gpt-6-luna, 2026-10-08).

* Q1/Q4 BASE questions stay neutral and keep the base question's whole scope: another intent's
  "해당 없음" never becomes this intent's premise, and "어떻게 사용하고 관리하나요?" is never
  narrowed to "어떤 것이 있나요?".
* Q2 a PROBE asks exactly the first missing aspect: the model gets it as `target_aspect`, and
  the aspect table lists the most essential aspect first.
* Q3 the "작업 순서" predicate accepts an order told in natural sentences per task, and still
  rejects task names or the order of tasks alone.
* Q5 a probe on a generic aspect label names the task the gap is about, or asks all tasks.
* Q6 a fallback model judges on the responses backend; a failed evaluation row records the
  per-operation config version (backend, effort), like a successful one.

The live behaviour is checked by tests/test_interview_live.py (opt-in); these pin the contract.
"""

import json

import httpx
import openai
import pytest

from app.ai import build_provider_from_env
from app.ai.aspects import ASPECTS_VERSION, aspects_for
from app.ai.contracts import (
    ContextNote,
    DialogueTurn,
    IntentBrief,
    QuestionRequest,
    SufficiencyRequest,
)
from app.ai.decisions import build_request
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.ai.openai_provider import OpenAiProvider
from app.ai.prompts import INSTRUCTIONS, PROMPT_VERSION
from app.ai.provider import FallbackAiProvider
from app.db.models import InterviewEvaluation
from app.interview.question_set import INTENTS_V1
from tests.test_interview_api import build_ctx, media_root, rows  # noqa: F401 (fixture)
from tests.test_interview_reviews import flow  # noqa: F401 (fixture)

V1 = {d.key: IntentBrief(key=d.key, stage=d.stage, base_question=d.base_question,
                         coverage_criteria=d.coverage_criteria) for d in INTENTS_V1}
QUESTION = INSTRUCTIONS["generate_question"]


def _section(start: str, end: str) -> str:
    return QUESTION[QUESTION.index(start):QUESTION.index(end)]


BASE_RULES = _section("- kind=BASE", "- kind=PROBE")
PROBE_RULES = _section("- kind=PROBE", "- 한 번에 한 가지만")


# --- Q1/Q4: BASE ----------------------------------------------------------------------------------


def test_base_keeps_the_whole_scope_of_the_base_question():
    assert "범위를 그대로 유지한다" in BASE_RULES and "줄이거나 바꾸지 않는다" in BASE_RULES
    # The narrowing seen live (EQUIPMENT) is the bad example.
    assert "어떤 것이 있나요?" in BASE_RULES and "어떻게 사용하고 관리하나요?" in BASE_RULES
    assert "base_question을 그대로 쓴다" in BASE_RULES


def test_base_is_neutral_and_other_intents_do_not_shape_its_premise():
    assert "중립적으로 묻는다" in BASE_RULES
    assert "질문과 guidance 어디에도 넣지 않는다" in BASE_RULES
    assert "따로 정해 두지 않으셨다면" in BASE_RULES  # the leaked premise seen live (EXCEPTIONS)
    assert "이 인텐트에 대해 아무것도 알려 주지 않는다" in BASE_RULES and "해당 없음" in BASE_RULES
    # The old "confirm what earlier answers said" rule invited a premise; it is gone.
    assert "확인하는 형태로 묻는다" not in QUESTION


def test_base_request_has_no_target_aspect_and_still_carries_the_context(fake_ai):
    request = QuestionRequest(kind="BASE", intent=V1["EXCEPTIONS"], depth=0, context=(
        ContextNote(intent_key="RULES", summary="따로 정해 둔 매장 규칙은 없다고 하셨어요."),))
    fake_ai.generate_question(request)
    data = fake_ai.calls_for("generate_question")[-1].data
    assert "target_aspect" not in data and data["context"][0]["intent_key"] == "RULES"


# --- Q2/Q5: PROBE ---------------------------------------------------------------------------------


def test_probe_asks_the_target_aspect_only_even_after_a_non_answer():
    assert "target_aspect(missing_aspects의 첫 항목) 하나만" in PROBE_RULES
    assert "다른 항목을 먼저 묻지 않는다" in PROBE_RULES
    # The rule that let the model skip ahead to a later aspect is gone.
    assert "그것을 먼저 묻는다" not in QUESTION
    assert "새로 만들어 묻지 않는다" in PROBE_RULES and "징후" in PROBE_RULES  # the drift seen live
    assert "더 작은 단위로 바꿔 묻는다" in PROBE_RULES


def test_probe_names_the_task_of_the_gap_or_asks_all_tasks():
    assert "남은 업무가 하나면 그 업무 이름을 짚어 묻는다" in PROBE_RULES
    assert "그중 하나를 고르지 말고" in PROBE_RULES and "말씀하신 업무마다" in PROBE_RULES
    assert "마지막에 말한 업무만 골라 묻지 않는다" in PROBE_RULES


@pytest.mark.parametrize("aspects", [
    ("돌발 상황의 대응 방법", "점주 연락의 기준"),
    ("공통 업무의 완료 기준",),
])
def test_probe_payload_names_the_first_missing_aspect_as_the_target(fake_ai, aspects):
    dialogue = (DialogueTurn(question="q", answer="손님 불만이나 기계 문제요.", depth=0),)
    fake_ai.generate_question(QuestionRequest(kind="PROBE", intent=V1["EXCEPTIONS"], depth=1,
                                              dialogue=dialogue, missing_aspects=aspects))
    data = fake_ai.calls_for("generate_question")[-1].data
    assert data["target_aspect"] == aspects[0]
    assert data["missing_aspects"] == list(aspects)  # the rest stays visible as "later"


def test_the_target_aspect_reaches_the_probe_from_the_judgement(flow, fake_ai):  # noqa: F811
    """Decisions (table order) -> probe batch -> generator payload, end to end with the fake."""
    sid = flow.started()
    flow.answer_and_run(sid)  # WORK_STRUCTURE: sufficient by default
    # COMMON_TASKS: kinds known, order and completion missing -> the order is asked first.
    fake_ai.script("judge_sufficiency", FakeOutcome.predicates(aspect_2=0.2, aspect_3=0.1))
    flow.answer_and_run(sid, "손님 응대하고 음료 만들고, 청소도 하고요.")
    probe = [c.data for c in fake_ai.calls_for("generate_question") if c.data["kind"] == "PROBE"][-1]
    assert probe["target_aspect"] == "공통 업무의 작업 순서"
    assert probe["missing_aspects"] == ["공통 업무의 작업 순서", "공통 업무의 완료 기준"]



# --- aspect order (Q2) and the order predicate (Q3) -----------------------------------------------


def test_aspects_are_listed_most_essential_first():
    labels = {key: [a.label for a in aspects_for(V1[key]).aspects] for key in V1}
    assert labels["EXCEPTIONS"] == ["돌발 상황의 종류", "돌발 상황의 대응 방법", "점주 연락의 기준"]
    assert labels["COMMON_TASKS"] == ["공통 업무의 종류", "공통 업무의 작업 순서", "공통 업무의 완료 기준"]
    # Safety before upkeep for a worker's first day.
    assert labels["EQUIPMENT"] == ["설비의 종류", "설비의 사용 순서", "설비의 주의 사항", "설비의 관리 방법"]
    for key, definition in V1.items():  # core aspects come before details
        cores = [a.core for a in aspects_for(definition).aspects]
        assert cores == sorted(cores, reverse=True), key


@pytest.mark.parametrize("key,label", [
    ("COMMON_TASKS", "공통 업무의 작업 순서"), ("SHIFT_TASKS", "근무조별 업무의 작업 순서"),
])
def test_the_order_predicate_accepts_natural_sentences_but_not_names_or_task_order(key, label):
    aspect = next(a for a in aspects_for(V1[key]).aspects if a.label == label)
    text = aspect.instructions
    assert aspect.core
    assert "메뉴를 누르고 결제까지 받아요" in text  # the false negative seen live
    assert "\"먼저\", \"그다음\", \"마지막\" 같은 말이나 번호가 없어도" in text
    assert "다른 이름으로 불러도 같은 업무" in text
    # Still false: names only, or the order of the tasks without what is done in them.
    assert "업무 이름이나 결과만 말했거나" in text and "차례만 말하고" in text and "거짓이다" in text


def test_equipment_use_order_accepts_sentences_and_rejects_names_only():
    aspect = aspects_for(V1["EQUIPMENT"]).aspects[1]
    assert aspect.label == "설비의 사용 순서" and aspect.core
    assert "번호가 없어도" in aspect.instructions and "설비 이름만 말했거나" in aspect.instructions


def test_the_aspect_version_was_bumped_and_is_recorded():
    assert ASPECTS_VERSION == "2026-10-08.2"
    judgement = FakeAiProvider().judge_sufficiency(SufficiencyRequest(
        intent=V1["COMMON_TASKS"], depth=1,
        dialogue=(DialogueTurn(question="q", answer="a", depth=0),)))
    assert f":decisions:aspects-{ASPECTS_VERSION}:" in judgement.meta.config_version


def test_the_tuned_predicate_is_what_the_decisions_body_carries():
    request = SufficiencyRequest(intent=V1["COMMON_TASKS"], depth=1,
                                 dialogue=(DialogueTurn(question="q", answer="a", depth=0),))
    body, labels = build_request(request, model="gpt-6-luna")
    assert labels[1] == "공통 업무의 작업 순서"
    assert "메뉴를 누르고 결제까지 받아요" in body["questions"][1]["instructions"]


# --- Q6a: the fallback model judges on the responses backend ------------------------------------


@pytest.fixture
def openai_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    for name in ("OPENAI_JUDGE_BACKEND", "OPENAI_REASONING_EFFORT", "OPENAI_FALLBACK_MODEL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_a_fallback_model_judges_on_the_responses_backend(openai_env):
    openai_env.setenv("OPENAI_FALLBACK_MODEL", "gpt-other")
    provider = build_provider_from_env()
    assert isinstance(provider, FallbackAiProvider)
    assert (provider.primary.judge_backend, provider.fallback.judge_backend) == ("decisions", "responses")
    assert provider.judge_backend == "decisions"  # what the session is configured with
    assert ":decisions:" in provider.judge_meta().config_version


def test_without_a_fallback_the_judge_backend_setting_is_kept(openai_env):
    assert build_provider_from_env().judge_backend == "decisions"
    openai_env.setenv("OPENAI_JUDGE_BACKEND", "responses")
    assert build_provider_from_env().judge_backend == "responses"


def _openai(handler, model: str, **options) -> OpenAiProvider:
    client = openai.OpenAI(api_key="sk-test-not-real", max_retries=0,
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return OpenAiProvider(api_key="", model=model, transcribe_model="gpt-transcribe", client=client, **options)


def test_jev_survives_a_primary_outage_on_a_fallback_model_without_decisions():
    paths = []

    def primary(request: httpx.Request) -> httpx.Response:
        paths.append(("primary", request.url.path))
        return httpx.Response(503, json={"error": {"message": "down"}})

    def fallback(request: httpx.Request) -> httpx.Response:
        paths.append(("fallback", request.url.path))
        if request.url.path == "/v1/decisions":  # the model is not served there
            return httpx.Response(404, json={"error": {"message": "model not found"}})
        output = json.dumps({"sufficient": False, "probability": 0.2, "missing_aspects": ["공통 업무의 작업 순서"]})
        return httpx.Response(200, json={"id": "r", "object": "response", "status": "completed", "output": [{
            "type": "message", "id": "m", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "annotations": [], "text": output}]}]})

    provider = FallbackAiProvider(_openai(primary, "gpt-6-luna"),
                                  _openai(fallback, "gpt-other", judge_backend="responses"))
    result = provider.judge_sufficiency(SufficiencyRequest(
        intent=V1["COMMON_TASKS"], depth=0,
        dialogue=(DialogueTurn(question="q", answer="홀 서빙이요.", depth=0),)))
    assert paths == [("primary", "/v1/decisions"), ("fallback", "/v1/responses")]
    assert not result.sufficient and result.missing_aspects == ("공통 업무의 작업 순서",)
    assert result.meta.model == "gpt-other"
    assert result.meta.config_version == f"openai:gpt-other:{PROMPT_VERSION}:responses:effort=low"


# --- Q6b: failed evaluation rows record the per-operation config version -------------------------


def test_judge_meta_names_the_configured_backend_and_effort():
    fake = FakeAiProvider()
    assert fake.judge_meta().config_version == fake.meta("judge_sufficiency", backend="decisions").config_version
    assert ":decisions:aspects-" in fake.judge_meta().config_version
    responses = _openai(lambda _r: httpx.Response(500), "gpt-6-luna", judge_backend="responses",
                        reasoning_effort="high")
    assert responses.judge_meta().config_version == f"openai:gpt-6-luna:{PROMPT_VERSION}:responses:effort=high"
    wrapped = FallbackAiProvider(fake, FakeAiProvider(judge_backend="responses"))
    assert wrapped.judge_meta() == fake.judge_meta()


@pytest.fixture
def ctx(api, db_engine, media_root):  # noqa: F811
    return build_ctx(api, db_engine)



def test_a_failed_evaluation_row_stores_the_per_operation_config_version(ctx, fake_ai):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", *[FakeOutcome.fail("timeout")] * 3)
    ctx.answer_and_run(sid, "오전조랑 오후조요.")
    [failed] = rows(ctx, InterviewEvaluation)
    assert (failed.status, failed.error_code) == ("FAILED", "TIMEOUT")
    assert failed.evaluation_config_version == fake_ai.judge_meta().config_version
    assert failed.evaluation_config_version.startswith(f"fake:fake-llm:{PROMPT_VERSION}:decisions:aspects-")
    assert failed.provider == "fake"

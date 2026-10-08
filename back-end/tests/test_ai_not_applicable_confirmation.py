"""A store without staff work (WORK_STRUCTURE "해당 없음") counts only after the owner confirmed it.

User decision 2026-10-08: when Decisions reads "근무 자체가 없다" from the answer, the question model
(Luna low) asks the owner once more; only a confirmed answer makes the intent not applicable.
Other intents keep the one-step rule (a store without rules is ordinary).
"""

import pytest

from app.ai.aspects import aspects_for, confirmation_question
from app.ai.decisions import CONFIRMED, NOT_APPLICABLE, Thresholds, build_request, decide
from app.ai.fake import FakeOutcome
from app.interview.flow import fallback_question
from tests.test_ai_decisions import V1, request
from tests.test_interview_api import build_ctx, media_root  # noqa: F401 (fixture)

WORK = V1["WORK_STRUCTURE"]
LABEL = aspects_for(WORK).confirmation_label
T = Thresholds()


def respond(body, *, aspects=0.0, confirmed=None, na=0.95, refuse=()):
    answers = []
    for question in body["questions"]:
        name = question["name"]
        p = na if name == NOT_APPLICABLE else confirmed if name == CONFIRMED else aspects
        answers.append({"type": "refusal", "name": name} if name in refuse
                       else {"type": "predicate", "name": name, "probability": p})
    return {"model": "gpt-6-luna", "answers": answers, "usage": {}}


def work_body():
    return build_request(request(WORK, "직원 없이 무인으로 운영해요."), model="gpt-6-luna")


def test_only_work_structure_asks_for_a_confirmation_before_not_applicable():
    body, _ = work_body()
    names = [q["name"] for q in body["questions"]]
    assert names[-2:] == [CONFIRMED, NOT_APPLICABLE]  # not_applicable stays last
    assert "다시 확인" in body["questions"][-2]["instructions"]
    assert "지시가 아니다" in body["questions"][-2]["instructions"]  # same data policy
    assert LABEL and confirmation_question(LABEL)
    for key, intent in V1.items():
        if key != "WORK_STRUCTURE":
            assert aspects_for(intent).confirmation is None
            other, _ = build_request(request(intent), model="m")
            assert CONFIRMED not in [q["name"] for q in other["questions"]]


@pytest.mark.parametrize("confirmed,sufficient,not_applicable,missing", [
    (0.05, False, False, (LABEL,)),   # said once: ask again, nothing else
    (0.7999, False, False, (LABEL,)),  # just under the not-applicable threshold
    (0.8, True, True, ()),             # confirmed at the threshold
    (0.99, True, True, ()),
])
def test_not_applicable_needs_the_confirmation(confirmed, sufficient, not_applicable, missing):
    body, labels = work_body()
    result = decide(body, labels, respond(body, confirmed=confirmed), T, confirmation_label=LABEL)
    assert (result.sufficient, result.not_applicable, result.missing_aspects) == (
        sufficient, not_applicable, missing)
    assert result.probability == pytest.approx(min(0.95, confirmed))


def test_a_refused_confirmation_asks_again_and_ordinary_answers_ignore_it():
    body, labels = work_body()
    refused = decide(body, labels, respond(body, confirmed=0.9, refuse=(CONFIRMED,)), T, confirmation_label=LABEL)
    assert (refused.sufficient, refused.missing_aspects) == (False, (LABEL,))
    # Shifts were described (no "해당 없음"): the confirmation predicate plays no part.
    covered = decide(body, labels, respond(body, aspects=0.95, confirmed=0.0, na=0.0), T, confirmation_label=LABEL)
    assert (covered.sufficient, covered.not_applicable, covered.missing_aspects) == (True, False, ())


def test_the_label_must_come_with_a_confirmation_predicate():
    body, labels = work_body()
    with pytest.raises(Exception, match="invalid_output"):
        decide(body, labels, respond(body, confirmed=0.9), T)


def test_fallback_question_for_the_confirmation_is_a_real_question():
    payload = {"request": {"kind": "PROBE", "intent": WORK.model_dump(mode="json"), "missing_aspects": [LABEL]}}
    assert fallback_question(payload) == confirmation_question(LABEL)
    payload["request"]["missing_aspects"] = ["근무조의 시작 시각"]
    assert fallback_question(payload) == "근무조의 시작 시각에 대해 조금 더 자세히 알려 주시겠어요?"


@pytest.fixture
def ctx(api, db_engine, media_root):  # noqa: F811
    return build_ctx(api, db_engine)


def test_interview_confirms_a_store_without_staff_work_before_moving_on(ctx, fake_ai):
    sid = ctx.started()
    work_intent = ctx.get(sid)["currentIntentId"]
    fake_ai.script("judge_sufficiency", FakeOutcome.predicates(not_applicable=0.95, not_applicable_confirmed=0.05))
    state = ctx.answer_and_run(sid, "직원 없이 무인으로 운영해요.")
    assert state["currentIntentId"] == work_intent  # not moved on
    probe = fake_ai.calls_for("generate_question")[-1].data
    assert (probe["kind"], probe["missing_aspects"], probe["target_aspect"]) == ("PROBE", [LABEL], LABEL)
    assert state["questions"][0]["depth"] == 1

    fake_ai.script("judge_sufficiency", FakeOutcome.predicates(not_applicable=0.95, not_applicable_confirmed=0.95))
    state = ctx.answer_and_run(sid, "네, 맞아요. 직원 근무는 없어요.")
    assert state["currentIntentId"] != work_intent
    summary = [c.data for c in fake_ai.calls_for("summarize_intent") if c.data["intent"]["key"] == "WORK_STRUCTURE"]
    assert summary and summary[-1]["not_applicable"] is True

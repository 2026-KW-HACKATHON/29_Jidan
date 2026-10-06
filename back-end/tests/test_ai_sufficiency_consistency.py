"""Contradictory Jev results must not finish an intent or erase its missing information."""

import pytest

from app.ai.contracts import DialogueTurn, IntentBrief, SufficiencyRequest
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.ai.provider import FallbackAiProvider


def request():
    return SufficiencyRequest(
        intent=IntentBrief(key="closing", stage="COMMON_TASKS",
                           base_question="마감 순서를 알려 주세요.",
                           coverage_criteria="작업 순서와 완료 기준"),
        dialogue=(DialogueTurn(question="마감 순서를 알려 주세요.", answer="모르겠어요.", depth=0),),
        depth=0,
    )


@pytest.mark.parametrize("sufficient,probability,aspects,detail", [
    (True, 0.49, [], "inconsistent_sufficiency_probability"),
    (False, 0.5, ["마감 순서"], "inconsistent_sufficiency_probability"),
    (False, 0.9, ["마감 순서"], "inconsistent_sufficiency_probability"),
    (True, 0.8, ["마감 순서"], "sufficient_with_missing_aspects"),
])
def test_contradictory_sufficiency_is_retryable_failure(fake_ai, sufficient, probability, aspects, detail):
    fake_ai.script("judge_sufficiency", FakeOutcome.ok({
        "sufficient": sufficient, "probability": probability, "missing_aspects": aspects,
    }))
    with pytest.raises(AiError) as caught:
        fake_ai.judge_sufficiency(request())
    assert caught.value.code == AiErrorCode.INVALID_OUTPUT
    assert caught.value.detail == detail and caught.value.retryable


@pytest.mark.parametrize("sufficient,probability,aspects", [
    (True, 0.5, []), (True, 1.0, []), (False, 0.0, ["마감 순서"]),
    (False, 0.499999, ["마감 순서"]), (True, 0.8, [" "]),
])
def test_consistent_threshold_and_cleaned_aspects_are_kept(fake_ai, sufficient, probability, aspects):
    fake_ai.script("judge_sufficiency", FakeOutcome.ok({
        "sufficient": sufficient, "probability": probability, "missing_aspects": aspects,
    }))
    result = fake_ai.judge_sufficiency(request())
    assert (result.sufficient, result.probability) == (sufficient, probability)
    assert result.needs_follow_up is not sufficient
    assert result.missing_aspects == tuple(a for a in aspects if a.strip())


def test_contradiction_retries_on_fallback_without_reusing_the_invalid_result():
    primary, fallback = FakeAiProvider(), FakeAiProvider()
    primary.model, fallback.model = "primary", "fallback"
    primary.script("judge_sufficiency", FakeOutcome.ok({
        "sufficient": True, "probability": 0.9, "missing_aspects": ["마감 순서"],
    }))
    fallback.script("judge_sufficiency", FakeOutcome.ok({
        "sufficient": False, "probability": 0.2, "missing_aspects": ["마감 순서"],
    }))
    result = FallbackAiProvider(primary, fallback).judge_sufficiency(request())
    assert result.needs_follow_up and result.missing_aspects == ("마감 순서",)
    assert result.meta.model == "fallback"
    assert len(primary.calls_for("judge_sufficiency")) == len(fallback.calls_for("judge_sufficiency")) == 1

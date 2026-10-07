"""B04 with the real model (opt-in: OPENAI_API_KEY and JIDAN_RUN_OPENAI=1).

Golden cases where the owner never gave the information: repeated "don't know", a deferred
non-answer, and "nothing more decided" in place of the procedure itself. Jev must keep them
insufficient with missing aspects, however many probes the dialogue shows. Only that structure is
asserted (no wording). At most 10 model calls; the explicit not-applicable control is only printed.
"""

import pytest

from app.ai import get_ai_provider
from app.ai.contracts import DialogueTurn, IntentBrief, SufficiencyRequest

pytestmark = pytest.mark.openai

MAX_CALLS = 10
CLOSING = IntentBrief(
    key="COMMON_TASKS", stage="COMMON_TASKS", base_question="마감할 때 직원이 하는 일을 순서대로 알려 주세요.",
    coverage_criteria="마감 작업의 종류와 순서, 각 작업의 완료 기준",
)


def turns(*pairs: tuple[str, str]) -> tuple[DialogueTurn, ...]:
    return tuple(DialogueTurn(question=q, answer=a, depth=d) for d, (q, a) in enumerate(pairs))


MISSING = {
    "dont_know_repeated": turns(
        (CLOSING.base_question, "커피 머신 닦고 쓰레기 정리해요."),
        ("커피 머신은 어떤 순서로 닦나요?", "잘 모르겠어요."),
        ("커피 머신을 닦을 때 가장 먼저 하는 일은 무엇인가요?", "기억이 안 나요."),
        ("커피 머신 청소가 끝났다는 건 무엇으로 확인하나요?", "모르겠어요."),
    ),
    "no_answer": turns(
        (CLOSING.base_question, "나중에 알려 드릴게요."),
        ("마감할 때 가장 먼저 하는 일은 무엇인가요?", "음..."),
        ("마감 때 꼭 하는 일 한 가지만 알려 주시겠어요?", "그건 다음에 얘기해요."),
    ),
    "nothing_decided_for_the_core": turns(
        (CLOSING.base_question, "따로 정한 건 없어요."),
        ("마감할 때 직원이 하는 일은 어떤 것들이 있나요?", "그 밖에 따로 정한 건 없어요."),
    ),
}
NOT_APPLICABLE = turns(
    (CLOSING.base_question, "저희는 24시간 무인 매장이라 마감 작업이 없어요. 직원이 마감할 일은 전혀 없어요."),
)


@pytest.fixture
def provider(monkeypatch):
    provider = get_ai_provider()
    calls = []

    def counting(original):
        def counted(*args, **kwargs):
            calls.append(original.__name__)
            if len(calls) > MAX_CALLS:
                raise AssertionError("live call budget exceeded")
            return original(*args, **kwargs)
        return counted

    # Jev goes to `_decide` on the Decisions backend (default) and `_complete` on Responses.
    for hook in ("_complete", "_decide"):
        monkeypatch.setattr(provider, hook, counting(getattr(provider, hook)))
    yield provider
    print(f"\nlive judge_sufficiency calls={len(calls)}")


def test_live_jev_keeps_missing_information_missing(provider):
    for name, dialogue in MISSING.items():
        for _ in range(2):  # twice each: a single lucky answer is not evidence
            judgement = provider.judge_sufficiency(SufficiencyRequest(
                intent=CLOSING, dialogue=dialogue, depth=len(dialogue) - 1))
            assert judgement.sufficient is False, name
            assert judgement.missing_aspects, name
            if provider.judge_backend == "responses":  # Decisions: a combined probability (decisions.py)
                assert judgement.probability < 0.5, name
    control = provider.judge_sufficiency(SufficiencyRequest(intent=CLOSING, dialogue=NOT_APPLICABLE, depth=0))
    print(f"\nnot-applicable control: sufficient={control.sufficient} aspects={len(control.missing_aspects)}")

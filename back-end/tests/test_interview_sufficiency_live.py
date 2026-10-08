"""B04 with the real model (opt-in: OPENAI_API_KEY and JIDAN_RUN_OPENAI=1).

Golden cases where the owner never gave the information: repeated "don't know", a deferred
non-answer, and "nothing more decided" in place of the procedure itself. Jev must keep them
insufficient with missing aspects, however many probes the dialogue shows. Only that structure is
asserted (no wording). At most 11 model calls; the explicit not-applicable control is only printed.
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
    original = provider._complete

    def counted(*args, **kwargs):
        calls.append(args[0])
        if len(calls) > MAX_CALLS:
            raise AssertionError("live call budget exceeded")
        return original(*args, **kwargs)

    monkeypatch.setattr(provider, "_complete", counted)
    yield provider
    print(f"\nlive judge_sufficiency calls={len(calls)}")


def test_live_jev_keeps_missing_information_missing(provider):
    for name, dialogue in MISSING.items():
        for _ in range(2):  # twice each: a single lucky answer is not evidence
            judgement = provider.judge_sufficiency(SufficiencyRequest(
                intent=CLOSING, dialogue=dialogue, depth=len(dialogue) - 1))
            assert judgement.sufficient is False, name
            assert judgement.missing_aspects, name
            assert judgement.probability < 0.5, name
    control = provider.judge_sufficiency(SufficiencyRequest(intent=CLOSING, dialogue=NOT_APPLICABLE, depth=0))
    print(f"\nnot-applicable control: sufficient={control.sufficient} aspects={len(control.missing_aspects)}")


@pytest.mark.parametrize("criteria,answer,expected", [
    ("시재 점검 시점, 순서, 완료 기준, 불일치 시 처리",
     "매일 마감 때 현금을 세고 장부 금액과 비교해요. 일치하면 완료예요. 다르면 점주에게 보고해요. 다른 절차는 없어요.", True),
    ("입고품 확인 순서, 완료 기준, 수량 불일치 시 처리",
     ("입고품 수량을 세고 발주서와 대조해요. 같으면 선반에 넣고 입고 완료로 기록해요. 다르면 점주에게 알리고 보관해요. "
      "추가로 정한 규칙은 없어요."), True),
    ("입고품 확인 순서, 완료 기준, 수량 불일치 시 처리",
     "입고품을 확인해요. 어떻게 확인하는지는 모르겠어요. 그게 전부예요.", False),
])
def test_live_jev_uses_stated_coverage_without_inventing_finer_requirements(provider, criteria, answer, expected):
    """Enough detail meets stated coverage; unknown core work is still insufficient.

    No gold wording or particular missing-aspect label is required. Independent
    review must reject extra probing or missing labels duplicating answered
    coverage, even if the overall sufficient boolean matches the expectation.
    """
    intent = IntentBrief(key="COMMON_TASKS", stage="COMMON_TASKS",
                         base_question="업무 방법을 알려 주세요.", coverage_criteria=criteria)
    result = provider.judge_sufficiency(SufficiencyRequest(
        intent=intent, dialogue=turns((intent.base_question, answer)), depth=0))
    assert result.sufficient is expected
    assert bool(result.missing_aspects) is (not expected)


def test_live_jev_missing_exception_does_not_erase_known_normal_procedure(provider):
    """One exception is unknown; all ordinary procedure/finish facts are answered.

    The lexical assertion is only a screen. Independent review must confirm
    EVERY missing aspect concerns the unresolved exception, with no duplicate
    demand for the ordinary sequence or finish condition. No exact gold phrase.
    """
    intent = IntentBrief(key="COMMON_TASKS", stage="COMMON_TASKS", base_question="설거지는 어떻게 하나요?",
                         coverage_criteria="설거지 순서, 완료 기준, 식기세척기 고장 시 대응")
    answer = ("음식물을 버리고 식기세척기에 넣어 돌려요. 물기가 마르면 선반에 넣어요. 선반 정리까지 하면 끝나요. "
              "식기세척기가 고장났을 때 어떻게 하는지는 아직 몰라요.")
    result = provider.judge_sufficiency(SufficiencyRequest(
        intent=intent, dialogue=turns((intent.base_question, answer)), depth=0))
    assert result.sufficient is False and result.missing_aspects
    assert all(any(marker in aspect for marker in ("고장", "오류", "작동하지", "멈"))
               for aspect in result.missing_aspects)
    print(f"\nindependent review required for exception scope: {result.missing_aspects}")

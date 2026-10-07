"""Jev on the real Decisions API (opt-in: OPENAI_API_KEY and JIDAN_RUN_OPENAI=1; see conftest).

Two COMMON_TASKS dialogues of question set v1: a complete answer must be sufficient, a vague one
must not, with the core aspect missing. Two Decisions calls in all; the probabilities per
predicate are printed (`-s`) for calibration of the thresholds.
"""

import pytest

from app.ai import get_ai_provider
from app.ai.contracts import DialogueTurn, IntentBrief, SufficiencyRequest
from app.ai.decisions import build_request
from app.interview.question_set import INTENTS_V1

pytestmark = pytest.mark.openai

COMMON = next(IntentBrief(key=d.key, stage=d.stage, base_question=d.base_question,
                          coverage_criteria=d.coverage_criteria) for d in INTENTS_V1 if d.key == "COMMON_TASKS")
COMPLETE = (
    "공통 업무는 홀 서빙이랑 설거지 두 가지예요. 홀 서빙은 손님이 앉으면 물과 수저를 먼저 드리고, 주문을 받아 포스에 "
    "입력한 뒤 음식이 나오면 테이블로 가져다드려요. 손님이 나가면 그릇을 치우고 테이블을 소독제로 닦아요. 테이블이 "
    "깨끗하게 닦이고 수저통이 채워져 있으면 홀 서빙이 끝난 거예요. 설거지는 그릇의 음식물을 버리고 애벌 세척한 다음 "
    "식기세척기에 넣고 돌려요. 다 돌면 그릇을 종류별로 선반에 쌓아요. 싱크대에 그릇이 하나도 없고 선반에 다 쌓여 "
    "있으면 설거지가 끝난 거예요."
)
VAGUE = "홀 서빙이랑 설거지 정도요."


def judge(provider, answer):
    request = SufficiencyRequest(intent=COMMON, depth=0, dialogue=(
        DialogueTurn(question=COMMON.base_question, answer=answer, depth=0),))
    calls = []
    original = provider._decide

    def recorded(body):
        raw = original(body)
        calls.append(raw)
        return raw

    provider._decide = recorded
    try:
        judgement = provider.judge_sufficiency(request)
    finally:
        del provider._decide
    names = [q["name"] for q in build_request(request, model=provider.model)[0]["questions"]]
    answers = {a["name"]: a.get("probability", "refusal") for a in calls[0]["answers"]} if calls else {}
    print(f"\n{judgement.sufficient=} {judgement.probability=:.3f} {judgement.missing_aspects=}"
          f"\n  " + ", ".join(f"{n}={answers.get(n)}" for n in names))
    return judgement


@pytest.fixture
def provider():
    provider = get_ai_provider()
    if provider.judge_backend != "decisions":
        pytest.skip("OPENAI_JUDGE_BACKEND is not decisions")
    return provider


def test_live_decisions_complete_answer_is_sufficient(provider):
    judgement = judge(provider, COMPLETE)
    assert judgement.sufficient and judgement.missing_aspects == ()
    assert ":decisions:" in judgement.meta.config_version


def test_live_decisions_vague_answer_is_missing_the_procedure(provider):
    judgement = judge(provider, VAGUE)
    assert not judgement.sufficient
    assert "공통 업무의 작업 순서" in judgement.missing_aspects
    assert "공통 업무의 종류" not in judgement.missing_aspects  # the kinds were named

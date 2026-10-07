"""B04: limiting how many follow-up questions are asked is not the same as having the
information. The Jev prompt must keep "don't know" and non-answers missing however often they
were asked; the server alone ends an intent at depth 5 as NEEDS_DETAIL
(docs/manual-interview-design.md), and owner confirmation never turns that into COVERED."""

import hashlib
import re

import pytest

from app.ai import get_ai_provider
from app.ai.contracts import DialogueTurn, IntentBrief, SufficiencyRequest
from app.ai.fake import FakeOutcome
from app.ai.prompts import INSTRUCTIONS, PROMPT_VERSION
from tests.test_interview_api import INSUFFICIENT
from tests.test_interview_reviews import confirm, flow  # noqa: F401 (fixture)

JEV = INSTRUCTIONS["judge_sufficiency"]

# Every prompt text change needs a new PROMPT_VERSION (it is part of the stored config version).
# Add the new version and its digest here together with the change.
PINNED_PROMPTS = {
    "2026-10-06.5": "c968d26430875d78de6cbb82abdf9d1ed71f19fc680fa18bf8fd721e6fe19ec2",
    "2026-10-06.6": "c968d26430875d78de6cbb82abdf9d1ed71f19fc680fa18bf8fd721e6fe19ec2",
    "2026-10-07.1": "1fd2f47f685d014d1c6032ffddf248c4e6be2bdf6ed69b7cdcd05bef5e12678d",
    "2026-10-08.1": "fec9b6976bbf1a7a27c19a2b219747958cb28a300f785b35ae6e2966ad4d9a6f",
    "2026-10-08.2": "e17b5da6368008396513935149f3c23ad3848697efc257ada0be7eebc8753244",
}


def _digest() -> str:
    text = "\n".join(f"{name}\n{body}" for name, body in sorted(INSTRUCTIONS.items()))
    return hashlib.sha256(text.encode()).hexdigest()


def test_prompt_text_is_pinned_to_its_version():
    assert PROMPT_VERSION in PINNED_PROMPTS, "prompt changed: bump PROMPT_VERSION and pin it"
    assert _digest() == PINNED_PROMPTS[PROMPT_VERSION], "prompt text changed without a version bump"


def test_question_count_is_never_a_reason_for_sufficiency():
    # The 50948e3 rule: "이미 두 번 이상 물었는데도 답이 나오지 않은 측면은 다시 넣지 않는다.
    # 남은 측면이 없으면 충분으로 판단한다."
    assert not re.search(r"(두|여러|몇) ?번[^\n]*(다시 넣지 않|충분)", JEV)
    assert "남은 측면이 없으면 충분" not in JEV
    assert "질문 횟수" in JEV and "서버" in JEV and "NEEDS_DETAIL" in JEV


@pytest.mark.parametrize("category,example,verdict", [
    ("명시적 해당 없음", "안 해요", "확보된 사실"),
    ("더 정한 규칙 없음", "따로 정한 게 없어요", "정한 규칙 없음"),
    ("모르겠음", "모르겠어요", "확보되지 않았다"),
    ("무응답", "나중에", "확보되지 않았다"),
])
def test_the_four_kinds_of_non_answers_are_told_apart(category, example, verdict):
    line = next((line for line in JEV.splitlines() if category in line), "")
    block = JEV[JEV.index(line):JEV.index(line) + 260] if line else ""
    assert example in block and verdict in block, category


def test_no_further_rule_does_not_stand_in_for_the_core_information():
    block = JEV[JEV.index("더 정한 규칙 없음"):]
    assert "기본 내용" in block[:300] and "채워지지 않는다" in block[:300]


def test_the_model_is_not_shown_the_probe_count(fake_ai):
    """Depth is the server's business: Jev gets the dialogue, not the counter."""
    intent = IntentBrief(key="closing", stage="COMMON_TASKS", base_question="마감할 때 뭘 하나요?",
                         coverage_criteria="마감 작업의 순서, 완료 기준")
    dialogue = tuple(DialogueTurn(question=f"q{d}", answer="모르겠어요", depth=d) for d in range(5))
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    get_ai_provider().judge_sufficiency(SufficiencyRequest(intent=intent, dialogue=dialogue, depth=4))
    data = fake_ai.calls_for("judge_sufficiency")[0].data
    assert "depth" not in data and len(data["dialogue"]) == 5


def test_repeated_dont_know_ends_needs_detail_and_confirmation_keeps_it(flow, fake_ai):  # noqa: F811
    """State machine with Fake Jev: six "모르겠어요" answers judged insufficient -> NEEDS_DETAIL at
    depth 5, the next intent starts, and confirming the review does not make it COVERED."""
    ctx = flow
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(INSUFFICIENT)] * 6)
    for _ in range(6):
        ctx.answer_and_run(sid, "잘 모르겠어요.")
    state = ctx.get(sid)
    first = state["intents"][0]
    assert (first["coverage"], first["depth"]) == ("NEEDS_DETAIL", 5)
    assert len(fake_ai.calls_for("judge_sufficiency")) == 6  # no seventh question for this intent
    assert state["currentIntentId"] == state["intents"][1]["id"]
    confirmed = confirm(ctx, sid, 0)
    assert confirmed.status_code == 200, confirmed.text
    after = ctx.get(sid)["intents"][0]
    assert (after["coverage"], after["depth"]) == ("NEEDS_DETAIL", 5)
    assert ctx.review(sid, ctx.intents[0]).json()["content"]["needsDetail"] is True

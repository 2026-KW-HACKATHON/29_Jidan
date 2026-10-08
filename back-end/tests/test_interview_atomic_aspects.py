"""Q-INT-1: Jev names one aspect per item and a probe asks one sub-item, so a partial answer is
not followed by a question about the rest of the same bundle.

The compound detectors are pinned on right and wrong samples (the wrong ones are real
gpt-6-luna output from the 2026-10-06/07 live runs) so they catch the defect without flagging
ordinary single questions."""

import pytest

from app.ai.contracts import IntentBrief, QuestionRequest
from app.ai.fake import FakeOutcome
from app.ai.prompts import INSTRUCTIONS
from app.ai.schemas import JUDGE_SCHEMA
from app.interview.flow import fallback_question
from tests.interview_quality import is_compound_aspect, is_compound_question
from tests.test_interview_reviews import flow  # noqa: F401 (fixture)

COMPOUND_QUESTIONS = [
    "홀 서빙과 설거지를 공통으로 하신다고 하셨는데, 먼저 홀 서빙은 어떤 순서와 방법으로 하고 언제 끝났다고 판단하시나요?",
    "홀 서빙과 설거지를 공통으로 하신다고 했고 홀 서빙 순서는 말씀해 주셨는데요, 설거지는 어떤 순서와 방법으로 하고 언제 끝났다고 판단하시나요?",
    "계산과 진열을 공통으로 하신다고 말씀하셨는데, 계산 업무는 어떤 순서와 방법으로 하고 어떻게 끝났다고 판단하나요?",
    "계산과 진열을 공통으로 하시고, 진열은 유통기한이 가까운 상품을 앞으로 빼신다고 하셨는데요, 진열은 어떤 방식으로 하며 언제 끝났다고 판단해요?",
    "홀 서빙과 설거지는 각각 언제 끝났다고 판단하나요?",
    "설거지는 어떤 순서로 하고 완료 기준은 무엇인가요?",
    "주문은 어떤 순서로 받고 예외 상황에는 어떻게 하나요?",
]
SINGLE_QUESTIONS = [
    "홀 서빙은 손님이 나간 뒤 그릇을 치우신다고 하셨는데, 어떤 상태가 되면 홀 서빙이 끝났다고 판단하시나요?",
    "식기세척기를 돌린 뒤 그릇을 선반에 쌓는다고 하셨는데, 어떤 상태가 되면 설거지가 끝났다고 보시나요?",
    "성수 국밥은 근무조를 따로 나누지 않는다고 하셨는데, 다들 몇 시부터 몇 시까지 일하나요?",
    "홀 서빙 순서는 말씀해 주셨는데요, 설거지는 어떤 순서로 하나요?",
    "폐기 등록은 어떤 순서로 해요?",
    "주문은 어떻게 받나요?",
    "설거지는 어떻게 끝났다고 판단하나요?",
    "근무조와 관계없이 모든 직원이 공통으로 하는 일은 무엇인가요?",
    "마감조가 있다고 하셨는데, 마감조는 몇 시에 끝나나요?",
    "근무조를 따로 나누지 않는다고 하셨는데, 업무 중 특정 직원만 맡아서 하는 일이 있나요?",
]
COMPOUND_ASPECTS = [
    "홀 서빙의 작업 순서와 방법, 완료 판단 기준이 없어요.",
    "설거지의 작업 순서와 방법, 끝났다고 판단하는 기준이 확인되지 않았어요.",
    "홀 서빙과 설거지 각각의 완료 기준이 명확하지 않아요.",
    "설거지와 테이블 닦기의 완료 기준이 확인되지 않았어요.",
    "계산의 순서 및 완료 기준",
]
ATOMIC_ASPECTS = [
    "설거지의 작업 순서", "홀 서빙의 완료 기준", "마감조의 종료 시각", "결과 확인의 작업 순서",
    "포스기 사용의 작업 방법", "재고 부족 시의 예외 처리",
    "계산 업무가 완료됐다고 판단하는 기준이 확인되지 않았어요.",
]


# Review labels (team/reviews/b-repro/int-1007, 2026-10-07): real model output; the label is the
# reviewer's, and comments give the reasoning where the first detector disagreed.
REVIEW_PROBES = [
    ('기계를 닦고 쓰레기를 정리한다고 하셨는데, 마감할 때 두 작업은 어떤 순서로 하세요?', False),
    ('마감할 때 어떤 일을 하는지 알려주셨는데, 그 작업들을 어떤 순서로 진행해요?', False),
    ('홀 서빙과 설거지를 공통으로 하신다고 하셨는데, 먼저 홀 서빙은 어떤 순서와 방법으로 하고 언제 끝났다고 판단하시나요?', True),
    ('홀 서빙과 설거지를 공통으로 하신다고 했고 홀 서빙 순서는 말씀해 주셨는데요, 설거지는 어떤 순서와 방법으로 하고 언제 끝났다고 판단하시나요?', True),
    ('홀 서빙은 손님이 나간 뒤 그릇을 치우신다고 하셨는데, 어떤 상태가 되면 홀 서빙이 끝났다고 판단하시나요?', False),
    ('식기세척기를 돌린 뒤 그릇을 선반에 쌓는다고 하셨는데, 어떤 상태가 되면 설거지가 끝났다고 보시나요?', False),
    ('오픈조와 마감조가 있다고 하셨는데, 각 조는 몇 시에 시작하고 끝나며 마감조는 자정을 넘어 다음 날까지 근무하나요?', True),  # 시간과 익일 근무 여부, 두 하위 항목
    ('주문을 받고 음료를 만든다고 하셨는데, 주문을 받을 때 어떤 순서와 방법으로 진행하는지 말씀해 주세요?', True),
    ('음료는 레시피 카드대로 만든다고 하셨는데, 음료별로 어떤 순서와 방법으로 만드나요?', True),
    ('음료는 레시피 카드대로 만드신다고 하셨는데, 카드에 적힌 구체적인 제조 순서는 어떻게 되나요?', False),  # 관용구 '어떻게 되나요'는 방법을 묻지 않는다: 순서 하나
    ('주간, 오후, 야간 세 조로 나뉜다고 하셨는데, 각 근무조는 몇 시부터 몇 시까지 일하나요?', False),
    ('계산과 진열을 공통으로 하신다고 하셨는데, 계산 업무는 어떤 순서와 방법으로 진행해요?', True),
    ('손님이 오면 인사하고 바코드를 찍으며, 담배는 신분증을 확인한다고 하셨는데요, 바코드 스캔부터 결제 완료까지 어떤 순서로 진행해요?', False),  # '…부터 결제 완료까지'는 범위의 끝이지 완료 기준이 아니다: 순서 하나
    ('유통기한이 지난 상품은 폐기 바구니에 넣고 폐기 등록한다고 하셨는데, 폐기 등록은 어떤 순서와 방법으로 해요?', True),
    ('계산과 진열을 공통으로 하시고, 진열은 유통기한이 가까운 상품을 앞으로 빼신다고 하셨는데요, 진열은 어떤 방식으로 하며 언제 끝났다고 판단해요?', True),
    ('손님이 오면 인사하고 바코드를 찍으며 담배는 신분증을 확인한다고 하셨는데요, 그다음 바코드 스캔부터 결제 완료까지 어떤 순서로 진행하고 언제 계산이 끝났다고 판단해요?', True),
    ('근무조를 나누지 않고 모두 같은 시간에 일하신다고 하셨고, 공통 업무는 홀 서빙과 설거지라고 하셨어요. 홀 서빙은 어떤 순서와 방법으로 하고, 언제 끝났다고 판단하시나요?', True),
    ('손님이 나간 뒤 그릇을 식기세척기에 넣는다고 하셨어요. 설거지는 식기세척기에 넣은 뒤 어떤 순서와 방법으로 마무리하고, 언제 끝났다고 판단하시나요?', True),
    ('손님이 나가면 그릇을 식기세척기에 넣고, 가득 차면 돌린 뒤 종류별로 선반에 쌓고 테이블을 소독제로 닦으신다고 하셨어요. 설거지는 어떤 상태가 되면 끝났다고 판단하시나요?', False),
    ('홀 서빙과 설거지를 공통으로 하신다고 하셨는데, 홀 서빙은 어떤 순서와 방법으로 진행해요?', True),
    ('홀 서빙은 손님이 나간 뒤 그릇을 치워 식기세척기에 넣는다고 하셨는데, 설거지는 어떤 순서와 방법으로 해요?', True),
    ('식기세척기를 돌린 뒤 그릇을 종류별로 선반에 쌓는다고 하셨는데, 설거지가 끝났다고 판단하는 기준은 무엇인가요?', False),
    ('설거지는 별도로 정한 완료 기준이 없다고 하셨는데, 홀 서빙은 어떤 상태가 되면 끝났다고 판단해요?', False),
]
REVIEW_ASPECTS = [
    ('기계 닦기와 쓰레기 정리의 작업 순서가 없어요.', False),  # 경계: 두 업무 사이의 순서 하나로 본다(업무마다의 완료 기준·방법과 달리 나눌 수 없음)
    ('각 작업을 어디까지 하면 완료인지 기준이 없어요.', False),
    ('마감 중 예외 상황이나 문제가 생겼을 때의 처리 방법이 없어요.', False),  # 예외 상황의 처리 방법 하나('방법'은 예외 처리의 일부)
    ('마감 작업의 전체 종류와 순서가 확인되지 않았어요.', True),  # 종류와 순서, 두 측면
    ('커피 머신 청소 방법과 순서를 알 수 없어요.', True),
    ('쓰레기 정리 방법과 완료 기준이 없어요.', True),
    ('각 작업의 완료 기준이 확인되지 않았어요.', False),
    ('마감 작업의 전체 종류와 실행 순서가 확보되지 않았어요.', True),  # 종류와 순서, 두 측면
    ('커피 머신을 닦는 구체적인 순서를 알 수 없어요.', False),
    ('커피 머신 청소의 완료 기준을 알 수 없어요.', False),
    ('마감 작업의 종류와 순서', True),  # 종류와 순서, 두 측면
    ('각 작업의 완료 기준', False),
    ('마감 작업의 종류와 순서가 확인되지 않았어요.', True),  # 종류와 순서, 두 측면
    ('홀 서빙의 작업 순서와 방법, 완료 판단 기준이 없어요.', True),
    ('설거지의 작업 순서와 방법, 완료 판단 기준이 없어요.', True),
    ('설거지의 작업 순서와 방법, 끝났다고 판단하는 기준이 확인되지 않았어요.', True),
    ('홀 서빙과 설거지 각각의 완료 기준이 명확하지 않아요.', True),
    ('홀 서빙을 끝났다고 판단하는 기준이 확인되지 않았어요.', False),
    ('설거지와 테이블 닦기의 완료 기준이 확인되지 않았어요.', True),
    ('설거지와 테이블 닦기를 언제 완료한 것으로 보는지 기준이 명시되지 않았어요.', True),  # 두 업무의 완료 기준
]


@pytest.mark.parametrize("text,compound", REVIEW_PROBES)
def test_reviewer_labelled_probes(text, compound):
    assert is_compound_question(text) is compound


@pytest.mark.parametrize("text,compound", REVIEW_ASPECTS)
def test_reviewer_labelled_aspects(text, compound):
    assert is_compound_aspect(text) is compound


@pytest.mark.parametrize("text", COMPOUND_QUESTIONS)
def test_compound_questions_are_detected(text):
    assert is_compound_question(text)


@pytest.mark.parametrize("text", SINGLE_QUESTIONS)
def test_single_questions_are_not_flagged(text):
    assert not is_compound_question(text)


@pytest.mark.parametrize("text", COMPOUND_ASPECTS)
def test_compound_aspects_are_detected(text):
    assert is_compound_aspect(text)


@pytest.mark.parametrize("text", ATOMIC_ASPECTS)
def test_atomic_aspects_are_not_flagged(text):
    assert not is_compound_aspect(text)


# --- the instructions (what the model is told) --------------------------------------------------

JEV = INSTRUCTIONS["judge_sufficiency"]
QUESTION = INSTRUCTIONS["generate_question"]


def test_jev_names_one_aspect_of_one_task_per_item_core_first():
    assert "한 항목은 업무 하나의 측면 하나" in JEV and "<대상>의 <측면>" in JEV
    assert "여러 측면이나 여러 업무를 한 항목에 묶지 않는다" in JEV
    assert "두 항목으로 나눠" in JEV and "다음 추가 질문은 첫 항목" in JEV
    assert "이미 답한 측면은 넣지 않는다" in JEV
    item = JUDGE_SCHEMA["properties"]["missing_aspects"]
    assert "측면 하나" in item["items"]["description"] and "묶지 않음" in item["description"]


def test_jev_aspect_rule_keeps_the_b04_policy():
    """Unknown and non-answers stay missing; "no further rule" does not replace the core content."""
    rule = JEV[JEV.index("이미 답한 측면은 넣지 않는다"):][:200]
    assert "3)·4)에 해당하는" in rule and "그대로 남긴다" in rule and "기본 내용" in rule


def test_probe_asks_the_first_aspect_one_sub_item_and_skips_answered_ones():
    assert "missing_aspects의 첫 항목 하나만" in QUESTION
    assert "업무 하나의 하위 항목 하나" in QUESTION and "두 업무를 한 질문에 묻지 않는다" in QUESTION
    assert "이미 답한 하위 항목" in QUESTION and "다시 묻지 않는다" in QUESTION
    # The bad examples the instruction shows are exactly what the detector flags.
    assert is_compound_question("어떤 순서로 하고 언제 끝났다고 판단하나요?")
    assert is_compound_question("홀 서빙과 설거지는 각각 언제 끝나나요?")


# --- the server side: what a probe is built from --------------------------------------------------

INTENT = IntentBrief(key="COMMON_TASKS", stage="COMMON_TASKS", base_question="공통 업무는 무엇인가요?",
                     coverage_criteria="업무별 작업 순서, 완료 기준")


def test_fallback_probe_asks_only_the_first_aspect():
    request = QuestionRequest(kind="PROBE", intent=INTENT, depth=2,
                              missing_aspects=("설거지의 작업 순서", "홀 서빙의 완료 기준"))
    text = fallback_question({"request": request.model_dump(mode="json")})
    assert "설거지의 작업 순서" in text and "홀 서빙" not in text and not is_compound_question(text)


ATOMIC_JUDGEMENTS = [
    {"sufficient": False, "probability": 0.2, "missing_aspects": ["홀 서빙의 작업 순서", "설거지의 작업 순서",
                                                                  "홀 서빙의 완료 기준"]},
    {"sufficient": False, "probability": 0.3, "missing_aspects": ["설거지의 작업 순서", "홀 서빙의 완료 기준"]},
    {"sufficient": False, "probability": 0.4, "missing_aspects": ["홀 서빙의 완료 기준"]},
    {"sufficient": True, "probability": 0.9, "missing_aspects": []},
]


def test_each_probe_gets_the_current_aspects_in_order_and_the_whole_dialogue(flow, fake_ai):  # noqa: F811
    """State machine with Fake Jev: every probe request leads with Jev's first aspect, carries
    the dialogue so far, and no aspect is asked twice."""
    sid = flow.started()
    flow.answer_and_run(sid)  # WORK_STRUCTURE: sufficient by default
    fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(j) for j in ATOMIC_JUDGEMENTS])
    answers = ["홀 서빙이랑 설거지요.", "물이랑 수저를 드리고 주문을 받아요.", "가득 차면 돌리고 선반에 쌓아요.",
               "그 밖에 따로 정한 건 없어요."]
    for answer in answers:
        flow.answer_and_run(sid, answer)
    probes = [c.data for c in fake_ai.calls_for("generate_question") if c.data["kind"] == "PROBE"]
    asked = [p["missing_aspects"][0] for p in probes]
    assert asked == ["홀 서빙의 작업 순서", "설거지의 작업 순서", "홀 서빙의 완료 기준"]
    assert len(set(asked)) == len(asked) and not any(is_compound_aspect(a) for a in asked)
    assert [len(p["dialogue"]) for p in probes] == [1, 2, 3]  # the answered sub-items are visible
    assert probes[-1]["dialogue"][-1]["answer"] == answers[2]
    state = flow.get(sid)
    assert state["intents"][1]["coverage"] == "COVERED" and state["intents"][1]["depth"] == 3

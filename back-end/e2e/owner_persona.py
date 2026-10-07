"""A scripted cafe owner for the interview evaluation (`e2e.interview_eval`). No LLM plays the owner.

The owner runs a two-shift cafe (오픈조 07:00-15:00, 마감조 15:00-22:30). For every interview
intent the answer is chosen by (intent key, question depth) only, so a run is reproducible:

* most intents start vague (to draw PROBE questions) and get specific on follow-ups; a depth past
  the script repeats the last, most specific answer;
* RULES is answered "해당 없음" (no store rules), which Jev should accept at depth 0;
* EXCEPTIONS stays vague at every depth, so it reaches depth 5 and ends NEEDS_DETAIL. With
  `skip_depth_five` it turns specific at depth 1 instead (fewer live calls).

`COMPLETE_FROM` is the depth from which the persona's answers cover the intent (None: never);
the fake Jev (`install_fake`, `E2E_AI_SCRIPT=persona`) judges by it, and the fake evaluation run
asserts the exact depths it implies.
"""
from collections.abc import Mapping

from app.ai.aspects import aspects_for
from app.ai.contracts import IntentBrief
from app.ai.fake import FakeAiProvider

STORE = "카페 (오픈조 07:00-15:00, 마감조 15:00-22:30)"
NOT_APPLICABLE = "따로 정해 둔 매장 규칙은 없어요. 이 항목은 해당 없음으로 해 주세요."
EXCEPTIONS_SPECIFIC = (
    "재료가 떨어지면 메뉴판에 품절 스티커를 붙이고 손님께 비슷한 다른 메뉴를 권해요. 에스프레소 머신이 고장 나면 "
    "커피 주문은 받지 말고 바로 저에게 전화해요. 손님이 음료에 불만을 말하면 먼저 사과하고 음료를 새로 만들어 "
    "드려요. 환불을 원하시면 직접 처리하지 말고 저에게 전화해서 확인받아요."
)
FALLBACK = "그 부분은 아직 따로 정해 두지 않았어요."

ANSWERS: dict[str, tuple[str, ...]] = {
    "WORK_STRUCTURE": (
        "오전이랑 오후 두 조로 나눠서 일해요.",
        ("오픈조는 아침 7시부터 오후 3시까지, 마감조는 오후 3시부터 밤 10시 30분까지 일해요. 두 조 모두 자정 "
        "전에 끝나요."),
    ),
    "COMMON_TASKS": (
        "손님 응대하고 음료 만들고, 청소도 하고요.",
        ("주문은 포스에서 메뉴를 누르고 결제까지 받아요. 음료는 바 옆에 붙은 레시피 카드대로 만들어서 픽업대에 "
        "올리고 주문 번호를 불러요. 손님이 나간 테이블은 바로 컵과 쓰레기를 치우고 행주로 닦아요."),
        ("주문은 영수증이 출력되면 끝난 거예요. 음료는 손님이 받아 가면 끝이고, 테이블은 컵과 쓰레기가 없고 물기 "
        "없이 닦여 있으면 다 된 거예요."),
    ),
    "SHIFT_TASKS": (
        "오픈조는 오픈 준비, 마감조는 마감 정리를 해요.",
        ("오픈조는 7시에 와서 불을 켜고 에스프레소 머신 전원을 켜서 예열하고, 그라인더에 원두를 채운 다음 7시 "
        "30분에 문을 열어요. 첫 샷이 25초 안팎으로 정상 추출되면 오픈 준비가 끝난 거예요. 마감조는 밤 10시에 "
        "주문을 마감하고, 머신 그룹헤드와 스팀 노즐을 청소하고, 바닥을 쓸고 닦은 다음 포스에서 마감 정산을 해요. "
        "정산 영수증을 금고에 넣고 출입문이 잠긴 걸 확인하면 마감이 끝나요."),
    ),
    "RULES": (NOT_APPLICABLE,),
    "EQUIPMENT": (
        "에스프레소 머신이랑 제빙기를 써요.",
        ("에스프레소 머신은 포터필터에 원두를 18그램 담고 탬핑한 뒤 그룹헤드에 끼우고 추출 버튼을 눌러요. "
        "제빙기는 뚜껑을 열고 전용 얼음 스쿱으로만 얼음을 퍼요."),
        ("에스프레소 머신은 마감 때 그룹헤드를 세척제로 백플러싱하고 스팀 노즐을 젖은 행주로 닦아요. 제빙기는 "
        "일요일 마감 때 얼음을 비우고 안쪽을 닦아요. 스팀 노즐은 뜨거우니 맨손으로 잡지 말고, 제빙기에는 손을 "
        "넣지 말고 꼭 스쿱을 써 주세요."),
    ),
    "EXCEPTIONS": (
        "가끔 이런저런 일이 생기는데 그때그때 잘 처리하면 돼요.",
        "손님 불만이나 기계 문제 같은 거요. 상황 봐서 적당히 하면 돼요.",
        "글쎄요, 경우마다 달라서 딱 정하기가 어려워요.",
        "그건 일하다 보면 자연스럽게 알게 돼요.",
        "웬만한 건 눈치껏 하면 돼요.",
        "음... 그냥 상식적으로 하면 돼요.",
    ),
}
# Depth from which the answers cover the intent (what the fake Jev accepts); None: never.
COMPLETE_FROM: dict[str, int | None] = {
    "WORK_STRUCTURE": 1, "COMMON_TASKS": 2, "SHIFT_TASKS": 1, "RULES": 0, "EQUIPMENT": 2, "EXCEPTIONS": None,
}
NEEDS_DETAIL = "EXCEPTIONS"
SKIPPABLE_COMPLETE_FROM = 1  # EXCEPTIONS with skip_depth_five
MAX_DEPTH = 5
# What the owner tells the review of WORK_STRUCTURE (exercises revise_structure).
REVIEW_CORRECTION = "마감조는 밤 10시 30분이 아니라 밤 11시에 끝나요."


def answer(intent_key: str, depth: int, *, skip_depth_five: bool = False) -> str:
    """The owner's answer to the question of `intent_key` at `depth` (deterministic)."""
    if depth < 0:
        raise ValueError("depth must be 0 or more")
    if skip_depth_five and intent_key == NEEDS_DETAIL and depth >= SKIPPABLE_COMPLETE_FROM:
        return EXCEPTIONS_SPECIFIC
    script = ANSWERS.get(intent_key)
    if not script:
        return FALLBACK
    return script[min(depth, len(script) - 1)]


def complete_from(intent_key: str, *, skip_depth_five: bool = False) -> int | None:
    if skip_depth_five and intent_key == NEEDS_DETAIL:
        return SKIPPABLE_COMPLETE_FROM
    return COMPLETE_FROM.get(intent_key)


def expected_depth(intent_key: str, *, skip_depth_five: bool = False) -> int:
    """The depth an intent ends at when Jev agrees with the persona (5 when it never covers)."""
    found = complete_from(intent_key, skip_depth_five=skip_depth_five)
    return MAX_DEPTH if found is None else found


def expected_calls(intent_keys, *, skip_depth_five: bool = False, corrections: int = 1) -> dict[str, int]:
    """Live calls of one evaluation run when Jev agrees with the persona: per intent one question
    and one judgement per depth, a summary per intent, `corrections` review corrections, one draft."""
    questions = sum(expected_depth(key, skip_depth_five=skip_depth_five) + 1 for key in intent_keys)
    keys = list(intent_keys)
    return {"generate_question": questions, "judge_sufficiency": questions, "summarize_intent": len(keys),
            "revise_structure": corrections, "compose_draft": 1}


def worst_case_calls(intent_count: int, *, corrections: int = 1) -> int:
    """Every intent probed to depth 5: 6 questions and 6 judgements each, plus the writing calls."""
    return intent_count * (MAX_DEPTH + 1) * 2 + intent_count + corrections + 1


def covered(intent_key: str, answer_text: str) -> bool:
    """Whether `answer_text` is one of the persona's covering answers for `intent_key` (in either
    mode). The fake Jev judges by this, so it needs no run mode."""
    if intent_key == NEEDS_DETAIL and answer_text == EXCEPTIONS_SPECIFIC:
        return True
    start = COMPLETE_FROM.get(intent_key)
    script = ANSWERS.get(intent_key, ())
    return start is not None and answer_text in script[start:]


def judge(data: Mapping) -> dict:
    """Fake Jev (Responses shape) for the persona: sufficient iff the latest answer covers."""
    intent = data["intent"]
    if covered(intent["key"], data["dialogue"][-1]["answer"]):
        return {"sufficient": True, "probability": 0.92, "missing_aspects": []}
    labels = [a.label for a in aspects_for(IntentBrief.model_validate(intent)).aspects]
    return {"sufficient": False, "probability": 0.18, "missing_aspects": labels[:2]}


def install_fake(fake: FakeAiProvider) -> FakeAiProvider:
    return fake.on("judge_sufficiency", judge)

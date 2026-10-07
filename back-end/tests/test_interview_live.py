"""Real OpenAI end-to-end smoke of the interview API (opt-in: OPENAI_API_KEY and JIDAN_RUN_OPENAI=1).

Three owners answer the first two intents in text through the real endpoints and task runner.
It checks that the configured model drives the state machine (questions, Jev, summaries) and
that every question is one polite Korean question; it does not grade answer quality. Set
JIDAN_INTERVIEW_SAMPLE_DIR to keep each dialogue as JSON (the samples in team/reviews).
"""

import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.interview.question_set import INTENTS_V1
from tests.api_contract import login
from tests.factories import NOW, make_store, make_user
from tests.interview_factories import InterviewDriver, ensure_question_set
from tests.interview_quality import is_compound_aspect, is_compound_question, kinds_in

pytestmark = pytest.mark.openai

FALLBACK_ANSWER = "그 밖에 따로 정한 건 없어요."
SCENARIOS = {
    "cafe": ("월계 커피", "CAFE", {
        "WORK_STRUCTURE": [
            "오픈조랑 마감조가 있어요.",
            "오픈조는 아침 8시부터 오후 3시까지, 마감조는 오후 3시부터 밤 10시까지예요. 둘 다 그날 끝나요.",
            "주말도 시간은 똑같고, 두 조가 겹치는 시간은 없어요.",
        ],
        "COMMON_TASKS": [
            "주문 받고 음료 만들어요.",
            ("손님이 오면 먼저 인사하고 포스기로 주문을 받아요. 결제가 끝나면 진동벨을 드리고, 음료는 벽에 붙은 "
             "레시피 카드대로 만들어요. 음료가 나가면 진동벨을 울리고 빈 컵은 바로 치워요."),
            "레시피 카드에 없는 메뉴 변경은 안 받고, 컵이 다 나가면 테이블을 행주로 닦으면 끝이에요.",
        ],
    }),
    "restaurant": ("성수 국밥", "RESTAURANT", {
        "WORK_STRUCTURE": [
            "따로 조를 나누지 않아요. 다들 오전 11시에 와서 밤 9시에 끝나요.",
            "쉬는 시간은 오후 3시부터 4시까지 한 시간이고, 그날 안에 끝나요.",
        ],
        "COMMON_TASKS": [
            "홀 서빙이랑 설거지요.",
            ("손님이 앉으면 물이랑 수저를 먼저 드리고 주문을 받아서 주방 프린터로 넣어요. 음식이 나오면 번호표대로 "
             "가져다 드리고, 손님이 나가면 바로 그릇을 치워 식기세척기에 넣어요."),
            "식기세척기는 가득 차면 돌리고, 끝나면 그릇을 선반에 종류별로 쌓아요. 테이블은 소독제를 뿌려서 닦아요.",
        ],
    }),
    "convenience": ("공릉 편의점", "CONVENIENCE_STORE", {
        "WORK_STRUCTURE": [
            "주간, 오후, 야간 세 조예요.",
            "주간은 오전 7시부터 오후 3시, 오후조는 오후 3시부터 밤 11시, 야간은 밤 11시부터 다음 날 아침 7시까지예요.",
            "주말도 같고, 교대할 때 10분 정도 인수인계해요.",
        ],
        "COMMON_TASKS": [
            "계산이랑 진열이요.",
            ("손님이 오면 인사하고 바코드를 찍어서 계산해요. 담배는 신분증을 꼭 확인하고요. 손님이 없을 때는 "
             "유통기한이 가까운 상품을 앞으로 빼서 진열해요."),
            "유통기한이 지난 상품은 폐기 바구니에 넣고 폐기 등록을 해요. 인수인계 때 시재를 같이 세요.",
        ],
    }),
}


def _assert_one_polite_question(text: str) -> None:
    assert text.count("?") <= 1, text
    assert any(ending in text for ending in ("요", "까", "세요")), text  # 해요체/합쇼체
    assert "다른 매장" not in text, text


def _probe_aspects(engine, session_id: str) -> list[str]:
    """The aspect each follow-up question was generated for (first item of its request)."""
    from app.db.models import BackgroundTask

    with Session(engine) as db:
        tasks = db.scalars(select(BackgroundTask).where(
            BackgroundTask.kind == "FOLLOWUP_GENERATION", BackgroundTask.subject_id == session_id,
        ).order_by(BackgroundTask.created_at)).all()
        return [t.payload["request"]["missing_aspects"][0] for t in tasks]


def _sub_item(aspect: str) -> tuple[str, frozenset[str]]:
    """(task, aspect kinds) of a "<대상>의 <측면>" aspect: the same pair asked twice is a repeat."""
    task = aspect.split("의 ")[0].strip() if "의 " in aspect else aspect
    return task, frozenset(kinds_in(aspect))


@pytest.mark.parametrize("db_engine", ["sqlite"], indirect=True)  # real calls: one database is enough
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_live_interview_first_two_intents(api, db_engine, scenario):
    name, industry, answers = SCENARIOS[scenario]
    with Session(db_engine) as db:
        intents = ensure_question_set(db)
        owner = make_user(db, "OWNER")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW, name=name,
                           industry=industry)
        db.commit()
        owner_id, store_id, intent_ids = owner.id, store.id, [i.id for i in intents]
    driver = InterviewDriver(api, login(api, owner_id), db_engine, store_id)
    sid = driver.started()
    keys = {intent_id: d.key for intent_id, d in zip(intent_ids, INTENTS_V1, strict=True)}
    used = {key: 0 for key in answers}
    dialogue = []
    for _ in range(14):
        state = driver.get(sid)
        if state["intents"][1]["coverage"] != "PENDING" or state["phase"] != "COLLECTING":
            break
        [question] = state["questions"]
        key = keys[question["intentId"]]
        _assert_one_polite_question(question["text"])
        script = answers[key]
        answer = script[used[key]] if used[key] < len(script) else FALLBACK_ANSWER
        used[key] += 1
        dialogue.append({"intent": key, "kind": question["kind"], "depth": question["depth"],
                         "question": question["text"], "answer": answer})
        response = driver.answer(sid, answer, question=question["id"], revision=state["revision"])
        assert response.status_code == 202, response.text
        driver.run()
    state = driver.get(sid)
    assert state["status"] == "IN_PROGRESS", state["error"]
    assert state["intents"][0]["coverage"] in ("COVERED", "NEEDS_DETAIL")
    assert state["intents"][1]["coverage"] in ("COVERED", "NEEDS_DETAIL")
    reviews = driver.reviews(sid)["items"]
    assert [r["status"] for r in reviews[:2]] == ["READY", "READY"], reviews
    work = reviews[0]["content"]
    assert work["summary"] and (work["shifts"] or work["missingInformation"])
    if state["phase"] == "COLLECTING":  # the third intent's base question, adapted to the dialogue
        _assert_one_polite_question(state["questions"][0]["text"])
    # Q-INT-1: every probe asks one sub-item of one task, and no sub-item is asked twice.
    asked = _probe_aspects(db_engine, sid)
    probes = [t["question"] for t in dialogue if t["kind"] == "PROBE"]
    assert not [q for q in probes if is_compound_question(q)], probes
    assert not [a for a in asked if is_compound_aspect(a)], asked
    assert len({_sub_item(a) for a in asked}) == len(asked), asked

    out = os.getenv("JIDAN_INTERVIEW_SAMPLE_DIR")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        record = {"scenario": scenario, "store": {"name": name, "industry": industry},
                  "dialogue": dialogue, "probe_aspects": asked, "intents": state["intents"][:2],
                  "reviews": [r["content"] for r in reviews[:2]],
                  "next_question": state["questions"][0]["text"] if state["questions"] else None}
        Path(out, f"{scenario}-{uuid.uuid4().hex[:6]}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2))


def test_live_partially_answered_bundle_is_not_asked_again():
    """The 2026-10-07 restaurant dialogue at the point where the old prompts asked again: the owner
    has answered the order of 홀 서빙 and of 설거지 (to bundled questions). The next probe must ask
    one sub-item that was not answered yet (two calls: Jev, question)."""
    from app.ai import get_ai_provider
    from app.ai.contracts import (
        DialogueTurn,
        IntentBrief,
        QuestionRequest,
        StoreContext,
        SufficiencyRequest,
    )

    common = INTENTS_V1[1]
    intent = IntentBrief(key=common.key, stage=common.stage, base_question=common.base_question,
                         coverage_criteria=common.coverage_criteria)
    store = StoreContext(name="성수 국밥", industry="음식점")
    dialogue = (
        DialogueTurn(depth=0, question="근무조와 관계없이 모든 직원이 공통으로 하는 일은 무엇인가요?",
                     answer="홀 서빙이랑 설거지요."),
        DialogueTurn(depth=1, question="홀 서빙과 설거지를 공통으로 하신다고 하셨는데, 먼저 홀 서빙은 어떤 순서와 방법으로 "
                     "하고 언제 끝났다고 판단하시나요?",
                     answer="손님이 앉으면 물이랑 수저를 먼저 드리고 주문을 받아서 주방 프린터로 넣어요. 음식이 나오면 "
                     "번호표대로 가져다 드리고, 손님이 나가면 바로 그릇을 치워 식기세척기에 넣어요."),
        DialogueTurn(depth=2, question="홀 서빙 순서는 말씀해 주셨는데요, 설거지는 어떤 순서와 방법으로 하고 언제 끝났다고 "
                     "판단하시나요?",
                     answer="식기세척기는 가득 차면 돌리고, 끝나면 그릇을 선반에 종류별로 쌓아요. 테이블은 소독제를 "
                     "뿌려서 닦아요."),
    )
    provider = get_ai_provider()
    judgement = provider.judge_sufficiency(SufficiencyRequest(intent=intent, dialogue=dialogue, depth=2, store=store))
    assert not [a for a in judgement.missing_aspects if is_compound_aspect(a)], judgement.missing_aspects
    answered = {("홀 서빙", frozenset({"순서"})), ("설거지", frozenset({"순서"}))}
    assert not answered & {_sub_item(a) for a in judgement.missing_aspects}, judgement.missing_aspects
    if judgement.sufficient:
        return
    question = provider.generate_question(QuestionRequest(
        kind="PROBE", intent=intent, depth=3, dialogue=dialogue, missing_aspects=judgement.missing_aspects,
        store=store))
    _assert_one_polite_question(question.text)
    assert not is_compound_question(question.text), question.text
    print(json.dumps({"missing_aspects": judgement.missing_aspects, "question": question.text}, ensure_ascii=False))

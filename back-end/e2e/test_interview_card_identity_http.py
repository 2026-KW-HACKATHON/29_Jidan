"""Card identity after merging two lists, through real HTTP and committed turns."""
import uuid

from sqlalchemy.orm import Session

from app.db.models import InterviewSession
from e2e.interview_helpers import interview_case, scenario_server


def merged_cards_provider():
    from app.ai.fake import FakeAiProvider

    def question(data):
        previous = data.get("previous_cards", [])

        def card(items):
            return {"type": "LIST", "title": "업무 예시", "footer": None, "items": items}

        if not previous:
            cards = [card([{"id": None, "label": label, "description": None, "status": None}])
                     for label in ("재고 정리", "시재 점검")]
        else:
            items = {}
            for prior in previous:
                for item in prior["items"]:
                    items.setdefault(item["id"], {**item, "status": None})
            cards = [card(list(items.values()))]
        return {"question": "업무 순서를 알려 주세요.", "guidance": None, "guidanceCards": cards}

    return (FakeAiProvider().on("generate_question", question)
            .on("judge_sufficiency", lambda _data: {
                "sufficient": False, "probability": 0.2, "missing_aspects": ["업무 순서"],
            }))


def test_merged_list_identity_survives_repeated_questions(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_interview_card_identity_http:merged_cards_provider") as origin,
          interview_case(real_db, origin) as case):
        started = case.start()
        state = case.wait(started["id"], lambda body: body["phase"] == "COLLECTING")
        initial = state["questions"][0]["guidanceCards"]
        assert len(initial) == 2
        merged = None
        question_cards = {state["questions"][0]["id"]: initial}
        for depth in (1, 2, 3):
            previous_revision = state["revision"]
            key = str(uuid.uuid4())
            response = case.answer(state, key=key)
            assert response.status_code == 202, response.text
            replay = case.answer(state, key=key)
            assert replay.status_code == 202 and replay.json() == response.json()
            state = case.wait(state["id"], lambda body: body["phase"] == "COLLECTING")
            question = state["questions"][0]
            assert question["depth"] == depth
            assert state["revision"] > previous_revision
            [current] = question["guidanceCards"]
            if merged is None:
                merged = current
                assert merged["id"] not in {card["id"] for card in initial}
                assert {item["id"] for item in merged["items"]} == {
                    item["id"] for card in initial for item in card["items"]}
            else:
                assert current == merged
            question_cards[question["id"]] = question["guidanceCards"]
            assert case.get(state["id"])["questions"] == state["questions"]
        saved = {turn.id: turn.guidance_cards for turn in case.turns(state["id"])
                 if turn.turn_kind == "QUESTION"}
        assert saved == question_cards
        with Session(real_db) as db:
            assert db.get(InterviewSession, state["id"]).revision == state["revision"]

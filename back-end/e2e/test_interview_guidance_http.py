"""Real HTTP/background runner and independent MySQL commit verification."""
from sqlalchemy.orm import Session

from app.db.models import InterviewSession, ManualVersion
from e2e.interview_helpers import interview_case, scenario_server


def test_interview_question_is_committed_and_restored(real_db, tmp_path):
    with scenario_server(tmp_path) as origin, interview_case(real_db, origin) as case:
        started = case.start()
        state = case.wait(started["id"], lambda body: body["phase"] == "COLLECTING")
        assert len(state["questions"]) == 1
        question = state["questions"][0]
        [turn] = case.turns(state["id"])
        assert (turn.id, turn.content, turn.turn_kind) == (question["id"], question["text"], "QUESTION")
        assert turn.guidance is None and turn.guidance_cards == []
        with Session(real_db) as db:
            saved = db.get(InterviewSession, state["id"])
            assert saved.revision == state["revision"] == 2
            assert saved.processing_task_id is None
            assert db.get(ManualVersion, saved.manual_version_id).status == "DRAFT"
        assert case.get(state["id"])["questions"] == state["questions"]


def guidance_provider():
    """Provider fixture only; application routing/task/database code remains real."""
    import time

    from app.ai.fake import FakeAiProvider

    def question(data):
        previous = data.get("previous_cards", [])
        item_id = previous[0]["items"][0]["id"] if previous else None
        return {"question": "재고 정리 순서를 알려 주세요.", "guidance": "실제 매장 기준으로 알려 주세요.",
                "guidanceCards": [{"type": "PROGRESS_CHECKLIST", "title": "진행", "footer": None,
                                   "items": [{"id": item_id, "label": "재고 정리", "description": None,
                                              "status": "CURRENT"}]}]}

    def judge(_data):
        time.sleep(0.5)  # permit HTTP GET during the real evaluation task
        return {"sufficient": False, "probability": 0.2, "missing_aspects": ["청소 기준"]}

    return FakeAiProvider().on("generate_question", question).on("judge_sufficiency", judge)


def test_guidance_and_answer_snapshot_are_committed_and_stable_over_http(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_interview_guidance_http:guidance_provider") as origin,
          interview_case(real_db, origin) as case):
        started = case.start()
        state = case.wait(started["id"], lambda body: body["phase"] == "COLLECTING")
        question = state["questions"][0]
        assert question["guidance"] and len(question["guidanceCards"]) == 1
        [turn] = case.turns(state["id"])
        assert turn.guidance_cards == question["guidanceCards"]
        response = case.answer(state)
        assert response.status_code == 202, response.text
        expected = {**question, "answered": True}
        assert response.json()["lastAnsweredQuestion"] == expected
        assert case.get(state["id"])["lastAnsweredQuestion"] == expected
        following = case.wait(state["id"], lambda body: body["phase"] == "COLLECTING")
        assert following["lastAnsweredQuestion"] is None
        assert following["questions"][0]["guidanceCards"][0]["id"] == question["guidanceCards"][0]["id"]
        original = case.turns(state["id"])[0]
        assert original.guidance_cards == question["guidanceCards"]
        assert case.get(state["id"])["questions"] == following["questions"]

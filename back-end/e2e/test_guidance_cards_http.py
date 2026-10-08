"""PR170 integration uses the current question-card and ANSWER snapshot contract.

The former session-column/state-card scenarios are superseded by
 test_interview_guidance_http, test_interview_snapshot_rollout_http and
 test_interview_photo_suggestions_http. This scenario covers Decisions alongside
 current model-authored cards through a real server and independent MySQL reads.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import InterviewEvaluation, InterviewTurn
from e2e.interview_helpers import interview_case, scenario_server


def integrated_provider():
    from app.ai.fake import FakeAiProvider, FakeOutcome

    def question(data):
        previous = data.get("previous_cards", [])
        item_id = previous[0]["items"][0]["id"] if previous else None
        return {"question": "실제 업무 순서를 알려 주세요.", "guidance": "매장 기준으로 알려 주세요.",
                "guidanceCards": [{"type": "LIST", "title": "답변 안내", "footer": None,
                                   "items": [{"id": item_id, "label": "작업 순서", "description": None,
                                              "status": None}]}]}

    return FakeAiProvider(judge_backend="decisions").on("generate_question", question).script(
        "judge_sufficiency", FakeOutcome.predicates(default=0.1, not_applicable=0.0))


def test_decisions_preserve_stored_question_cards_and_answer_snapshot(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_guidance_cards_http:integrated_provider") as origin,
          interview_case(real_db, origin) as case):
        sid = case.start()["id"]
        state = case.wait(sid, lambda body: body["phase"] == "COLLECTING")
        question = state["questions"][0]
        answer = case.answer(state)
        assert answer.status_code == 202, answer.text
        assert answer.json()["lastAnsweredQuestion"] == {**question, "answered": True}
        following = case.wait(sid, lambda body: body["phase"] == "COLLECTING")
        assert following["questions"][0]["guidanceCards"] == question["guidanceCards"]
        with Session(real_db) as db:
            evaluation = db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id == sid)).one()
            assert "decisions" in evaluation.evaluation_config_version
            assert evaluation.missing_aspects and evaluation.applied_at
            original = db.get(InterviewTurn, question["id"])
            saved_answer = db.scalars(select(InterviewTurn).where(
                InterviewTurn.reply_to_question_turn_id == question["id"], InterviewTurn.turn_kind == "ANSWER")).one()
            assert original.guidance_cards == saved_answer.guidance_cards == question["guidanceCards"]
        assert case.get(sid)["questions"] == following["questions"]

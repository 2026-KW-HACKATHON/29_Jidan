"""Sufficiency persistence through real HTTP and the background AI worker."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import InterviewEvaluation, InterviewSession
from e2e.interview_helpers import interview_case, scenario_server


def test_successful_evaluation_commits_empty_known_aspects_once(real_db, tmp_path):
    with scenario_server(tmp_path) as origin, interview_case(real_db, origin) as case:
        started = case.start()
        state = case.wait(started["id"], lambda body: body["phase"] == "COLLECTING")
        key = str(uuid.uuid4())
        response = case.answer(state, key=key)
        assert response.status_code == 202, response.text
        next_state = case.wait(state["id"], lambda body: body["currentIntentId"] != state["currentIntentId"])
        with Session(real_db) as db:
            [evaluation] = list(db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == state["id"])))
            assert evaluation.status == "SUCCEEDED" and evaluation.applied_at is not None
            assert evaluation.needs_follow_up is False
            assert evaluation.missing_aspects == []
            assert db.get(InterviewSession, state["id"]).revision >= next_state["revision"]
            evaluation_id = evaluation.id
        replay = case.answer(state, key=key)
        assert replay.status_code == 202 and replay.json() == response.json()
        with Session(real_db) as db:
            [evaluation] = list(db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == state["id"])))
            assert evaluation.id == evaluation_id and evaluation.missing_aspects == []
        assert case.get(state["id"])["intents"][0]["coverage"] == "COVERED"

"""Real HTTP/background runner and independent MySQL commit verification."""
from sqlalchemy.orm import Session

from app.db.models import InterviewSession, ManualVersion
from e2e.interview_helpers import interview_case


def test_interview_question_is_committed_and_restored(real_db, base_url):
    with interview_case(real_db, base_url) as case:
        started = case.start()
        state = case.wait(started["id"], lambda body: body["phase"] == "COLLECTING")
        assert len(state["questions"]) == 1
        question = state["questions"][0]
        [turn] = case.turns(state["id"])
        assert (turn.id, turn.content, turn.turn_kind) == (question["id"], question["text"], "QUESTION")
        assert turn.guidance is None and turn.guidance_cards is None
        with Session(real_db) as db:
            saved = db.get(InterviewSession, state["id"])
            assert saved.revision == state["revision"] == 2
            assert saved.processing_task_id is None
            assert db.get(ManualVersion, saved.manual_version_id).status == "DRAFT"
        assert case.get(state["id"])["questions"] == state["questions"]

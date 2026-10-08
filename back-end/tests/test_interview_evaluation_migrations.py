"""Old successful evaluations remain unknown, never fabricated empty results."""
from alembic import command
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import InterviewEvaluation, InterviewQuestionSet, InterviewTurn
from tests.conftest import alembic_config
from tests.factories import NOW, make_interview, make_manual_draft, make_store
from tests.interview_factories import ensure_question_set


def test_old_evaluation_aspects_remain_unknown_after_upgrade(db_engine):
    with Session(db_engine) as db:
        intents = ensure_question_set(db)
        version = make_manual_draft(db, make_store(db))
        interview = make_interview(db, version, db.get(InterviewQuestionSet, intents[0].question_set_id), intents)
        turn = InterviewTurn(session_id=interview.id, intent_id=intents[0].id, turn_no=1,
                             speaker="AI", turn_kind="QUESTION", question_kind="BASE", content="질문")
        db.add(turn)
        db.flush()
        evaluation = InterviewEvaluation(
            session_id=interview.id, intent_id=intents[0].id, depth=0, attempt_no=1,
            evaluated_through_turn_id=turn.id, input_snapshot={}, evaluation_config_version="legacy",
            provider="fake", status="SUCCEEDED", needs_follow_up=False, probability=0.9, applied_at=NOW,
        )
        db.add(evaluation)
        db.commit()
        evaluation_id = evaluation.id
    with db_engine.connect() as connection:
        config = alembic_config(connection)
        try:
            command.downgrade(config, "0042")
            connection.commit()
            command.upgrade(config, "head")
            connection.commit()
            row = connection.execute(text(
                "SELECT status, probability, missing_aspects FROM interview_evaluations WHERE id=:id"
            ), {"id": evaluation_id}).one()
            assert row == ("SUCCEEDED", 0.9, None)
        finally:
            command.upgrade(config, "head")
            connection.commit()

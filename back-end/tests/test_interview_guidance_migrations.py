"""Additive guidance migrations keep every historical turn shape and constraint."""
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db.models import InterviewQuestionSet, InterviewTurn
from tests.conftest import alembic_config
from tests.factories import make_interview, make_manual_draft, make_store
from tests.interview_factories import ensure_question_set


def test_guidance_upgrade_preserves_legacy_turns_and_nullable_defaults(db_engine):
    engine = db_engine
    with Session(engine) as db:
        intents = ensure_question_set(db)
        store = make_store(db)
        version = make_manual_draft(db, store)
        interview = make_interview(db, version, db.get(InterviewQuestionSet, intents[0].question_set_id), intents)
        question = InterviewTurn(session_id=interview.id, intent_id=intents[0].id, turn_no=1,
                                 speaker="AI", turn_kind="QUESTION", question_kind="BASE", content="질문")
        db.add(question)
        db.flush()
        db.add_all([
            InterviewTurn(session_id=interview.id, intent_id=intents[0].id, turn_no=2,
                          speaker="OWNER", turn_kind="ANSWER", input_method="TEXT", content="답변",
                          reply_to_question_turn_id=question.id),
            InterviewTurn(session_id=interview.id, intent_id=intents[0].id, turn_no=3,
                          speaker="OWNER", turn_kind="CORRECTION", input_method="TEXT", content="정정"),
        ])
        db.commit()
        session_id = interview.id
    with engine.connect() as connection:
        config = alembic_config(connection)
        try:
            command.downgrade(config, "0041")
            connection.commit()
            assert "guidance" not in {c["name"] for c in inspect(connection).get_columns("interview_turns")}
            command.upgrade(config, "head")
            connection.commit()
            rows = connection.execute(text(
                "SELECT content, guidance, guidance_cards FROM interview_turns "
                "WHERE session_id=:id ORDER BY turn_no"), {"id": session_id}).all()
            assert rows == [("질문", None, None), ("답변", None, None), ("정정", None, None)]
            assert all(c["nullable"] for c in inspect(connection).get_columns("interview_turns")
                       if c["name"] in {"guidance", "guidance_cards"})
        finally:
            command.upgrade(config, "head")
            connection.commit()

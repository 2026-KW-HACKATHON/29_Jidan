"""B07: the processing triple (kind, task id, attempt) is all set or all empty on both tables.

Before 0041 the CHECK said `... AND processing_attempt >= 1`; with attempt NULL that is UNKNOWN,
and a CHECK lets UNKNOWN through, so kind + task with no attempt was stored.
"""

import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.models import InterviewIntentReview, InterviewSession
from tests.conftest import alembic_config
from tests.factories import (
    make_interview,
    make_manual_draft,
    make_question_set,
    make_store,
    make_user,
)

TASK = "8c1f2d36-7a0b-4b7e-9a51-0e5c7d9f1a22"
PARTIAL = [
    pytest.param({"processing_attempt": None}, id="attempt-null"),
    pytest.param({"processing_attempt": 0}, id="attempt-0"),
    pytest.param({"processing_attempt": -1}, id="attempt-negative"),
    pytest.param({"processing_task_id": None}, id="task-null"),
    pytest.param({"processing_kind": None}, id="kind-null"),
]


def interview(db):
    owner = make_user(db, "OWNER")
    draft = make_manual_draft(db, make_store(db, owner=owner))
    question_set, intents = make_question_set(db)
    return draft, question_set, intents


def assert_check_rejection(error, name, *also):
    message = str(error.value.orig)
    assert any(f"ck_{name}_{check}" in message for check in ("processing", *also)), message
    if error.value.orig.__class__.__module__.startswith("pymysql"):
        assert error.value.orig.args[0] == 3819  # ER_CHECK_CONSTRAINT_VIOLATED


@pytest.mark.parametrize("override", PARTIAL)
def test_partial_processing_on_a_session_is_rejected(session, override):
    draft, question_set, intents = interview(session)
    values = {"processing_kind": "INITIAL_QUESTION", "processing_task_id": TASK, "processing_attempt": 1,
              **override}
    with pytest.raises((IntegrityError, OperationalError)) as error, session.begin_nested():
        make_interview(session, draft, question_set, intents, **values)
    assert_check_rejection(error, "interview_sessions")


@pytest.mark.parametrize("override", PARTIAL)
def test_partial_processing_on_a_review_is_rejected(session, override):
    draft, question_set, intents = interview(session)
    row = make_interview(session, draft, question_set, intents)
    values = {"processing_kind": "UNDERSTANDING", "processing_task_id": TASK, "processing_attempt": 1, **override}
    with pytest.raises((IntegrityError, OperationalError)) as error, session.begin_nested():
        session.add(InterviewIntentReview(session_id=row.id, intent_id=intents[0].id, status="PROCESSING", **values))
        session.flush()
    # A PROCESSING review without its task also breaks status_consistency (checked first by SQLite).
    assert_check_rejection(error, "interview_intent_reviews", "status_consistency")


def test_complete_and_empty_triples_are_accepted(session):
    draft, question_set, intents = interview(session)
    row = make_interview(session, draft, question_set, intents, processing_kind="INITIAL_QUESTION",
                         processing_task_id=TASK, processing_attempt=1)
    session.add(InterviewIntentReview(session_id=row.id, intent_id=intents[0].id, status="PROCESSING",
                                      processing_kind="UNDERSTANDING", processing_task_id=str(uuid.uuid4()),
                                      processing_attempt=3))
    session.flush()
    row.processing_kind = row.processing_task_id = row.processing_attempt = None
    session.flush()


# --- migration 0041 --------------------------------------------------------------------------


def sqlite_at(tmp_path, revision):
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.sqlite'}")
    with engine.connect() as connection:
        command.upgrade(alembic_config(connection), revision)
        connection.commit()
    return engine


def migrate(engine, action, revision):
    with engine.connect() as connection:
        getattr(command, action)(alembic_config(connection), revision)
        connection.commit()


def check_text(engine, table) -> str:
    with engine.connect() as connection:
        checks = inspect(connection).get_check_constraints(table)
    text_ = next(c["sqltext"] for c in checks if c["name"] == f"ck_{table}_processing")
    return text_.replace("`", "")  # MySQL quotes identifiers in the stored expression


def seed(engine, *, attempt):
    with Session(engine) as db:
        draft, question_set, intents = interview(db)
        row = make_interview(db, draft, question_set, intents, processing_kind="INITIAL_QUESTION",
                             processing_task_id=TASK, processing_attempt=attempt)
        db.add(InterviewIntentReview(session_id=row.id, intent_id=intents[0].id, status="READY",
                                     ready_content={"summary": "요약"}))
        db.commit()
        return row.id


def test_upgrade_keeps_rows_and_downgrade_restores_the_old_check(tmp_path):
    engine = sqlite_at(tmp_path, "0040")
    row_id = seed(engine, attempt=2)
    migrate(engine, "upgrade", "head")
    for table in ("interview_sessions", "interview_intent_reviews"):
        assert "processing_attempt IS NOT NULL" in check_text(engine, table)
    with Session(engine) as db:
        assert db.get(InterviewSession, row_id).processing_attempt == 2
    migrate(engine, "downgrade", "0040")
    assert "processing_attempt IS NOT NULL" not in check_text(engine, "interview_sessions")
    migrate(engine, "upgrade", "head")
    engine.dispose()


def test_upgrade_refuses_existing_incomplete_rows_before_any_ddl(tmp_path):
    engine = sqlite_at(tmp_path, "0040")
    seed(engine, attempt=None)  # allowed by the 0034 check (NULL >= 1 is UNKNOWN)
    with pytest.raises(RuntimeError, match="interview_sessions"):
        migrate(engine, "upgrade", "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0040"
    assert "processing_attempt IS NOT NULL" not in check_text(engine, "interview_sessions")
    engine.dispose()


@pytest.mark.mysql
def test_mysql_upgrade_refuses_incomplete_rows_and_upgrades_clean_ones(mysql_engine):
    config = alembic_config()
    try:
        command.downgrade(config, "0040")
        bad = seed(mysql_engine, attempt=None)
        with pytest.raises(RuntimeError, match="interview_sessions"):
            command.upgrade(config, "head")
        with mysql_engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0040"
        with mysql_engine.begin() as connection:
            connection.execute(text("UPDATE interview_sessions SET processing_attempt = 1 WHERE id = :id"),
                               {"id": bad})
        command.upgrade(config, "head")
        with mysql_engine.connect() as connection:
            assert "processing_attempt is not null" in check_text(mysql_engine, "interview_sessions").lower()
            assert "processing_attempt is not null" in check_text(mysql_engine, "interview_intent_reviews").lower()
            assert connection.execute(text("SELECT processing_attempt FROM interview_sessions WHERE id = :id"),
                                      {"id": bad}).scalar() == 1
        with Session(mysql_engine) as db:
            row = db.get(InterviewSession, bad)
            row.processing_attempt = None
            with pytest.raises((IntegrityError, OperationalError)) as error:
                db.flush()
            assert error.value.orig.args[0] == 3819
    finally:
        command.upgrade(config, "head")
        from tests.conftest import reset_mysql_schema
        reset_mysql_schema()

"""Migration 0040 seeds exactly the question set app.interview.question_set defines."""

import uuid

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import InterviewIntent, InterviewQuestionSet
from app.interview.question_set import (
    CURRENT_QUESTION_SET_ID,
    CURRENT_REVISION_NO,
    INTENTS_V1,
    NAMESPACE,
    intent_id,
)


def _check_seed(session):
    question_set = session.get(InterviewQuestionSet, CURRENT_QUESTION_SET_ID)
    assert question_set is not None and question_set.revision_no == CURRENT_REVISION_NO == 1
    rows = list(session.scalars(select(InterviewIntent).where(
        InterviewIntent.question_set_id == CURRENT_QUESTION_SET_ID).order_by(InterviewIntent.sort_order)))
    assert [(r.id, r.sort_order, r.intent_key, r.stage, r.base_question, r.coverage_criteria) for r in rows] == [
        (intent_id(1, d.key), order, d.key, d.stage, d.base_question, d.coverage_criteria)
        for order, d in enumerate(INTENTS_V1)
    ]


def test_seeded_rows_match_the_definition(sqlite_session):
    _check_seed(sqlite_session)


@pytest.mark.mysql
def test_mysql_migration_0040_seeds_the_same_rows(mysql_engine):
    # Other MySQL tests empty every table, so run the data migration itself again.
    from tests.conftest import alembic_config

    with mysql_engine.begin() as connection:  # earlier tests may have re-created the rows
        connection.exec_driver_sql("DELETE FROM interview_intents")
        connection.exec_driver_sql("DELETE FROM interview_question_sets")
    config = alembic_config()
    command.downgrade(config, "0036")
    command.upgrade(config, "head")
    with Session(mysql_engine) as session:
        _check_seed(session)


def test_ids_are_stable_names():
    assert CURRENT_QUESTION_SET_ID == str(uuid.uuid5(NAMESPACE, "jidan:interview-question-set:1"))
    assert len({intent_id(1, d.key) for d in INTENTS_V1}) == 6


def test_question_set_follows_the_interview_stages():
    assert [d.key for d in INTENTS_V1] == [
        "WORK_STRUCTURE", "COMMON_TASKS", "SHIFT_TASKS", "RULES", "EQUIPMENT", "EXCEPTIONS"]
    assert [d.stage for d in INTENTS_V1] == [
        "WORK_STRUCTURE", "COMMON_TASKS", "SHIFT_TASKS", "COMPLEMENTS", "COMPLEMENTS", "COMPLEMENTS"]


def test_base_questions_ask_one_polite_question():
    for definition in INTENTS_V1:
        assert definition.base_question.count("?") == 1 and definition.base_question.endswith("?")
        assert definition.base_question.endswith(("나요?", "가요?", "까요?", "되나요?"))
        # A "none" answer is information too, so a store without equipment is not probed 5 times.
        assert "그 사실" in definition.coverage_criteria

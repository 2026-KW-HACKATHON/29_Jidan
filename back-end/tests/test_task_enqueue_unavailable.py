"""TASK_ENQUEUE_503: the task INSERT failing for availability (connection lost, server gone, disk
or table full) is 503 JOB_QUEUE_UNAVAILABLE with the whole start rolled back, not a 500
(openapi startManualInterview). Lock waits, integrity errors and the caller's own rows keep
their meaning."""

import contextlib
import uuid

import pymysql
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.models import (
    BackgroundTask,
    IdempotencyRecord,
    InterviewSession,
    InterviewSessionIntent,
    ManualVersion,
    StoreManual,
)
from app.tasks import runner
from tests.test_interview_api import ctx, media_root  # noqa: F401 (fixtures)

TABLES = (StoreManual, ManualVersion, InterviewSession, InterviewSessionIntent, BackgroundTask, IdempotencyRecord)


def counts(engine) -> dict[str, int]:
    with Session(engine) as db:  # an independent session, not the request's
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model)) for model in TABLES}


@contextlib.contextmanager
def failing_insert(engine, table: str, error: Exception):
    """Every `INSERT INTO <table>` fails with `error` until the block ends; yields the hit list."""
    hits = []

    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(f"INSERT INTO {table.upper()}"):
            hits.append(1)
            raise error

    event.listen(engine, "before_cursor_execute", fail)
    try:
        yield hits
    finally:
        event.remove(engine, "before_cursor_execute", fail)


def unavailable() -> OperationalError:
    return OperationalError("INSERT INTO background_tasks", {}, pymysql.err.OperationalError(2013, "Lost connection"))


def test_task_insert_unavailable_is_503_rolled_back_and_retryable_with_the_same_key(ctx):  # noqa: F811
    key = str(uuid.uuid4())
    before = counts(ctx.engine)
    runner._wake.clear()
    with failing_insert(ctx.engine, "background_tasks", unavailable()) as hits:
        response = ctx.start(key=key)
    assert (response.status_code, response.json()["code"]) == (503, "JOB_QUEUE_UNAVAILABLE"), response.text
    assert hits == [1]
    assert counts(ctx.engine) == before
    assert not runner._wake.is_set()  # nothing committed, nothing to wake for
    retried = ctx.start(key=key)
    assert retried.status_code == 201, retried.text
    after = counts(ctx.engine)
    assert after["interview_sessions"] == before["interview_sessions"] + 1
    assert after["background_tasks"] == before["background_tasks"] + 1
    replay = ctx.start(key=key)
    assert replay.status_code == 201 and replay.headers.get("Idempotent-Replayed") == "true"
    assert counts(ctx.engine) == after


@pytest.mark.parametrize("error", [
    pytest.param(OperationalError("INSERT", {}, pymysql.err.OperationalError(1205, "Lock wait timeout")),
                 id="lock-wait"),
    pytest.param(OperationalError("INSERT", {}, pymysql.err.OperationalError(1213, "Deadlock")), id="deadlock"),
    pytest.param(IntegrityError("INSERT", {}, pymysql.err.IntegrityError(1062, "Duplicate")), id="integrity"),
])
def test_other_task_insert_errors_are_not_turned_into_503(ctx, error):  # noqa: F811
    before = counts(ctx.engine)
    with failing_insert(ctx.engine, "background_tasks", error):
        response = ctx.start()
    assert response.status_code != 503 and response.json()["code"] != "JOB_QUEUE_UNAVAILABLE"
    assert counts(ctx.engine) == before


def test_a_failure_of_the_callers_own_rows_is_not_a_queue_failure(ctx):  # noqa: F811
    """enqueue flushes the caller's pending rows too; an error there is not the queue's."""
    before = counts(ctx.engine)
    with failing_insert(ctx.engine, "interview_session_intents", unavailable()) as hits:
        response = ctx.start()
    assert hits and (response.status_code, response.json()["code"]) == (500, "INTERNAL_ERROR")
    assert counts(ctx.engine) == before


def test_enqueue_reports_unavailability_only_for_its_own_insert(db_engine):
    from app.tasks import TaskQueueUnavailable, enqueue

    with Session(db_engine) as db, failing_insert(db_engine, "background_tasks", unavailable()):
        with pytest.raises(TaskQueueUnavailable) as raised:
            enqueue(db, "TRANSCRIPTION", str(uuid.uuid4()), {"x": 1})
        assert isinstance(raised.value.__cause__, OperationalError)
        assert "jidan_tasks_enqueued" not in db.info
        db.rollback()
    with Session(db_engine) as db, pytest.raises(ValueError):
        enqueue(db, "NOT_A_KIND", str(uuid.uuid4()), {})  # programming error stays as it is

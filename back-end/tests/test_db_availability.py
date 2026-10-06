"""`app.db.availability.is_unavailable`: the driver's error code decides, including the SQLite
result-code path (exceptions that carry `sqlite_errorcode`, as sqlite3 raises them)."""

import sqlite3
import uuid

import pymysql
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.availability import is_unavailable
from app.tasks import TaskQueueUnavailable, enqueue
from tests.test_unavailable_precision import driver_failure


def wrapped(original) -> OperationalError:
    return OperationalError("INSERT INTO background_tasks", {}, original)


def coded(code: int, name: str, message: str = "opaque driver failure") -> sqlite3.OperationalError:
    """A sqlite3 error carrying a result code; the message matches no fallback text, so only
    the result-code path can classify it."""
    error = sqlite3.OperationalError(message)
    error.sqlite_errorcode, error.sqlite_errorname = code, name
    return error


@pytest.mark.parametrize("code,name", [
    (10, "SQLITE_IOERR"), (778, "SQLITE_IOERR_WRITE"), (1034, "SQLITE_IOERR_FSYNC"),
    (13, "SQLITE_FULL"), (14, "SQLITE_CANTOPEN"),
])
def test_sqlite_availability_result_codes(code, name):
    assert is_unavailable(wrapped(coded(code, name)))


@pytest.mark.parametrize("code,name", [
    (5, "SQLITE_BUSY"), (6, "SQLITE_LOCKED"), (8, "SQLITE_READONLY"), (1, "SQLITE_ERROR"),
    (19, "SQLITE_CONSTRAINT"), (2067, "SQLITE_CONSTRAINT_UNIQUE"),
])
def test_other_sqlite_result_codes_are_not_unavailability(code, name):
    # A code wins over the message: even a "disk I/O error" text with a BUSY code is contention.
    assert not is_unavailable(wrapped(coded(code, name, "disk I/O error")))


def test_a_real_sqlite_full_database_is_unavailability():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA max_page_count = 2")
    connection.execute("CREATE TABLE t (x)")
    with pytest.raises(sqlite3.OperationalError) as raised:
        for _ in range(100):
            connection.execute("INSERT INTO t VALUES (?)", ("a" * 4000,))
    assert raised.value.sqlite_errorcode == 13
    assert is_unavailable(wrapped(raised.value))


@pytest.mark.parametrize("code", [4031, 1927])
def test_mysql_idle_disconnect_and_killed_connection_are_unavailability(code):
    assert is_unavailable(wrapped(pymysql.err.OperationalError(code, "connection ended by the server")))


def test_coded_sqlite_io_error_on_the_task_insert_is_503(engine):
    failure = driver_failure(engine, "INSERT INTO BACKGROUND_TASKS", lambda: coded(778, "SQLITE_IOERR_WRITE"))
    with Session(engine) as db, failure, pytest.raises(TaskQueueUnavailable):
        enqueue(db, "TRANSCRIPTION", str(uuid.uuid4()), {"x": 1})


@pytest.mark.parametrize("code", [4031, 1927])
def test_mysql_disconnects_on_the_task_insert_are_503(db_engine, code):
    if db_engine.dialect.name != "mysql":
        pytest.skip("MySQL error codes")
    failure = driver_failure(db_engine, "INSERT INTO BACKGROUND_TASKS",
                             lambda: pymysql.err.OperationalError(code, "connection ended by the server"))
    with Session(db_engine) as db, failure, pytest.raises(TaskQueueUnavailable):
        enqueue(db, "TRANSCRIPTION", str(uuid.uuid4()), {"x": 1})

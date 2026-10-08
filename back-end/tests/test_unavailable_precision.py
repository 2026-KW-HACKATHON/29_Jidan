"""503 precision (review of e95ee58): availability is decided by driver error code, the caller's
rows stay outside the task boundary, every storage step that can fail for availability is
covered, and real driver failures (which invalidate the connection) still end in a clean 503.

Faults are raised at the DBAPI layer (`do_execute`) as real driver exceptions, so SQLAlchemy
wraps them exactly as it would a live failure (a lost connection is also invalidated)."""

import contextlib
import errno
import sqlite3
import uuid

import pymysql
import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.models import ManualMedia
from app.media import storage as storage_module
from app.media.storage import LocalMediaStorage
from app.tasks import TaskQueueUnavailable, enqueue
from tests import media_samples as samples
from tests.factories import NOW, make_store, make_user
from tests.test_manual_503_operations import all_counts
from tests.test_manual_media_api import ctx, media_root, transcribe, upload  # noqa: F401 (fixtures)


@contextlib.contextmanager
def driver_failure(engine, prefix: str, make_error, times: int = 1):
    """The next `times` statements starting with `prefix` raise `make_error()` from the driver."""
    hits = []

    def do_execute(cursor, statement, parameters, context):
        if statement.lstrip().upper().startswith(prefix) and len(hits) < times:
            hits.append(1)
            raise make_error()

    event.listen(engine, "do_execute", do_execute)
    try:
        yield hits
    finally:
        event.remove(engine, "do_execute", do_execute)


def lost(engine, code=2013):
    """A real lost-connection / server-gone error of the engine's driver."""
    if engine.dialect.name == "mysql":
        return lambda: pymysql.err.OperationalError(code, "Lost connection to MySQL server during query")
    return lambda: sqlite3.OperationalError("disk I/O error")


# --- 1. availability by error code ---------------------------------------------------------------


@pytest.mark.parametrize("code", [2013, 2006, 1114])
def test_availability_codes_on_the_task_insert_are_unavailable(db_engine, code):
    failure = driver_failure(db_engine, "INSERT INTO BACKGROUND_TASKS", lost(db_engine, code))
    with Session(db_engine) as db, failure, pytest.raises(TaskQueueUnavailable):
        enqueue(db, "TRANSCRIPTION", str(uuid.uuid4()), {"x": 1})


def test_a_check_violation_of_the_task_row_is_not_unavailability(db_engine):
    """attempt >= 1 is a CHECK on background_tasks; PyMySQL reports 3819 as OperationalError."""
    with Session(db_engine) as db, pytest.raises((OperationalError, IntegrityError)) as caught:
        enqueue(db, "TRANSCRIPTION", str(uuid.uuid4()), {"x": 1}, attempt=0)
    assert not isinstance(caught.value, TaskQueueUnavailable)


@pytest.mark.parametrize("code,message", [(3819, "Check constraint violated"), (1205, "Lock wait timeout"),
                                          (1213, "Deadlock"), (1366, "Incorrect string value")])
def test_other_operational_codes_on_the_task_insert_are_not_unavailability(db_engine, code, message):
    if db_engine.dialect.name != "mysql":
        pytest.skip("MySQL error codes")
    failure = driver_failure(db_engine, "INSERT INTO BACKGROUND_TASKS",
                             lambda: pymysql.err.OperationalError(code, message))
    with Session(db_engine) as db, failure, pytest.raises(OperationalError) as caught:
        enqueue(db, "TRANSCRIPTION", str(uuid.uuid4()), {"x": 1})
    assert not isinstance(caught.value, TaskQueueUnavailable)


# --- 2. the caller's pending rows stay outside the task boundary ------------------------------------


def _pending_media(db):
    store = make_store(db, owner=make_user(db, "OWNER"), approval_status="APPROVED", approved_at=NOW)
    db.flush()
    db.add(ManualMedia(id=str(uuid.uuid4()), store_id=store.id, uploaded_by_owner_id=store.owner_id, kind="IMAGE",
                       object_key=f"manual/{store.id}/{uuid.uuid4()}", mime_type="image/jpeg", byte_size=10,
                       created_at=NOW, expires_at=NOW))


def test_an_availability_error_of_the_callers_pending_row_is_not_the_queues(db_engine):
    """Without the separate flush of the caller's rows, this lost connection would be reported as
    a queue failure: it happens in enqueue's flush, but on the caller's INSERT."""
    with Session(db_engine) as db:
        _pending_media(db)
        failure = driver_failure(db_engine, "INSERT INTO MANUAL_MEDIA", lost(db_engine))
        with failure as hits, pytest.raises(OperationalError) as caught:
            enqueue(db, "TRANSCRIPTION", str(uuid.uuid4()), {"x": 1})
        assert hits and not isinstance(caught.value, TaskQueueUnavailable)


def test_a_constraint_error_of_the_callers_pending_row_is_not_the_queues(db_engine):
    with Session(db_engine) as db:
        _pending_media(db)
        db.new.copy().pop().byte_size = -1  # breaks a CHECK on the caller's row
        with pytest.raises((OperationalError, IntegrityError)) as caught:
            enqueue(db, "TRANSCRIPTION", str(uuid.uuid4()), {"x": 1})
        assert not isinstance(caught.value, TaskQueueUnavailable)


# --- 3. every storage step that can fail for availability -----------------------------------------


def _key():
    return storage_module.object_key("manual", str(uuid.uuid4()), str(uuid.uuid4()))


def test_unwritable_directory_is_unavailable(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    (root / "manual").write_text("a file where the scope directory should be")  # mkdir fails
    with pytest.raises(storage_module.StorageUnavailable) as caught:
        LocalMediaStorage(root).write(_key(), b"bytes")
    assert isinstance(caught.value.__cause__, OSError)


def test_temporary_file_creation_failure_is_unavailable(tmp_path, monkeypatch):
    def mkstemp(*_args, **_kwargs):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(storage_module.tempfile, "mkstemp", mkstemp)
    storage = LocalMediaStorage(tmp_path / "media")
    with pytest.raises(storage_module.StorageUnavailable) as caught:
        storage.write(_key(), b"bytes")
    assert caught.value.__cause__.errno == errno.EROFS
    assert [p for p in storage.root.rglob("*") if p.is_file()] == []


def test_upload_into_an_unwritable_directory_is_503_with_nothing_left(ctx, monkeypatch):  # noqa: F811
    def mkstemp(*_args, **_kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(storage_module.tempfile, "mkstemp", mkstemp)
    before = all_counts(ctx.engine)
    response = upload(ctx, samples.png())
    assert (response.status_code, response.json()["code"]) == (503, "JOB_QUEUE_UNAVAILABLE")
    assert all_counts(ctx.engine) == before


# --- 4. a real lost connection through the HTTP stack ------------------------------------------------


def test_lost_connection_on_the_task_insert_is_503_rolled_back_and_retryable(ctx):  # noqa: F811
    """The driver error invalidates the connection; the response must still be the 503, with
    nothing kept and the same key accepted afterwards."""
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    before = all_counts(ctx.engine)
    key = str(uuid.uuid4())
    with driver_failure(ctx.engine, "INSERT INTO BACKGROUND_TASKS", lost(ctx.engine)) as hits:
        response = transcribe(ctx, media_id, key=key)
    assert (response.status_code, response.json()["code"]) == (503, "JOB_QUEUE_UNAVAILABLE"), response.text
    assert hits and all_counts(ctx.engine) == before
    again = transcribe(ctx, media_id, key=key)
    assert again.status_code == 202, again.text
    after = all_counts(ctx.engine)
    assert after["background_tasks"] == before["background_tasks"] + 1
    assert after["media_transcriptions"] == before["media_transcriptions"] + 1

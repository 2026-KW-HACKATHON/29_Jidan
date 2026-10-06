"""Lock order between request paths and the TRANSCRIPTION task finalizer (MySQL).

Request paths lock the recording first and its transcription second: deleting a recording
(media FOR UPDATE, then a FOR SHARE look at a RUNNING transcription) and (re)starting a
transcription (media FOR UPDATE, then `begin_transcription` locks the transcription). The
task's apply/fail must take the same order; locking the transcription first and then updating
the recording's expiry closes a cycle that MySQL breaks with error 1213 (deadlock), turning a
request into a 500 or making the task re-run.

The interleaving is forced, not hoped for: the request holds the recording lock and waits until
the finalizer has locked the transcription (or a short timeout passes when, correctly, the
finalizer is itself waiting for the recording), then asks for the transcription.
"""

import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db import session_scope, utcnow
from app.db.models import MediaTranscription, QaMedia
from app.media.references import transcription_running
from app.media.storage import object_key
from app.media.transcription import begin_transcription
from app.tasks import TaskContext, claim, run_claimed
from tests import media_samples as samples
from tests import test_manual_media_api as media_api
from tests.factories import make_worker
from tests.test_manual_media_api import delete, transcribe, upload

media_root = media_api.media_root
ctx = media_api.ctx

pytestmark = [pytest.mark.mysql, pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)]

WAIT = 3.0  # how long the request waits for the finalizer before going on


class Interleave:
    """request: lock recording -> `request_locked` -> wait `finalizer_locked` -> next lock.
    finalizer: starts after `request_locked`; `finalizer_locked` fires in `ctx.ensure`, i.e.
    once the finalizer holds the transcription row (and, in the right order, the recording)."""

    def __init__(self, monkeypatch):
        self.request_locked = threading.Event()
        self.finalizer_locked = threading.Event()
        original = TaskContext.ensure

        def ensure(task_ctx, condition):
            self.finalizer_locked.set()
            return original(task_ctx, condition)

        monkeypatch.setattr(TaskContext, "ensure", ensure)

    def after_request_lock(self):
        self.request_locked.set()
        self.finalizer_locked.wait(WAIT)

    def run(self, request, claimed):
        """(request result, task run). A deadlock victim on the finalizer side shows up as a
        logged "task finalize failed" (the runner then retries), so callers also check logs."""
        results = {}

        def finalize():
            self.request_locked.wait(WAIT)
            results["run"] = run_claimed(claimed)

        def send():
            try:
                results["request"] = request()
            except OperationalError as error:  # service-level requests surface the DB error
                results["request"] = error

        threads = [threading.Thread(target=send), threading.Thread(target=finalize)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        return results["request"], results["run"]


def _deadlocked(value) -> bool:
    return isinstance(value, OperationalError) and "1213" in str(value)


def _recording(ctx) -> str:
    response = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO")
    assert response.status_code == 201, response.text
    media_id = response.json()["id"]
    assert transcribe(ctx, media_id).status_code == 202
    return media_id


# --- owner interview recordings (API paths) ----------------------------------------------------


@pytest.mark.parametrize("outcome", ["ready", "error"])
def test_finishing_transcription_and_deleting_the_recording_do_not_deadlock(ctx, monkeypatch, fake_ai, caplog,
                                                                             outcome):
    media_id = _recording(ctx)
    if outcome == "error":
        fake_ai.script("transcribe", FakeOutcome.fail("input_rejected"))  # -> handler.fail
    gate = Interleave(monkeypatch)
    from app import manual_media

    in_use = manual_media.manual_media_in_use

    def in_use_after_lock(db, media):  # runs right after DELETE locked the recording
        gate.after_request_lock()
        return in_use(db, media)

    monkeypatch.setattr(manual_media, "manual_media_in_use", in_use_after_lock)
    response, run = gate.run(lambda: delete(ctx, media_id), claim()[0])
    assert response.status_code in (204, 409), response.text  # never a 500 from error 1213
    assert run.outcome == ("succeeded" if outcome == "ready" else "failed"), run
    assert "finalize failed" not in caplog.text
    with Session(ctx.engine) as db:
        row = db.scalars(select(MediaTranscription)).one()
        assert row.status == ("READY" if outcome == "ready" else "ERROR")


def test_finishing_transcription_and_requesting_it_again_do_not_deadlock(ctx, monkeypatch, caplog):
    media_id = _recording(ctx)
    gate = Interleave(monkeypatch)
    from app import manual_media

    begin = manual_media.begin_transcription

    def begin_after_lock(db, media, **kwargs):  # the request already holds the recording lock
        gate.after_request_lock()
        return begin(db, media, **kwargs)

    monkeypatch.setattr(manual_media, "begin_transcription", begin_after_lock)
    response, run = gate.run(lambda: transcribe(ctx, media_id), claim()[0])
    assert response.status_code in (200, 202), response.text
    assert run.outcome == "succeeded", run
    assert "finalize failed" not in caplog.text
    with Session(ctx.engine) as db:
        assert db.scalars(select(MediaTranscription)).one().status == "READY"


# --- worker question recordings (the request protocol at service level) -------------------------


def _qa_recording(ctx) -> str:
    with Session(ctx.engine) as db:
        worker = make_worker(db)
        media_id = str(uuid.uuid4())
        key = object_key("qa", ctx.store, media_id)
        ctx.storage.write(key, samples.wav_seconds(1))
        now = utcnow()
        db.add(QaMedia(id=media_id, store_id=ctx.store, worker_id=worker.id, kind="AUDIO", object_key=key,
                       mime_type="audio/wav", byte_size=10, duration_ms=1000, created_at=now,
                       expires_at=now + timedelta(hours=24)))
        db.flush()
        begin_transcription(db, db.get(QaMedia, media_id))
        db.commit()
    return media_id


@pytest.mark.parametrize("protocol", ["delete", "restart"])
def test_question_recordings_follow_the_same_order(ctx, monkeypatch, caplog, protocol):
    media_id = _qa_recording(ctx)
    gate = Interleave(monkeypatch)

    def request():
        with session_scope() as db:
            media = db.scalars(select(QaMedia).where(QaMedia.id == media_id).with_for_update()).one()
            gate.after_request_lock()
            if protocol == "delete":
                if not transcription_running(db, qa_media_id=media.id):
                    media.deleted_at = utcnow()
                return "checked"
            return begin_transcription(db, media)[1]

    result, run = gate.run(request, claim()[0])
    assert not _deadlocked(result), result
    assert result in ("checked", 200, 202), result
    assert run.outcome == "succeeded", run
    assert "finalize failed" not in caplog.text

"""#121 worker question media and transcription endpoints (contract-checked, SQLite and MySQL)."""

import io
import threading
import uuid
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.auth import SESSION_COOKIE_NAME
from app.db import utcnow
from app.db.models import ManualMedia, MediaTranscription, QaMedia, Store, StoreAccessGrant, User
from app.media.storage import LocalMediaStorage, object_key, set_media_storage
from app.tasks import drain
from tests import media_samples as samples
from tests.api_contract import login
from tests.factories import NOW, make_store
from tests.qa_factories import (
    end_access,
    make_conversation,
    make_qa_media,
    make_qa_world,
    make_question,
    mysql_only,
)

MiB = 1024 * 1024


@pytest.fixture
def media_root(tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    yield storage
    set_media_storage(None)


class Ctx:
    pass


@pytest.fixture
def ctx(api, db_engine, media_root):
    with Session(db_engine) as db:
        world = make_qa_world(db)
    context = Ctx()
    context.__dict__.update(world.__dict__, api=api, engine=db_engine, storage=media_root,
                            auth=login(api, world.worker), other_auth=login(api, world.other_worker))
    return context


def as_(ctx, auth=None, key: str | None = None) -> dict:
    """Make `auth` (default: the worker) the client's session and return its write headers."""
    auth = auth or ctx.auth
    ctx.api.cookies.set(SESSION_COOKIE_NAME, auth.token)
    return auth.headers(key)


def base(ctx, store=None) -> str:
    return f"/api/stores/{store or ctx.store}/manual/qa"


def upload(ctx, data, purpose="QUESTION_IMAGE", *, key=None, store=None, auth=None,
           filename="photo.jpg", content_type="image/jpeg"):
    return ctx.api.post(
        f"{base(ctx, store)}/media", headers=as_(ctx, auth, key or str(uuid.uuid4())),
        data={"purpose": purpose}, files={"file": (filename, data, content_type)},
    )


def uploaded(ctx, data=None, purpose="QUESTION_IMAGE", **kwargs) -> str:
    response = upload(ctx, data if data is not None else samples.jpeg(), purpose, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def delete(ctx, media_id, *, key=None, auth=None, store=None):
    return ctx.api.delete(f"{base(ctx, store)}/media/{media_id}",
                          headers=as_(ctx, auth, key or str(uuid.uuid4())))


def content(ctx, media_id, *, auth=None, store=None):
    return ctx.api.get(f"{base(ctx, store)}/media/{media_id}/content", headers=as_(ctx, auth))


def transcribe(ctx, media_id, *, key=None, auth=None, store=None):
    return ctx.api.post(f"{base(ctx, store)}/transcriptions", json={"mediaId": media_id},
                        headers=as_(ctx, auth, key or str(uuid.uuid4())))


def read_transcription(ctx, transcription_id, *, auth=None, store=None):
    return ctx.api.get(f"{base(ctx, store)}/transcriptions/{transcription_id}",
                       headers=as_(ctx, auth))


def retry_transcription(ctx, transcription_id, *, key=None, auth=None, store=None):
    return ctx.api.post(f"{base(ctx, store)}/transcriptions/{transcription_id}/retries", json={},
                        headers=as_(ctx, auth, key or str(uuid.uuid4())))


def media_row(ctx, media_id) -> QaMedia:
    with Session(ctx.engine) as db:
        return db.get(QaMedia, media_id)


def code(response) -> str:
    return response.json()["code"]


def set_media(ctx, media_id, **values) -> None:
    with Session(ctx.engine) as db:
        row = db.get(QaMedia, media_id)
        for name, value in values.items():
            setattr(row, name, value)
        db.commit()


def expire(ctx, media_id) -> None:
    set_media(ctx, media_id, created_at=utcnow() - timedelta(days=8), expires_at=utcnow() - timedelta(seconds=1))


# --- upload -----------------------------------------------------------------------------------


def test_photo_upload_is_private_metadata_free_and_kept_seven_days(ctx):
    response = upload(ctx, samples.jpeg(gps=True))
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "storeId", "purpose", "mimeType", "sizeBytes", "createdAt", "expiresAt"}
    assert (body["storeId"], body["purpose"], body["mimeType"]) == (ctx.store, "QUESTION_IMAGE", "image/jpeg")
    row = media_row(ctx, body["id"])
    stored = ctx.storage.read(row.object_key)
    assert body["sizeBytes"] == len(stored) == row.byte_size
    assert not Image.open(io.BytesIO(stored)).getexif() and b"secret note" not in stored
    assert row.expires_at - row.created_at == timedelta(days=7)
    assert (row.worker_id, row.store_id) == (ctx.worker, ctx.store)
    assert row.object_key.startswith(f"qa/{ctx.store}/") and row.object_key not in response.text
    with Session(ctx.engine) as db:  # never an owner interview file
        assert db.scalar(select(func.count()).select_from(ManualMedia)) == 0


@pytest.mark.parametrize("data,mime", [
    (samples.wav_seconds(2), "audio/wav"), (samples.mp3(40), "audio/mpeg"),
    (samples.mp4(), "audio/mp4"), (samples.webm(), "audio/webm"),
])
def test_recordings_are_accepted_by_content_and_kept_one_day(ctx, data, mime):
    response = upload(ctx, data, "QUESTION_AUDIO", filename="q.bin", content_type="application/octet-stream")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["purpose"], body["mimeType"]) == ("QUESTION_AUDIO", mime)
    row = media_row(ctx, body["id"])
    assert row.expires_at - row.created_at == timedelta(hours=24)
    assert ctx.storage.read(row.object_key) == data


@pytest.mark.parametrize("purpose,data,status,error", [
    ("QUESTION_IMAGE", b"", 422, "MEDIA_INVALID"),
    ("QUESTION_IMAGE", samples.jpeg()[:300], 422, "MEDIA_INVALID"),
    ("QUESTION_AUDIO", samples.wav(0), 422, "MEDIA_INVALID"),
    ("QUESTION_IMAGE", samples.gif(), 415, "MEDIA_TYPE_UNSUPPORTED"),
    ("QUESTION_IMAGE", samples.wav_seconds(1), 415, "MEDIA_TYPE_UNSUPPORTED"),
    ("QUESTION_AUDIO", samples.png(), 415, "MEDIA_TYPE_UNSUPPORTED"),
    ("QUESTION_AUDIO", samples.mp4(video=True), 415, "MEDIA_TYPE_UNSUPPORTED"),
    ("QUESTION_IMAGE", samples.padded_jpeg(10 * MiB + 1), 413, "MEDIA_TOO_LARGE"),
    ("QUESTION_AUDIO", samples.wav_seconds(120.5), 413, "MEDIA_TOO_LARGE"),
])
def test_rejected_files_store_nothing(ctx, purpose, data, status, error):
    response = upload(ctx, data, purpose)
    assert (response.status_code, code(response)) == (status, error), response.text
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(QaMedia)) == 0
    assert list(ctx.storage.iter_files("qa")) == []


def test_limits_are_inclusive(ctx):
    assert upload(ctx, samples.padded_jpeg(10 * MiB)).status_code == 201
    assert upload(ctx, samples.wav_seconds(120), "QUESTION_AUDIO").status_code == 201


@pytest.mark.parametrize("purpose", ["MANUAL_PHOTO", "INTERVIEW_AUDIO", "question_image", ""])
def test_owner_purposes_and_unknown_purposes_are_rejected(ctx, purpose):
    response = upload(ctx, samples.jpeg(), purpose)
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")


def test_same_key_replays_and_another_file_with_it_conflicts(ctx):
    key = str(uuid.uuid4())
    first = upload(ctx, samples.jpeg(), key=key)
    again = upload(ctx, samples.jpeg(), key=key)
    assert again.status_code == 201 and again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    other = upload(ctx, samples.png(), key=key)
    assert (other.status_code, code(other)) == (409, "IDEMPOTENCY_KEY_REUSED")
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(QaMedia)) == 1


# --- access (shared by every Q&A media operation) ---------------------------------------------


def end_access_in(ctx, **values):
    with Session(ctx.engine) as db:
        for grant in db.scalars(select(StoreAccessGrant).where(
                StoreAccessGrant.store_id == ctx.store, StoreAccessGrant.worker_id == ctx.worker)):
            for name, value in values.items():
                setattr(grant, name, value)
        db.commit()


@pytest.mark.parametrize("change", [
    {"revoked_at": "now"}, {"valid_until": "now"}, {"granted_at": "future"},
])
def test_worker_without_current_access_gets_404(ctx, change):
    media_id = uploaded(ctx)
    now = utcnow()
    end_access_in(ctx, **{k: now + timedelta(days=1) if v == "future" else now for k, v in change.items()})
    for response in (upload(ctx, samples.jpeg()), content(ctx, media_id), delete(ctx, media_id)):
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND"), response.text


def test_unknown_store_and_store_without_grant_are_404(ctx):
    with Session(ctx.engine) as db:  # an approved store this worker never had access to
        stranger_id = make_store(db, approval_status="APPROVED", approved_at=NOW).id
        db.commit()
    for store in (str(uuid.uuid4()), stranger_id):
        response = upload(ctx, samples.jpeg(), store=store)
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("change", ["approval_lost", "owner_suspended"])
def test_store_not_operating_is_403_for_a_worker_with_a_valid_grant(ctx, change):
    """Same rule as reading the published manual: a valid grant to a store that is not
    operating is 403 STORE_APPROVAL_REQUIRED; without a grant the store stays hidden (404)."""
    media_id = uploaded(ctx)
    with Session(ctx.engine) as db:
        if change == "approval_lost":
            store = db.get(Store, ctx.store)
            store.approval_status, store.approved_at = "PENDING", None
        else:
            db.get(User, ctx.owner).status = "SUSPENDED"
        db.commit()
    for response in (content(ctx, media_id), upload(ctx, samples.jpeg()), delete(ctx, media_id),
                     transcribe(ctx, media_id)):
        assert (response.status_code, code(response)) == (403, "STORE_APPROVAL_REQUIRED"), response.text
    with Session(ctx.engine) as db:
        end_access(db, ctx.store, ctx.worker)
    for response in (content(ctx, media_id), upload(ctx, samples.jpeg())):
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND"), response.text


def test_owner_of_the_store_is_forbidden(ctx):
    response = upload(ctx, samples.jpeg(), auth=login(ctx.api, ctx.owner))
    assert (response.status_code, code(response)) == (403, "FORBIDDEN")


def test_replay_rechecks_current_access(ctx):
    key = str(uuid.uuid4())
    assert upload(ctx, samples.jpeg(), key=key).status_code == 201
    with Session(ctx.engine) as db:
        end_access(db, ctx.store, ctx.worker)
    response = upload(ctx, samples.jpeg(), key=key)
    assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")


# --- delete -----------------------------------------------------------------------------------


def test_unlinked_upload_is_deleted_idempotently_and_its_bytes_released(ctx):
    media_id = uploaded(ctx)
    key = str(uuid.uuid4())
    first = delete(ctx, media_id, key=key)
    assert first.status_code == 204 and first.content == b""
    row = media_row(ctx, media_id)
    assert row.deleted_at is not None and row.content_deleted_at is not None
    assert not ctx.storage.exists(row.object_key)
    again = delete(ctx, media_id, key=key)
    assert again.status_code == 204 and again.headers["Idempotent-Replayed"] == "true"
    other_key = delete(ctx, media_id)
    assert (other_key.status_code, code(other_key)) == (404, "RESOURCE_NOT_FOUND")
    assert content(ctx, media_id).status_code == 404


def test_delete_scoping(ctx):
    media_id = uploaded(ctx)
    for response in (
        delete(ctx, media_id, auth=ctx.other_auth),            # another worker of the store
        delete(ctx, media_id, store=ctx.other_store),          # the worker's other store
        delete(ctx, str(uuid.uuid4())),                        # unknown
    ):
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")
    assert media_row(ctx, media_id).deleted_at is None


def test_photo_used_by_a_question_cannot_be_deleted(ctx):
    media_id = uploaded(ctx)
    from app.db.models import ManualQaPhoto

    with Session(ctx.engine) as db:
        conversation = make_conversation(db, ctx.store, ctx.worker)
        question = make_question(db, conversation, ctx.version)
        db.add(ManualQaPhoto(qa_id=question, media_id=media_id, sort_order=0))
        db.commit()
    response = delete(ctx, media_id)
    assert (response.status_code, code(response)) == (409, "MEDIA_IN_USE")
    assert media_row(ctx, media_id).deleted_at is None


def test_recording_under_transcription_cannot_be_deleted_until_it_ends(ctx):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    assert transcribe(ctx, media_id).status_code == 202
    response = delete(ctx, media_id)
    assert (response.status_code, code(response)) == (409, "MEDIA_IN_USE")
    drain()
    assert delete(ctx, media_id).status_code == 204


def test_expired_upload_can_still_be_deleted(ctx):
    media_id = uploaded(ctx)
    expire(ctx, media_id)
    assert delete(ctx, media_id).status_code == 204


# --- protected content ------------------------------------------------------------------------


def test_own_upload_is_served_with_its_real_type_and_no_store(ctx):
    media_id = uploaded(ctx, samples.png())
    response = content(ctx, media_id)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in response.headers["cache-control"]
    assert response.content == ctx.storage.read(media_row(ctx, media_id).object_key)
    assert "filename" not in response.headers.get("content-disposition", "")
    audio = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    response = content(ctx, audio)
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"


def test_content_is_never_shared_with_another_worker_or_store(ctx):
    media_id = uploaded(ctx)
    assert content(ctx, media_id, auth=ctx.other_auth).status_code == 404
    assert content(ctx, media_id, store=ctx.other_store).status_code == 404
    owner_file = str(uuid.uuid4())
    with Session(ctx.engine) as db:  # an owner interview photo id is not a Q&A file
        key = object_key("manual", ctx.store, owner_file)
        ctx.storage.write(key, samples.png())
        db.add(ManualMedia(id=owner_file, store_id=ctx.store, uploaded_by_owner_id=ctx.owner, kind="IMAGE",
                           object_key=key, mime_type="image/png", byte_size=10, created_at=utcnow(),
                           expires_at=utcnow() + timedelta(days=1)))
        db.commit()
    response = content(ctx, owner_file)
    assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("change", ["expired", "purged", "file_missing"])
def test_expired_content_is_410(ctx, change):
    media_id = uploaded(ctx)
    if change == "expired":
        expire(ctx, media_id)
    elif change == "purged":
        set_media(ctx, media_id, content_deleted_at=utcnow())
    else:
        ctx.storage.delete(media_row(ctx, media_id).object_key)
    response = content(ctx, media_id)
    assert (response.status_code, code(response)) == (410, "QA_MEDIA_EXPIRED")


# --- transcriptions ---------------------------------------------------------------------------


def test_transcription_runs_asynchronously_and_is_reused(ctx, fake_ai):
    media_id = uploaded(ctx, samples.wav_seconds(2), "QUESTION_AUDIO")
    fake_ai.script("transcribe", FakeOutcome.ok("포스기 마감은 어떻게 해요?"))
    started = transcribe(ctx, media_id)
    assert started.status_code == 202, started.text
    body = started.json()
    assert (body["status"], body["mediaId"], body["text"], body["error"]) == ("RUNNING", media_id, None, None)
    running = read_transcription(ctx, body["id"])
    assert running.status_code == 200 and running.json()["status"] == "RUNNING"
    again = transcribe(ctx, media_id)  # same media, new key: the existing job
    assert again.status_code == 202 and again.json()["id"] == body["id"]
    drain()
    ready = read_transcription(ctx, body["id"]).json()
    assert (ready["status"], ready["text"]) == ("READY", "포스기 마감은 어떻게 해요?")
    assert ready["completedAt"] is not None
    reused = transcribe(ctx, media_id)
    assert reused.status_code == 202 and reused.json() == ready
    assert len(fake_ai.calls_for("transcribe")) == 1
    row = media_row(ctx, media_id)  # the original is kept 24 h after the transcription ends
    assert row.expires_at > utcnow() + timedelta(hours=23)


def test_transcript_stays_readable_after_the_recording_is_gone(ctx):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    transcription = transcribe(ctx, media_id).json()["id"]
    drain()
    assert delete(ctx, media_id).status_code == 204
    response = read_transcription(ctx, transcription)
    assert response.status_code == 200 and response.json()["status"] == "READY"


@pytest.mark.parametrize("failure", [FakeOutcome.fail("input_rejected"), FakeOutcome.ok("")])
def test_failure_is_kept_until_an_explicit_retry(ctx, fake_ai, failure):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    fake_ai.script("transcribe", failure, FakeOutcome.ok("다시 들은 질문"))
    transcription = transcribe(ctx, media_id).json()["id"]
    drain()
    failed = read_transcription(ctx, transcription).json()
    assert failed["status"] == "ERROR" and failed["text"] is None
    assert failed["error"]["code"] == "TRANSCRIPTION_FAILED" and failed["error"]["retryable"] is True
    create_again = transcribe(ctx, media_id)  # does not restart a failed job
    assert create_again.status_code == 202 and create_again.json()["status"] == "ERROR"
    key = str(uuid.uuid4())
    retried = retry_transcription(ctx, transcription, key=key)
    assert retried.status_code == 202, retried.text
    assert (retried.json()["id"], retried.json()["status"]) == (transcription, "RUNNING")
    replay = retry_transcription(ctx, transcription, key=key)
    assert replay.status_code == 202 and replay.headers["Idempotent-Replayed"] == "true"
    running = retry_transcription(ctx, transcription)
    assert (running.status_code, code(running)) == (409, "TRANSCRIPTION_NOT_RETRYABLE")
    drain()
    assert read_transcription(ctx, transcription).json()["text"] == "다시 들은 질문"
    ready = retry_transcription(ctx, transcription)
    assert (ready.status_code, code(ready)) == (409, "TRANSCRIPTION_NOT_RETRYABLE")
    with Session(ctx.engine) as db:
        assert db.get(MediaTranscription, transcription).attempt == 2


def test_retry_after_the_recording_expired_is_410(ctx, fake_ai):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    fake_ai.script("transcribe", FakeOutcome.fail("input_rejected"))
    transcription = transcribe(ctx, media_id).json()["id"]
    drain()
    expire(ctx, media_id)
    response = retry_transcription(ctx, transcription)
    assert (response.status_code, code(response)) == (410, "QA_MEDIA_EXPIRED")


def test_transcription_input_rules(ctx):
    photo = uploaded(ctx)
    response = transcribe(ctx, photo)
    assert (response.status_code, code(response)) == (422, "MEDIA_PURPOSE_INVALID")
    expired = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    expire(ctx, expired)
    response = transcribe(ctx, expired)
    assert (response.status_code, code(response)) == (410, "QA_MEDIA_EXPIRED")
    theirs = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO", auth=ctx.other_auth)
    deleted = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    assert delete(ctx, deleted).status_code == 204
    for media_id in (theirs, deleted, str(uuid.uuid4())):
        response = transcribe(ctx, media_id)
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")
    response = ctx.api.post(f"{base(ctx)}/transcriptions", json={"mediaId": "nope"},
                            headers=as_(ctx, None, str(uuid.uuid4())))
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")
    response = ctx.api.post(f"{base(ctx)}/transcriptions", json={"mediaId": photo, "extra": 1},
                            headers=as_(ctx, None, str(uuid.uuid4())))
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")


def test_transcriptions_are_scoped_to_the_worker_and_store(ctx):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    transcription = transcribe(ctx, media_id).json()["id"]
    drain()
    for response in (
        read_transcription(ctx, transcription, auth=ctx.other_auth),
        read_transcription(ctx, transcription, store=ctx.other_store),
        retry_transcription(ctx, transcription, auth=ctx.other_auth),
        read_transcription(ctx, str(uuid.uuid4())),
    ):
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")
    with Session(ctx.engine) as db:
        end_access(db, ctx.store, ctx.worker)
    response = read_transcription(ctx, transcription)  # late results stay hidden after access ends
    assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")


def test_owner_interview_transcriptions_are_not_reachable(ctx):
    owner_file = str(uuid.uuid4())
    with Session(ctx.engine) as db:
        db.add(ManualMedia(id=owner_file, store_id=ctx.store, uploaded_by_owner_id=ctx.owner, kind="AUDIO",
                           object_key=object_key("manual", ctx.store, owner_file), mime_type="audio/wav",
                           byte_size=10, duration_ms=1000, created_at=utcnow(),
                           expires_at=utcnow() + timedelta(days=1)))
        row = MediaTranscription(store_id=ctx.store, manual_media_id=owner_file, status="READY",
                                 text="점주 비공개 답변", completed_at=utcnow())
        db.add(row)
        db.commit()
        transcription = row.id
    for response in (read_transcription(ctx, transcription), retry_transcription(ctx, transcription),
                     transcribe(ctx, owner_file)):
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")
        assert "점주 비공개 답변" not in response.text


def test_transcript_text_never_reaches_the_logs(ctx, fake_ai, caplog):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    # The marker must not be a substring a random UUID in the request logs can contain
    # ("1234" alone appeared inside a store id once and failed the run).
    secret = "비밀번호는 1234예요"
    fake_ai.script("transcribe", FakeOutcome.ok(secret))
    with caplog.at_level("DEBUG"):
        transcription = transcribe(ctx, media_id).json()["id"]
        drain()
        read_transcription(ctx, transcription)
    assert read_transcription(ctx, transcription).json()["text"] == secret  # the text did flow
    assert "비밀번호" not in caplog.text and secret not in caplog.text


# --- MySQL races ------------------------------------------------------------------------------


def _parallel(count, send):
    barrier = threading.Barrier(count)
    results = []

    def run():
        barrier.wait()
        results.append(send())

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_concurrent_transcription_requests_start_one_job(ctx):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    results = _parallel(4, lambda: transcribe(ctx, media_id))
    assert [r.status_code for r in results] == [202] * 4, [r.text for r in results]
    assert len({r.json()["id"] for r in results}) == 1
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(MediaTranscription)) == 1
        from app.db.models import BackgroundTask

        assert db.scalar(select(func.count()).select_from(BackgroundTask)) == 1


@mysql_only
def test_concurrent_retries_restart_once(ctx, fake_ai):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    fake_ai.script("transcribe", FakeOutcome.fail("input_rejected"))
    transcription = transcribe(ctx, media_id).json()["id"]
    drain()
    results = _parallel(3, lambda: retry_transcription(ctx, transcription))
    assert sorted(r.status_code for r in results) == [202, 409, 409], [r.text for r in results]
    assert all(code(r) == "TRANSCRIPTION_NOT_RETRYABLE" for r in results if r.status_code == 409)
    with Session(ctx.engine) as db:
        assert db.get(MediaTranscription, transcription).attempt == 2


@mysql_only
def test_delete_and_transcription_race_leave_a_consistent_state(ctx):
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    calls = iter([lambda: delete(ctx, media_id), lambda: transcribe(ctx, media_id)])
    lock = threading.Lock()

    def send():
        with lock:
            action = next(calls)
        return action()

    results = {r.request.method: r for r in _parallel(2, send)}
    deleted, started = results["DELETE"], results["POST"]
    if deleted.status_code == 204:  # delete won: transcription saw no live file
        assert (started.status_code, code(started)) == (404, "RESOURCE_NOT_FOUND")
        with Session(ctx.engine) as db:
            assert db.scalar(select(func.count()).select_from(MediaTranscription)) == 0
    else:  # transcription won: the running job protects the recording
        assert (deleted.status_code, code(deleted), started.status_code) == (409, "MEDIA_IN_USE", 202)
        assert media_row(ctx, media_id).deleted_at is None


def test_question_uploads_of_a_store_never_list_in_another_store(ctx):
    make = uploaded(ctx, store=ctx.other_store)
    row = media_row(ctx, make)
    assert row.store_id == ctx.other_store
    assert content(ctx, make).status_code == 404 and content(ctx, make, store=ctx.other_store).status_code == 200


def test_factory_media_matches_upload_rules(ctx):
    with Session(ctx.engine) as db:
        media_id = make_qa_media(db, ctx.storage, ctx.store, ctx.worker, age=timedelta(days=8))
    response = content(ctx, media_id)
    assert (response.status_code, code(response)) == (410, "QA_MEDIA_EXPIRED")


def test_transcription_key_reused_for_another_recording_conflicts(ctx):
    """transcribeQAQuestionAudio 409: the same key with another mediaId is another request."""
    first, second = (uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO") for _ in range(2))
    key = str(uuid.uuid4())
    assert transcribe(ctx, first, key=key).status_code == 202
    response = transcribe(ctx, second, key=key)
    assert (response.status_code, code(response)) == (409, "IDEMPOTENCY_KEY_REUSED")
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(MediaTranscription)) == 1

"""#119 owner manual media and transcription endpoints (contract-checked, SQLite and MySQL)."""

import io
import threading
import uuid
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    InterviewTurn,
    InterviewTurnPhoto,
    ManualMedia,
    ManualPhotoAttachment,
    MediaTranscription,
    StoreAccessGrant,
    StoreManual,
)
from app.media.references import add_snapshot_refs, lock_photos_for_link
from app.media.storage import LocalMediaStorage, set_media_storage
from app.tasks import drain
from tests import media_samples as samples
from tests.api_contract import ORIGIN, login
from tests.factories import (
    NOW,
    make_interview,
    make_invitation,
    make_manual_draft,
    make_question_set,
    make_store,
    make_user,
    make_worker,
)

MiB = 1024 * 1024


@pytest.fixture
def media_root(tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    yield storage
    set_media_storage(None)


@pytest.fixture
def ctx(api, db_engine, media_root):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW)
        other_owner = make_user(db, "OWNER")
        other_store = make_store(db, owner=other_owner, approval_status="APPROVED", approved_at=NOW)
        pending = make_store(db, owner=owner)
        db.commit()
        ids = {"owner": owner.id, "store": store.id, "other_owner": other_owner.id,
               "other_store": other_store.id, "pending": pending.id}
    auth = login(api, ids["owner"])

    class Context:
        pass

    context = Context()
    context.__dict__.update(ids, api=api, auth=auth, engine=db_engine, storage=media_root)
    return context


def upload(ctx, data, purpose="MANUAL_PHOTO", *, key=None, store=None, filename="photo.jpg",
           content_type="image/jpeg", auth=None, extra=None):
    fields = {"purpose": purpose, **(extra or {})}
    return ctx.api.post(
        f"/api/stores/{store or ctx.store}/manual/media",
        headers=(auth or ctx.auth).headers(key or str(uuid.uuid4())),
        data=fields, files={"file": (filename, data, content_type)},
    )


def media_row(ctx, media_id) -> ManualMedia:
    with Session(ctx.engine) as db:
        return db.get(ManualMedia, media_id)


def count(ctx, model) -> int:
    with Session(ctx.engine) as db:
        return db.scalar(select(func.count()).select_from(model))


def code(response) -> str:
    return response.json()["code"]


# --- upload: success and stored content -------------------------------------------------------


def test_photo_upload_stores_a_metadata_free_copy_and_returns_only_the_contract(ctx):
    response = upload(ctx, samples.jpeg(gps=True))
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "storeId", "purpose", "mimeType", "sizeBytes", "createdAt"}
    assert (body["storeId"], body["purpose"], body["mimeType"]) == (ctx.store, "MANUAL_PHOTO", "image/jpeg")
    row = media_row(ctx, body["id"])
    stored = ctx.storage.read(row.object_key)
    assert body["sizeBytes"] == len(stored) == row.byte_size
    assert not Image.open(io.BytesIO(stored)).getexif() and b"PhoneMaker" not in stored
    assert row.expires_at - row.created_at == timedelta(hours=24) and row.uploaded_by_owner_id == ctx.owner
    assert row.object_key not in response.text and "/" not in body["id"]


@pytest.mark.parametrize("data,mime", [
    (samples.wav_seconds(2), "audio/wav"), (samples.mp3(40), "audio/mpeg"),
    (samples.mp4(), "audio/mp4"), (samples.webm(), "audio/webm"),
])
def test_recordings_are_accepted_by_content(ctx, data, mime):
    response = upload(ctx, data, "INTERVIEW_AUDIO", filename="answer.bin", content_type="application/octet-stream")
    assert response.status_code == 201, response.text
    assert response.json()["mimeType"] == mime
    assert ctx.storage.read(media_row(ctx, response.json()["id"]).object_key) == data


def test_declared_name_and_type_are_ignored(ctx):
    response = upload(ctx, samples.png(), filename="../../evil.jpg", content_type="image/jpeg")
    assert response.status_code == 201 and response.json()["mimeType"] == "image/png"
    assert "evil" not in media_row(ctx, response.json()["id"]).object_key


# --- upload: rejections -----------------------------------------------------------------------


@pytest.mark.parametrize("purpose,data,status,error", [
    ("MANUAL_PHOTO", b"", 422, "MEDIA_INVALID"),
    ("INTERVIEW_AUDIO", b"", 422, "MEDIA_INVALID"),
    ("MANUAL_PHOTO", samples.jpeg()[:300], 422, "MEDIA_INVALID"),
    ("INTERVIEW_AUDIO", samples.wav(0), 422, "MEDIA_INVALID"),
    ("MANUAL_PHOTO", samples.gif(), 415, "UNSUPPORTED_MEDIA_TYPE"),
    ("MANUAL_PHOTO", b"<svg onload='x'/>", 415, "UNSUPPORTED_MEDIA_TYPE"),
    ("MANUAL_PHOTO", samples.wav_seconds(1), 415, "UNSUPPORTED_MEDIA_TYPE"),
    ("INTERVIEW_AUDIO", samples.jpeg(), 415, "UNSUPPORTED_MEDIA_TYPE"),
    ("INTERVIEW_AUDIO", samples.mp4(video=True), 415, "UNSUPPORTED_MEDIA_TYPE"),
    ("MANUAL_PHOTO", samples.webp(animated=True), 415, "UNSUPPORTED_MEDIA_TYPE"),
    ("INTERVIEW_AUDIO", samples.wav_seconds(120.1), 413, "MEDIA_TOO_LARGE"),
])
def test_invalid_unsupported_and_oversized_files_are_distinguished(ctx, purpose, data, status, error):
    response = upload(ctx, data, purpose)
    assert (response.status_code, code(response)) == (status, error)
    assert count(ctx, ManualMedia) == 0 and list(ctx.storage.iter_files("manual")) == []


def test_byte_and_duration_limits_are_inclusive(ctx):
    assert upload(ctx, samples.padded_jpeg(10 * MiB)).status_code == 201
    response = upload(ctx, samples.padded_jpeg(10 * MiB + 1))
    assert (response.status_code, code(response)) == (413, "MEDIA_TOO_LARGE")
    assert upload(ctx, samples.wav_seconds(120.0), "INTERVIEW_AUDIO").status_code == 201


@pytest.mark.parametrize("size", [20 * MiB + 1, 30 * MiB])
def test_bodies_over_the_largest_limit_are_cut_off(ctx, size):
    response = upload(ctx, b"\x00" * size, "INTERVIEW_AUDIO")
    assert (response.status_code, code(response)) == (413, "MEDIA_TOO_LARGE")


def multipart(*parts) -> tuple[bytes, str]:
    """A hand-built multipart body: parts are (name, value) or (name, filename, value)."""
    boundary = "jidan-test-boundary"
    chunks = []
    for part in parts:
        name, *rest = part
        if len(rest) == 2:
            disposition = f'form-data; name="{name}"; filename="{rest[0]}"'
            value = rest[1]
        else:
            disposition = f'form-data; name="{name}"'
            value = rest[0]
        chunks.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n".encode() + value + b"\r\n")
    return b"".join(chunks) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


PNG = samples.png()


@pytest.mark.parametrize("parts,field", [
    ([("file", "a.png", PNG)], "purpose"),
    ([("purpose", b"MANUAL_PHOTO")], "file"),
    ([("purpose", b"VIDEO"), ("file", "a.png", PNG)], "purpose"),
    ([("purpose", b"manual_photo"), ("file", "a.png", PNG)], "purpose"),
    ([("purpose", b"MANUAL_PHOTO" * 10), ("file", "a.png", PNG)], "purpose"),
    ([("purpose", b"MANUAL_PHOTO"), ("storeId", b"x"), ("file", "a.png", PNG)], "storeId"),
    ([("purpose", b"MANUAL_PHOTO"), ("file", "a.png", PNG), ("file", "b.png", PNG)], "file"),
    ([("purpose", b"MANUAL_PHOTO"), ("purpose", b"MANUAL_PHOTO"), ("file", "a.png", PNG)], "purpose"),
    ([("purpose", b"MANUAL_PHOTO"), ("file", PNG)], "file"),  # a text field, not a file
])
def test_form_fields_are_validated(ctx, parts, field):
    body, content_type = multipart(*parts)
    response = ctx.api.post(f"/api/stores/{ctx.store}/manual/media", content=body,
                            headers={**ctx.auth.headers(str(uuid.uuid4())), "Content-Type": content_type})
    assert response.status_code == 422 and code(response) == "VALIDATION_ERROR", response.text
    assert response.json()["fieldErrors"][0]["field"] == field
    assert count(ctx, ManualMedia) == 0


def test_broken_multipart_framing_is_a_validation_error(ctx):
    body, content_type = multipart(("purpose", b"MANUAL_PHOTO"), ("file", "a.png", PNG))
    response = ctx.api.post(f"/api/stores/{ctx.store}/manual/media", content=body[:-30],
                            headers={**ctx.auth.headers(str(uuid.uuid4())), "Content-Type": content_type})
    assert response.status_code in (201, 422)  # a cut final boundary still has every byte of the file
    garbage = ctx.api.post(f"/api/stores/{ctx.store}/manual/media", content=b"--x\r\nnot a header\r\n",
                           headers={**ctx.auth.headers(str(uuid.uuid4())),
                                    "Content-Type": "multipart/form-data; boundary=x"})
    assert (garbage.status_code, code(garbage)) == (422, "VALIDATION_ERROR")


def test_non_multipart_body_is_a_validation_error(ctx):
    response = ctx.api.post(f"/api/stores/{ctx.store}/manual/media",
                            headers=ctx.auth.headers(str(uuid.uuid4())), json={"purpose": "MANUAL_PHOTO"})
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")


# --- upload: authorization --------------------------------------------------------------------


def test_upload_requires_a_session_owner_role_csrf_and_key(ctx):
    data = samples.png()
    files = {"file": ("p.png", data, "image/png")}
    url = f"/api/stores/{ctx.store}/manual/media"
    ctx.api.cookies.clear()
    assert code(ctx.api.post(url, headers={"Origin": ORIGIN}, data={"purpose": "MANUAL_PHOTO"}, files=files)) == "SESSION_EXPIRED"
    auth = login(ctx.api, ctx.owner)
    no_csrf = ctx.api.post(url, headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())},
                           data={"purpose": "MANUAL_PHOTO"}, files=files)
    assert (no_csrf.status_code, code(no_csrf)) == (403, "CSRF_INVALID")
    bad_origin = ctx.api.post(url, headers={**auth.headers(str(uuid.uuid4())), "Origin": "https://evil.test"},
                              data={"purpose": "MANUAL_PHOTO"}, files=files)
    assert code(bad_origin) == "CSRF_INVALID"
    no_key = ctx.api.post(url, headers=auth.headers(), data={"purpose": "MANUAL_PHOTO"}, files=files)
    assert (no_key.status_code, code(no_key)) == (422, "VALIDATION_ERROR")
    with Session(ctx.engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    worker_auth = login(ctx.api, worker_id)
    response = upload(ctx, data, auth=worker_auth)
    assert (response.status_code, code(response)) == (403, "FORBIDDEN")
    assert count(ctx, ManualMedia) == 0


@pytest.mark.parametrize("which,status,error", [
    ("other_store", 404, "STORE_NOT_FOUND"), ("missing", 404, "STORE_NOT_FOUND"),
    ("pending", 403, "STORE_APPROVAL_REQUIRED"), ("malformed", 422, "VALIDATION_ERROR"),
])
def test_store_ownership_and_approval(ctx, which, status, error):
    store = {"other_store": ctx.other_store, "missing": str(uuid.uuid4()), "pending": ctx.pending,
             "malformed": "not-a-uuid"}[which]
    response = upload(ctx, samples.png(), store=store)
    assert (response.status_code, code(response)) == (status, error)


# --- upload: idempotency and failures ---------------------------------------------------------


def test_same_key_replays_and_other_file_with_same_key_conflicts(ctx):
    key = str(uuid.uuid4())
    first = upload(ctx, samples.png(), key=key)
    replay = upload(ctx, samples.png(), key=key)
    assert replay.status_code == 201 and replay.json() == first.json()
    assert replay.headers.get("Idempotent-Replayed") == "true"
    other = upload(ctx, samples.jpeg(), key=key)
    assert (other.status_code, code(other)) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert count(ctx, ManualMedia) == 1


def test_rejections_are_not_fixed_to_the_key(ctx):
    key = str(uuid.uuid4())
    assert upload(ctx, b"", key=key).status_code == 422
    assert upload(ctx, b"", key=key).status_code == 422  # still judged again
    assert upload(ctx, samples.png(), "MANUAL_PHOTO", key=str(uuid.uuid4())).status_code == 201


def test_commit_failure_leaves_neither_row_nor_file(ctx, monkeypatch):
    from app import idempotency

    def broken(*_args, **_kwargs):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(idempotency, "_complete", broken)
    response = upload(ctx, samples.png())
    assert (response.status_code, code(response)) == (500, "INTERNAL_ERROR")
    assert count(ctx, ManualMedia) == 0 and list(ctx.storage.iter_files("manual")) == []


def test_concurrent_retries_with_one_key_store_one_file(ctx):
    key = str(uuid.uuid4())
    data = samples.png()
    results = []
    barrier = threading.Barrier(3)

    def send():
        barrier.wait()
        results.append(upload(ctx, data, key=key).status_code)

    threads = [threading.Thread(target=send) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert min(results) == 201 and set(results) <= {201, 409}
    assert count(ctx, ManualMedia) == 1 and len(list(ctx.storage.iter_files("manual"))) == 1


# --- delete -----------------------------------------------------------------------------------


def delete(ctx, media_id, store=None, auth=None):
    return ctx.api.delete(f"/api/stores/{store or ctx.store}/manual/media/{media_id}",
                          headers=(auth or ctx.auth).headers())


def link_to_answer(ctx, media_id):
    with Session(ctx.engine) as db:
        question_set, intents = make_question_set(db)
        from app.db.models import Store

        version = make_manual_draft(db, db.get(Store, ctx.store))
        interview = make_interview(db, version, question_set, intents)
        question = InterviewTurn(session_id=interview.id, turn_no=1, speaker="AI", turn_kind="QUESTION",
                                 question_kind="BASE", intent_id=intents[0].id, content="질문")
        db.add(question)
        db.flush()
        answer = InterviewTurn(session_id=interview.id, turn_no=2, speaker="OWNER", turn_kind="ANSWER",
                               intent_id=intents[0].id, reply_to_question_turn_id=question.id,
                               input_method="TEXT", content="답변")
        db.add(answer)
        db.flush()
        lock_photos_for_link(db, ctx.store, [media_id])
        db.add(InterviewTurnPhoto(turn_id=answer.id, media_id=media_id, sort_order=0))
        db.commit()
        return version.id


def test_unreferenced_file_is_deleted_idempotently_and_its_bytes_released(ctx):
    media_id = upload(ctx, samples.png()).json()["id"]
    key = media_row(ctx, media_id).object_key
    assert delete(ctx, media_id).status_code == 204
    row = media_row(ctx, media_id)
    assert row.deleted_at is not None and row.content_deleted_at is not None
    assert not ctx.storage.exists(key)
    assert delete(ctx, media_id).status_code == 204  # tombstone proves the original owner


@pytest.mark.parametrize("holder", ["answer", "snapshot"])
def test_referenced_photo_cannot_be_deleted(ctx, holder):
    media_id = upload(ctx, samples.png()).json()["id"]
    if holder == "answer":
        link_to_answer(ctx, media_id)
    else:
        with Session(ctx.engine) as db:
            lock_photos_for_link(db, ctx.store, [media_id])
            add_snapshot_refs(db, "DRAFT_GENERATION", str(uuid.uuid4()), [media_id])
            db.commit()
    response = delete(ctx, media_id)
    assert (response.status_code, code(response)) == (409, "MEDIA_IN_USE")
    assert media_row(ctx, media_id).deleted_at is None and ctx.storage.exists(media_row(ctx, media_id).object_key)


def test_recording_under_transcription_cannot_be_deleted(ctx):
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    assert transcribe(ctx, media_id).status_code == 202
    assert code(delete(ctx, media_id)) == "MEDIA_IN_USE"
    drain()
    assert delete(ctx, media_id).status_code == 204  # transcript text stays readable


def test_delete_scoping(ctx):
    media_id = upload(ctx, samples.png()).json()["id"]
    assert code(delete(ctx, str(uuid.uuid4()))) == "MANUAL_RESOURCE_NOT_FOUND"
    assert code(delete(ctx, media_id, store=ctx.other_store)) == "STORE_NOT_FOUND"
    assert delete(ctx, "nope").status_code == 422
    no_csrf = ctx.api.delete(f"/api/stores/{ctx.store}/manual/media/{media_id}", headers={"Origin": ORIGIN})
    assert code(no_csrf) == "CSRF_INVALID"
    other_auth = login(ctx.api, ctx.other_owner)
    assert code(delete(ctx, media_id, store=ctx.other_store, auth=other_auth)) == "MANUAL_RESOURCE_NOT_FOUND"
    assert media_row(ctx, media_id).deleted_at is None


def test_delete_and_link_race_never_leaves_a_dangling_reference(ctx):
    if ctx.engine.dialect.name != "mysql":
        pytest.skip("row locks are a MySQL behaviour")
    for _ in range(5):
        race_delete_and_link(ctx, upload(ctx, samples.png()).json()["id"])


def race_delete_and_link(ctx, media_id):
    holder = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    outcome = {}

    def link():
        barrier.wait()
        with Session(ctx.engine) as db:
            try:
                lock_photos_for_link(db, ctx.store, [media_id])
                add_snapshot_refs(db, "DRAFT_GENERATION", holder, [media_id])
                db.commit()
                outcome["link"] = "ok"
            except Exception as error:  # noqa: BLE001 - MediaLinkError when the delete won
                outcome["link"] = type(error).__name__

    def remove():
        barrier.wait()
        outcome["delete"] = delete(ctx, media_id).status_code

    threads = [threading.Thread(target=link), threading.Thread(target=remove)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    row = media_row(ctx, media_id)
    if outcome["link"] == "ok":
        assert outcome["delete"] == 409 and row.deleted_at is None
    else:
        assert outcome["link"] == "MediaLinkError"
        assert outcome["delete"] == 204 and row.deleted_at is not None


# --- protected photo content ------------------------------------------------------------------


def content(ctx, media_id, store=None, auth=None, **params):
    if auth is not None:
        ctx.api.cookies.clear()
        login(ctx.api, auth)
    return ctx.api.get(f"/api/stores/{store or ctx.store}/manual/media/{media_id}/content", params=params)


def publish_with_photo(ctx, media_id):
    from app.db.models import Store

    with Session(ctx.engine) as db:
        version = make_manual_draft(db, db.get(Store, ctx.store), status="PUBLISHED", generation_status="READY",
                                    published_at=NOW, published_by_owner_id=ctx.owner)
        lock_photos_for_link(db, ctx.store, [media_id])
        db.add(ManualPhotoAttachment(version_id=version.id, media_id=media_id, sort_order=0, title="사진 1"))
        manual = db.get(StoreManual, version.manual_id)
        manual.current_published_version_id = version.id
        db.commit()
        return version.id


def grant(ctx, worker_id, **values):
    from app.db.models import Store

    with Session(ctx.engine) as db:
        invitation = make_invitation(db, db.get(Store, ctx.store))
        db.add(StoreAccessGrant(store_id=ctx.store, worker_id=worker_id, invitation_id=invitation.id,
                                granted_at=values.pop("granted_at", utcnow() - timedelta(days=1)), **values))
        db.commit()


def test_owner_sees_linked_photos_only(ctx):
    linked = upload(ctx, samples.jpeg()).json()["id"]
    loose = upload(ctx, samples.png()).json()["id"]
    audio = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    link_to_answer(ctx, linked)
    response = content(ctx, linked)
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff" and "no-store" in response.headers["cache-control"]
    assert response.content == ctx.storage.read(media_row(ctx, linked).object_key)
    assert code(content(ctx, loose)) == "MANUAL_RESOURCE_NOT_FOUND"
    assert code(content(ctx, audio)) == "MANUAL_RESOURCE_NOT_FOUND"
    assert code(content(ctx, linked, expectedVersionId=str(uuid.uuid4()))) == "VALIDATION_ERROR"
    assert code(content(ctx, linked, store=ctx.other_store)) == "STORE_NOT_FOUND"


def test_worker_reads_only_current_published_photos_with_valid_access(ctx):
    published = upload(ctx, samples.jpeg()).json()["id"]
    draft_only = upload(ctx, samples.png()).json()["id"]
    link_to_answer(ctx, draft_only)
    version = publish_with_photo(ctx, published)
    with Session(ctx.engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    assert code(content(ctx, published, auth=worker_id)) == "STORE_NOT_FOUND"  # no access yet
    grant(ctx, worker_id)
    assert content(ctx, published, auth=worker_id).status_code == 200
    assert content(ctx, published, auth=worker_id, expectedVersionId=version).status_code == 200
    changed = content(ctx, published, auth=worker_id, expectedVersionId=str(uuid.uuid4()))
    assert (changed.status_code, code(changed)) == (409, "MANUAL_VERSION_CHANGED")
    assert code(content(ctx, draft_only, auth=worker_id)) == "MANUAL_RESOURCE_NOT_FOUND"


@pytest.mark.parametrize("values", [
    {"valid_until": "now"},  # the end instant itself is already closed
    {"revoked_at": "past"},
    {"granted_at": "future"},
])
def test_worker_access_window_is_enforced(ctx, values):
    media_id = upload(ctx, samples.jpeg()).json()["id"]
    publish_with_photo(ctx, media_id)
    with Session(ctx.engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    now = utcnow()
    resolved = {key: {"now": now, "past": now - timedelta(minutes=1), "future": now + timedelta(hours=1)}[value]
                for key, value in values.items()}
    if "revoked_at" in resolved:
        resolved["granted_at"] = now - timedelta(days=1)
    if "valid_until" in resolved:
        resolved["granted_at"] = now - timedelta(days=1)
    grant(ctx, worker_id, **resolved)
    assert code(content(ctx, media_id, auth=worker_id)) == "STORE_NOT_FOUND"


def test_worker_of_a_store_no_longer_approved_is_forbidden(ctx):
    from app.db.models import Store

    media_id = upload(ctx, samples.jpeg()).json()["id"]
    publish_with_photo(ctx, media_id)
    with Session(ctx.engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    grant(ctx, worker_id)
    with Session(ctx.engine) as db:
        store = db.get(Store, ctx.store)
        store.approval_status, store.approved_at = "PENDING", None
        db.commit()
    assert code(content(ctx, media_id, auth=worker_id)) == "STORE_APPROVAL_REQUIRED"


def test_content_requires_a_session(ctx):
    media_id = upload(ctx, samples.jpeg()).json()["id"]
    ctx.api.cookies.clear()
    assert code(content(ctx, media_id)) == "SESSION_EXPIRED"


# --- transcriptions ---------------------------------------------------------------------------


def transcribe(ctx, media_id, *, key=None, store=None, auth=None):
    return ctx.api.post(f"/api/stores/{store or ctx.store}/manual/transcriptions",
                        headers=(auth or ctx.auth).headers(key or str(uuid.uuid4())), json={"mediaId": media_id})


def read_transcription(ctx, transcription_id, store=None):
    return ctx.api.get(f"/api/stores/{store or ctx.store}/manual/transcriptions/{transcription_id}")


def test_transcription_runs_asynchronously_and_ready_is_returned_as_is(ctx, fake_ai):
    media_id = upload(ctx, samples.webm(), "INTERVIEW_AUDIO").json()["id"]
    fake_ai.script("transcribe", FakeOutcome.ok("야간조는 밤 10시부터 다음 날 아침 7시까지예요."))
    started = transcribe(ctx, media_id)
    assert started.status_code == 202
    body = started.json()
    assert (body["status"], body["text"], body["error"], body["completedAt"]) == ("RUNNING", None, None, None)
    assert read_transcription(ctx, body["id"]).json()["status"] == "RUNNING"
    again = transcribe(ctx, media_id)  # a new key while RUNNING joins the same task
    assert again.status_code == 202 and again.json()["id"] == body["id"]
    assert [run.outcome for run in drain()] == ["succeeded"]
    ready = read_transcription(ctx, body["id"]).json()
    assert ready["status"] == "READY" and ready["text"].startswith("야간조는") and ready["completedAt"]
    repeated = transcribe(ctx, media_id)
    assert repeated.status_code == 200 and repeated.json() == ready
    assert fake_ai.calls_for("transcribe")[0].extra["mime_type"] == "audio/webm"
    row = media_row(ctx, media_id)
    with Session(ctx.engine) as db:
        done = db.get(MediaTranscription, body["id"]).completed_at
    assert row.expires_at == done + timedelta(hours=24)


def test_same_key_replays_the_first_response(ctx):
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    key = str(uuid.uuid4())
    first = transcribe(ctx, media_id, key=key)
    drain()
    replay = transcribe(ctx, media_id, key=key)
    assert replay.status_code == 202 and replay.json() == first.json()
    assert replay.headers.get("Idempotent-Replayed") == "true"
    other = transcribe(ctx, str(uuid.uuid4()), key=key)
    assert code(other) == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize("outcomes", [
    [FakeOutcome.ok("")], [FakeOutcome.ok("   \n")],  # silence / blank -> not retried
    [FakeOutcome.fail("timeout")] * 3,  # automatic retries exhausted
    [FakeOutcome.fail("input_rejected")],
])
def test_failed_transcription_is_an_error_and_can_be_retried_with_a_new_key(ctx, fake_ai, outcomes):
    media_id = upload(ctx, samples.mp3(40), "INTERVIEW_AUDIO").json()["id"]
    fake_ai.script("transcribe", *outcomes)
    first = transcribe(ctx, media_id).json()
    later = utcnow()
    for minutes in range(len(outcomes)):
        drain(now=later + timedelta(minutes=minutes))
    failed = read_transcription(ctx, first["id"]).json()
    assert failed["status"] == "ERROR" and failed["text"] is None and failed["completedAt"]
    assert failed["error"]["code"] == "TRANSCRIPTION_FAILED" and failed["error"]["retryable"] is True
    assert "timeout" not in failed["error"]["message"] and "fake" not in str(failed)
    fake_ai.script("transcribe", FakeOutcome.ok("다시 말한 답변이에요."))
    retried = transcribe(ctx, media_id)
    assert retried.status_code == 202 and retried.json()["id"] == first["id"]
    drain(now=utcnow() + timedelta(hours=1))
    assert read_transcription(ctx, first["id"]).json()["text"] == "다시 말한 답변이에요."
    with Session(ctx.engine) as db:
        row = db.get(MediaTranscription, first["id"])
        assert row.attempt == 2
        assert db.scalar(select(func.count()).select_from(BackgroundTask)) == 2


def test_retry_after_the_recording_was_purged_needs_a_new_upload(ctx, fake_ai):
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    fake_ai.script("transcribe", FakeOutcome.ok(""))
    first = transcribe(ctx, media_id).json()
    drain()
    from app.media.retention import purge_media_content

    assert purge_media_content(now=utcnow() + timedelta(hours=25)) == 1
    response = transcribe(ctx, media_id)
    assert (response.status_code, code(response)) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    assert read_transcription(ctx, first["id"]).json()["status"] == "ERROR"


def test_ready_transcript_survives_purge_of_the_recording(ctx):
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    first = transcribe(ctx, media_id).json()
    drain()
    from app.media.retention import purge_media_content

    purge_media_content(now=utcnow() + timedelta(hours=25))
    assert media_row(ctx, media_id).content_deleted_at is not None
    again = transcribe(ctx, media_id)
    assert again.status_code == 200 and again.json()["text"] == read_transcription(ctx, first["id"]).json()["text"]


def test_recording_removed_while_queued_fails_without_retry(ctx):
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    first = transcribe(ctx, media_id).json()
    ctx.storage.delete(media_row(ctx, media_id).object_key)  # bytes lost underneath the task
    assert [run.outcome for run in drain()] == ["failed"]
    assert read_transcription(ctx, first["id"]).json()["status"] == "ERROR"


@pytest.mark.parametrize("which,status,error", [
    ("photo", 422, "MEDIA_PURPOSE_INVALID"), ("unknown", 404, "MANUAL_RESOURCE_NOT_FOUND"),
    ("deleted", 404, "MANUAL_RESOURCE_NOT_FOUND"), ("other_store", 404, "MANUAL_RESOURCE_NOT_FOUND"),
    ("expired", 404, "MANUAL_RESOURCE_NOT_FOUND"), ("malformed", 422, "VALIDATION_ERROR"),
])
def test_transcription_input_rules(ctx, which, status, error):
    if which == "photo":
        media_id = upload(ctx, samples.png()).json()["id"]
    elif which == "deleted":
        media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
        delete(ctx, media_id)
    elif which == "other_store":
        other_auth = login(ctx.api, ctx.other_owner)
        media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO", store=ctx.other_store,
                          auth=other_auth).json()["id"]
        ctx.api.cookies.clear()
        ctx.auth = login(ctx.api, ctx.owner)
    elif which == "expired":
        media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
        with Session(ctx.engine) as db:
            row = db.get(ManualMedia, media_id)
            row.created_at, row.expires_at = utcnow() - timedelta(days=2), utcnow() - timedelta(seconds=1)
            db.commit()
    else:
        media_id = {"unknown": str(uuid.uuid4()), "malformed": "x"}[which]
    response = transcribe(ctx, media_id)
    assert (response.status_code, code(response)) == (status, error)
    assert count(ctx, MediaTranscription) == 0


def test_transcription_reads_are_scoped_to_the_owner_store(ctx):
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    transcription_id = transcribe(ctx, media_id).json()["id"]
    assert code(read_transcription(ctx, str(uuid.uuid4()))) == "MANUAL_RESOURCE_NOT_FOUND"
    assert code(read_transcription(ctx, transcription_id, store=ctx.other_store)) == "STORE_NOT_FOUND"
    ctx.api.cookies.clear()
    login(ctx.api, ctx.other_owner)
    response = read_transcription(ctx, transcription_id, store=ctx.other_store)
    assert code(response) == "MANUAL_RESOURCE_NOT_FOUND"
    with Session(ctx.engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    ctx.api.cookies.clear()
    login(ctx.api, worker_id)
    assert code(read_transcription(ctx, transcription_id)) == "FORBIDDEN"


def test_concurrent_requests_with_different_keys_start_one_job(ctx):
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    results = []
    barrier = threading.Barrier(4)

    def send():
        barrier.wait()
        results.append(transcribe(ctx, media_id))

    threads = [threading.Thread(target=send) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {response.status_code for response in results} == {202}
    assert len({response.json()["id"] for response in results}) == 1
    assert count(ctx, MediaTranscription) == 1 and count(ctx, BackgroundTask) == 1


def test_transcript_text_never_reaches_the_logs(ctx, fake_ai, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    media_id = upload(ctx, samples.wav_seconds(1), "INTERVIEW_AUDIO").json()["id"]
    fake_ai.script("transcribe", FakeOutcome.ok("비밀 번호는 1234예요"))
    transcribe(ctx, media_id)
    drain()
    assert "1234" not in caplog.text and "stt call" in caplog.text


def test_worker_loses_photos_of_a_store_whose_owner_is_suspended(ctx):
    from app.db.models import User

    media_id = upload(ctx, samples.jpeg()).json()["id"]
    publish_with_photo(ctx, media_id)
    with Session(ctx.engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    grant(ctx, worker_id)
    assert content(ctx, media_id, auth=worker_id).status_code == 200
    with Session(ctx.engine) as db:
        db.get(User, ctx.owner).status = "SUSPENDED"
        db.commit()
    assert code(content(ctx, media_id, auth=worker_id)) == "STORE_NOT_FOUND"


# --- review-119: idempotency endpoint is the canonical path -----------------------------------

def test_upload_retry_with_another_store_id_case_replays(ctx):
    key = str(uuid.uuid4())
    first = upload(ctx, samples.png(), key=key)
    retry = upload(ctx, samples.png(), key=key, store=ctx.store.upper())
    assert retry.status_code == 201 and retry.json() == first.json()
    assert retry.headers.get("Idempotent-Replayed") == "true"
    assert count(ctx, ManualMedia) == 1


def test_transcription_retry_with_another_store_id_case_replays(ctx):
    media_id = upload(ctx, samples.webm(), "INTERVIEW_AUDIO").json()["id"]
    key = str(uuid.uuid4())
    headers = ctx.auth.headers(key)
    first = ctx.api.post(f"/api/stores/{ctx.store}/manual/transcriptions", json={"mediaId": media_id},
                         headers=headers)
    retry = ctx.api.post(f"/api/stores/{ctx.store.upper()}/manual/transcriptions",
                         json={"mediaId": media_id.upper()}, headers=headers)
    assert first.status_code == 202 and retry.status_code == 202
    assert retry.json() == first.json() and retry.headers.get("Idempotent-Replayed") == "true"

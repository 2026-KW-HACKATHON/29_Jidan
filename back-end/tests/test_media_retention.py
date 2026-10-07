"""Retention of media bytes: deleted, unattached and expired files go; referenced ones stay."""

import os
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import (
    ManualMedia,
    ManualQa,
    ManualQaConversation,
    ManualQaPhoto,
    MediaTranscription,
    QaMedia,
    Store,
)
from app.media.references import (
    MediaLinkError,
    add_snapshot_refs,
    lock_photos_for_link,
    manual_media_in_use,
    remove_snapshot_refs,
    replace_snapshot_refs,
)
from app.media.retention import purge_media_content, sweep_orphan_files
from app.media.storage import LocalMediaStorage, object_key, set_media_storage
from tests.factories import NOW, make_manual_draft, make_store, make_user, make_worker


@pytest.fixture
def env(db_engine, tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    with Session(db_engine) as db:
        store = make_store(db, owner=make_user(db, "OWNER"), approval_status="APPROVED", approved_at=NOW)
        db.commit()
        store_id, owner_id = store.id, store.owner_id
    yield db_engine, storage, store_id, owner_id
    set_media_storage(None)


def stored(env, kind="IMAGE", age=timedelta(0), model=ManualMedia, **values):
    engine, storage, store_id, owner_id = env
    media_id = str(uuid.uuid4())
    key = object_key("manual" if model is ManualMedia else "qa", store_id, media_id)
    storage.write(key, b"bytes")
    created = utcnow() - age
    with Session(engine) as db:
        owner = {"uploaded_by_owner_id": owner_id} if model is ManualMedia else {"worker_id": values.pop("worker_id")}
        db.add(model(
            id=media_id, store_id=store_id, kind=kind, object_key=key,
            mime_type="image/png" if kind == "IMAGE" else "audio/wav", byte_size=5,
            duration_ms=None if kind == "IMAGE" else 1000, created_at=created,
            expires_at=created + timedelta(hours=24), **owner, **values,
        ))
        db.commit()
    return media_id, key


def row(env, media_id, model=ManualMedia):
    with Session(env[0]) as db:
        return db.get(model, media_id)


def test_expired_unattached_and_deleted_files_are_purged_and_others_kept(env):
    _engine, storage, *_ = env
    fresh, fresh_key = stored(env)
    old, old_key = stored(env, age=timedelta(hours=25))
    _deleted, deleted_key = stored(env, deleted_at=utcnow())
    assert purge_media_content() == 2
    assert storage.exists(fresh_key) and not storage.exists(old_key) and not storage.exists(deleted_key)
    assert row(env, old).content_deleted_at is not None and row(env, fresh).content_deleted_at is None
    assert purge_media_content() == 0  # already released


def test_referenced_photo_is_never_purged_and_gets_a_grace_period_when_released(env):
    engine, storage, store_id, _ = env
    media_id, key = stored(env, age=timedelta(days=3))
    holder, intent = str(uuid.uuid4()), str(uuid.uuid4())
    with Session(engine) as db:
        lock_photos_for_link(db, store_id, [media_id])
        add_snapshot_refs(db, "INTENT_REVIEW", holder, [media_id], intent_id=intent)
        db.commit()
    assert purge_media_content() == 0 and storage.exists(key)
    assert row(env, media_id).expires_at > utcnow() + timedelta(hours=23)  # re-checked tomorrow
    with Session(engine) as db:
        db.get(ManualMedia, media_id).expires_at = utcnow() - timedelta(minutes=1)
        released = remove_snapshot_refs(db, "INTENT_REVIEW", holder, intent_id=intent)
        db.commit()
    assert released == [media_id]
    assert purge_media_content() == 0  # a fresh 24 h after losing its last link
    assert purge_media_content(now=utcnow() + timedelta(hours=25)) == 1 and not storage.exists(key)


def test_snapshot_reference_bookkeeping(env):
    engine, _storage, store_id, _ = env
    first, _ = stored(env)
    second, _ = stored(env)
    holder = str(uuid.uuid4())
    with Session(engine) as db:
        lock_photos_for_link(db, store_id, [first, second])
        add_snapshot_refs(db, "DRAFT_GENERATION", holder, [first, second, first])
        add_snapshot_refs(db, "DRAFT_GENERATION", holder, [first])  # idempotent
        assert manual_media_in_use(db, first) and manual_media_in_use(db, second)
        replace_snapshot_refs(db, "DRAFT_GENERATION", holder, [second])
        assert not manual_media_in_use(db, first) and manual_media_in_use(db, second)
        db.commit()


@pytest.mark.parametrize("case", ["other_store", "audio", "deleted", "purged", "unknown"])
def test_only_live_photos_of_the_store_can_be_linked(env, case):
    engine, _storage, store_id, _ = env
    if case == "other_store":
        with Session(engine) as db:
            other = make_store(db)
            db.commit()
            store_id = other.id
        media_id, _ = stored(env)
    elif case == "audio":
        media_id, _ = stored(env, kind="AUDIO")
    elif case == "deleted":
        media_id, _ = stored(env, deleted_at=utcnow())
    elif case == "purged":
        media_id, _ = stored(env, content_deleted_at=utcnow())
    else:
        media_id = str(uuid.uuid4())
    with Session(engine) as db, pytest.raises(MediaLinkError) as caught:
        lock_photos_for_link(db, store_id, [media_id])
    assert caught.value.reason == ("not_image" if case == "audio" else "not_found")


def test_recording_is_kept_while_transcribing_and_purged_a_day_after(env):
    engine, storage, store_id, _ = env
    media_id, key = stored(env, kind="AUDIO", age=timedelta(hours=30))
    with Session(engine) as db:
        db.add(MediaTranscription(store_id=store_id, manual_media_id=media_id, status="RUNNING",
                                  attempt=1, task_id=str(uuid.uuid4())))
        db.commit()
    assert purge_media_content() == 0 and storage.exists(key)
    with Session(engine) as db:
        transcription = db.query(MediaTranscription).one()
        transcription.status, transcription.text, transcription.completed_at = "READY", "텍스트", utcnow()
        transcription.task_id = None
        db.get(ManualMedia, media_id).expires_at = utcnow() + timedelta(hours=24)
        db.commit()
    assert purge_media_content() == 0
    assert purge_media_content(now=utcnow() + timedelta(hours=24, seconds=1)) == 1
    assert not storage.exists(key)
    with Session(engine) as db:
        assert db.query(MediaTranscription).one().text == "텍스트"  # the transcript stays


def test_question_photos_stay_while_a_question_uses_them(env):
    engine, storage, store_id, owner_id = env
    with Session(engine) as db:
        worker = make_worker(db)
        version = make_manual_draft(db, db.get(Store, store_id),
                                    status="PUBLISHED", generation_status="READY", published_at=NOW,
                                    published_by_owner_id=owner_id)
        conversation = ManualQaConversation(store_id=store_id, worker_id=worker.id)
        db.add(conversation)
        db.commit()
        worker_id, version_id, conversation_id = worker.id, version.id, conversation.id
    used, used_key = stored(env, model=QaMedia, age=timedelta(days=8), worker_id=worker_id)
    loose, loose_key = stored(env, model=QaMedia, age=timedelta(days=8), worker_id=worker_id)
    with Session(engine) as db:
        question = ManualQa(conversation_id=conversation_id, sequence=1, published_version_id=version_id,
                            input_method="TEXT", question="이건 뭐예요?", status="RUNNING",
                            task_id=str(uuid.uuid4()))
        db.add(question)
        db.flush()
        db.add(ManualQaPhoto(qa_id=question.id, media_id=used, sort_order=0))
        db.commit()
    assert purge_media_content() == 1
    assert storage.exists(used_key) and not storage.exists(loose_key)
    assert row(env, loose, QaMedia).content_deleted_at is not None


@pytest.mark.parametrize("status", ["READY", "ERROR"])
def test_question_photos_of_finished_questions_expire_after_seven_days(env, status):
    """Only a RUNNING question holds its photos; afterwards the 7 day period applies (a retry of
    an ERROR question then needs a new question: 410 QA_INPUT_EXPIRED)."""
    engine, storage, store_id, owner_id = env
    with Session(engine) as db:
        worker = make_worker(db)
        version = make_manual_draft(db, db.get(Store, store_id),
                                    status="PUBLISHED", generation_status="READY", published_at=NOW,
                                    published_by_owner_id=owner_id)
        conversation = ManualQaConversation(store_id=store_id, worker_id=worker.id)
        db.add(conversation)
        db.commit()
        worker_id, version_id, conversation_id = worker.id, version.id, conversation.id
    old, old_key = stored(env, model=QaMedia, age=timedelta(days=8), worker_id=worker_id)
    fresh, fresh_key = stored(env, model=QaMedia, age=timedelta(hours=1), worker_id=worker_id)
    finished = {"READY": {"outcome": "NEEDS_OWNER", "answer": "점주님께 확인해 주세요."},
                "ERROR": {"public_error_code": "AI_PROCESSING_FAILED"}}[status]
    with Session(engine) as db:
        question = ManualQa(conversation_id=conversation_id, sequence=1, published_version_id=version_id,
                            input_method="TEXT", question="이건 뭐예요?", status=status,
                            completed_at=NOW, **finished)
        db.add(question)
        db.flush()
        db.add_all([ManualQaPhoto(qa_id=question.id, media_id=old, sort_order=0),
                    ManualQaPhoto(qa_id=question.id, media_id=fresh, sort_order=1)])
        db.commit()
    assert purge_media_content() == 1
    assert not storage.exists(old_key) and storage.exists(fresh_key)
    assert row(env, old, QaMedia).content_deleted_at is not None
    with Session(engine) as db:  # the question keeps its photo link and text
        assert db.query(ManualQaPhoto).count() == 2 and db.query(ManualQa).one().question == "이건 뭐예요?"


def test_orphan_sweep_removes_files_without_live_rows(env):
    _engine, storage, store_id, _ = env
    live, live_key = stored(env)
    orphan_key = object_key("manual", store_id, str(uuid.uuid4()))
    storage.write(orphan_key, b"left behind by a crash")
    # The sweep compares file times with time.time(); stamp the file with that clock so the test
    # does not depend on the file system clock agreeing with it (tests/clock_shift.py).
    os.utime(storage._path(orphan_key), (time.time(), time.time()))
    assert sweep_orphan_files(min_age_seconds=3600) == 0  # too recent to judge
    assert sweep_orphan_files(min_age_seconds=-1) == 1
    assert storage.exists(live_key) and not storage.exists(orphan_key)
    assert row(env, live).content_deleted_at is None

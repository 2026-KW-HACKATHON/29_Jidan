"""Database rules of manual/Q&A media and transcriptions (both SQLite and MySQL)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.models import MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, ManualMedia, MediaTranscription, QaMedia
from tests.factories import NOW, make_store, make_user, make_worker


def media(session, store, **overrides) -> ManualMedia:
    values = {
        "store_id": store.id, "uploaded_by_owner_id": store.owner_id, "kind": "IMAGE",
        "object_key": f"manual/{uuid.uuid4()}", "mime_type": "image/jpeg", "byte_size": 100,
        "created_at": NOW, "expires_at": NOW + timedelta(hours=24), **overrides,
    }
    row = ManualMedia(**values)
    session.add(row)
    session.flush()
    return row


def rejected(session, build):
    with pytest.raises((IntegrityError, OperationalError)), session.begin_nested():
        build()


@pytest.fixture
def store(session):
    return make_store(session, owner=make_user(session, "OWNER"))


def test_exact_limits_are_accepted(session, store):
    media(session, store, byte_size=MAX_IMAGE_BYTES)
    media(session, store, kind="AUDIO", mime_type="audio/webm", byte_size=MAX_AUDIO_BYTES, duration_ms=120_000)


@pytest.mark.parametrize("overrides", [
    {"byte_size": MAX_IMAGE_BYTES + 1},
    {"byte_size": 0},
    {"mime_type": "image/gif"},
    {"mime_type": "audio/wav"},  # an IMAGE row with an audio type
    {"duration_ms": 1000},  # photos have no duration
    {"kind": "AUDIO", "mime_type": "audio/wav", "byte_size": MAX_AUDIO_BYTES + 1, "duration_ms": 1},
    {"kind": "AUDIO", "mime_type": "audio/wav", "duration_ms": 120_001},
    {"kind": "AUDIO", "mime_type": "audio/wav", "duration_ms": None},
    {"kind": "AUDIO", "mime_type": "image/png", "duration_ms": 10},
    {"kind": "VIDEO"},
    {"expires_at": NOW},
])
def test_media_shape_violations_are_rejected(session, store, overrides):
    rejected(session, lambda: media(session, store, **overrides))


def test_object_keys_are_unique_across_case_sensitive_values(session, store):
    media(session, store, object_key="manual/AbC")
    media(session, store, object_key="manual/abc")
    rejected(session, lambda: media(session, store, object_key="manual/abc"))


def transcription(session, store, **overrides) -> MediaTranscription:
    values = {"store_id": store.id, "status": "RUNNING", "attempt": 1, "task_id": str(uuid.uuid4()),
              **overrides}
    row = MediaTranscription(**values)
    session.add(row)
    session.flush()
    return row


def test_one_transcription_per_recording_and_exactly_one_source(session, store):
    audio = media(session, store, kind="AUDIO", mime_type="audio/mpeg", duration_ms=5000)
    transcription(session, store, manual_media_id=audio.id)
    rejected(session, lambda: transcription(session, store, manual_media_id=audio.id))
    rejected(session, lambda: transcription(session, store))
    worker_audio = QaMedia(
        store_id=store.id, worker_id=make_worker(session).id, kind="AUDIO", object_key="qa/x",
        mime_type="audio/mp4", byte_size=10, duration_ms=10, created_at=NOW, expires_at=NOW + timedelta(days=1),
    )
    session.add(worker_audio)
    session.flush()
    other = media(session, store, kind="AUDIO", mime_type="audio/mpeg", duration_ms=5000)
    rejected(session, lambda: transcription(
        session, store, manual_media_id=other.id, qa_media_id=worker_audio.id))
    transcription(session, store, qa_media_id=worker_audio.id)


@pytest.mark.parametrize("overrides", [
    {"status": "RUNNING", "task_id": None},
    {"status": "RUNNING", "text": "x"},
    {"status": "READY", "text": None, "completed_at": NOW},
    {"status": "READY", "text": "ok", "completed_at": None},
    {"status": "READY", "text": " 　\n", "completed_at": NOW},
    {"status": "ERROR", "completed_at": NOW},
    {"status": "ERROR", "error_code": "TIMEOUT", "completed_at": NOW},
    {"status": "ERROR", "error_code": "TRANSCRIPTION_FAILED", "text": "x", "completed_at": NOW},
    {"status": "DONE"},
    {"attempt": 0},
])
def test_transcription_state_rules(session, store, overrides):
    audio = media(session, store, kind="AUDIO", mime_type="audio/mpeg", duration_ms=5000)
    rejected(session, lambda: transcription(session, store, manual_media_id=audio.id, **overrides))


def test_valid_transcription_states(session, store):
    for values in (
        {"status": "READY", "text": "야간조는 7시에 끝나요", "completed_at": NOW, "task_id": None},
        {"status": "ERROR", "error_code": "TRANSCRIPTION_FAILED", "completed_at": NOW},
    ):
        audio = media(session, store, kind="AUDIO", mime_type="audio/mpeg", duration_ms=5000)
        transcription(session, store, manual_media_id=audio.id, **values)

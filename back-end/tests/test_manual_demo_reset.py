"""Re-running the demo seed removes the demo stores' manual, interview, Q&A and media rows only."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import demo_seed, manual_demo_seed
from app.db import utcnow
from app.db.models import (
    InterviewTurn,
    InterviewTurnPhoto,
    ManualMedia,
    ManualPhotoAttachment,
    ManualQa,
    ManualQaConversation,
    ManualSection,
    ManualShift,
    ManualVersion,
    MediaTranscription,
    Store,
    StoreManual,
    User,
)
from tests.factories import (
    make_interview,
    make_manual_draft,
    make_question_set,
    make_store,
    make_user,
)


@pytest.fixture
def safe_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan_seed_test")


def manual_rows(db, store, owner_id, worker_id):
    """A published manual with a photo, an interview with an answer and a Q&A question."""
    photo = ManualMedia(store_id=store.id, uploaded_by_owner_id=owner_id, kind="IMAGE",
                        object_key=f"manual/{store.id}/{uuid.uuid4()}", mime_type="image/png", byte_size=1,
                        created_at=utcnow(), expires_at=utcnow() + timedelta(days=1))
    audio = ManualMedia(store_id=store.id, uploaded_by_owner_id=owner_id, kind="AUDIO",
                        object_key=f"manual/{store.id}/{uuid.uuid4()}", mime_type="audio/wav", byte_size=1,
                        duration_ms=1000, created_at=utcnow(), expires_at=utcnow() + timedelta(days=1))
    db.add_all([photo, audio])
    db.flush()
    transcript = MediaTranscription(store_id=store.id, manual_media_id=audio.id, status="READY", text="답",
                                    attempt=1, completed_at=utcnow())
    db.add(transcript)
    version = make_manual_draft(db, store)
    shift = ManualShift(version_id=version.id, sort_order=0, name="오전")
    db.add(shift)
    db.flush()
    db.add(ManualSection(version_id=version.id, sort_order=0, category="SHIFT_TASK", shift_id=shift.id, title="오픈"))
    db.add(ManualPhotoAttachment(version_id=version.id, media_id=photo.id, sort_order=0, title="사진 1"))
    question_set, intents = make_question_set(db)
    interview = make_interview(db, version, question_set, intents)
    question = InterviewTurn(session_id=interview.id, turn_no=1, speaker="AI", turn_kind="QUESTION",
                             question_kind="BASE", intent_id=intents[0].id, content="질문")
    db.add(question)
    db.flush()
    answer = InterviewTurn(session_id=interview.id, turn_no=2, speaker="OWNER", turn_kind="ANSWER",
                           intent_id=intents[0].id, reply_to_question_turn_id=question.id, input_method="VOICE",
                           transcription_id=transcript.id, content="답")
    db.add(answer)
    db.flush()
    db.add(InterviewTurnPhoto(turn_id=answer.id, media_id=photo.id, sort_order=0))
    version.status, version.generation_status = "PUBLISHED", "READY"
    version.published_at, version.published_by_owner_id = utcnow(), owner_id
    db.flush()
    db.get(StoreManual, version.manual_id).current_published_version_id = version.id
    conversation = ManualQaConversation(store_id=store.id, worker_id=worker_id)
    db.add(conversation)
    db.flush()
    db.add(ManualQa(conversation_id=conversation.id, sequence=1, published_version_id=version.id,
                    input_method="TEXT", question="오픈은?", status="READY", outcome="NEEDS_OWNER",
                    answer="확인 필요", completed_at=utcnow()))
    db.flush()


def count(db, model, **where):
    statement = select(func.count()).select_from(model)
    for key, value in where.items():
        statement = statement.where(getattr(model, key) == value)
    return db.scalar(statement)


def test_reseed_clears_demo_manual_data_and_keeps_others(db_engine, safe_env):
    ids = demo_seed.run()
    with Session(db_engine) as db:
        cafe = db.get(Store, ids["cafe"])
        worker = db.scalars(select(User).where(User.role == "WORKER",
                                               User.google_sub.like(demo_seed.SUB_PREFIX + "%"))).first()
        manual_rows(db, cafe, cafe.owner_id, worker.id)
        outsider_owner = make_user(db, "OWNER")
        outsider_store = make_store(db, owner=outsider_owner, approval_status="APPROVED", approved_at=utcnow())
        manual_rows(db, outsider_store, outsider_owner.id, worker.id)
        db.commit()
        outsider_store_id = outsider_store.id
    assert demo_seed.run() == ids
    with Session(db_engine) as db:
        # Only the seed's own published manual is back (app.manual_demo_seed); the added rows are gone.
        seeded = db.scalars(select(StoreManual).where(StoreManual.store_id == ids["cafe"])).one()
        assert seeded.current_published_version_id == manual_demo_seed.manual_id("version:cafe:1")
        assert count(db, ManualVersion, manual_id=seeded.id) == 1
        assert count(db, ManualMedia, store_id=ids["cafe"]) == 0
        # The outsider's manual survives, but the demo worker's question there belongs to demo data.
        assert count(db, StoreManual, store_id=outsider_store_id) == 1
        assert count(db, ManualMedia, store_id=outsider_store_id) == 2
        assert count(db, ManualVersion) == 2 and count(db, InterviewTurn) == 2
        assert count(db, ManualQaConversation) == 0 and count(db, ManualQa) == 0

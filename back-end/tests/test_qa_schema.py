"""Database rules of worker AI Q&A (SQLite and MySQL)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.models import (
    ManualQa,
    ManualQaCitation,
    ManualQaConversation,
    ManualQaPhoto,
    ManualSection,
    QaMedia,
)
from tests.factories import NOW, make_manual_draft, make_store, make_user, make_worker


def rejected(session, build):
    with pytest.raises((IntegrityError, OperationalError)), session.begin_nested():
        build()
        session.flush()


@pytest.fixture
def setup(session):
    store = make_store(session, owner=make_user(session, "OWNER"), approval_status="APPROVED", approved_at=NOW)
    version = make_manual_draft(session, store, status="PUBLISHED", generation_status="READY",
                                published_at=NOW, published_by_owner_id=store.owner_id)
    worker = make_worker(session)
    conversation = ManualQaConversation(store_id=store.id, worker_id=worker.id)
    session.add(conversation)
    session.flush()
    return store, version, worker, conversation


def question(session, conversation, version, sequence, **values):
    values = {"input_method": "TEXT", "question": "마감은 언제 해요?", **values}
    row = ManualQa(conversation_id=conversation.id, published_version_id=version.id, sequence=sequence,
                   **values)
    session.add(row)
    session.flush()
    return row


def test_one_running_question_per_conversation(session, setup):
    _store, version, _worker, conversation = setup
    question(session, conversation, version, 1, status="RUNNING", task_id=str(uuid.uuid4()))
    rejected(session, lambda: question(session, conversation, version, 2, status="RUNNING",
                                       task_id=str(uuid.uuid4())))
    rejected(session, lambda: question(session, conversation, version, 1, status="ERROR",
                                       public_error_code="AI_PROCESSING_FAILED", completed_at=NOW))
    question(session, conversation, version, 2, status="ERROR", public_error_code="AI_PROCESSING_FAILED",
             completed_at=NOW)


@pytest.mark.parametrize("values", [
    {"status": "RUNNING"},  # no task
    {"status": "READY", "answer": "답", "completed_at": NOW},  # no outcome
    {"status": "READY", "outcome": "ANSWERED", "answer": " ", "completed_at": NOW},
    {"status": "ERROR", "public_error_code": "TIMEOUT", "completed_at": NOW},
    {"status": "ERROR", "public_error_code": "AI_PROCESSING_FAILED", "answer": "x", "completed_at": NOW},
    {"status": "READY", "outcome": "MAYBE", "answer": "답", "completed_at": NOW},
    {"status": "RUNNING", "task_id": "t", "input_method": "VOICE"},  # VOICE needs its transcription
])
def test_question_state_rules(session, setup, values):
    _store, version, _worker, conversation = setup
    rejected(session, lambda: question(session, conversation, version, 1, **values))


def test_citations_and_photos_are_bounded_and_unique(session, setup):
    store, version, worker, conversation = setup
    qa = question(session, conversation, version, 1, status="READY", outcome="ANSWERED", answer="답",
                  completed_at=NOW)
    section = ManualSection(version_id=version.id, sort_order=0, category="RULE", title="복장")
    session.add(section)
    session.flush()
    session.add(ManualQaCitation(qa_id=qa.id, section_id=section.id, sort_order=0, excerpt="앞치마"))
    session.flush()
    rejected(session, lambda: session.add(ManualQaCitation(qa_id=qa.id, section_id=section.id,
                                                           sort_order=1, excerpt="중복")))
    images = []
    for n in range(4):
        image = QaMedia(store_id=store.id, worker_id=worker.id, kind="IMAGE", object_key=f"qa/{n}",
                        mime_type="image/jpeg", byte_size=10, created_at=NOW, expires_at=NOW + timedelta(days=7))
        session.add(image)
        images.append(image)
    session.flush()
    for order, image in enumerate(images[:3]):
        session.add(ManualQaPhoto(qa_id=qa.id, media_id=image.id, sort_order=order))
    session.flush()
    rejected(session, lambda: session.add(ManualQaPhoto(qa_id=qa.id, media_id=images[3].id, sort_order=3)))

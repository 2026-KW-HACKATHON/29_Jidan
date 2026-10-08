"""Test data for the worker AI Q&A (#121): a published manual and the people around it.

Published versions come from #118's tests/manual_factories (`make_published`).
"""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import ManualQa, ManualQaConversation, QaMedia, StoreAccessGrant
from tests.factories import (
    NOW,
    make_regular_grant,
    make_store,
    make_user,
    make_worker,
)
from tests.manual_factories import make_published

DEFAULT_SECTIONS = (
    ("오픈 준비", "COMMON_TASK", ("매장 불을 켜고 포스기를 켭니다.", "커피 머신을 예열합니다.")),
    ("재고 정리", "COMMON_TASK", ("먼저 들어온 제품을 앞쪽에 진열하세요.",)),
    ("커피 머신 세척", "EQUIPMENT", ("마감 30분 전에 그룹 헤드를 세척합니다.", "세척 후 물을 두 번 흘려보냅니다.")),
)


def sections_content(sections=DEFAULT_SECTIONS) -> dict:
    """`ManualContent` (as tests/manual_factories seeds it) with only common sections and steps,
    so the Q&A tests know every title and step text."""
    return {
        "shifts": [],
        "sections": [
            {"id": str(uuid.uuid4()), "category": category, "shiftId": None, "title": title, "photos": [],
             "steps": [{"id": str(uuid.uuid4()), "instruction": text, "checklistItem": False} for text in steps]}
            for title, category, steps in sections
        ],
        "missingInformation": [],
    }


def make_published_manual(db: Session, store, sections=DEFAULT_SECTIONS):
    """Publish a new version of `store`'s manual (#118's `make_published`) holding `sections`;
    returns the version and its section IDs in order."""
    content = sections_content(sections)
    version = make_published(db, store, content)
    return version, [section["id"] for section in content["sections"]]


@dataclass
class QaWorld:
    owner: str
    store: str
    worker: str
    other_worker: str
    other_store: str
    version: str
    sections: list[str] = field(default_factory=list)


def make_qa_world(db: Session, *, published: bool = True) -> QaWorld:
    """An APPROVED store with a published manual, its worker (valid REGULAR grant), another
    worker of the same store, and another approved store the worker also works at."""
    owner = make_user(db, "OWNER")
    store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW)
    worker = make_worker(db)
    other_worker = make_worker(db)
    make_regular_grant(db, store, worker)
    make_regular_grant(db, store, other_worker)
    other_store = make_store(db, owner=make_user(db, "OWNER"), approval_status="APPROVED", approved_at=NOW)
    make_regular_grant(db, other_store, worker)
    version_id, sections = None, []
    if published:
        version, sections = make_published_manual(db, store)
        version_id = version.id
    db.commit()
    return QaWorld(owner.id, store.id, worker.id, other_worker.id, other_store.id, version_id, sections)


def end_access(db: Session, store_id: str, worker_id: str) -> None:
    """Revoke the worker's grants at the store now (manual end)."""
    for grant in db.scalars(select(StoreAccessGrant).where(
            StoreAccessGrant.store_id == store_id, StoreAccessGrant.worker_id == worker_id)):
        grant.revoked_at = utcnow()
    db.commit()


def make_qa_media(db: Session, storage, store_id: str, worker_id: str, *, kind="IMAGE", age=timedelta(0),
                  data=b"bytes", ttl=None) -> str:
    """A stored question upload, `age` old (default TTL: 7 days for photos, 24 h for audio)."""
    from app.media.storage import object_key

    media_id = str(uuid.uuid4())
    key = object_key("qa", store_id, media_id)
    storage.write(key, data)
    created = utcnow() - age
    ttl = ttl or (timedelta(days=7) if kind == "IMAGE" else timedelta(hours=24))
    db.add(QaMedia(
        id=media_id, store_id=store_id, worker_id=worker_id, kind=kind, object_key=key,
        mime_type="image/png" if kind == "IMAGE" else "audio/wav", byte_size=len(data),
        duration_ms=None if kind == "IMAGE" else 1000, created_at=created, expires_at=created + ttl,
    ))
    db.commit()
    return media_id


def make_conversation(db: Session, store_id: str, worker_id: str) -> str:
    conversation = ManualQaConversation(store_id=store_id, worker_id=worker_id)
    db.add(conversation)
    db.commit()
    return conversation.id


def make_question(db: Session, conversation_id: str, version_id: str, *, sequence=1, status="READY",
                  question="오픈 때 뭐 해요?", **values) -> str:
    finished = {
        "RUNNING": {"task_id": str(uuid.uuid4())},
        "READY": {"outcome": "NEEDS_OWNER", "answer": "점주님께 확인해 주세요.", "completed_at": utcnow()},
        "ERROR": {"public_error_code": "AI_PROCESSING_FAILED", "completed_at": utcnow()},
    }[status]
    row = ManualQa(conversation_id=conversation_id, sequence=sequence, published_version_id=version_id,
                   input_method="TEXT", question=question, status=status, **(finished | values))
    db.add(row)
    db.commit()
    return row.id


def mysql_only(test):
    """Races that rely on row locks: SQLite has none (it serializes whole transactions), so
    only MySQL can show that exactly one competitor wins."""
    return pytest.mark.mysql(pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)(test))

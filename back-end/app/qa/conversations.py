"""Worker AI Q&A conversations (#121; openapi tag AI 질문).

    POST /api/stores/{storeId}/manual/qa/conversations                    start
    GET  /api/stores/{storeId}/manual/qa/conversations                    own list (newest first)
    GET  /api/stores/{storeId}/manual/qa/conversations/{conversationId}   restore (latest turns)

A conversation belongs to the worker and the store; another worker's, another store's and
unknown conversations are the same 404, and every read re-checks current access (app.qa.access).
Starting one needs a published manual (404 MANUAL_NOT_PUBLISHED). Questions: app.qa.questions.
"""

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentWorker, DbSession
from app.csrf import CsrfWorker
from app.db import iso_utc, utcnow
from app.db.models import ManualQa, ManualQaConversation
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.manual_content import require_published_version
from app.pagination import Pagination
from app.qa.access import ConversationIdPath, authorize, not_found, qa_store
from app.qa.serialization import conversation_body, question_bodies
from app.store_access import StoreIdPath, normalize_uuid

router = APIRouter()

def conversations_path(store_id: str) -> str:
    return f"/api/stores/{normalize_uuid(store_id)}/manual/qa/conversations"


def _own_conversation(db: Session, worker_id: str, store_id: str, conversation_id: str, *,
                      lock: bool = False) -> ManualQaConversation | None:
    statement = select(ManualQaConversation).where(
        ManualQaConversation.id == normalize_uuid(conversation_id),
        ManualQaConversation.store_id == normalize_uuid(store_id),
        ManualQaConversation.worker_id == worker_id,
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return db.scalars(statement).first()


def owned_conversation(db: Session, worker_id: str, store_id: str, conversation_id: str, *,
                       lock: bool = False) -> ManualQaConversation:
    """Lock (optionally) the worker's conversation first, then check current access: another
    worker's, another store's and unknown conversations are the same 404."""
    conversation = _own_conversation(db, worker_id, store_id, conversation_id, lock=lock)
    qa_store(db, worker_id, store_id)
    if conversation is None:
        raise not_found()
    return conversation


# --- conversations ----------------------------------------------------------------------------


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/api/stores/{storeId}/manual/qa/conversations", status_code=201)
def create_conversation(
    store_id: StoreIdPath, body: ConversationCreate, worker: CsrfWorker, db: DbSession, key: IdempotencyKey,
) -> Response:
    authorize(db, worker.user_id, store_id)

    def work() -> IdempotentResult:
        store = qa_store(db, worker.user_id, store_id)
        require_published_version(db, store.id)  # 404 MANUAL_NOT_PUBLISHED
        now = utcnow()
        conversation = ManualQaConversation(store_id=store.id, worker_id=worker.user_id, created_at=now,
                                            updated_at=now)
        db.add(conversation)
        db.flush()
        return IdempotentResult(201, conversation_body(conversation))

    return run_idempotent(
        db=db, principal=worker, key=key, method="POST", path=conversations_path(store_id), body={}, handler=work,
        revalidate=lambda: qa_store(db, worker.user_id, store_id),
    )


@router.get("/api/stores/{storeId}/manual/qa/conversations")
def list_conversations(store_id: StoreIdPath, worker: CurrentWorker, db: DbSession, params: Pagination) -> dict:
    now = utcnow()
    store = qa_store(db, worker.user_id, store_id, now)
    owned = (ManualQaConversation.store_id == store.id, ManualQaConversation.worker_id == worker.user_id)
    total = db.scalar(select(func.count()).select_from(ManualQaConversation).where(*owned))
    rows = db.scalars(
        select(ManualQaConversation).where(*owned)
        .order_by(ManualQaConversation.updated_at.desc(), ManualQaConversation.id.desc())
        .offset(params.offset).limit(params.limit)
    ).all()
    return {"items": [conversation_body(row) for row in rows], "page": params.page, "size": params.size,
            "totalItems": total, "asOf": iso_utc(now)}


@router.get("/api/stores/{storeId}/manual/qa/conversations/{conversationId}")
def read_conversation(
    store_id: StoreIdPath, conversation_id: ConversationIdPath, worker: CurrentWorker, db: DbSession,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    before_sequence: Annotated[int | None, Query(alias="beforeSequence", ge=1, le=2_147_483_647)] = None,
) -> dict:
    conversation = owned_conversation(db, worker.user_id, store_id, conversation_id)
    statement = select(ManualQa).where(ManualQa.conversation_id == conversation.id)
    if before_sequence is not None:
        statement = statement.where(ManualQa.sequence < before_sequence)
    newest = db.scalars(statement.order_by(ManualQa.sequence.desc()).limit(size + 1)).all()
    turns = list(reversed(newest[:size]))
    more = len(newest) > size
    return {
        "conversation": conversation_body(conversation),
        "turns": question_bodies(db, turns),
        "nextBeforeSequence": turns[0].sequence if more else None,
    }

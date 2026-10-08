"""Who may use a store's AI Q&A (openapi tag AI 질문/AI 질문 미디어).

Every Q&A request needs an ACTIVE WORKER session (`CurrentWorker`/`CsrfWorker`) and is
re-checked here: the worker currently holds a valid access grant (USE_AI_QA comes with every
valid grant, app.store_access.GRANT_PERMISSIONS) and the store is APPROVED with an ACTIVE
owner. Ended, revoked or never-granted access and unknown stores are the same 404
RESOURCE_NOT_FOUND, so neither the store nor its Q&A rows are revealed. A worker whose grant
is still valid but whose store is not operating (approval lost or owner suspended,
`store_operating`) gets 403 STORE_APPROVAL_REQUIRED (the operations' 403 "매장 승인"), the
same rule as reading the published manual (app.manual_content).
"""

from datetime import datetime
from typing import Annotated

from fastapi import Path
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import Store, StoreAccessGrant
from app.errors import ApiError, ErrorCode
from app.store_access import (
    STORE_APPROVAL_REQUIRED_MESSAGE,
    UUID_PATTERN,
    normalize_uuid,
    require_worker_store_access,
    store_operating,
    valid_grant_clause,
)

NOT_FOUND_MESSAGE = "리소스를 찾을 수 없습니다."

MediaIdPath = Annotated[str, Path(alias="mediaId", pattern=UUID_PATTERN)]
TranscriptionIdPath = Annotated[str, Path(alias="transcriptionId", pattern=UUID_PATTERN)]
ConversationIdPath = Annotated[str, Path(alias="conversationId", pattern=UUID_PATTERN)]
QuestionIdPath = Annotated[str, Path(alias="questionId", pattern=UUID_PATTERN)]


def not_found() -> ApiError:
    return ApiError(404, ErrorCode.RESOURCE_NOT_FOUND, NOT_FOUND_MESSAGE)


def qa_store(db: Session, worker_id: str, store_id: str, now: datetime | None = None) -> Store:
    """The store if `worker_id` may use its AI Q&A at `now`; ApiError otherwise."""
    now = now or utcnow()
    store = db.get(Store, normalize_uuid(store_id))
    if store is not None and not store_operating(db, store) and db.scalar(select(exists().where(
        StoreAccessGrant.store_id == store.id, StoreAccessGrant.worker_id == worker_id,
        valid_grant_clause(now),
    ))):
        raise ApiError(403, ErrorCode.STORE_APPROVAL_REQUIRED, STORE_APPROVAL_REQUIRED_MESSAGE)
    return require_worker_store_access(db, worker_id, store_id, now=now)  # else 404 RESOURCE_NOT_FOUND


def authorize(db: Session, worker_id: str, store_id: str) -> None:
    """Check access and end the read-only transaction (used before streaming an upload and as
    an early check outside `run_idempotent`, which re-checks inside its fresh transaction)."""
    qa_store(db, worker_id, store_id)
    db.rollback()

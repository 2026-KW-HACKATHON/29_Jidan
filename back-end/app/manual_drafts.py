"""Owner manual state, draft review, editing, acknowledgements and publication (#118).

    GET  /api/stores/{storeId}/manual                       state (published / draft / interview IDs)
    GET  /api/stores/{storeId}/manual/draft                 draft, issues, latest correction
    PUT  /api/stores/{storeId}/manual/draft/content         replace the READY draft's content
    GET  /api/stores/{storeId}/manual/draft/preview         the draft as workers would see it
    POST /api/stores/{storeId}/manual/draft/acknowledgements  owner acknowledges gaps
    POST /api/stores/{storeId}/manual/draft/publication     acknowledge the rest and publish

Every change runs in `run_idempotent` and starts with `lock_current_draft`: the store_manuals
row lock (shared with draft corrections, publication and draft replacement by a new interview),
then, in this order, the current DRAFT id (409 MANUAL_VERSION_CONFLICT), its revision (409
REVISION_CONFLICT), READY (409 MANUAL_STATE_CONFLICT; MANUAL_NOT_READY for publication) and no
RUNNING correction (409 MANUAL_CORRECTION_IN_PROGRESS). The transaction runs in READ COMMITTED
on MySQL so every read after the lock sees what the previous lock holder committed.

State transitions (draft = `manual_versions` row with status DRAFT):

| From | Request | To |
| --- | --- | --- |
| READY rev r | content edit with a real change | READY rev r+1, content_revision+1, acknowledgements void |
| READY rev r | identical content | unchanged (200, same revision) |
| READY rev r | acknowledge new issues / change a note | READY rev r+1 (content_revision kept) |
| READY rev r | acknowledge already acknowledged issues, same note | unchanged |
| READY rev r | publication, every OPEN issue listed | PUBLISHED, store pointer swapped, MANUAL_PUBLISHED |
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentOwner, DbSession, MemberPrincipal
from app.csrf import CsrfOwner
from app.db import utcnow
from app.db.models import InterviewSession, ManualDraftCorrection, ManualVersion, Store, StoreManual
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.jobs.state import begin_transition
from app.manual_attachments import AttachError, lock_attachable
from app.manual_content import (
    active_draft,
    content_body,
    iso,
    manual_not_found,
    store_manual,
)
from app.manual_editing import (
    ContentIn,
    ContentInvalid,
    acknowledge,
    current_acknowledgements,
    issue_bodies,
    open_issues,
    prepare_content,
    replace_content,
    validation_error,
)
from app.notification_events import manual_published
from app.store_access import UUID_PATTERN, StoreIdPath, load_owned_store, normalize_uuid

router = APIRouter()

CONFLICT_MESSAGES = {
    ErrorCode.MANUAL_VERSION_CONFLICT: "검토하던 초안이 바뀌었습니다. 최신 초안을 다시 확인해 주세요.",
    ErrorCode.REVISION_CONFLICT: "초안이 변경되었습니다. 최신 내용을 다시 확인해 주세요.",
    ErrorCode.MANUAL_STATE_CONFLICT: "생성이 끝난 초안에서만 할 수 있습니다.",
    ErrorCode.MANUAL_NOT_READY: "초안이 아직 준비되지 않았습니다.",
    ErrorCode.MANUAL_CORRECTION_IN_PROGRESS: "초안 정정을 처리하고 있습니다. 완료 후 다시 시도해 주세요.",
}


def conflict(code: ErrorCode) -> ApiError:
    return ApiError(409, code, CONFLICT_MESSAGES[code])


# --- shared draft checks (also used by corrections) -------------------------------------------


def latest_correction(db: Session, version_id: str) -> ManualDraftCorrection | None:
    """The draft's newest correction. Only the narrow key columns are ordered: a filesort of
    whole rows would carry the 10000-character input and can exceed MySQL's sort buffer
    (1038 "Out of sort memory"), so the row is loaded by ID afterwards."""
    latest_id = db.scalar(
        select(ManualDraftCorrection.id).where(ManualDraftCorrection.version_id == version_id)
        .order_by(ManualDraftCorrection.created_at.desc(), ManualDraftCorrection.id.desc()).limit(1)
    )
    return None if latest_id is None else db.get(ManualDraftCorrection, latest_id)


def ensure_no_running_correction(db: Session, version_id: str) -> None:
    """409 MANUAL_CORRECTION_IN_PROGRESS while the draft has a RUNNING correction. Call it under
    the manual lock (draft replacement by a new interview must call it too)."""
    # running_version_id is version_id while RUNNING: a lock on one unique key, not a range.
    running = db.scalars(select(ManualDraftCorrection.id).where(
        ManualDraftCorrection.running_version_id == version_id,
    ).with_for_update()).first()
    if running is not None:
        raise conflict(ErrorCode.MANUAL_CORRECTION_IN_PROGRESS)


def lock_current_draft(
    db: Session, owner_id: str, store_id: str, expected_version_id: str, expected_revision: int, *,
    not_ready: ErrorCode = ErrorCode.MANUAL_STATE_CONFLICT,
) -> tuple[Store, StoreManual, ManualVersion]:
    """Ownership, then the manual lock and the draft checks in contract order. Call it first in a
    `run_idempotent` handler (it opens the transaction in READ COMMITTED on MySQL)."""
    begin_transition(db)
    store = load_owned_store(db, owner_id, store_id)
    manual = store_manual(db, store.id, lock=True)
    draft = None if manual is None else active_draft(db, manual.id, refresh=True)
    if draft is None or draft.id != normalize_uuid(expected_version_id):
        raise conflict(ErrorCode.MANUAL_VERSION_CONFLICT)
    if draft.revision != expected_revision:
        raise conflict(ErrorCode.REVISION_CONFLICT)
    if draft.generation_status != "READY" or not _interview_done(db, draft.id):
        raise conflict(not_ready)
    ensure_no_running_correction(db, draft.id)
    return store, manual, draft


def _interview_done(db: Session, version_id: str) -> bool:
    status = db.scalar(select(InterviewSession.status).where(InterviewSession.manual_version_id == version_id))
    return status in (None, "COMPLETED")


# --- bodies -----------------------------------------------------------------------------------


def correction_body(row: ManualDraftCorrection) -> dict:
    """API `ManualDraftCorrection`. Only AI_PROCESSING_FAILED is retryable (the table stores
    the public code; every other failure needs a new instruction or a fresh review)."""
    error = None
    if row.status == "ERROR":
        error = {"code": row.error_code, "message": CORRECTION_ERROR_MESSAGES[row.error_code],
                 "retryable": row.error_code == "AI_PROCESSING_FAILED"}
    return {
        "id": row.id, "versionId": row.version_id, "baseRevision": row.base_revision,
        "target": {"kind": row.target_kind, "targetId": row.target_id},
        "status": row.status, "attempt": row.attempt, "resultRevision": row.result_revision,
        "error": error, "createdAt": iso(row.created_at), "completedAt": iso(row.completed_at),
    }


CORRECTION_ERROR_MESSAGES = {
    "AI_PROCESSING_FAILED": "정정을 처리하지 못했습니다. 다시 시도해 주세요.",
    "CORRECTION_CLARIFICATION_REQUIRED": "어떤 내용을 고칠지 알 수 없었어요. 고칠 부분을 구체적으로 다시 말해 주세요.",
    "MANUAL_REFERENCE_CONFLICT": "다른 업무가 연결된 근무조라 바꿀 수 없었어요. 연결된 업무를 함께 말해 주세요.",
    "MANUAL_VERSION_CONFLICT": "정정하던 초안이 바뀌었습니다. 최신 초안을 다시 확인해 주세요.",
    "REVISION_CONFLICT": "정정하는 동안 초안이 변경되었습니다. 최신 내용을 다시 확인해 주세요.",
}


def draft_body(db: Session, store_id: str, draft: ManualVersion) -> dict:
    """API `ManualDraft`: content and issues only once generated (READY)."""
    ready = draft.generation_status == "READY"
    latest = latest_correction(db, draft.id) if ready else None
    return {
        "versionId": draft.id, "storeId": store_id, "versionNumber": draft.revision_no,
        "revision": draft.revision, "status": "DRAFT", "generationStatus": draft.generation_status,
        "interviewSessionId": db.scalar(
            select(InterviewSession.id).where(InterviewSession.manual_version_id == draft.id)),
        "content": content_body(db, draft.id, owner=True) if ready else None,
        "issues": issue_bodies(db, draft) if ready else [],
        "updatedAt": iso(draft.updated_at),
        "latestCorrection": None if latest is None else correction_body(latest),
    }


def _owner_draft(db: Session, owner_id: str, store_id: str) -> tuple[Store, ManualVersion]:
    store = load_owned_store(db, owner_id, store_id)
    manual = store_manual(db, store.id)
    draft = None if manual is None else active_draft(db, manual.id)
    if draft is None:
        raise manual_not_found()
    return store, draft


def _path(store_id: str, tail: str) -> str:
    return f"/api/stores/{normalize_uuid(store_id)}/manual{tail}"


# --- reads ------------------------------------------------------------------------------------


@router.get("/api/stores/{storeId}/manual")
def get_manual_state(store_id: StoreIdPath, owner: CurrentOwner, db: DbSession) -> dict:
    store = load_owned_store(db, owner.user_id, store_id)
    manual = store_manual(db, store.id)
    draft = None if manual is None else active_draft(db, manual.id)
    session_id = None if draft is None else db.scalar(
        select(InterviewSession.id).where(InterviewSession.manual_version_id == draft.id))
    return {
        "storeId": store.id,
        "currentPublishedVersionId": None if manual is None else manual.current_published_version_id,
        "draftVersionId": None if draft is None else draft.id,
        "interviewSessionId": session_id,
    }


@router.get("/api/stores/{storeId}/manual/draft")
def get_manual_draft(store_id: StoreIdPath, owner: CurrentOwner, db: DbSession) -> dict:
    store, draft = _owner_draft(db, owner.user_id, store_id)
    return draft_body(db, store.id, draft)


@router.get("/api/stores/{storeId}/manual/draft/preview")
def preview_manual_draft(store_id: StoreIdPath, owner: CurrentOwner, db: DbSession) -> dict:
    """The last saved READY content (also while a correction runs); no issues, notes or history."""
    _store, draft = _owner_draft(db, owner.user_id, store_id)
    if draft.generation_status != "READY":
        raise conflict(ErrorCode.MANUAL_NOT_READY)
    return {"preview": True, "versionId": draft.id, "revision": draft.revision,
            "content": content_body(db, draft.id)}


# --- edit -------------------------------------------------------------------------------------


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_version_id: str = Field(alias="expectedVersionId", pattern=UUID_PATTERN)
    expected_revision: int = Field(alias="expectedRevision", ge=1)

    def key_body(self) -> dict:
        body = self.model_dump(by_alias=True, mode="json")
        body["expectedVersionId"] = normalize_uuid(self.expected_version_id)
        return body


class ContentUpdate(_Command):
    content: ContentIn


@router.put("/api/stores/{storeId}/manual/draft/content")
def replace_manual_draft_content(
    store_id: StoreIdPath, body: ContentUpdate, owner: CsrfOwner, db: DbSession, key: IdempotencyKey,
) -> Response:
    def work() -> IdempotentResult:
        store, _manual, draft = lock_current_draft(
            db, owner.user_id, store_id, body.expected_version_id, body.expected_revision)
        try:
            prepared = prepare_content(db, draft, store.id, body.content)
        except ContentInvalid as invalid:
            raise validation_error(invalid.errors) from None
        replace_content(db, draft, prepared)
        return IdempotentResult(200, draft_body(db, store.id, draft))

    return run_idempotent(
        db=db, principal=owner, key=key, method="PUT", path=_path(store_id, "/draft/content"),
        body=body.key_body(), handler=work, revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    )


# --- acknowledgements and publication ---------------------------------------------------------


class AcknowledgementIn(_Command):
    issue_ids: list[Annotated[str, Field(pattern=UUID_PATTERN)]] = Field(
        alias="issueIds", min_length=1, max_length=200)
    confirmed: Literal[True]
    note: str | None = Field(default=None, min_length=1, max_length=2000, pattern=r"\S")


class PublishIn(_Command):
    confirmed: Literal[True]
    acknowledged_issue_ids: list[Annotated[str, Field(pattern=UUID_PATTERN)]] = Field(
        alias="acknowledgedIssueIds", max_length=200)


def _unique_ids(values: list[str], field: str) -> list[str]:
    ids = [normalize_uuid(value) for value in values]
    if len(ids) != len(set(ids)):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": field, "code": "INVALID_FORMAT", "message": "같은 항목을 두 번 보낼 수 없습니다."}])
    return ids


def _select_issues(db: Session, draft: ManualVersion, ids: list[str]):
    """The open issues of the current draft named by `ids` (404 for any other ID)."""
    issues = {issue.id: issue for issue in open_issues(db, draft)}
    if any(issue_id not in issues for issue_id in ids):
        raise manual_not_found()
    return issues


@router.post("/api/stores/{storeId}/manual/draft/acknowledgements")
def acknowledge_manual_issues(
    store_id: StoreIdPath, body: AcknowledgementIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey,
) -> Response:
    ids = _unique_ids(body.issue_ids, "issueIds")

    def work() -> IdempotentResult:
        store, _manual, draft = lock_current_draft(
            db, owner.user_id, store_id, body.expected_version_id, body.expected_revision)
        issues = _select_issues(db, draft, ids)
        acknowledge(db, draft, [issues[issue_id] for issue_id in ids], owner.user_id, body.note)
        return IdempotentResult(200, draft_body(db, store.id, draft))

    key_body = body.key_body() | {"issueIds": sorted(ids)}
    return run_idempotent(
        db=db, principal=owner, key=key, method="POST", path=_path(store_id, "/draft/acknowledgements"),
        body=key_body, handler=work, revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    )


def publish_draft(db: Session, owner: MemberPrincipal, store: Store, manual: StoreManual,
                  draft: ManualVersion, acknowledged_ids: list[str], *, now: datetime | None = None) -> None:
    """Acknowledge the remaining OPEN issues and publish, in the caller's transaction.

    Every OPEN issue must be listed (409 MANUAL_REVIEW_REQUIRED); listed IDs must be open issues
    of this draft (404). Photos are re-checked (422). Then: PUBLISHED, pointer swap (which also
    frees the single-draft slot) and MANUAL_PUBLISHED to every worker who can read it now.
    """
    issues = _select_issues(db, draft, acknowledged_ids)
    acked = current_acknowledgements(db, draft)
    open_ids = {issue_id for issue_id in issues if issue_id not in acked}
    if not open_ids <= set(acknowledged_ids):
        raise ApiError(409, ErrorCode.MANUAL_REVIEW_REQUIRED, "확인하지 않은 부족 항목이 있습니다. 모두 확인해 주세요.")
    content = content_body(db, draft.id, owner=True)
    photo_ids = [p["mediaId"] for p in content["structurePhotos"]] + [
        p["mediaId"] for section in content["sections"] for p in section["photos"]]
    try:
        lock_attachable(db, store.id, photo_ids)
    except AttachError:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[{
            "field": "content.photos", "code": "INVALID_FORMAT",
            "message": "삭제되었거나 다른 매장의 사진이 연결되어 있습니다. 초안을 수정해 주세요."}]) from None
    now = now or utcnow()
    acknowledge(db, draft, [issues[issue_id] for issue_id in sorted(open_ids)], owner.user_id, None, now=now)
    draft.status = "PUBLISHED"
    draft.published_at = now
    draft.published_by_owner_id = owner.user_id
    draft.updated_at = now
    db.flush()
    manual.current_published_version_id = draft.id
    manual.updated_at = now
    db.flush()
    manual_published(db, store, draft, at=now)


def published_body(db: Session, store_id: str, version: ManualVersion) -> dict:
    return {
        "versionId": version.id, "storeId": store_id, "versionNumber": version.revision_no,
        "status": "PUBLISHED", "ownerConfirmed": True, "content": content_body(db, version.id),  # published: posters only
        "publishedAt": iso(version.published_at),
    }


@router.post("/api/stores/{storeId}/manual/draft/publication")
def publish_manual_draft(
    store_id: StoreIdPath, body: PublishIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey,
) -> Response:
    ids = _unique_ids(body.acknowledged_issue_ids, "acknowledgedIssueIds")

    def work() -> IdempotentResult:
        store, manual, draft = lock_current_draft(
            db, owner.user_id, store_id, body.expected_version_id, body.expected_revision,
            not_ready=ErrorCode.MANUAL_NOT_READY)
        publish_draft(db, owner, store, manual, draft, ids)
        return IdempotentResult(200, published_body(db, store.id, draft))

    key_body = body.key_body() | {"acknowledgedIssueIds": sorted(ids)}
    return run_idempotent(
        db=db, principal=owner, key=key, method="POST", path=_path(store_id, "/draft/publication"),
        body=key_body, handler=work, revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    )

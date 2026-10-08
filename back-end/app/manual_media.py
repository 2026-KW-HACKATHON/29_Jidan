"""Owner manual media and transcription API (#119; openapi tag 매뉴얼 미디어).

    POST   /api/stores/{storeId}/manual/media                       upload a photo or recording
    DELETE /api/stores/{storeId}/manual/media/{mediaId}             delete an unreferenced file
    GET    /api/stores/{storeId}/manual/media/{mediaId}/content     protected photo bytes
    POST   /api/stores/{storeId}/manual/transcriptions              start / retry transcription
    GET    /api/stores/{storeId}/manual/transcriptions/{id}         transcription status and text

Owners need an ACTIVE OWNER session, ownership of the store and APPROVED status on every
request (app.store_access). The photo endpoint also serves workers with a currently valid
store access, but only photos of the store's current published version.
"""

import hashlib
import logging
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.auth import ROLE_OWNER, CurrentMember, CurrentOwner, DbSession
from app.csrf import CsrfOwner
from app.db import new_uuid, utcnow
from app.db.models import (
    ManualMedia,
    ManualPhotoAttachment,
    MediaTranscription,
    Store,
    StoreAccessGrant,
)
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.media.errors import MediaRejected
from app.media.inspection import inspect_media
from app.media.multipart import read_media_form
from app.media.references import manual_media_in_use, transcription_running
from app.media.retention import UNATTACHED_TTL, purge_media_content
from app.media.storage import get_media_storage, object_key
from app.media.transcription import (
    RecordingUnavailable,
    begin_transcription,
    iso,
    transcription_body,
)
from app.store_access import (
    APPROVED,
    UUID_PATTERN,
    StoreIdPath,
    has_worker_store_access,
    load_owned_store,
    normalize_uuid,
    valid_grant_clause,
)
from app.worker_stores import published_version_id

router = APIRouter()
logger = logging.getLogger("jidan.media")

PURPOSE_KIND = {"MANUAL_PHOTO": "IMAGE", "INTERVIEW_AUDIO": "AUDIO"}
KIND_PURPOSE = {kind: purpose for purpose, kind in PURPOSE_KIND.items()}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MANUAL_NOT_FOUND = "매뉴얼 리소스를 찾을 수 없습니다."

MediaIdPath = Annotated[str, Path(alias="mediaId", pattern=UUID_PATTERN)]
TranscriptionIdPath = Annotated[str, Path(alias="transcriptionId", pattern=UUID_PATTERN)]


def _not_found() -> ApiError:
    return ApiError(404, ErrorCode.MANUAL_RESOURCE_NOT_FOUND, MANUAL_NOT_FOUND)


def media_body(media: ManualMedia) -> dict:
    return {
        "id": media.id, "storeId": media.store_id, "purpose": KIND_PURPOSE[media.kind],
        "mimeType": media.mime_type, "sizeBytes": media.byte_size, "createdAt": iso(media.created_at),
    }


# --- upload -----------------------------------------------------------------------------------


def _store_upload(db: Session, owner, store_id: str, key: str, path: str, purpose: str,
                  data: bytes) -> Response:
    storage = get_media_storage()
    written: list[str] = []

    def work() -> IdempotentResult:
        try:
            inspected = inspect_media(data, PURPOSE_KIND[purpose])
        except MediaRejected as rejected:
            raise ApiError(rejected.status_code, rejected.code) from None
        store = load_owned_store(db, owner.user_id, store_id)
        now = utcnow()
        media_id = new_uuid()
        location = object_key("manual", store.id, media_id)
        storage.write(location, inspected.data)
        written.append(location)
        media = ManualMedia(
            id=media_id, store_id=store.id, uploaded_by_owner_id=owner.user_id, kind=inspected.kind,
            object_key=location, mime_type=inspected.mime_type, byte_size=len(inspected.data),
            duration_ms=inspected.duration_ms, created_at=now, expires_at=now + UNATTACHED_TTL,
        )
        db.add(media)
        db.flush()
        return IdempotentResult(201, media_body(media))

    body = {"purpose": purpose, "fileSha256": hashlib.sha256(data).hexdigest()}
    try:
        return run_idempotent(
            db=db, principal=owner, key=key, method="POST", path=path, body=body, handler=work,
            revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
        )
    except BaseException:
        for location in written:  # the row never committed: do not keep its bytes
            try:
                storage.delete(location)
            except OSError:
                logger.error("upload cleanup failed; the orphan sweep removes the file")
        raise


def _authorize_upload(db: Session, owner_id: str, store_id: str) -> None:
    load_owned_store(db, owner_id, store_id)
    db.rollback()


@router.post("/api/stores/{storeId}/manual/media", status_code=201)
async def upload_manual_media(
    request: Request, store_id: StoreIdPath, owner: CsrfOwner, db: DbSession, key: IdempotencyKey,
) -> Response:
    # Authorization before reading the body: a stranger cannot make us buffer 20 MiB. The
    # read-only transaction ends here so no snapshot stays open while the upload streams in.
    await run_in_threadpool(_authorize_upload, db, owner.user_id, store_id)
    form = await read_media_form(request, max_file_bytes=MAX_UPLOAD_BYTES, purposes=tuple(PURPOSE_KIND))
    # The canonical path, not request.url.path: a retry spelling the store UUID in another
    # letter case is the same request and must replay, not collide as another endpoint.
    path = f"/api/stores/{normalize_uuid(store_id)}/manual/media"
    return await run_in_threadpool(
        _store_upload, db, owner, store_id, key, path, form.purpose, form.data,
    )


# --- delete -----------------------------------------------------------------------------------


@router.delete("/api/stores/{storeId}/manual/media/{mediaId}", status_code=204)
def delete_unused_manual_media(
    store_id: StoreIdPath, media_id: MediaIdPath, owner: CsrfOwner, db: DbSession,
) -> Response:
    store = load_owned_store(db, owner.user_id, store_id)
    # The row lock serializes this with photo linking (app.media.references).
    media = db.scalars(select(ManualMedia).where(
        ManualMedia.id == normalize_uuid(media_id), ManualMedia.store_id == store.id,
    ).with_for_update()).first()
    if media is None:
        raise _not_found()
    if media.deleted_at is None:
        if manual_media_in_use(db, media.id) or (
            media.kind == "AUDIO" and transcription_running(db, manual_media_id=media.id)
        ):
            raise ApiError(409, ErrorCode.MEDIA_IN_USE, "사용 중인 파일은 삭제할 수 없습니다. 먼저 연결을 해제해 주세요.")
        media.deleted_at = utcnow()
        db.commit()
        try:  # best effort now; the retention task retries anything left behind
            purge_media_content(media_ids=[media.id])
        except Exception:  # noqa: BLE001 - deletion is already recorded
            logger.error("immediate media purge failed; retention retries")
    return Response(status_code=204)


# --- protected photo bytes --------------------------------------------------------------------


def _worker_store(db: Session, worker_id: str, store_id: str) -> Store:
    """The store if the worker may read its materials now (app.store_access, shared with every
    manual/Q&A endpoint); 404 STORE_NOT_FOUND otherwise. The spec answers a store that lost its
    approval with 403 STORE_APPROVAL_REQUIRED, so that one case is told apart first."""
    now = utcnow()
    store = db.get(Store, normalize_uuid(store_id))
    if store is not None and store.approval_status != APPROVED and db.scalar(select(exists().where(
        StoreAccessGrant.store_id == store.id, StoreAccessGrant.worker_id == worker_id,
        valid_grant_clause(now),
    ))):
        raise ApiError(403, ErrorCode.STORE_APPROVAL_REQUIRED, "매장 운영 승인 후 이용할 수 있습니다.")
    if not has_worker_store_access(db, worker_id, store_id, now):
        raise ApiError(404, ErrorCode.STORE_NOT_FOUND, "매장을 찾을 수 없습니다.")
    return store


def _photo_bytes(media: ManualMedia | None) -> Response:
    if media is None or media.kind != "IMAGE" or media.deleted_at or media.content_deleted_at:
        raise _not_found()
    try:
        data = get_media_storage().read(media.object_key)
    except FileNotFoundError:
        raise _not_found() from None
    return Response(data, media_type=media.mime_type, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    })


@router.get("/api/stores/{storeId}/manual/media/{mediaId}/content")
def read_manual_photo(
    store_id: StoreIdPath, media_id: MediaIdPath, member: CurrentMember, db: DbSession,
    expected_version_id: Annotated[
        str | None, Query(alias="expectedVersionId", pattern=UUID_PATTERN)] = None,
) -> Response:
    media_id = normalize_uuid(media_id)
    if member.role == ROLE_OWNER:
        if expected_version_id is not None:
            raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[{
                "field": "expectedVersionId", "code": "INVALID_FORMAT",
                "message": "점주의 사진 조회에는 사용할 수 없습니다.",
            }])
        store = load_owned_store(db, member.user_id, store_id)
        media = db.scalars(select(ManualMedia).where(
            ManualMedia.id == media_id, ManualMedia.store_id == store.id)).first()
        if media is None or not manual_media_in_use(db, media.id):
            raise _not_found()  # unattached uploads are shown from the device, not served
        return _photo_bytes(media)

    store = _worker_store(db, member.user_id, store_id)
    current = published_version_id(db, store.id)
    if current is None:
        raise _not_found()
    if expected_version_id is not None and normalize_uuid(expected_version_id) != current:
        raise ApiError(409, ErrorCode.MANUAL_VERSION_CHANGED, "최신 매뉴얼 목록을 다시 확인해 주세요.")
    published = db.scalar(select(exists().where(
        ManualPhotoAttachment.version_id == current, ManualPhotoAttachment.media_id == media_id)))
    if not published:
        raise _not_found()
    return _photo_bytes(db.get(ManualMedia, media_id))


# --- transcriptions ---------------------------------------------------------------------------


class TranscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str = Field(alias="mediaId", pattern=UUID_PATTERN)


@router.post("/api/stores/{storeId}/manual/transcriptions", status_code=202)
def create_manual_transcription(
    request: Request, store_id: StoreIdPath, body: TranscriptionCreate, owner: CsrfOwner,
    db: DbSession, key: IdempotencyKey,
) -> Response:
    load_owned_store(db, owner.user_id, store_id)

    def work() -> IdempotentResult:
        store = load_owned_store(db, owner.user_id, store_id)
        media = db.scalars(select(ManualMedia).where(
            ManualMedia.id == normalize_uuid(body.media_id), ManualMedia.store_id == store.id,
        ).with_for_update()).first()
        if media is None or media.deleted_at is not None:
            raise _not_found()
        if media.kind != "AUDIO":
            raise ApiError(422, ErrorCode.MEDIA_PURPOSE_INVALID, "음성 파일만 전사할 수 있습니다.")
        try:
            row, status = begin_transcription(db, media)
        except RecordingUnavailable:
            raise _not_found() from None  # expired or purged: upload the recording again
        return IdempotentResult(status, transcription_body(row))

    return run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path=f"/api/stores/{normalize_uuid(store_id)}/manual/transcriptions",
        body={"mediaId": normalize_uuid(body.media_id)}, handler=work,
        revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    )


@router.get("/api/stores/{storeId}/manual/transcriptions/{transcriptionId}")
def read_manual_transcription(
    store_id: StoreIdPath, transcription_id: TranscriptionIdPath, owner: CurrentOwner, db: DbSession,
) -> dict:
    store = load_owned_store(db, owner.user_id, store_id)
    row = db.scalars(select(MediaTranscription).where(
        MediaTranscription.id == normalize_uuid(transcription_id),
        MediaTranscription.store_id == store.id, MediaTranscription.manual_media_id.is_not(None),
    )).first()
    if row is None:
        raise _not_found()
    return transcription_body(row)

"""Worker question media and transcription API (#121; openapi tag AI 질문 미디어).

    POST   /api/stores/{storeId}/manual/qa/media                          upload a photo or recording
    DELETE /api/stores/{storeId}/manual/qa/media/{mediaId}                delete an unlinked upload
    GET    /api/stores/{storeId}/manual/qa/media/{mediaId}/content        protected bytes (own uploads)
    POST   /api/stores/{storeId}/manual/qa/transcriptions                 start transcription
    GET    /api/stores/{storeId}/manual/qa/transcriptions/{id}            status and text
    POST   /api/stores/{storeId}/manual/qa/transcriptions/{id}/retries    retry a failed one

Files belong to the uploading worker and the store (`qa_media.worker_id`/`store_id`); another
worker's, another store's and unknown files are the same 404 RESOURCE_NOT_FOUND. Owner
interview media (app.manual_media) is a separate table and never reachable from here.
Photos are kept 7 days and recordings 24 hours (plus 24 hours after their transcription
ends); an expired file is 410 QA_MEDIA_EXPIRED. The recording, its transcript and the
TRANSCRIPTION task are shared with the owner flow (app.media.transcription).
"""

import hashlib
import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentWorker, DbSession
from app.csrf import CsrfWorker
from app.db import new_uuid, utcnow
from app.db.models import MediaTranscription, QaMedia
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.media.errors import MediaRejected, MediaUnsupported
from app.media.inspection import inspect_media
from app.media.multipart import read_media_form
from app.media.references import qa_media_in_use, transcription_running
from app.media.retention import QA_AUDIO_TTL, QA_IMAGE_TTL, purge_media_content
from app.media.storage import get_media_storage, object_key
from app.media.transcription import (
    RecordingUnavailable,
    begin_transcription,
    iso,
    recording_available,
    transcription_body,
)
from app.qa.access import MediaIdPath, TranscriptionIdPath, authorize, not_found, qa_store
from app.store_access import UUID_PATTERN, StoreIdPath, normalize_uuid

router = APIRouter()
logger = logging.getLogger("jidan.media")

PURPOSE_KIND = {"QUESTION_IMAGE": "IMAGE", "QUESTION_AUDIO": "AUDIO"}
KIND_PURPOSE = {kind: purpose for purpose, kind in PURPOSE_KIND.items()}
KIND_TTL = {"IMAGE": QA_IMAGE_TTL, "AUDIO": QA_AUDIO_TTL}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
EXPIRED_MESSAGE = "질문 미디어 보관 기한이 끝났습니다."
AUDIO_EXPIRED_MESSAGE = "음성 보관 기한이 끝났습니다. 다시 녹음해 주세요."


def media_body(media: QaMedia) -> dict:
    return {
        "id": media.id, "storeId": media.store_id, "purpose": KIND_PURPOSE[media.kind],
        "mimeType": media.mime_type, "sizeBytes": media.byte_size,
        "createdAt": iso(media.created_at), "expiresAt": iso(media.expires_at),
    }


def media_expired(media: QaMedia, now) -> bool:
    return media.content_deleted_at is not None or media.expires_at <= now


def own_media(db: Session, worker_id: str, store_id: str, media_id: str, *, lock: bool = False
              ) -> QaMedia:
    """The worker's live (not deleted) upload in this store, else 404."""
    statement = select(QaMedia).where(
        QaMedia.id == normalize_uuid(media_id), QaMedia.store_id == store_id,
        QaMedia.worker_id == worker_id,
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    media = db.scalars(statement).first()
    if media is None or media.deleted_at is not None:
        raise not_found()
    return media


# --- upload -----------------------------------------------------------------------------------


def _rejection(rejected: MediaRejected) -> ApiError:
    # The Q&A contract names the 415 code MEDIA_TYPE_UNSUPPORTED (owner uploads use
    # UNSUPPORTED_MEDIA_TYPE); the others are shared.
    if isinstance(rejected, MediaUnsupported):
        return ApiError(415, ErrorCode.MEDIA_TYPE_UNSUPPORTED, "지원하지 않는 파일 형식입니다.")
    return ApiError(rejected.status_code, rejected.code)


def _store_upload(db: Session, worker, store_id: str, key: str, path: str, purpose: str,
                  data: bytes) -> Response:
    storage = get_media_storage()
    written: list[str] = []

    def work() -> IdempotentResult:
        try:
            inspected = inspect_media(data, PURPOSE_KIND[purpose])
        except MediaRejected as rejected:
            raise _rejection(rejected) from None
        store = qa_store(db, worker.user_id, store_id)
        now = utcnow()
        media_id = new_uuid()
        location = object_key("qa", store.id, media_id)
        storage.write(location, inspected.data)
        written.append(location)
        media = QaMedia(
            id=media_id, store_id=store.id, worker_id=worker.user_id, kind=inspected.kind,
            object_key=location, mime_type=inspected.mime_type, byte_size=len(inspected.data),
            duration_ms=inspected.duration_ms, created_at=now, expires_at=now + KIND_TTL[inspected.kind],
        )
        db.add(media)
        db.flush()
        return IdempotentResult(201, media_body(media))

    body = {"purpose": purpose, "fileSha256": hashlib.sha256(data).hexdigest()}
    try:
        return run_idempotent(
            db=db, principal=worker, key=key, method="POST", path=path, body=body, handler=work,
            revalidate=lambda: qa_store(db, worker.user_id, store_id),
        )
    except BaseException:
        for location in written:  # the row never committed: do not keep its bytes
            try:
                storage.delete(location)
            except OSError:
                logger.error("upload cleanup failed; the orphan sweep removes the file")
        raise


@router.post("/api/stores/{storeId}/manual/qa/media", status_code=201)
async def upload_qa_media(
    request: Request, store_id: StoreIdPath, worker: CsrfWorker, db: DbSession, key: IdempotencyKey,
) -> Response:
    # Authorize before reading the body, and end that read-only transaction so no snapshot
    # stays open while the upload streams in.
    await run_in_threadpool(authorize, db, worker.user_id, store_id)
    form = await read_media_form(request, max_file_bytes=MAX_UPLOAD_BYTES, purposes=tuple(PURPOSE_KIND))
    path = f"/api/stores/{normalize_uuid(store_id)}/manual/qa/media"
    return await run_in_threadpool(_store_upload, db, worker, store_id, key, path, form.purpose, form.data)


# --- delete -----------------------------------------------------------------------------------


@router.delete("/api/stores/{storeId}/manual/qa/media/{mediaId}", status_code=204)
def delete_qa_media(
    store_id: StoreIdPath, media_id: MediaIdPath, worker: CsrfWorker, db: DbSession, key: IdempotencyKey,
) -> Response:
    media_id = normalize_uuid(media_id)

    def work() -> IdempotentResult:
        store = qa_store(db, worker.user_id, store_id)
        # The row lock serializes this with linking the photo to a question (app.qa.questions).
        media = own_media(db, worker.user_id, store.id, media_id, lock=True)
        if qa_media_in_use(db, media.id) or (
            media.kind == "AUDIO" and transcription_running(db, qa_media_id=media.id)
        ):
            raise ApiError(409, ErrorCode.MEDIA_IN_USE, "질문에 사용했거나 처리 중인 파일은 삭제할 수 없습니다.")
        media.deleted_at = utcnow()
        return IdempotentResult(204)

    response = run_idempotent(
        db=db, principal=worker, key=key, method="DELETE",
        path=f"/api/stores/{normalize_uuid(store_id)}/manual/qa/media/{media_id}", body={}, handler=work,
        revalidate=lambda: qa_store(db, worker.user_id, store_id),
    )
    try:  # best effort now; the retention task retries anything left behind
        purge_media_content(media_ids=[media_id])
    except Exception:  # noqa: BLE001 - deletion is already recorded
        logger.error("immediate media purge failed; retention retries")
    return response


# --- protected bytes --------------------------------------------------------------------------


@router.get("/api/stores/{storeId}/manual/qa/media/{mediaId}/content")
def read_qa_media(store_id: StoreIdPath, media_id: MediaIdPath, worker: CurrentWorker, db: DbSession,
                  ) -> Response:
    now = utcnow()
    store = qa_store(db, worker.user_id, store_id, now)
    media = own_media(db, worker.user_id, store.id, media_id)
    if media_expired(media, now):
        raise ApiError(410, ErrorCode.QA_MEDIA_EXPIRED, EXPIRED_MESSAGE)
    try:
        data = get_media_storage().read(media.object_key)
    except FileNotFoundError:
        raise ApiError(410, ErrorCode.QA_MEDIA_EXPIRED, EXPIRED_MESSAGE) from None
    return Response(data, media_type=media.mime_type, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline",
    })


# --- transcriptions ---------------------------------------------------------------------------


class TranscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str = Field(alias="mediaId", pattern=UUID_PATTERN)


class ProcessingRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")


def own_transcription(db: Session, worker_id: str, store_id: str, transcription_id: str
                      ) -> tuple[MediaTranscription, QaMedia]:
    """The worker's transcription in this store (never an owner recording's), else 404. The
    transcript stays readable after its recording was deleted or purged."""
    found = db.execute(
        select(MediaTranscription, QaMedia)
        .join(QaMedia, QaMedia.id == MediaTranscription.qa_media_id)
        .where(MediaTranscription.id == normalize_uuid(transcription_id),
               QaMedia.store_id == store_id, QaMedia.worker_id == worker_id)
    ).first()
    if found is None:
        raise not_found()
    return found[0], found[1]


@router.post("/api/stores/{storeId}/manual/qa/transcriptions", status_code=202)
def create_qa_transcription(
    store_id: StoreIdPath, body: TranscriptionCreate, worker: CsrfWorker, db: DbSession, key: IdempotencyKey,
) -> Response:
    authorize(db, worker.user_id, store_id)

    def work() -> IdempotentResult:
        store = qa_store(db, worker.user_id, store_id)
        media = own_media(db, worker.user_id, store.id, body.media_id, lock=True)
        if media.kind != "AUDIO":
            raise ApiError(422, ErrorCode.MEDIA_PURPOSE_INVALID, "음성 파일만 전사할 수 있습니다.")
        existing = db.scalars(select(MediaTranscription).where(
            MediaTranscription.qa_media_id == media.id).with_for_update()).first()
        if existing is not None:  # running, ready or failed: reuse it (failures use /retries)
            return IdempotentResult(202, transcription_body(existing))
        if not recording_available(media, utcnow()):
            raise ApiError(410, ErrorCode.QA_MEDIA_EXPIRED, AUDIO_EXPIRED_MESSAGE)
        row, _status = begin_transcription(db, media)
        return IdempotentResult(202, transcription_body(row))

    return run_idempotent(
        db=db, principal=worker, key=key, method="POST",
        path=f"/api/stores/{normalize_uuid(store_id)}/manual/qa/transcriptions",
        body={"mediaId": normalize_uuid(body.media_id)}, handler=work,
        revalidate=lambda: qa_store(db, worker.user_id, store_id),
    )


@router.get("/api/stores/{storeId}/manual/qa/transcriptions/{transcriptionId}")
def read_qa_transcription(
    store_id: StoreIdPath, transcription_id: TranscriptionIdPath, worker: CurrentWorker, db: DbSession,
) -> dict:
    store = qa_store(db, worker.user_id, store_id)
    row, _media = own_transcription(db, worker.user_id, store.id, transcription_id)
    return transcription_body(row)


@router.post("/api/stores/{storeId}/manual/qa/transcriptions/{transcriptionId}/retries", status_code=202)
def retry_qa_transcription(
    store_id: StoreIdPath, transcription_id: TranscriptionIdPath, body: ProcessingRetry,
    worker: CsrfWorker, db: DbSession, key: IdempotencyKey,
) -> Response:
    authorize(db, worker.user_id, store_id)
    transcription_id = normalize_uuid(transcription_id)

    def work() -> IdempotentResult:
        store = qa_store(db, worker.user_id, store_id)
        _row, media = own_transcription(db, worker.user_id, store.id, transcription_id)
        # Lock order as everywhere: recording first, then its transcription.
        media = db.scalars(select(QaMedia).where(QaMedia.id == media.id).with_for_update()
                           .execution_options(populate_existing=True)).one()
        row = db.scalars(select(MediaTranscription).where(MediaTranscription.id == transcription_id)
                         .with_for_update().execution_options(populate_existing=True)).one()
        if row.status != "ERROR":
            raise ApiError(409, ErrorCode.TRANSCRIPTION_NOT_RETRYABLE, "실패한 전사만 다시 시도할 수 있습니다.")
        try:
            row, _status = begin_transcription(db, media)
        except RecordingUnavailable:
            raise ApiError(410, ErrorCode.QA_MEDIA_EXPIRED, AUDIO_EXPIRED_MESSAGE) from None
        return IdempotentResult(202, transcription_body(row))

    return run_idempotent(
        db=db, principal=worker, key=key, method="POST",
        path=f"/api/stores/{normalize_uuid(store_id)}/manual/qa/transcriptions/{transcription_id}/retries",
        body={}, handler=work, revalidate=lambda: qa_store(db, worker.user_id, store_id),
    )

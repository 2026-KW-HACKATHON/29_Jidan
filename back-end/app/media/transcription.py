"""Speech-to-text tasks shared by owner interview audio and worker question audio.

`begin_transcription` starts (or returns) the single transcription of a recording inside the
caller's request transaction; the TRANSCRIPTION task then calls the STT provider outside any
transaction and applies the result only if the row still waits for that task and attempt.
Blank or silent results end as ERROR TRANSCRIPTION_FAILED; no empty text is ever stored.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import get_ai_provider
from app.ai.contracts import TranscriptionRequest
from app.ai.errors import AiError, AiErrorCode
from app.db import iso_utc, new_uuid, session_scope, utcnow
from app.db.models import ManualMedia, MediaTranscription, QaMedia
from app.media.retention import AFTER_TRANSCRIPTION_TTL
from app.media.storage import get_media_storage
from app.tasks import TaskContext, TaskHandler, enqueue, register_handler

KIND = "TRANSCRIPTION"
FAILED_MESSAGE = "음성을 인식하지 못했어요. 다시 녹음하거나 다시 시도해 주세요."


class RecordingUnavailable(Exception):
    """The recording is deleted, expired or its bytes were purged: a new upload is needed."""


def iso(value: datetime | None) -> str | None:
    return iso_utc(value)


def transcription_body(row: MediaTranscription) -> dict:
    """The API `ManualTranscription` representation (also used by Q&A transcriptions)."""
    error = None
    if row.status == "ERROR":
        error = {"code": row.error_code, "message": FAILED_MESSAGE, "retryable": True}
    return {
        "id": row.id,
        "mediaId": row.manual_media_id or row.qa_media_id,
        "status": row.status,
        "text": row.text if row.status == "READY" else None,
        "error": error,
        "createdAt": iso(row.created_at),
        "completedAt": iso(row.completed_at),
    }


def recording_available(media: ManualMedia | QaMedia, now: datetime) -> bool:
    return (
        media.kind == "AUDIO" and media.deleted_at is None and media.content_deleted_at is None
        and media.expires_at > now
    )


def begin_transcription(db: Session, media: ManualMedia | QaMedia, *, now: datetime | None = None
                        ) -> tuple[MediaTranscription, int]:
    """Return (transcription, 200 | 202) for a recording the caller has locked FOR UPDATE.

    READY -> 200 as is. RUNNING -> 202 as is. None or ERROR -> (re)start: needs the recording's
    bytes (RecordingUnavailable otherwise), keeps the same transcription ID, increments
    `attempt` and enqueues a new task. Nothing is committed here.
    """
    now = now or utcnow()
    column = (MediaTranscription.manual_media_id if isinstance(media, ManualMedia)
              else MediaTranscription.qa_media_id)
    row = db.scalars(select(MediaTranscription).where(column == media.id).with_for_update()).first()
    if row is not None and row.status == "READY":
        return row, 200
    if row is not None and row.status == "RUNNING":
        return row, 202
    if not recording_available(media, now):
        raise RecordingUnavailable(media.id)
    if row is None:
        transcription_id = new_uuid()
        try:
            with db.begin_nested():
                row = MediaTranscription(
                    id=transcription_id, store_id=media.store_id, status="RUNNING", attempt=1,
                    created_at=now, updated_at=now,
                    manual_media_id=media.id if isinstance(media, ManualMedia) else None,
                    qa_media_id=media.id if isinstance(media, QaMedia) else None,
                    task_id=enqueue(db, KIND, transcription_id, {"transcriptionId": transcription_id},
                                    attempt=1),
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            # Lost a race the media row lock could not prevent (no row locks on SQLite): the
            # winner's task is the one transcription of this recording.
            row = db.scalars(select(MediaTranscription).where(column == media.id)).one()
            return row, 200 if row.status == "READY" else 202
        return row, 202
    else:
        row.attempt += 1
        row.status, row.error_code, row.completed_at, row.text = "RUNNING", None, None, None
        row.updated_at = now
        row.task_id = enqueue(db, KIND, row.id, {"transcriptionId": row.id}, attempt=row.attempt)
    db.flush()
    return row, 202


def _recording(db: Session, row: MediaTranscription) -> ManualMedia | QaMedia | None:
    if row.manual_media_id:
        return db.get(ManualMedia, row.manual_media_id)
    return db.get(QaMedia, row.qa_media_id)


def _execute(ctx: TaskContext) -> str:
    with session_scope() as db:
        row = db.get(MediaTranscription, ctx.subject_id)
        media = _recording(db, row) if row is not None else None
        if media is None or media.deleted_at is not None or media.content_deleted_at is not None:
            raise AiError(AiErrorCode.INPUT_REJECTED, detail="recording_gone")
        key, mime_type = media.object_key, media.mime_type
    try:
        audio = get_media_storage().read(key)
    except FileNotFoundError:
        raise AiError(AiErrorCode.INPUT_REJECTED, detail="recording_gone") from None
    return get_ai_provider().transcribe(TranscriptionRequest(audio=audio, mime_type=mime_type)).text


def _locked_current(db: Session, ctx: TaskContext) -> tuple[MediaTranscription, ManualMedia | QaMedia]:
    """Lock the recording, then its transcription, and check the row still waits for this task.

    Requests lock in this order too (delete and (re)start lock the recording, then look at or
    lock the transcription). Taking the transcription first and then updating the recording's
    expiry would close a cycle that MySQL breaks with a deadlock (1213). The recording ID never
    changes, so a plain read finds it; the guard below reads the locked, refreshed rows."""
    ids = db.execute(select(MediaTranscription.manual_media_id, MediaTranscription.qa_media_id)
                     .where(MediaTranscription.id == ctx.subject_id)).first()
    ctx.ensure(ids is not None)
    model, media_id = (ManualMedia, ids[0]) if ids[0] is not None else (QaMedia, ids[1])
    media = db.scalars(select(model).where(model.id == media_id).with_for_update()
                       .execution_options(populate_existing=True)).one()
    row = db.scalars(
        select(MediaTranscription).where(MediaTranscription.id == ctx.subject_id).with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    ctx.ensure(row is not None and row.status == "RUNNING" and row.task_id == ctx.task_id
               and row.attempt == ctx.attempt)
    return row, media


def _finish(row: MediaTranscription, media: ManualMedia | QaMedia, now: datetime) -> None:
    row.completed_at = now
    row.updated_at = now
    media.expires_at = now + AFTER_TRANSCRIPTION_TTL  # the original is kept 24 h after it ends


def _apply(db: Session, ctx: TaskContext, text: str) -> None:
    row, media = _locked_current(db, ctx)
    row.status, row.text, row.error_code = "READY", text, None
    _finish(row, media, utcnow())


def _fail(db: Session, ctx: TaskContext, _error: Exception) -> None:
    row, media = _locked_current(db, ctx)
    row.status, row.text, row.error_code = "ERROR", None, "TRANSCRIPTION_FAILED"
    _finish(row, media, utcnow())


HANDLER = TaskHandler(kind=KIND, execute=_execute, apply=_apply, fail=_fail, max_tries=3,
                      lease_seconds=300, backoff_seconds=(2.0, 10.0))
register_handler(HANDLER)

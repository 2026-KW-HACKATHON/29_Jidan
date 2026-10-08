"""Worker AI Q&A questions, answers and retries (#121; openapi tag AI 질문).

    POST /api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions        ask
    GET  /api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions/{id}   poll
    POST /api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions/{id}/retries

A question captures the store's current published version when it is asked and is answered
only from that immutable version by the QA_ANSWER task (app.qa.answers), outside any request
transaction. One question per conversation runs at a time (409 QA_BUSY), enforced by a lock
on the conversation row and, as a backstop, the UNIQUE `running_conversation_id`.

Locking (MySQL REPEATABLE READ, TEAM_BRIEF §9): a state change locks the conversation row as
its first statement, so every plain read after it sees what a competing request committed.
Photos are then locked in id order, the same protocol the delete endpoint uses. The task
finalizer locks conversation then question too, so the two never wait on each other in a cycle.
"""

from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentWorker, DbSession
from app.csrf import CsrfWorker
from app.db import new_uuid, utcnow
from app.db.keyed import lock_by_key
from app.db.models import ManualQa, ManualQaPhoto, MediaTranscription, QaMedia
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.manual_content import require_published_version
from app.media.references import qa_media_in_use
from app.qa.access import ConversationIdPath, QuestionIdPath, authorize, not_found
from app.qa.answers import KIND as QA_ANSWER
from app.qa.conversations import conversations_path, owned_conversation
from app.qa.media import media_expired
from app.qa.serialization import question_body
from app.store_access import UUID_PATTERN, StoreIdPath, normalize_uuid
from app.tasks import enqueue

router = APIRouter()

MAX_QUESTION_CHARS = 2000
MAX_PHOTOS = 3
BUSY_MESSAGE = "이전 질문의 답변을 만들고 있어요. 답변이 끝난 뒤 다시 질문해 주세요."

MediaId = Annotated[str, Field(pattern=UUID_PATTERN)]


def _field_error(field: str, code: str, message: str) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[{"field": field, "code": code, "message": message}])


def _busy() -> ApiError:
    return ApiError(409, ErrorCode.QA_BUSY, BUSY_MESSAGE)


def _has_running(db: Session, conversation_id: str) -> bool:
    return db.scalar(select(ManualQa.id).where(
        ManualQa.conversation_id == conversation_id, ManualQa.status == "RUNNING").limit(1)) is not None


class QuestionInput(BaseModel):
    """openapi QAQuestionInput: every field is required; `text`/`transcriptionId` are nullable
    and exactly one of them is set according to `kind`."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["TEXT", "VOICE"]
    text: str | None = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    transcription_id: str | None = Field(alias="transcriptionId", pattern=UUID_PATTERN)
    image_media_ids: list[MediaId] = Field(alias="imageMediaIds", max_length=MAX_PHOTOS)

    def normalized(self) -> dict:
        return {
            "kind": self.kind, "text": self.text,
            "transcriptionId": None if self.transcription_id is None else normalize_uuid(self.transcription_id),
            "imageMediaIds": [normalize_uuid(media_id) for media_id in self.image_media_ids],
        }


def _check_input(body: QuestionInput) -> None:
    if body.kind == "TEXT":
        if body.text is None:
            raise _field_error("text", "REQUIRED", "질문을 입력해 주세요.")
        if not body.text.strip():
            raise _field_error("text", "INVALID_FORMAT", "질문을 입력해 주세요.")
        if body.transcription_id is not None:
            raise _field_error("transcriptionId", "NOT_ALLOWED", "텍스트 질문에는 전사 ID를 보내지 않아요.")
    else:
        if body.transcription_id is None:
            raise _field_error("transcriptionId", "REQUIRED", "음성 질문에는 전사 ID가 필요해요.")
        if body.text is not None:
            raise _field_error("text", "NOT_ALLOWED", "수정한 텍스트는 TEXT 질문으로 보내 주세요.")
    ids = [normalize_uuid(media_id) for media_id in body.image_media_ids]
    if len(set(ids)) != len(ids):
        raise _field_error("imageMediaIds", "DUPLICATE", "같은 사진을 두 번 보낼 수 없어요.")


def _voice_text(db: Session, worker_id: str, store_id: str, transcription_id: str) -> str:
    """The worker's READY transcript in this store. The recording may already be gone: the text
    stays usable for the worker's own questions."""
    found = db.execute(
        select(MediaTranscription)
        .join(QaMedia, QaMedia.id == MediaTranscription.qa_media_id)
        .where(MediaTranscription.id == transcription_id, QaMedia.store_id == store_id,
               QaMedia.worker_id == worker_id)
    ).scalars().first()
    if found is None:
        raise not_found()
    if found.status != "READY":
        raise ApiError(409, ErrorCode.TRANSCRIPTION_NOT_READY, "음성 인식이 끝난 뒤 질문해 주세요.")
    text = found.text.strip()
    if len(text) > MAX_QUESTION_CHARS:
        raise _field_error("transcriptionId", "TOO_LONG",
                           "질문이 2000자를 넘어요. 텍스트로 줄여서 보내 주세요.")
    return text


def _lock_photos(db: Session, worker_id: str, store_id: str, media_ids: list[str]) -> None:
    """Lock the photos (id order, like deletion) and check each is the worker's live photo in
    this store that no other question uses yet."""
    if not media_ids:
        return
    rows = lock_by_key(db, QaMedia, media_ids, populate_existing=True)
    now = utcnow()
    for index, media_id in enumerate(media_ids):
        row = rows.get(media_id)
        if row is None or row.store_id != store_id or row.worker_id != worker_id or row.deleted_at is not None:
            raise not_found()
        if row.kind != "IMAGE":
            raise _field_error(f"imageMediaIds.{index}", "MEDIA_PURPOSE_INVALID", "사진만 첨부할 수 있어요.")
        if media_expired(row, now):
            raise _field_error(f"imageMediaIds.{index}", "EXPIRED", "보관 기한이 지난 사진이에요. 다시 촬영해 주세요.")
        if qa_media_in_use(db, row.id):
            raise ApiError(409, ErrorCode.MEDIA_IN_USE, "이미 다른 질문에 사용한 사진이에요. 다시 첨부해 주세요.")


@router.post("/api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions", status_code=202)
def ask_question(
    store_id: StoreIdPath, conversation_id: ConversationIdPath, body: QuestionInput, worker: CsrfWorker,
    db: DbSession, key: IdempotencyKey,
) -> Response:
    _check_input(body)
    authorize(db, worker.user_id, store_id)
    data = body.normalized()

    def work() -> IdempotentResult:
        conversation = owned_conversation(db, worker.user_id, store_id, conversation_id, lock=True)
        # The version current now is the question's fixed ground (404 MANUAL_NOT_PUBLISHED).
        version_id = require_published_version(db, conversation.store_id).id
        if _has_running(db, conversation.id):
            raise _busy()
        if data["kind"] == "VOICE":
            text = _voice_text(db, worker.user_id, conversation.store_id, data["transcriptionId"])
        else:
            text = data["text"].strip()
        _lock_photos(db, worker.user_id, conversation.store_id, data["imageMediaIds"])
        now = utcnow()
        sequence = (db.scalar(select(func.max(ManualQa.sequence)).where(
            ManualQa.conversation_id == conversation.id)) or 0) + 1
        question_id = new_uuid()
        try:
            with db.begin_nested():
                question = ManualQa(
                    id=question_id, conversation_id=conversation.id, sequence=sequence,
                    published_version_id=version_id, input_method=data["kind"], question=text,
                    transcription_id=data["transcriptionId"], status="RUNNING", attempt=1, asked_at=now,
                    task_id=enqueue(db, QA_ANSWER, question_id, {"questionId": question_id}, attempt=1),
                )
                db.add(question)
                db.flush()
                db.add_all([ManualQaPhoto(qa_id=question_id, media_id=media_id, sort_order=order)
                            for order, media_id in enumerate(data["imageMediaIds"])])
                db.flush()
        except IntegrityError:
            # Only reachable without row locks (SQLite): the UNIQUE running question wins.
            raise _busy() from None
        conversation.updated_at = now
        return IdempotentResult(202, question_body(db, question))

    return run_idempotent(
        db=db, principal=worker, key=key, method="POST",
        path=f"{conversations_path(store_id)}/{normalize_uuid(conversation_id)}/questions", body=data, handler=work,
        revalidate=lambda: owned_conversation(db, worker.user_id, store_id, conversation_id),
    )


def _question(db: Session, conversation_id: str, question_id: str, *, lock: bool = False) -> ManualQa:
    statement = select(ManualQa).where(
        ManualQa.id == normalize_uuid(question_id), ManualQa.conversation_id == conversation_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    question = db.scalars(statement).first()
    if question is None:
        raise not_found()
    return question


@router.get("/api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions/{questionId}")
def read_question(
    store_id: StoreIdPath, conversation_id: ConversationIdPath, question_id: QuestionIdPath,
    worker: CurrentWorker, db: DbSession,
) -> dict:
    conversation = owned_conversation(db, worker.user_id, store_id, conversation_id)
    return question_body(db, _question(db, conversation.id, question_id))


class ProcessingRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post(
    "/api/stores/{storeId}/manual/qa/conversations/{conversationId}/questions/{questionId}/retries",
    status_code=202,
)
def retry_question(
    store_id: StoreIdPath, conversation_id: ConversationIdPath, question_id: QuestionIdPath,
    body: ProcessingRetry, worker: CsrfWorker, db: DbSession, key: IdempotencyKey,
) -> Response:
    authorize(db, worker.user_id, store_id)

    def work() -> IdempotentResult:
        conversation = owned_conversation(db, worker.user_id, store_id, conversation_id, lock=True)
        question = _question(db, conversation.id, question_id, lock=True)
        if question.status != "ERROR":
            raise ApiError(409, ErrorCode.QA_NOT_RETRYABLE, "실패한 질문만 다시 시도할 수 있어요.")
        if _has_running(db, conversation.id):
            raise _busy()
        photo_ids = list(db.scalars(select(ManualQaPhoto.media_id).where(ManualQaPhoto.qa_id == question.id)))
        now = utcnow()
        for photo in lock_by_key(db, QaMedia, photo_ids, populate_existing=True).values():
            if photo.deleted_at is not None or media_expired(photo, now):
                raise ApiError(410, ErrorCode.QA_INPUT_EXPIRED,
                               "질문 사진 보관 기한이 끝났습니다. 새 질문을 작성해 주세요.")
        # Same question, sequence, input and fixed manual version; only the attempt moves on.
        question.attempt += 1
        question.status, question.public_error_code, question.completed_at = "RUNNING", None, None
        question.task_id = enqueue(db, QA_ANSWER, question.id, {"questionId": question.id},
                                   attempt=question.attempt)
        conversation.updated_at = now
        db.flush()
        return IdempotentResult(202, question_body(db, question))

    return run_idempotent(
        db=db, principal=worker, key=key, method="POST",
        path=(f"{conversations_path(store_id)}/{normalize_uuid(conversation_id)}/questions/"
              f"{normalize_uuid(question_id)}/retries"),
        body={}, handler=work,
        revalidate=lambda: owned_conversation(db, worker.user_id, store_id, conversation_id),
    )

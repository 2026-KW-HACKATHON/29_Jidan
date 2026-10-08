"""QA_ANSWER task: answer a worker's question from the question's fixed published version.

    ask/retry (request tx): question RUNNING + enqueue(QA_ANSWER, question.id, attempt)
    execute (no tx):        read question, version snapshot, photos -> provider.answer_question
    apply (finalize tx):    lock question, ensure it still waits for this task/attempt,
                            re-check the worker's access, store answer + citations -> READY
    fail:                   ERROR AI_PROCESSING_FAILED (retryable through the retry endpoint)

Grounding is enforced by app.ai: ANSWERED needs 1..10 citations that exist in the given
manual, NEEDS_OWNER has none, and every excerpt is the server's verbatim join of the cited
steps (never model text). `apply` re-checks that each cited section belongs to the question's
version before storing it. If the worker lost access while the answer was being made, the
answer is discarded (status ERROR) so a late result is never stored for a reader who may no
longer see it.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import get_ai_provider
from app.ai.contracts import ImageInput, QaAnswer, QaRequest
from app.ai.errors import AiError, AiErrorCode
from app.db import session_scope, utcnow
from app.db.models import (
    ManualQa,
    ManualQaCitation,
    ManualQaConversation,
    ManualQaPhoto,
    ManualSection,
    QaMedia,
)
from app.manual_content import structure_snapshot
from app.media.storage import get_media_storage
from app.store_access import has_worker_store_access
from app.tasks import TaskContext, TaskHandler, register_handler

KIND = "QA_ANSWER"
PUBLIC_ERROR = "AI_PROCESSING_FAILED"


def _waiting(question: ManualQa | None, ctx: TaskContext) -> bool:
    return (question is not None and question.status == "RUNNING" and question.task_id == ctx.task_id
            and question.attempt == ctx.attempt)


def _execute(ctx: TaskContext) -> QaAnswer | None:
    with session_scope() as db:
        question = db.get(ManualQa, ctx.subject_id)
        if not _waiting(question, ctx):
            return None  # superseded: apply cancels the task without touching anything
        conversation = db.get(ManualQaConversation, question.conversation_id)
        if not has_worker_store_access(db, conversation.worker_id, conversation.store_id, utcnow()):
            raise AiError(AiErrorCode.INPUT_REJECTED, detail="access_ended")
        manual = structure_snapshot(db, question.published_version_id)
        photos = db.execute(
            select(QaMedia.object_key, QaMedia.mime_type, QaMedia.content_deleted_at, QaMedia.deleted_at)
            .join(ManualQaPhoto, ManualQaPhoto.media_id == QaMedia.id)
            .where(ManualQaPhoto.qa_id == question.id).order_by(ManualQaPhoto.sort_order)
        ).all()
        text = question.question
    storage = get_media_storage()
    images = []
    for key, mime_type, purged, deleted in photos:
        if purged is not None or deleted is not None:
            raise AiError(AiErrorCode.INPUT_REJECTED, detail="photo_gone")
        try:
            images.append(ImageInput(mime_type=mime_type, data=storage.read(key)))
        except FileNotFoundError:
            raise AiError(AiErrorCode.INPUT_REJECTED, detail="photo_gone") from None
    return get_ai_provider().answer_question(QaRequest(question=text, manual=manual, images=tuple(images)))


def _locked(db: Session, ctx: TaskContext) -> ManualQa:
    """Lock the conversation, then the question: the order ask/retry use, so a retry racing a
    late finish cannot deadlock (conversation_id never changes, a plain read finds it)."""
    conversation_id = db.scalar(select(ManualQa.conversation_id).where(ManualQa.id == ctx.subject_id))
    ctx.ensure(conversation_id is not None)
    db.execute(select(ManualQaConversation.id).where(ManualQaConversation.id == conversation_id)
               .with_for_update())
    question = db.scalars(select(ManualQa).where(ManualQa.id == ctx.subject_id).with_for_update()
                          .execution_options(populate_existing=True)).first()
    ctx.ensure(_waiting(question, ctx))
    return question


def _finish(db: Session, question: ManualQa, now) -> None:
    question.completed_at = now
    conversation = db.get(ManualQaConversation, question.conversation_id)
    conversation.updated_at = now


def _apply(db: Session, ctx: TaskContext, answer: QaAnswer | None) -> None:
    question = _locked(db, ctx)
    ctx.ensure(answer is not None)
    now = utcnow()
    conversation = db.get(ManualQaConversation, question.conversation_id)
    if not has_worker_store_access(db, conversation.worker_id, conversation.store_id, now):
        question.status, question.public_error_code = "ERROR", PUBLIC_ERROR
        _finish(db, question, now)
        return
    cited = [citation.section_id for citation in answer.citations]
    if cited:
        known = set(db.scalars(select(ManualSection.id).where(
            ManualSection.id.in_(cited), ManualSection.version_id == question.published_version_id)))
        if known != set(cited):  # cannot happen: the snapshot is the same immutable version
            raise RuntimeError("citation outside the question's manual version")
    question.status, question.outcome, question.answer = "READY", answer.outcome, answer.text
    db.add_all([
        ManualQaCitation(qa_id=question.id, section_id=citation.section_id, sort_order=order,
                         excerpt=citation.excerpt)
        for order, citation in enumerate(answer.citations)
    ])
    _finish(db, question, now)


def _fail(db: Session, ctx: TaskContext, _error: Exception) -> None:
    question = _locked(db, ctx)
    question.status, question.public_error_code = "ERROR", PUBLIC_ERROR
    _finish(db, question, utcnow())


HANDLER = TaskHandler(kind=KIND, execute=_execute, apply=_apply, fail=_fail, max_tries=3,
                      lease_seconds=300, backoff_seconds=(2.0, 10.0))
register_handler(HANDLER)

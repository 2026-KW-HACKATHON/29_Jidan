"""Loading interview rows for an owner, the lock order, and the API representations.

Lock order (every writer, request or task; one global order shared with #118):
store_manuals row -> interview_sessions row -> manual_versions row -> interview_intent_reviews.
Paths that only touch the interview (answers, reviews, question/Jev/summary tasks) start at the
session lock, which is then the first statement of the transaction, so under MySQL REPEATABLE
READ the plain reads that follow see what the previous holder committed. Paths that also change
the draft (start, completion, session retries, draft generation) take store_manuals first.

Phase projection (API `phase`, docs/manual-interview-design.md):
    COMPLETED                      status COMPLETED
    ERROR                          status ERROR (processing keeps the failed task)
    GENERATING                     IN_PROGRESS, processing DRAFT_GENERATION
    PROCESSING                     IN_PROGRESS, processing a question/evaluation task
    READY_TO_GENERATE              IN_PROGRESS, no processing, no current intent
    COLLECTING                     IN_PROGRESS, no processing, the current unanswered question
"""

import os
from datetime import datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db import iso_utc
from app.db.models import (
    InterviewIntent,
    InterviewIntentReview,
    InterviewQuestionSet,
    InterviewSession,
    InterviewSessionIntent,
    InterviewTurn,
    ManualVersion,
    Store,
    StoreManual,
)
from app.errors import ApiError, ErrorCode
from app.jobs.state import begin_transition
from app.manual_content import store_manual
from app.store_access import load_owned_store, normalize_uuid

NOT_FOUND_MESSAGE = "매뉴얼 리소스를 찾을 수 없습니다."
SESSION_FAILED_MESSAGE = "AI 처리에 실패했어요. 저장한 답변으로 다시 시도해 주세요."
DRAFT_FAILED_MESSAGE = "매뉴얼 초안을 만들지 못했어요. 저장한 내용으로 다시 시도해 주세요."
SUMMARY_FAILED_MESSAGE = "요약을 만들지 못했어요. 저장한 답변으로 다시 시도해 주세요."
CORRECTION_FAILED_MESSAGE = "정정 내용을 다시 구성하지 못했습니다. 저장한 입력으로 재시도해 주세요."
MEDIA_WRITING_FAILED_MESSAGE = "사진·영상으로 업무 내용을 작성하지 못했어요. 다시 시도해 주세요."
REVIEW_FAILED_MESSAGES = {"CORRECTION": CORRECTION_FAILED_MESSAGE, "MEDIA_WRITING": MEDIA_WRITING_FAILED_MESSAGE}
STATE_CONFLICT_MESSAGE = "인터뷰의 현재 상태에서는 처리할 수 없어요. 최신 상태를 다시 확인해 주세요."
REVISION_CONFLICT_MESSAGE = "최신 내용을 다시 확인해 주세요."
REVIEW_NOT_READY_MESSAGE = "아직 요약이 준비되지 않았어요."
REVIEW_PROCESSING_MESSAGE = "요약을 처리하는 중이에요. 잠시 후 다시 확인해 주세요."

QUESTION_KINDS = ("INITIAL_QUESTION", "FOLLOWUP_GENERATION")
GUIDANCE_RESPONSES_ENV = "INTERVIEW_GUIDANCE_RESPONSES"


def guidance_responses() -> bool:
    """Whether responses carry the 0.11.0 guidance fields (guidance, guidanceCards,
    lastAnsweredQuestion). Off unless "true": the frontend's additionalProperties:false
    validator rejects them until its contract is updated (docs/manual-interview-design.md).
    Stored guidance does not depend on it."""
    return os.getenv(GUIDANCE_RESPONSES_ENV, "").strip().lower() == "true"


def not_found() -> ApiError:
    return ApiError(404, ErrorCode.MANUAL_RESOURCE_NOT_FOUND, NOT_FOUND_MESSAGE)


def state_conflict() -> ApiError:
    return ApiError(409, ErrorCode.INTERVIEW_STATE_CONFLICT, STATE_CONFLICT_MESSAGE)


def revision_conflict() -> ApiError:
    return ApiError(409, ErrorCode.REVISION_CONFLICT, REVISION_CONFLICT_MESSAGE)


def iso(value: datetime | None) -> str | None:
    return iso_utc(value)  # the shared API notation: "...+00:00"


def _belongs_to_store(db: Session, session: InterviewSession, store_id: str) -> bool:
    return bool(db.scalar(select(exists().where(
        ManualVersion.id == session.manual_version_id, StoreManual.id == ManualVersion.manual_id,
        StoreManual.store_id == store_id,
    ))))


def load_session(db: Session, owner_id: str, store_id: str, session_id: str) -> tuple[Store, InterviewSession]:
    """Read access: the owner's APPROVED store and its session, else 404."""
    store = load_owned_store(db, owner_id, store_id)
    session = db.get(InterviewSession, normalize_uuid(session_id))
    if session is None or not _belongs_to_store(db, session, store.id):
        raise not_found()
    return store, session


def lock_session(db: Session, owner_id: str, store_id: str, session_id: str) -> tuple[Store, InterviewSession]:
    """Write access: lock the session row first (see module docstring), then authorize.

    A row of another store may be locked for a moment before the 404; nothing is revealed."""
    session = db.execute(
        select(InterviewSession).where(InterviewSession.id == normalize_uuid(session_id))
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    store = load_owned_store(db, owner_id, store_id)
    if session is None or not _belongs_to_store(db, session, store.id):
        raise not_found()
    return store, session


def lock_session_row(db: Session, session_id: str) -> InterviewSession | None:
    """Task side: lock the session row (first statement of apply/fail)."""
    return db.execute(
        select(InterviewSession).where(InterviewSession.id == session_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def lock_review(db: Session, session_id: str, intent_id: str) -> InterviewIntentReview | None:
    return db.execute(
        select(InterviewIntentReview).where(
            InterviewIntentReview.session_id == session_id, InterviewIntentReview.intent_id == intent_id,
        ).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def lock_version(db: Session, version_id: str) -> ManualVersion:
    return db.execute(
        select(ManualVersion).where(ManualVersion.id == version_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one()


def lock_manual_and_session(db: Session, owner_id: str, store_id: str, session_id: str
                            ) -> tuple[Store, StoreManual, InterviewSession, ManualVersion]:
    """Write access for paths that also change the draft (completion, session retries).

    The manual lock comes first: store_manuals -> session -> version is the one global order
    shared with #118 (draft edits, corrections and publication lock store_manuals first too).
    The transaction runs in READ COMMITTED on MySQL, so reads after the locks are current; call
    it first in a `run_idempotent` handler."""
    begin_transition(db)
    store = load_owned_store(db, owner_id, store_id)
    # Plain read before the locks: only to find the session's ID; every state check below uses
    # the locked rows (READ COMMITTED reads after the locks are current).
    session = db.get(InterviewSession, normalize_uuid(session_id))
    manual = store_manual(db, store.id, lock=True)
    if session is None or manual is None:
        raise not_found()
    session = lock_session_row(db, session.id)
    version = lock_version(db, session.manual_version_id)
    if version.manual_id != manual.id:
        raise not_found()
    return store, manual, session, version


def session_intents(db: Session, session_id: str) -> list[tuple[InterviewSessionIntent, InterviewIntent]]:
    """Progress rows with their intent definitions, in question set order."""
    return [(progress, intent) for progress, intent in db.execute(
        select(InterviewSessionIntent, InterviewIntent)
        .join(InterviewIntent, InterviewIntent.id == InterviewSessionIntent.intent_id)
        .where(InterviewSessionIntent.session_id == session_id)
        .order_by(InterviewIntent.sort_order)
    )]


def session_intent(db: Session, session_id: str, intent_id: str
                   ) -> tuple[InterviewSessionIntent, InterviewIntent] | None:
    row = db.execute(
        select(InterviewSessionIntent, InterviewIntent)
        .join(InterviewIntent, InterviewIntent.id == InterviewSessionIntent.intent_id)
        .where(InterviewSessionIntent.session_id == session_id,
               InterviewSessionIntent.intent_id == intent_id)
    ).first()
    return None if row is None else (row[0], row[1])


# --- phase and current question ------------------------------------------------------------


def phase_of(session: InterviewSession) -> str:
    if session.status == "COMPLETED":
        return "COMPLETED"
    if session.status == "ERROR":
        return "ERROR"
    if session.processing_kind == "DRAFT_GENERATION":
        return "GENERATING"
    if session.processing_kind is not None:
        return "PROCESSING"
    if session.current_intent_id is None:
        return "READY_TO_GENERATE"
    return "COLLECTING"


def latest_question(db: Session, session_id: str, intent_id: str) -> InterviewTurn | None:
    return db.scalars(
        select(InterviewTurn).where(
            InterviewTurn.session_id == session_id, InterviewTurn.intent_id == intent_id,
            InterviewTurn.turn_kind == "QUESTION",
        ).order_by(InterviewTurn.turn_no.desc()).limit(1)
    ).first()


def is_answered(db: Session, question_id: str) -> bool:
    return bool(db.scalar(select(exists().where(InterviewTurn.reply_to_question_turn_id == question_id))))


def last_answered_question(db: Session, session: InterviewSession) -> InterviewTurn | None:
    """The question whose answer the evaluation judges, while it runs or after it failed (ERROR
    keeps the failed task). Questions are immutable, so this is the snapshot taken when the
    answer was accepted, and it survives retries; a new question, the next intent or the draft
    replaces the EVALUATION task and the snapshot is gone."""
    if session.processing_kind != "EVALUATION" or session.current_intent_id is None:
        return None
    question = latest_question(db, session.id, session.current_intent_id)
    if question is None or not is_answered(db, question.id):
        return None
    return question


def current_question(db: Session, session: InterviewSession) -> InterviewTurn | None:
    """The single question shown in COLLECTING (the latest unanswered one of the intent)."""
    if phase_of(session) != "COLLECTING":
        return None
    question = latest_question(db, session.id, session.current_intent_id)
    if question is None or is_answered(db, question.id):
        return None
    return question


# --- representations ------------------------------------------------------------------------


def processing_body(kind: str | None, task_id: str | None, attempt: int | None) -> dict | None:
    if kind is None:
        return None
    return {"taskId": task_id, "kind": kind, "attempt": attempt}


def intent_body(progress: InterviewSessionIntent, intent: InterviewIntent) -> dict:
    return {
        "id": intent.id, "key": intent.intent_key, "stage": intent.stage,
        "coverage": progress.coverage_status, "depth": progress.depth,
        "finishedAt": iso(progress.finished_at),
    }


def question_body(turn: InterviewTurn, *, answered: bool = False, guidance: bool = False) -> dict:
    """ManualInterviewQuestion. Questions written before guidance existed show null / []."""
    body = {
        "id": turn.id, "intentId": turn.intent_id, "kind": turn.question_kind, "depth": turn.depth,
        "batchId": turn.probe_batch_id, "text": turn.content, "answered": answered,
    }
    if guidance:
        body["guidance"], body["guidanceCards"] = turn.guidance, list(turn.guidance_cards or [])
    return body


def session_body(db: Session, session: InterviewSession) -> dict:
    version = db.get(ManualVersion, session.manual_version_id)
    manual = db.get(StoreManual, version.manual_id)
    question_set = db.get(InterviewQuestionSet, session.question_set_id)
    question = current_question(db, session)
    guidance = guidance_responses()
    error = None
    if session.status == "ERROR":
        message = DRAFT_FAILED_MESSAGE if session.processing_kind == "DRAFT_GENERATION" else SESSION_FAILED_MESSAGE
        error = {"code": session.error_code, "message": message, "retryable": True}
    body = {
        "id": session.id,
        "storeId": manual.store_id,
        "draftVersionId": version.id,
        "questionSetVersion": question_set.revision_no,
        "revision": session.revision,
        "status": session.status,
        "phase": phase_of(session),
        "intents": [intent_body(progress, intent) for progress, intent in session_intents(db, session.id)],
        "currentIntentId": session.current_intent_id,
        "questions": [question_body(question, guidance=guidance)] if question is not None else [],
        "processing": processing_body(session.processing_kind, session.processing_task_id,
                                      session.processing_attempt),
        "error": error,
        "startedAt": iso(session.started_at),
        "completedAt": iso(session.completed_at),
    }
    if guidance:
        answered = last_answered_question(db, session)
        body["lastAnsweredQuestion"] = (
            None if answered is None else question_body(answered, answered=True, guidance=True))
    return body


def review_body(review: InterviewIntentReview) -> dict:
    error = None
    if review.status == "ERROR":
        message = REVIEW_FAILED_MESSAGES.get(review.processing_kind, SUMMARY_FAILED_MESSAGE)
        error = {"code": review.error_code, "message": message, "retryable": True}
    return {
        "intentId": review.intent_id,
        "revision": review.revision,
        "status": review.status,
        "content": review.ready_content,
        "confirmedAt": iso(review.confirmed_at),
        "processing": processing_body(review.processing_kind, review.processing_task_id,
                                      review.processing_attempt),
        "error": error,
    }


def turn_body(turn: InterviewTurn, photo_ids: list[str]) -> dict:
    return {
        "id": turn.id, "sequence": turn.turn_no, "intentId": turn.intent_id, "kind": turn.turn_kind,
        "speaker": turn.speaker, "questionKind": turn.question_kind, "depth": turn.depth,
        "batchId": turn.probe_batch_id, "replyToQuestionId": turn.reply_to_question_turn_id,
        "inputMethod": turn.input_method, "content": turn.content, "photoIds": photo_ids,
        "createdAt": iso(turn.created_at),
    }


def reviews_in_order(db: Session, session_id: str) -> list[InterviewIntentReview]:
    """The session's reviews in question set order. Sorted here, not with ORDER BY: MySQL 8 puts
    whole rows (with the large ready_content JSON) into the filesort and fails with 1038 "Out of
    sort memory" once the reviews are big."""
    order = dict(db.execute(
        select(InterviewIntent.id, InterviewIntent.sort_order)
        .join(InterviewSessionIntent, InterviewSessionIntent.intent_id == InterviewIntent.id)
        .where(InterviewSessionIntent.session_id == session_id)
    ).all())
    reviews = db.scalars(select(InterviewIntentReview).where(InterviewIntentReview.session_id == session_id))
    return sorted(reviews, key=lambda review: order[review.intent_id])


def content_of(review: InterviewIntentReview) -> dict[str, Any] | None:
    return review.ready_content

"""Task handlers of the interview: questions, Jev, intent summaries/corrections, the draft.

Every `apply`/`fail` locks the session row first (then the review), and checks with
`ctx.ensure` that the row still waits for exactly this task (task ID, state and the input
revision), so late, duplicated or superseded results change nothing (the runner then marks
the task CANCELLED).

Failures. Question wording falls back to a fixed text after the runner's retries
(app.interview.flow.fallback_question), so question generation alone never stops the
interview. Jev and draft failures make the session ERROR; summary/correction failures make only
that review ERROR. Each keeps its stored input for the retry endpoints.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ai import get_ai_provider
from app.ai.contracts import (
    EvidenceChunk,
    GeneratedQuestion,
    IntentSummary,
    IntentSummaryRequest,
    QuestionRequest,
    StructureRevision,
    StructureRevisionRequest,
    SufficiencyJudgement,
    SufficiencyRequest,
)
from app.db import session_scope, utcnow
from app.db.models import (
    InterviewEvaluation,
    InterviewIntent,
    InterviewIntentReview,
    InterviewSession,
    InterviewTurn,
)
from app.interview import drafting
from app.interview.cards import InvalidGuidance, example_card
from app.interview.common import lock_review, lock_session_row
from app.interview.content import content_from_structure, photo_ids, snapshot_from_content
from app.interview.evidence import correction_evidence
from app.interview.flow import (
    apply_judgement,
    available_shifts,
    fallback_question,
    shift_summary_pending,
    store_context,
    store_of,
    write_question,
)
from app.media.references import replace_snapshot_refs
from app.tasks import TaskContext, TaskDeferred, TaskHandler, register_handler, task_error_code

logger = logging.getLogger(__name__)

PUBLIC_FAILURE = "AI_PROCESSING_FAILED"
FALLBACK_SOURCE = "fallback:template"
SHIFT_WAIT_SECONDS = 5.0
LIMITS = {"max_tries": 3, "lease_seconds": 300, "backoff_seconds": (2.0, 10.0, 30.0)}


def _waiting_session(db: Session, ctx: TaskContext) -> InterviewSession:
    session = lock_session_row(db, ctx.subject_id)
    ctx.ensure(session is not None and session.processing_task_id == ctx.task_id
               and session.processing_attempt == ctx.attempt and session.status == "IN_PROGRESS"
               and session.revision == ctx.input_revision)
    return session


def _failing_session(db: Session, ctx: TaskContext) -> InterviewSession:
    session = lock_session_row(db, ctx.subject_id)
    ctx.ensure(session is not None and session.processing_task_id == ctx.task_id
               and session.status == "IN_PROGRESS" and session.revision == ctx.input_revision)
    return session


def _set_error(session: InterviewSession) -> None:
    session.status, session.error_code = "ERROR", PUBLIC_FAILURE  # processing keeps the task
    session.revision += 1


# --- INITIAL_QUESTION / FOLLOWUP_GENERATION ----------------------------------------------------


def _question_execute(ctx: TaskContext) -> GeneratedQuestion:
    return get_ai_provider().generate_question(QuestionRequest.model_validate(ctx.payload["request"]))


def _question_apply(db: Session, ctx: TaskContext, result: GeneratedQuestion) -> None:
    session = _waiting_session(db, ctx)
    try:
        card = example_card(session.id, ctx.payload["intentId"],
                            [(example.label, example.description) for example in result.examples])
    except InvalidGuidance as error:  # examples are only decoration: the question goes on
        logger.warning("interview examples dropped: session=%s reason=%s", session.id, error)
        card = None
    write_question(db, session, ctx.payload, result.text, result.meta.config_version,
                   guidance=result.guidance, cards=[card] if card else [])


def _question_fail(db: Session, ctx: TaskContext, _error: Exception) -> None:
    session = _failing_session(db, ctx)
    write_question(db, session, ctx.payload, fallback_question(ctx.payload), FALLBACK_SOURCE)


# --- EVALUATION (Jev) ---------------------------------------------------------------------------


def _evaluation_execute(ctx: TaskContext) -> SufficiencyJudgement:
    return get_ai_provider().judge_sufficiency(SufficiencyRequest.model_validate(ctx.payload["request"]))


def _evaluation_row(session: InterviewSession, ctx: TaskContext, **values: Any) -> InterviewEvaluation:
    payload = ctx.payload
    return InterviewEvaluation(
        session_id=session.id, intent_id=payload["intentId"], probe_batch_id=payload["batchId"],
        depth=payload["depth"], attempt_no=ctx.attempt, evaluated_through_turn_id=payload["throughTurnId"],
        input_snapshot={"request": payload["request"], "contextReviews": payload["contextReviews"]},
        task_id=ctx.task_id, **values,
    )


def _evaluation_apply(db: Session, ctx: TaskContext, judgement: SufficiencyJudgement) -> None:
    session = _waiting_session(db, ctx)
    ctx.ensure(session.current_intent_id == ctx.payload["intentId"])
    now = utcnow()
    db.add(_evaluation_row(
        session, ctx, evaluation_config_version=judgement.meta.config_version[:200],
        provider=judgement.meta.provider[:32], status="SUCCEEDED",
        needs_follow_up=judgement.needs_follow_up, probability=judgement.probability, applied_at=now,
    ))
    db.flush()  # the (session, intent, applied_depth) UNIQUE rejects a second applied result
    apply_judgement(db, session, judgement.sufficient, judgement.missing_aspects, now)


def _evaluation_fail(db: Session, ctx: TaskContext, error: Exception) -> None:
    session = _failing_session(db, ctx)
    provider = get_ai_provider()
    db.add(_evaluation_row(
        session, ctx, evaluation_config_version=provider.config_version[:200],
        provider=provider.provider_name[:32], status="FAILED", error_code=task_error_code(error),
    ))
    _set_error(session)


# --- REVIEW_UNDERSTANDING / REVIEW_CORRECTION ---------------------------------------------------


def _waiting_review(db: Session, ctx: TaskContext) -> InterviewIntentReview:
    session = lock_session_row(db, ctx.subject_id)  # lock order: session, then review
    ctx.ensure(session is not None)
    review = lock_review(db, session.id, ctx.payload["intentId"])
    ctx.ensure(review is not None and review.processing_task_id == ctx.task_id
               and review.processing_attempt == ctx.attempt and review.status == "PROCESSING"
               and review.revision == ctx.input_revision)
    return review


def _review_ready(review: InterviewIntentReview, content: dict[str, Any] | None) -> None:
    review.ready_content = content
    review.status, review.error_code = "READY", None
    review.processing_kind = review.processing_task_id = review.processing_attempt = None
    review.revision += 1


def _review_fail(db: Session, ctx: TaskContext, _error: Exception) -> None:
    review = _waiting_review(db, ctx)
    review.status, review.error_code = "ERROR", PUBLIC_FAILURE  # last READY content is kept
    review.revision += 1


def _understanding_execute(ctx: TaskContext) -> IntentSummary:
    request = IntentSummaryRequest.model_validate(ctx.payload["request"])
    if request.intent.stage != "WORK_STRUCTURE":
        # Shifts are read now, not when the task was queued: the work-structure summary may
        # still have been generating then, and a shift task needs its shift IDs. While it is
        # still generating, wait (no try used); afterwards use its latest READY shifts.
        with session_scope() as db:
            if shift_summary_pending(db, ctx.subject_id):
                raise TaskDeferred(SHIFT_WAIT_SECONDS)
            shifts = available_shifts(db, ctx.subject_id, ctx.payload["intentId"])
        request = request.model_copy(update={"available_shifts": tuple(shifts)})
    return get_ai_provider().summarize_intent(request)


def _understanding_apply(db: Session, ctx: TaskContext, summary: IntentSummary) -> None:
    review = _waiting_review(db, ctx)
    _review_ready(review, content_from_structure(
        review.intent_id, summary.summary, summary.structure, needs_detail=ctx.payload["needsDetail"],
    ))


def correction_request(db: Session, session_id: str, intent_id: str, turn_id: str,
                       frozen_evidence: list[dict[str, Any]] | None = None) -> StructureRevisionRequest:
    """The review's last READY content (unchanged while the correction is PROCESSING), the
    correction turn's text, the other reviews' shifts, the store wording context and the
    owner's words up to this correction as evidence (app.interview.evidence). The evidence is the
    one frozen in the task payload when the correction was accepted; tasks queued before it was
    frozen there rebuild it (same turns, same review content, so the same chunks)."""
    review = db.get(InterviewIntentReview, (session_id, intent_id))
    content = review.ready_content
    session = db.get(InterviewSession, session_id)
    turn = db.get(InterviewTurn, turn_id)
    current = snapshot_from_content(content)
    if frozen_evidence is None:
        evidence = correction_evidence(db, session_id, db.get(InterviewIntent, intent_id).intent_key, turn, current)
    else:
        evidence = tuple(EvidenceChunk.model_validate(chunk) for chunk in frozen_evidence)
    return StructureRevisionRequest(
        current=current, summary=content["summary"], instruction=turn.content,
        external_shifts=tuple(available_shifts(db, session_id, intent_id)),
        store=store_context(store_of(db, session)), evidence=evidence,
    )


def _correction_execute(ctx: TaskContext) -> StructureRevision:
    with session_scope() as db:
        request = correction_request(db, ctx.subject_id, ctx.payload["intentId"], ctx.payload["correctionTurnId"],
                                     ctx.payload.get("evidence"))
    return get_ai_provider().revise_structure(request)


def _correction_apply(db: Session, ctx: TaskContext, revision: StructureRevision) -> None:
    review = _waiting_review(db, ctx)
    previous = review.ready_content
    if revision.outcome != "APPLIED":
        # NO_CHANGE, or a correction the model could not apply without guessing
        # (CLARIFICATION_REQUIRED / REFERENCE_CONFLICT): the READY content stays as it was, and
        # so does its confirmation. The contract has no public code for "say it again".
        _review_ready(review, previous)
        confirmed = ctx.payload.get("previousConfirmation")
        if confirmed:
            review.confirmed_at = datetime.fromisoformat(confirmed["at"])
            review.confirmed_by_owner_id = confirmed["by"]
        return
    content = content_from_structure(
        review.intent_id, revision.summary or previous["summary"], revision.structure,
        needs_detail=previous["needsDetail"], previous=previous,
    )
    # Photos of deleted sections are unlinked; the others stay on their section IDs.
    replace_snapshot_refs(db, "INTENT_REVIEW", review.session_id, photo_ids(content), intent_id=review.intent_id)
    _review_ready(review, content)


# --- registration -------------------------------------------------------------------------------

HANDLERS = (
    TaskHandler(kind="INITIAL_QUESTION", execute=_question_execute, apply=_question_apply,
                fail=_question_fail, **LIMITS),
    TaskHandler(kind="FOLLOWUP_GENERATION", execute=_question_execute, apply=_question_apply,
                fail=_question_fail, **LIMITS),
    TaskHandler(kind="EVALUATION", execute=_evaluation_execute, apply=_evaluation_apply,
                fail=_evaluation_fail, **LIMITS),
    TaskHandler(kind="REVIEW_UNDERSTANDING", execute=_understanding_execute, apply=_understanding_apply,
                fail=_review_fail, **LIMITS),
    TaskHandler(kind="REVIEW_CORRECTION", execute=_correction_execute, apply=_correction_apply,
                fail=_review_fail, **LIMITS),
    TaskHandler(kind="DRAFT_GENERATION", execute=drafting.execute, apply=drafting.apply,
                fail=drafting.fail, **LIMITS),
)
for _handler in HANDLERS:
    register_handler(_handler)

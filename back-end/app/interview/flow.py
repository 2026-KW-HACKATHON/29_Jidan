"""Question progress: one question -> one answer -> Jev judgement, as a DB state machine.

Every transition runs while the caller holds the session row lock (app.interview.common) and
bumps `session.revision` once. AI work is only enqueued here; app.interview.tasks runs it.

    start            -> INITIAL_QUESTION(intent 1, BASE)                      phase PROCESSING
    question applied -> QUESTION turn (batch READY)                           phase COLLECTING
    answer           -> ANSWER turn + EVALUATION(depth d)                     phase PROCESSING
    judged sufficient            -> intent COVERED  -> review + next intent
    judged insufficient, d < 5   -> probe batch d+1 -> FOLLOWUP_GENERATION(PROBE)
    judged insufficient, d == 5  -> intent NEEDS_DETAIL -> review + next intent
    next intent      -> INITIAL_QUESTION(BASE) or, after the last one, READY_TO_GENERATE

Inputs of every task are an immutable snapshot taken when it is enqueued: the dialogue of the
current intent from its BASE question on, the store's name/industry (wording only) and the
summaries of the other finished intents whose review had READY content at that moment (their
revisions are recorded as `contextReviews`). A later correction only affects tasks enqueued
after it (docs/erd/manual.md 실행 제약).
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.contracts import (
    MAX_SHIFTS,
    ContextNote,
    DialogueTurn,
    IntentBrief,
    IntentSummaryRequest,
    QuestionRequest,
    ShiftItem,
    StoreContext,
    SufficiencyRequest,
)
from app.db.models import (
    InterviewIntent,
    InterviewIntentReview,
    InterviewProbeBatch,
    InterviewSession,
    InterviewSessionIntent,
    InterviewTurn,
    ManualVersion,
    Store,
    StoreManual,
)
from app.interview.cards import InvalidGuidance, check_guidance, clean_cards
from app.interview.common import reviews_in_order, session_intents
from app.interview.evidence import summary_evidence
from app.tasks import enqueue

logger = logging.getLogger(__name__)

MAX_DEPTH = 5
INDUSTRY_LABELS = {"CAFE": "카페", "RESTAURANT": "음식점", "CONVENIENCE_STORE": "편의점", "OTHER": "매장"}
INTENT_LABELS = {
    "WORK_STRUCTURE": "근무 구조", "COMMON_TASKS": "공통 업무", "SHIFT_TASKS": "근무조별 업무",
    "RULES": "매장 규칙", "EQUIPMENT": "설비 사용", "EXCEPTIONS": "예외 상황",
}


def store_of(db: Session, session: InterviewSession) -> Store:
    return db.scalars(
        select(Store).join(StoreManual, StoreManual.store_id == Store.id)
        .join(ManualVersion, ManualVersion.manual_id == StoreManual.id)
        .where(ManualVersion.id == session.manual_version_id)
    ).one()


def store_context(store: Store) -> StoreContext:
    return StoreContext(name=store.name, industry=INDUSTRY_LABELS.get(store.industry, "매장"))


def intent_brief(intent: InterviewIntent) -> IntentBrief:
    return IntentBrief(key=intent.intent_key, stage=intent.stage, base_question=intent.base_question,
                       coverage_criteria=intent.coverage_criteria)


def intent_label(intent: InterviewIntent) -> str:
    return INTENT_LABELS.get(intent.intent_key, intent.intent_key)


def next_turn_no(db: Session, session_id: str) -> int:
    return (db.scalar(select(func.max(InterviewTurn.turn_no)).where(InterviewTurn.session_id == session_id))
            or 0) + 1


def dialogue_of(db: Session, session_id: str, intent_id: str) -> list[DialogueTurn]:
    """Answered questions of an intent, BASE first, in order."""
    questions = list(db.scalars(select(InterviewTurn).where(
        InterviewTurn.session_id == session_id, InterviewTurn.intent_id == intent_id,
        InterviewTurn.turn_kind == "QUESTION",
    ).order_by(InterviewTurn.turn_no)))
    answers = {
        turn.reply_to_question_turn_id: turn for turn in db.scalars(select(InterviewTurn).where(
            InterviewTurn.session_id == session_id, InterviewTurn.intent_id == intent_id,
            InterviewTurn.turn_kind == "ANSWER",
        ))
    }
    return [
        DialogueTurn(question=q.content, answer=answers[q.id].content, depth=q.depth)
        for q in questions if q.id in answers
    ]


def context_from_reviews(db: Session, session_id: str, exclude_intent_id: str | None
                         ) -> tuple[list[ContextNote], list[dict[str, Any]]]:
    """Summaries of the other finished intents (last READY content) and which revisions were used."""
    keys = {intent.id: intent.intent_key for _progress, intent in session_intents(db, session_id)}
    notes, used = [], []
    for review in reviews_in_order(db, session_id):
        if review.intent_id == exclude_intent_id or not review.ready_content:
            continue
        notes.append(ContextNote(intent_key=keys[review.intent_id], summary=review.ready_content["summary"]))
        used.append({"intentId": review.intent_id, "revision": review.revision})
    return notes, used


def available_shifts(db: Session, session_id: str, exclude_intent_id: str | None) -> list[ShiftItem]:
    """Shifts defined by the other reviews (read-only references for SHIFT_TASK sections)."""
    shifts: dict[str, ShiftItem] = {}
    for review in reviews_in_order(db, session_id):
        if review.intent_id == exclude_intent_id or not review.ready_content:
            continue
        for s in review.ready_content.get("shifts", []):
            shifts.setdefault(s["id"], ShiftItem(id=s["id"], name=s["name"], start_time=s["startTime"],
                                                 end_time=s["endTime"], ends_next_day=s["endsNextDay"]))
    return list(shifts.values())[:MAX_SHIFTS]


def shift_summary_pending(db: Session, session_id: str) -> bool:
    """A work-structure review whose first summary is still being generated (no READY content
    yet). Its task always ends, so waiting for it terminates; an ERROR summary does not block."""
    return any(
        intent.stage == "WORK_STRUCTURE" and review.status == "PROCESSING" and not review.ready_content
        for review, intent in db.execute(
            select(InterviewIntentReview, InterviewIntent)
            .join(InterviewIntent, InterviewIntent.id == InterviewIntentReview.intent_id)
            .where(InterviewIntentReview.session_id == session_id)
        )
    )


def _set_processing(session: InterviewSession, kind: str | None, task_id: str | None = None,
                    attempt: int | None = None) -> None:
    session.processing_kind, session.processing_task_id, session.processing_attempt = kind, task_id, attempt


# --- question generation --------------------------------------------------------------------


def enqueue_base_question(db: Session, session: InterviewSession, intent: InterviewIntent, store: Store) -> None:
    notes, used = context_from_reviews(db, session.id, intent.id)
    request = QuestionRequest(kind="BASE", intent=intent_brief(intent), depth=0, context=tuple(notes),
                              store=store_context(store))
    payload = {"intentId": intent.id, "depth": 0, "batchId": None, "contextReviews": used,
               "request": request.model_dump(mode="json")}
    task_id = enqueue(db, "INITIAL_QUESTION", session.id, payload, input_revision=session.revision, attempt=1)
    _set_processing(session, "INITIAL_QUESTION", task_id, 1)


def enqueue_probe(db: Session, session: InterviewSession, progress: InterviewSessionIntent,
                  intent: InterviewIntent, store: Store, aspects: tuple[str, ...]) -> None:
    depth = progress.depth + 1
    batch = InterviewProbeBatch(session_id=session.id, intent_id=intent.id, depth=depth, status="GENERATING")
    db.add(batch)
    db.flush()
    progress.depth = depth
    notes, used = context_from_reviews(db, session.id, intent.id)
    request = QuestionRequest(
        kind="PROBE", intent=intent_brief(intent), depth=depth,
        dialogue=tuple(dialogue_of(db, session.id, intent.id)), missing_aspects=aspects,
        context=tuple(notes), store=store_context(store),
    )
    payload = {"intentId": intent.id, "depth": depth, "batchId": batch.id, "contextReviews": used,
               "request": request.model_dump(mode="json")}
    task_id = enqueue(db, "FOLLOWUP_GENERATION", session.id, payload, input_revision=session.revision,
                      attempt=1)
    _set_processing(session, "FOLLOWUP_GENERATION", task_id, 1)


def write_question(db: Session, session: InterviewSession, payload: dict[str, Any], text: str,
                   source: str, *, guidance: str | None = None,
                   cards: Sequence[dict[str, Any]] = ()) -> InterviewTurn:
    """Store the question with its guidance (app.interview.cards) in the same revision. Guidance
    that breaks the rules is left out and logged; it never blocks the question."""
    batch_id = payload["batchId"]
    try:
        guidance = check_guidance(guidance)
    except InvalidGuidance as error:
        logger.warning("interview guidance left out: session=%s intent=%s reason=%s",
                       session.id, payload["intentId"], error)
        guidance = None
    cards, notes = clean_cards(db, session.id, list(cards))
    for note in notes:
        logger.warning("interview guidance card: session=%s intent=%s reason=%s",
                       session.id, payload["intentId"], note)
    turn = InterviewTurn(
        session_id=session.id, turn_no=next_turn_no(db, session.id), speaker="AI", turn_kind="QUESTION",
        question_kind="PROBE" if batch_id else "BASE", intent_id=payload["intentId"],
        depth=payload["depth"], probe_batch_id=batch_id, content=text,
        guidance=guidance, guidance_cards=cards,
    )
    db.add(turn)
    if batch_id:
        batch = db.get(InterviewProbeBatch, batch_id)
        batch.status, batch.generator_source, batch.error_code = "READY", source[:200], None
    _set_processing(session, None)
    session.revision += 1
    db.flush()
    return turn


def fallback_question(payload: dict[str, Any]) -> str:
    """The question used when generation failed after its retries: the intent's base question
    as written, or a fixed sentence about the first missing aspect. Never "information is
    sufficient" and never an ERROR (docs/manual-interview-design.md)."""
    request = payload["request"]
    if request["kind"] == "BASE":
        return request["intent"]["base_question"]
    aspect = request["missing_aspects"][0]
    return f"{aspect}에 대해 조금 더 자세히 알려 주시겠어요?"


# --- answers and evaluation -----------------------------------------------------------------


def enqueue_evaluation(db: Session, session: InterviewSession, progress: InterviewSessionIntent,
                       intent: InterviewIntent, store: Store, question: InterviewTurn,
                       answer: InterviewTurn) -> None:
    notes, used = context_from_reviews(db, session.id, intent.id)
    request = SufficiencyRequest(
        intent=intent_brief(intent), dialogue=tuple(dialogue_of(db, session.id, intent.id)),
        depth=question.depth, context=tuple(notes), store=store_context(store),
    )
    payload = {"intentId": intent.id, "depth": question.depth, "batchId": question.probe_batch_id,
               "questionTurnId": question.id, "throughTurnId": answer.id, "contextReviews": used,
               "request": request.model_dump(mode="json")}
    task_id = enqueue(db, "EVALUATION", session.id, payload, input_revision=session.revision, attempt=1)
    _set_processing(session, "EVALUATION", task_id, 1)


def apply_judgement(db: Session, session: InterviewSession, sufficient: bool,
                    aspects: tuple[str, ...], now: datetime) -> None:
    """Move on from a successful evaluation of the current intent (caller wrote the row)."""
    intent = db.get(InterviewIntent, session.current_intent_id)
    progress = db.get(InterviewSessionIntent, (session.id, intent.id))
    store = store_of(db, session)
    session.revision += 1  # before enqueueing: the next task is bound to the new revision
    if not sufficient and progress.depth < MAX_DEPTH:
        enqueue_probe(db, session, progress, intent, store, aspects)
    else:
        finish_intent(db, session, progress, intent, store, covered=sufficient, aspects=aspects, now=now)


def finish_intent(db: Session, session: InterviewSession, progress: InterviewSessionIntent,
                  intent: InterviewIntent, store: Store, *, covered: bool, aspects: tuple[str, ...],
                  now: datetime) -> None:
    progress.finished_at = now
    if covered:
        progress.coverage_status, progress.covered_at = "COVERED", now
    else:
        # Internal only: what Jev still missed at depth 5 (owner-facing issue text in the draft).
        progress.coverage_status, progress.coverage_note = "NEEDS_DETAIL", "\n".join(aspects) or None
    db.flush()
    enqueue_understanding(db, session, intent, store, needs_detail=not covered)
    advance(db, session, intent, store)


def advance(db: Session, session: InterviewSession, finished: InterviewIntent, store: Store) -> None:
    following = [intent for progress, intent in session_intents(db, session.id)
                 if intent.sort_order > finished.sort_order and progress.coverage_status == "PENDING"]
    if following:
        session.current_intent_id = following[0].id
        enqueue_base_question(db, session, following[0], store)
    else:
        session.current_intent_id = None
        _set_processing(session, None)


# --- reviews ----------------------------------------------------------------------------------


def enqueue_understanding(db: Session, session: InterviewSession, intent: InterviewIntent, store: Store,
                          *, needs_detail: bool) -> InterviewIntentReview:
    """The finished intent's review, created PROCESSING with its summary task (same tx).

    Summary failures stay on the review (ERROR + review retries); question progress goes on."""
    request = IntentSummaryRequest(
        intent=intent_brief(intent), dialogue=tuple(dialogue_of(db, session.id, intent.id)),
        needs_detail=needs_detail, available_shifts=tuple(available_shifts(db, session.id, intent.id)),
        store=store_context(store),
        evidence=summary_evidence(db, session.id, intent),  # frozen in the payload (retries)
    )
    review = InterviewIntentReview(session_id=session.id, intent_id=intent.id, revision=1, status="PROCESSING")
    payload = {"intentId": intent.id, "needsDetail": needs_detail, "request": request.model_dump(mode="json")}
    task_id = enqueue(db, "REVIEW_UNDERSTANDING", session.id, payload, input_revision=1, attempt=1)
    review.processing_kind, review.processing_task_id, review.processing_attempt = "UNDERSTANDING", task_id, 1
    db.add(review)
    db.flush()
    return review

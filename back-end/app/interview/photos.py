"""Optional review photo cards using the existing question and attachment contracts."""

from uuid import uuid4

from sqlalchemy.orm import Session

from app.ai import get_ai_provider
from app.ai.contracts import PhotoSuggestions, PhotoSuggestionsRequest
from app.db import session_scope
from app.db.models import InterviewIntentReview, InterviewSession
from app.interview.common import current_question, lock_review, lock_session_row, session_intents
from app.interview.content import snapshot_from_content
from app.interview.flow import store_context, store_of
from app.tasks import TaskContext


def photo_cards(intent_id: str, request: PhotoSuggestionsRequest, result: PhotoSuggestions) -> list[dict]:
    """Only persisted sections supplied to this call can become attachment targets."""
    section_ids = {section.id for section in request.structure.sections}
    cards = []
    for suggestion in result.suggestions:
        if suggestion.section_id not in section_ids:
            continue
        cards.append({
            "id": str(uuid4()), "type": "PHOTO_SUGGESTIONS", "title": suggestion.title,
            "items": [{"id": str(uuid4()), "label": item.label, "description": item.description}
                      for item in suggestion.items],
            "footer": suggestion.footer,
            "attachmentTarget": {"intentId": intent_id, "target": "SECTION",
                                 "sectionId": suggestion.section_id},
        })
    return cards


def _has_source_cards(question, intent_id: str) -> bool:
    return any(card.get("type") == "PHOTO_SUGGESTIONS"
               and (card.get("attachmentTarget") or {}).get("intentId") == intent_id
               for card in question.guidance_cards or [])


def _candidate(db: Session, session_id: str):
    session = db.get(InterviewSession, session_id)
    if session is None:
        return None
    question = current_question(db, session)
    if question is None or question.question_kind != "BASE" or len(question.guidance_cards or []) >= 5:
        return None
    previous = None
    for progress, intent in session_intents(db, session.id):
        if intent.id == question.intent_id:
            break
        previous = intent.id if progress.finished_at is not None else None
    if previous is None or _has_source_cards(question, previous):
        return None
    review = db.get(InterviewIntentReview, (session.id, previous))
    if review is None or review.status != "READY" or not review.ready_content or not review.ready_content.get("sections"):
        return None
    request = PhotoSuggestionsRequest(summary=review.ready_content["summary"],
                                      structure=snapshot_from_content(review.ready_content),
                                      store=store_context(store_of(db, session)))
    return question.id, previous, review.revision, request


def suggest_current_question_photos(ctx: TaskContext) -> None:
    """Best-effort after either primary commit; never a prerequisite for progress.

    Two callbacks can call the provider, but session locking makes the persisted result
    visible at most once. Process interruption may lose recommendations; ordinary manual
    attachment remains available. Inputs/payloads and answered question snapshots stay intact.
    """
    with session_scope() as db:
        candidate = _candidate(db, ctx.subject_id)
    if candidate is None:
        return
    question_id, intent_id, revision, request = candidate
    result = get_ai_provider().suggest_review_photos(request)
    cards = photo_cards(intent_id, request, result)
    if not cards:
        return
    # The AI call above ran after both primary commit and candidate-read transaction exit.
    with session_scope() as db:
        session = lock_session_row(db, ctx.subject_id)
        if session is None:
            return
        question = current_question(db, session)
        if question is None or question.id != question_id or _has_source_cards(question, intent_id):
            return
        review = lock_review(db, session.id, intent_id)
        if review is None or review.status != "READY" or review.revision != revision or not review.ready_content:
            return
        section_ids = {section["id"] for section in review.ready_content.get("sections", [])}
        valid = [card for card in cards if card["attachmentTarget"]["sectionId"] in section_ids]
        existing = list(question.guidance_cards or [])
        added = valid[:max(0, 5 - len(existing))]
        if added:
            question.guidance_cards = existing + added
            session.revision += 1

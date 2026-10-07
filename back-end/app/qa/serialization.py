"""API bodies of Q&A conversations and questions (openapi QAConversation, QAQuestion, QAAnswer).

Citations are read back from the stored rows: the excerpt is the one built from the cited
steps when the answer was made, and the section title comes from the question's own fixed
(immutable) published version, so a later publication never changes a past answer.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ManualQa,
    ManualQaCitation,
    ManualQaConversation,
    ManualQaPhoto,
    ManualSection,
)
from app.media.transcription import iso

ERROR_MESSAGE = "답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요."


def conversation_body(conversation: ManualQaConversation) -> dict:
    return {
        "id": conversation.id, "storeId": conversation.store_id,
        "createdAt": iso(conversation.created_at), "updatedAt": iso(conversation.updated_at),
    }


def question_bodies(db: Session, questions: list[ManualQa]) -> list[dict]:
    """QAQuestion bodies in the given order, loading photos and citations in two queries."""
    ids = [question.id for question in questions]
    photos: dict[str, list[str]] = {qa_id: [] for qa_id in ids}
    citations: dict[str, list[dict]] = {qa_id: [] for qa_id in ids}
    if ids:
        for qa_id, media_id in db.execute(
            select(ManualQaPhoto.qa_id, ManualQaPhoto.media_id).where(ManualQaPhoto.qa_id.in_(ids))
            .order_by(ManualQaPhoto.qa_id, ManualQaPhoto.sort_order)
        ):
            photos[qa_id].append(media_id)
        for qa_id, section_id, version_id, title, excerpt in db.execute(
            select(ManualQaCitation.qa_id, ManualQaCitation.section_id, ManualSection.version_id,
                   ManualSection.title, ManualQaCitation.excerpt)
            .join(ManualSection, ManualSection.id == ManualQaCitation.section_id)
            .where(ManualQaCitation.qa_id.in_(ids))
            .order_by(ManualQaCitation.qa_id, ManualQaCitation.sort_order)
        ):
            citations[qa_id].append({
                "versionId": version_id, "sectionId": section_id, "sectionTitle": title, "excerpt": excerpt,
            })
    return [_question_body(q, photos[q.id], citations[q.id]) for q in questions]


def question_body(db: Session, question: ManualQa) -> dict:
    return question_bodies(db, [question])[0]


def _question_body(question: ManualQa, photos: list[str], citations: list[dict]) -> dict:
    answer = error = None
    if question.status == "READY":
        answer = {
            "outcome": question.outcome, "text": question.answer,
            "citations": citations if question.outcome == "ANSWERED" else [],
        }
    elif question.status == "ERROR":
        error = {"code": question.public_error_code, "message": ERROR_MESSAGE, "retryable": True}
    return {
        "id": question.id,
        "conversationId": question.conversation_id,
        "sequence": question.sequence,
        "manualVersionId": question.published_version_id,
        "status": question.status,
        "text": question.question,
        "imageMediaIds": photos,
        "createdAt": iso(question.asked_at),
        "completedAt": iso(question.completed_at),
        "answer": answer,
        "error": error,
    }

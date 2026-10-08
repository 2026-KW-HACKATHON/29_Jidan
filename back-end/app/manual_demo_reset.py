"""Demo seed reset step for manuals, AI interviews, Q&A and media (registered in app.demo_seed).

Deletes every manual/interview/Q&A/media row that belongs to the demo accounts or demo stores,
children first so each row-by-row foreign key check passes on MySQL. Stored media bytes are
left to the orphan sweep (app.media.retention), which removes files whose rows are gone.
"""

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    InterviewEvaluation,
    InterviewIntentReview,
    InterviewProbeBatch,
    InterviewReviewConfirmation,
    InterviewSession,
    InterviewSessionIntent,
    InterviewTurn,
    InterviewTurnPhoto,
    ManualDraftCorrection,
    ManualIssueAcknowledgement,
    ManualMedia,
    ManualMediaSnapshotRef,
    ManualPhotoAttachment,
    ManualQa,
    ManualQaCitation,
    ManualQaConversation,
    ManualQaPhoto,
    ManualReviewIssue,
    ManualSection,
    ManualShift,
    ManualStep,
    ManualVersion,
    MediaTranscription,
    QaMedia,
    StoreManual,
)


def _ids(db: Session, statement) -> list[str]:
    return list(dict.fromkeys(db.scalars(statement)))


def reset_manual_data(db: Session, users: list[str], stores: list[str]) -> None:
    manuals = _ids(db, select(StoreManual.id).where(StoreManual.store_id.in_(stores)))
    versions = _ids(db, select(ManualVersion.id).where(or_(
        ManualVersion.manual_id.in_(manuals), ManualVersion.created_by_owner_id.in_(users),
        ManualVersion.published_by_owner_id.in_(users))))
    manual_media = _ids(db, select(ManualMedia.id).where(or_(
        ManualMedia.store_id.in_(stores), ManualMedia.uploaded_by_owner_id.in_(users))))
    qa_media = _ids(db, select(QaMedia.id).where(or_(QaMedia.store_id.in_(stores), QaMedia.worker_id.in_(users))))
    conversations = _ids(db, select(ManualQaConversation.id).where(or_(
        ManualQaConversation.store_id.in_(stores), ManualQaConversation.worker_id.in_(users))))
    questions = _ids(db, select(ManualQa.id).where(or_(
        ManualQa.conversation_id.in_(conversations), ManualQa.published_version_id.in_(versions))))
    sessions = _ids(db, select(InterviewSession.id).where(or_(
        InterviewSession.manual_version_id.in_(versions), InterviewSession.owner_id.in_(users))))
    issues = _ids(db, select(ManualReviewIssue.id).where(ManualReviewIssue.version_id.in_(versions)))
    sections = _ids(db, select(ManualSection.id).where(ManualSection.version_id.in_(versions)))
    transcriptions = _ids(db, select(MediaTranscription.id).where(or_(
        MediaTranscription.store_id.in_(stores), MediaTranscription.manual_media_id.in_(manual_media),
        MediaTranscription.qa_media_id.in_(qa_media))))

    for statement in (
        delete(ManualQaCitation).where(or_(ManualQaCitation.qa_id.in_(questions),
                                           ManualQaCitation.section_id.in_(sections))),
        delete(ManualQaPhoto).where(or_(ManualQaPhoto.qa_id.in_(questions), ManualQaPhoto.media_id.in_(qa_media))),
        delete(ManualQa).where(ManualQa.id.in_(questions)),
        delete(ManualQaConversation).where(ManualQaConversation.id.in_(conversations)),
        delete(InterviewTurnPhoto).where(or_(
            InterviewTurnPhoto.turn_id.in_(select(InterviewTurn.id).where(InterviewTurn.session_id.in_(sessions))),
            InterviewTurnPhoto.media_id.in_(manual_media))),
        delete(InterviewEvaluation).where(InterviewEvaluation.session_id.in_(sessions)),
        # Answers point at their questions: remove them before the questions.
        delete(InterviewTurn).where(InterviewTurn.session_id.in_(sessions),
                                    InterviewTurn.reply_to_question_turn_id.is_not(None)),
        delete(InterviewTurn).where(InterviewTurn.session_id.in_(sessions)),
        delete(InterviewReviewConfirmation).where(InterviewReviewConfirmation.session_id.in_(sessions)),
        delete(InterviewIntentReview).where(InterviewIntentReview.session_id.in_(sessions)),
        delete(InterviewProbeBatch).where(InterviewProbeBatch.session_id.in_(sessions)),
        delete(InterviewSessionIntent).where(InterviewSessionIntent.session_id.in_(sessions)),
        delete(InterviewSession).where(InterviewSession.id.in_(sessions)),
        delete(ManualIssueAcknowledgement).where(or_(ManualIssueAcknowledgement.issue_id.in_(issues),
                                                     ManualIssueAcknowledgement.owner_id.in_(users))),
        delete(ManualReviewIssue).where(ManualReviewIssue.id.in_(issues)),
        delete(ManualDraftCorrection).where(or_(ManualDraftCorrection.version_id.in_(versions),
                                                ManualDraftCorrection.requested_by_owner_id.in_(users),
                                                ManualDraftCorrection.transcription_id.in_(transcriptions))),
        delete(ManualPhotoAttachment).where(or_(ManualPhotoAttachment.version_id.in_(versions),
                                                ManualPhotoAttachment.media_id.in_(manual_media),
                                                ManualPhotoAttachment.video_media_id.in_(manual_media))),
        delete(ManualStep).where(ManualStep.section_id.in_(sections)),
        delete(ManualSection).where(ManualSection.id.in_(sections)),
        delete(ManualShift).where(ManualShift.version_id.in_(versions)),
        update(StoreManual).where(StoreManual.id.in_(manuals)).values(current_published_version_id=None),
        delete(ManualVersion).where(ManualVersion.id.in_(versions)),
        delete(StoreManual).where(StoreManual.id.in_(manuals)),
        delete(ManualMediaSnapshotRef).where(ManualMediaSnapshotRef.media_id.in_(manual_media)),
        delete(MediaTranscription).where(MediaTranscription.id.in_(transcriptions)),
        # A video refers to its poster (same table): unlink before the rows go.
        update(ManualMedia).where(ManualMedia.id.in_(manual_media)).values(poster_media_id=None),
        delete(ManualMedia).where(ManualMedia.id.in_(manual_media)),
        delete(QaMedia).where(QaMedia.id.in_(qa_media)),
    ):
        db.execute(statement.execution_options(synchronize_session=False))

"""Operator cleanup for expired demo visitors, run by the demo Compose retention service.

Only exact demo visitor prefixes in the isolated demo DB are removed. A 30 minute grace
exceeds the existing request/AI deadlines. Task rows are deleted first: an in-flight external
call can finish, but its lease can no longer apply its result after cleanup.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.db import session_scope, utcnow
from app.db.keyed import delete_by_key
from app.db.models import (
    AuthSession,
    BackgroundTask,
    InterviewSession,
    ManualDraftCorrection,
    ManualMedia,
    ManualQa,
    ManualQaConversation,
    MediaTranscription,
    QaMedia,
    User,
)
from app.demo import PREFIX, require_demo
from app.demo_seed import delete_accounts
from app.jobs.state import begin_transition
from app.media.retention import sweep_orphan_files
from app.media.storage import get_media_storage
from app.operator_cli import operator_cli

GRACE = timedelta(minutes=30)
logger = logging.getLogger("jidan.demo")


def _resources(db: Session, user_id: str) -> tuple[set[str], list[str]]:
    manual_media = list(db.scalars(select(ManualMedia).where(ManualMedia.uploaded_by_owner_id == user_id)))
    qa_media = list(db.scalars(select(QaMedia).where(QaMedia.worker_id == user_id)))
    subjects = set(db.scalars(select(InterviewSession.id).where(InterviewSession.owner_id == user_id)))
    subjects.update(db.scalars(select(ManualDraftCorrection.id).where(
        ManualDraftCorrection.requested_by_owner_id == user_id)))
    subjects.update(db.scalars(select(ManualQa.id).join(ManualQaConversation).where(
        ManualQaConversation.worker_id == user_id)))
    subjects.update(db.scalars(select(MediaTranscription.id).where(or_(
        MediaTranscription.manual_media_id.in_([row.id for row in manual_media]),
        MediaTranscription.qa_media_id.in_([row.id for row in qa_media])))))
    return subjects, [row.object_key for row in [*manual_media, *qa_media]]


def purge(*, now: datetime | None = None) -> int:
    require_demo()
    now = now or utcnow()
    storage = get_media_storage()
    with session_scope() as db:
        recent_session = exists(select(AuthSession.id).where(
            AuthSession.user_id == User.id,
            or_(AuthSession.revoked_at > now - GRACE,
                and_(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now - GRACE))))
        candidates = list(db.scalars(select(User.id).where(
            func.substr(User.google_sub, 1, len(PREFIX)) == PREFIX,
            User.created_at <= now - GRACE, ~recent_session).order_by(User.created_at).limit(100)))
    removed = 0
    for user_id in candidates:
        keys = []
        with operator_cli(), session_scope() as db:
            begin_transition(db)
            # A second cleanup process cannot remove the same user concurrently.
            user = db.scalar(select(User).where(User.id == user_id).with_for_update(skip_locked=True))
            if user is None or not user.google_sub.startswith(PREFIX):
                continue
            sessions = list(db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)))
            if any((row.revoked_at or row.expires_at) > now - GRACE for row in sessions):
                continue
            subjects, keys = _resources(db, user.id)
            # A finisher may enqueue another task while we wait for its PK lock. After
            # deleting that predecessor, READ COMMITTED observes its committed successor.
            # Once empty, every late finisher lacks a live lease and cannot apply/enqueue.
            while task_ids := list(db.scalars(select(BackgroundTask.id).where(
                    BackgroundTask.subject_id.in_(subjects)))):
                delete_by_key(db, BackgroundTask, task_ids)
            delete_accounts(db, user.google_sub)
        # Byte deletion follows the DB commit. A crash leaves recoverable orphan files.
        for key in keys:
            try:
                storage.delete(key)
            except OSError:
                logger.error("demo file removal failed; orphan sweep will retry")
        removed += 1
    sweep_orphan_files()
    return removed


if __name__ == "__main__":
    print(f"expired demo visitors removed: {purge()}")

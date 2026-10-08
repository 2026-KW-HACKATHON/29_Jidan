"""Live references to owner photos, and the locking protocol that keeps them consistent.

A photo is "in use" while any interview answer (interview_turn_photos), any version's
attachment (manual_photo_attachments, published ones included) or any JSON snapshot that lists
it (manual_media_snapshot_refs: review content, confirmation history, generation/correction
input) refers to it. Deletion is refused and retention never purges such a photo.

Linking and deleting serialize on the manual_media row: both take `SELECT ... FOR UPDATE` on
it first (in id order when locking several), and the in-use checks are locking reads, so a
concurrent delete and link can never leave a dangling reference (tests/test_manual_media_api.py
races them on MySQL). Followers must link photos only through `lock_photos_for_link`.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.keyed import delete_by_key, lock_by_key
from app.db.models import (
    InterviewTurnPhoto,
    ManualMedia,
    ManualMediaSnapshotRef,
    ManualPhotoAttachment,
    ManualQa,
    ManualQaPhoto,
    MediaTranscription,
)

RELINK_GRACE = timedelta(hours=24)  # an unlinked photo is kept this long before retention


class MediaLinkError(Exception):
    """A photo cannot be linked. `reason`: not_found (other store, unknown, deleted, purged)
    or not_image (an audio file). Map it to the endpoint's contract error."""

    def __init__(self, reason: str, media_id: str):
        self.reason = reason
        self.media_id = media_id
        super().__init__(reason)


def _any(db: Session, column, *conditions) -> bool:
    """A locking read (FOR SHARE): under MySQL REPEATABLE READ a plain SELECT reads the
    transaction's old snapshot and would miss a link committed while we waited for the media
    row lock, letting a delete win over it. Locking reads always see the latest commit."""
    return db.execute(
        select(column).where(*conditions).limit(1).with_for_update(read=True)
    ).first() is not None


def manual_media_in_use(db: Session, media_id: str) -> bool:
    return (
        _any(db, InterviewTurnPhoto.turn_id, InterviewTurnPhoto.media_id == media_id)
        or _any(db, ManualPhotoAttachment.id, ManualPhotoAttachment.media_id == media_id)
        or _any(db, ManualMediaSnapshotRef.id, ManualMediaSnapshotRef.media_id == media_id)
    )


def transcription_running(db: Session, *, manual_media_id: str | None = None,
                          qa_media_id: str | None = None) -> bool:
    column = MediaTranscription.manual_media_id if manual_media_id else MediaTranscription.qa_media_id
    return _any(db, MediaTranscription.id, column == (manual_media_id or qa_media_id),
                MediaTranscription.status == "RUNNING")


def qa_media_in_use(db: Session, media_id: str) -> bool:
    """Linked to any question: the worker cannot delete it (409 MEDIA_IN_USE)."""
    return _any(db, ManualQaPhoto.qa_id, ManualQaPhoto.media_id == media_id)


def qa_media_needed(db: Session, media_id: str) -> bool:
    """Linked to a question that is still being answered. Only then does retention wait: a
    question photo otherwise ends with its 7 day period (openapi: an expired input photo makes
    a retry 410 QA_INPUT_EXPIRED), while the question, answer and citations stay."""
    return _any(db, ManualQaPhoto.qa_id, ManualQaPhoto.media_id == media_id,
                ManualQaPhoto.qa_id == ManualQa.id, ManualQa.status == "RUNNING")


def lock_photos_for_link(db: Session, store_id: str, media_ids: Iterable[str],
                         now: datetime | None = None) -> list[ManualMedia]:
    """Lock and return the store's linkable photos in the given order, or raise MediaLinkError.

    Linkable: same store, IMAGE, not deleted, bytes still stored. Call it in the transaction
    that writes the link rows (turn photos, attachments, snapshot refs)."""
    ordered = list(dict.fromkeys(media_ids))
    if not ordered:
        return []
    rows = lock_by_key(db, ManualMedia, ordered)  # one row at a time, ascending ids
    result = []
    for media_id in ordered:
        row = rows.get(media_id)
        if row is None or row.store_id != store_id or row.deleted_at is not None or row.content_deleted_at:
            raise MediaLinkError("not_found", media_id)
        if row.kind != "IMAGE":
            raise MediaLinkError("not_image", media_id)
        result.append(row)
    return result


def add_snapshot_refs(db: Session, holder_kind: str, holder_id: str, media_ids: Iterable[str], *,
                      intent_id: str | None = None) -> None:
    """Record that a JSON snapshot (review content, confirmation, generation/correction input)
    references these photos. Lock them with `lock_photos_for_link` first."""
    existing = set(db.scalars(select(ManualMediaSnapshotRef.media_id).where(
        ManualMediaSnapshotRef.holder_kind == holder_kind, ManualMediaSnapshotRef.holder_id == holder_id,
        ManualMediaSnapshotRef.holder_intent_id.is_(None) if intent_id is None
        else ManualMediaSnapshotRef.holder_intent_id == intent_id,
    )))
    for media_id in dict.fromkeys(media_ids):
        if media_id not in existing:
            db.add(ManualMediaSnapshotRef(
                media_id=media_id, holder_kind=holder_kind, holder_id=holder_id, holder_intent_id=intent_id,
            ))
    db.flush()


def replace_snapshot_refs(db: Session, holder_kind: str, holder_id: str, media_ids: Iterable[str], *,
                          intent_id: str | None = None, now: datetime | None = None) -> None:
    """Make a holder's references exactly `media_ids` (e.g. a review's photos changed);
    photos that lost their last reference get a fresh retention grace period."""
    wanted = list(dict.fromkeys(media_ids))
    remove_snapshot_refs(db, holder_kind, holder_id, intent_id=intent_id, keep=wanted, now=now)
    add_snapshot_refs(db, holder_kind, holder_id, wanted, intent_id=intent_id)


def remove_snapshot_refs(db: Session, holder_kind: str, holder_id: str, *, intent_id: str | None = None,
                         keep: Iterable[str] = (), now: datetime | None = None) -> list[str]:
    """Drop a holder's references (except `keep`) and return the photos that were released."""
    condition = [
        ManualMediaSnapshotRef.holder_kind == holder_kind, ManualMediaSnapshotRef.holder_id == holder_id,
        ManualMediaSnapshotRef.holder_intent_id.is_(None) if intent_id is None
        else ManualMediaSnapshotRef.holder_intent_id == intent_id,
    ]
    keep = set(keep)
    if keep:
        condition.append(ManualMediaSnapshotRef.media_id.not_in(keep))
    refs = db.execute(select(ManualMediaSnapshotRef.id, ManualMediaSnapshotRef.media_id).where(*condition)).all()
    media_ids = [media_id for _ref_id, media_id in refs]
    if refs:
        delete_by_key(db, ManualMediaSnapshotRef, [ref_id for ref_id, _media_id in refs])
        release_unreferenced(db, media_ids, now=now)
    return media_ids


def release_unreferenced(db: Session, media_ids: Iterable[str], *, now: datetime | None = None) -> None:
    """Give photos that are no longer referenced anywhere a full grace period before retention
    purges them (so an unlink followed by a quick re-link does not lose the file)."""
    now = now or utcnow()
    for media_id in set(media_ids):
        if not manual_media_in_use(db, media_id):
            db.execute(
                update(ManualMedia)
                .where(ManualMedia.id == media_id, ManualMedia.expires_at < now + RELINK_GRACE)
                .values(expires_at=now + RELINK_GRACE)
                .execution_options(synchronize_session=False)
            )

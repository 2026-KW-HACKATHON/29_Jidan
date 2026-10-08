"""Retention: purge stored bytes of deleted, unattached or expired media (app.lifespan PERIODIC_JOBS).

Policy (openapi uploadManualMedia, docs/erd/qa.md):
* owner photos/recordings: 24 h after upload while unattached; a photo is never purged while
  referenced (references.manual_media_in_use) and gets a new 24 h when its last link goes;
* recordings: 24 h after their transcription ends (READY or ERROR), never while it RUNS;
* worker question photos 7 days, worker recordings 24 h; a photo is kept past that only while a
  question using it is still RUNNING (an answered or failed question keeps its text, not the photo);
* owner videos (VIDEO, MANUAL_VIDEO): AI input only, never shown to anyone. A media-writing
  snapshot reference protects their bytes while the task waits or runs. After the last
  reference is released, they get the same fresh 24 h grace as an unattached photo;
* deleted (tombstoned) media: at once.
Metadata rows stay (tombstone, transcription text, answers); only `content_deleted_at` is set.
The DB mark commits before the file is removed, so a crash leaves at most an orphan file, which
`sweep_orphan_files` removes; a mark is never left pointing at bytes that silently vanished.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select

from app.db import session_scope, utcnow
from app.db.models import ManualMedia, QaMedia
from app.media.references import manual_media_in_use, qa_media_needed, transcription_running
from app.media.storage import get_media_storage

logger = logging.getLogger("jidan.media")

UNATTACHED_TTL = timedelta(hours=24)
AFTER_TRANSCRIPTION_TTL = timedelta(hours=24)
VIDEO_HOLD_TTL = timedelta(hours=24)
QA_IMAGE_TTL = timedelta(days=7)
QA_AUDIO_TTL = timedelta(hours=24)
RECHECK_IN_USE = timedelta(hours=24)
RECHECK_RUNNING = timedelta(hours=1)
ORPHAN_MIN_AGE_SECONDS = 3600
BATCH = 100
MAX_BATCHES = 20
INTERVAL_SECONDS = 300


def hold_video_bytes(media: ManualMedia, now: datetime | None = None) -> None:
    """Keep a video's bytes for `VIDEO_HOLD_TTL` from now (never shortens the current hold).
    Call it with the row locked, in the transaction that attaches the video or queues/finishes
    the task that digests it. Snapshot references additionally protect queued/running tasks."""
    now = now or utcnow()
    if media.kind == "VIDEO" and media.expires_at < now + VIDEO_HOLD_TTL:
        media.expires_at = now + VIDEO_HOLD_TTL


def _in_use(db, row) -> timedelta | None:
    """How long to wait before looking at a still-needed row again, None when purgeable.
    Photos and videos remain needed while a snapshot or attachment references them."""
    if isinstance(row, ManualMedia):
        if row.kind in ("IMAGE", "VIDEO") and manual_media_in_use(db, row.id):
            return RECHECK_IN_USE
        if row.kind == "AUDIO" and transcription_running(db, manual_media_id=row.id):
            return RECHECK_RUNNING
    else:
        if row.kind == "IMAGE" and qa_media_needed(db, row.id):
            return RECHECK_IN_USE
        if row.kind == "AUDIO" and transcription_running(db, qa_media_id=row.id):
            return RECHECK_RUNNING
    return None


def purge_media_content(*, now: datetime | None = None, media_ids: list[str] | None = None) -> int:
    """Mark and delete the bytes of purgeable media; returns how many files were released."""
    now = now or utcnow()
    storage = get_media_storage()
    purged = 0
    for model in (ManualMedia, QaMedia):
        for _ in range(MAX_BATCHES):
            keys: list[str] = []
            with session_scope() as db:
                query = select(model).where(
                    model.content_deleted_at.is_(None),
                    or_(model.deleted_at.is_not(None), model.expires_at <= now),
                )
                if media_ids is not None:
                    query = query.where(model.id.in_(media_ids))
                rows = db.scalars(
                    query.order_by(model.expires_at, model.id).limit(BATCH).with_for_update(skip_locked=True)
                ).all()
                for row in rows:
                    wait = None if row.deleted_at is not None else _in_use(db, row)
                    if wait is not None:
                        row.expires_at = now + wait  # still needed: look again later
                        continue
                    row.content_deleted_at = now
                    keys.append(row.object_key)
            for key in keys:  # after the commit: a crash here leaves an orphan, never a lie
                try:
                    storage.delete(key)
                except OSError:
                    logger.error("media file removal failed; the orphan sweep retries")
            purged += len(keys)
            if len(rows) < BATCH or media_ids is not None:
                break
    if purged:
        logger.info("media retention released %d file(s)", purged)
    return purged


def sweep_orphan_files(*, min_age_seconds: float = ORPHAN_MIN_AGE_SECONDS) -> int:
    """Delete stored files with no live metadata row (failed uploads, crashes after marking)."""
    import time

    storage = get_media_storage()
    removed = storage.remove_stale_temporaries(min_age_seconds)
    cutoff = time.time() - min_age_seconds
    for scope, model in (("manual", ManualMedia), ("qa", QaMedia)):
        candidates = [item for item in storage.iter_files(scope) if item.modified_at < cutoff]
        for start in range(0, len(candidates), 500):
            chunk = candidates[start:start + 500]
            with session_scope() as db:
                live = set(db.scalars(select(model.object_key).where(
                    model.object_key.in_([item.key for item in chunk]), model.content_deleted_at.is_(None),
                )))
            for item in chunk:
                if item.key not in live and storage.delete(item.key):
                    removed += 1
    if removed:
        logger.info("media orphan sweep removed %d file(s)", removed)
    return removed


def run_retention() -> None:
    purge_media_content()
    sweep_orphan_files()

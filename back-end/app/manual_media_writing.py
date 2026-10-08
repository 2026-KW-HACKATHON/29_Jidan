"""Writing a section from photos and videos (OpenAPI 0.12.0, docs/manual-interview-design.md
"사진·영상 기반 작성").

User decision (2026-10-08): photos and videos are only **input the AI reads information from**.
The manual keeps text only: nothing here attaches a file to a section, derives a poster or adds
a field to any content. The request names the files (`mediaIds`, this store's MANUAL_PHOTO /
MANUAL_VIDEO uploads, in the order the model should see them).

Request side (review media writing, draft correction MEDIA), inside the request transaction:
`lock_media_for_writing` locks and checks the files, `hold_media` keeps them while the task waits
or runs: a snapshot reference (the existing retention/deletion protection: a referenced file is
neither purged nor deletable, 409 MEDIA_IN_USE) and, for videos, `hold_video_bytes` (media-A;
video bytes are otherwise purged at `expires_at` whatever references them). When the task ends
(applied, or failed for good) `release_media` drops the reference; the files then fall back to
the normal rule for unattached uploads and are purged after a fresh 24 h grace. A retry holds
them again.

Task side (`gather_media`, in `execute`, no transaction open): one short read of the rows, then
storage reads, video decoding (`app.media.video.digest_video`, media-A) and speech-to-text of a
video's sound track (`AiProvider.transcribe`). Evidence, in request order:
* photo -> PHOTO "media:<id>", re-encoded as a JPEG of at most PHOTO_MAX_SIDE px;
* video -> VIDEO_FRAME "media:<id>@<t_ms>" per sampled frame in time order, then
  VIDEO_TRANSCRIPT "media:<id>#transcript" when the sound track was recognized (a failed or empty
  transcription is left out; the frames still go in).
`title`/`caption` are None: the files carry no owner labels here. Totals stay within MAX_MEDIA
items, MAX_MEDIA_IMAGES images and MAX_MEDIA_IMAGE_BYTES (media-C refuses more); what does not
fit is left out in request order. A file whose bytes are gone is skipped; no evidence at all is a
non-retryable INPUT_REJECTED.
"""

import io
import logging
from collections.abc import Iterable, Sequence

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import get_ai_provider
from app.ai.contracts import (
    MAX_MEDIA,
    MAX_MEDIA_IMAGE_BYTES,
    MAX_MEDIA_IMAGES,
    ImageInput,
    MediaEvidence,
    TranscriptionRequest,
)
from app.ai.errors import AiError, AiErrorCode
from app.db import session_scope, utcnow
from app.db.keyed import lock_by_key
from app.db.models import ManualMedia
from app.media import video as video_layer
from app.media.errors import MediaRejected
from app.media.references import add_snapshot_refs, remove_snapshot_refs
from app.media.retention import hold_video_bytes
from app.media.storage import get_media_storage
from app.tasks import TaskContext

logger = logging.getLogger("jidan.media_writing")

MEDIA_HOLDER = "MEDIA_WRITING"  # snapshot-ref holder of a review request (session + intent)
MAX_MEDIA_IDS = 10  # files per request (openapi ManualMediaWritingRequest.mediaIds)
MAX_VIDEOS = 2  # videos per request: each is decoded and transcribed in the task
# Sequential provider calls of one media-writing execute: a transcription per video and the
# writing call (app.tasks.validate_task_leases checks the handlers' leases against it).
PROVIDER_CALLS = MAX_VIDEOS + 1
LEASE_SECONDS = 900  # PROVIDER_CALLS x a fallback provider's 2 x 120 s + 30 s margin, with headroom
PHOTO_MAX_SIDE = 2048
PHOTO_QUALITY = 85


class MediaWritingRejected(Exception):
    """A request's files cannot be read. `reason`: not_found (another store's, unknown, deleted
    or purged: 404), not_photo_or_video (a recording: 422), too_many_videos (422)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# --- request side ---------------------------------------------------------------------------


def lock_media_for_writing(db: Session, store_id: str, media_ids: Sequence[str]) -> list[ManualMedia]:
    """Lock (ascending IDs, the linking protocol of app.media.references) and return the
    request's files in request order, or raise MediaWritingRejected."""
    rows = lock_by_key(db, ManualMedia, media_ids)
    ordered = []
    for media_id in media_ids:
        row = rows.get(media_id)
        if row is None or row.store_id != store_id or row.deleted_at is not None or row.content_deleted_at:
            raise MediaWritingRejected("not_found")
        if row.kind not in ("IMAGE", "VIDEO"):
            raise MediaWritingRejected("not_photo_or_video")
        ordered.append(row)
    if sum(row.kind == "VIDEO" for row in ordered) > MAX_VIDEOS:
        raise MediaWritingRejected("too_many_videos")
    return ordered


def hold_media(db: Session, holder_kind: str, holder_id: str, rows: Iterable[ManualMedia], *,
               intent_id: str | None = None) -> None:
    """Keep the (locked) files while a task waits for or reads them (module docstring)."""
    rows = list(rows)
    now = utcnow()
    for row in rows:
        hold_video_bytes(row, now)
    add_snapshot_refs(db, holder_kind, holder_id, [row.id for row in rows], intent_id=intent_id)


def release_media(db: Session, holder_kind: str, holder_id: str, *, intent_id: str | None = None) -> None:
    """The task is over: the files follow the unattached-upload rule again (24 h grace)."""
    remove_snapshot_refs(db, holder_kind, holder_id, intent_id=intent_id)


def relock_for_retry(db: Session, store_id: str, media_ids: Sequence[str]) -> list[ManualMedia]:
    """The files of a failed request that are still readable, locked, for a retry's hold
    (purged or deleted ones are skipped; the task then reads what is left)."""
    rows = lock_by_key(db, ManualMedia, media_ids)
    return [rows[m] for m in media_ids if m in rows and rows[m].store_id == store_id
            and rows[m].deleted_at is None and rows[m].content_deleted_at is None]


# --- task side ------------------------------------------------------------------------------


def shrink_photo(data: bytes) -> bytes | None:
    """A stored photo as a JPEG of at most PHOTO_MAX_SIDE px (the model's image budget).
    None when it cannot be decoded (the photo is then left out)."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    image.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=PHOTO_QUALITY)
    return out.getvalue()


def _media_rows(ids: Sequence[str]) -> dict[str, tuple[str, str, bool]]:
    """id -> (kind, object key, bytes available)."""
    if not ids:
        return {}
    with session_scope() as db:
        return {
            row.id: (row.kind, row.object_key, row.deleted_at is None and row.content_deleted_at is None)
            for row in db.scalars(select(ManualMedia).where(ManualMedia.id.in_(list(ids))))
        }


def _read(row: tuple[str, str, bool] | None) -> bytes | None:
    if row is None or not row[2]:
        return None
    try:
        return get_media_storage().read(row[1])
    except FileNotFoundError:
        return None


def _transcript(ctx: TaskContext | None, audio: bytes | None, mime: str | None) -> str | None:
    if not audio or not mime or (ctx is not None and ctx.lease_lost()):
        return None
    try:
        return get_ai_provider().transcribe(TranscriptionRequest(audio=audio, mime_type=mime)).text
    except (AiError, ValidationError) as error:  # the frames still describe the video
        logger.info("video transcript skipped: %s", getattr(error, "code", type(error).__name__))
        return None


def gather_media(media_ids: Sequence[str], ctx: TaskContext | None = None) -> tuple[MediaEvidence, ...]:
    """The evidence for the request's files (module docstring)."""
    rows = _media_rows(media_ids)
    evidence: list[MediaEvidence] = []
    images = image_bytes = videos = 0

    def add_image(media_id: str, kind: str, data: bytes | None) -> None:
        nonlocal images, image_bytes
        if (data is None or images >= MAX_MEDIA_IMAGES or len(evidence) >= MAX_MEDIA
                or image_bytes + len(data) > MAX_MEDIA_IMAGE_BYTES):
            return
        evidence.append(MediaEvidence(id=media_id, kind=kind, image=ImageInput(mime_type="image/jpeg", data=data)))
        images += 1
        image_bytes += len(data)

    for media_id in dict.fromkeys(media_ids):
        row = rows.get(media_id)
        data = _read(row)
        if data is None:
            continue
        if row[0] == "IMAGE":
            add_image(f"media:{media_id}", "PHOTO", shrink_photo(data))
            continue
        if row[0] != "VIDEO" or videos >= MAX_VIDEOS:
            continue
        try:
            digest = video_layer.digest_video(data)
        except MediaRejected as rejected:
            logger.info("video digest refused: %s", rejected.code)
            continue
        videos += 1
        for frame in sorted(digest.frames, key=lambda f: f.t_ms):  # <= 1024 px JPEG (media-A)
            add_image(f"media:{media_id}@{frame.t_ms}", "VIDEO_FRAME", frame.jpeg)
        text = _transcript(ctx, digest.audio, digest.audio_mime)
        if text and len(evidence) < MAX_MEDIA:
            evidence.append(MediaEvidence(id=f"media:{media_id}#transcript", kind="VIDEO_TRANSCRIPT", text=text))
    if not evidence:
        raise AiError(AiErrorCode.INPUT_REJECTED, detail="no_media")
    return tuple(evidence)


def evidence_query(section: dict) -> str:
    """Retrieval query for the owner's words about a section (title and steps)."""
    parts = [section.get("title"), *(step.get("instruction") for step in section.get("steps", []))]
    return "\n".join(part for part in parts if part)

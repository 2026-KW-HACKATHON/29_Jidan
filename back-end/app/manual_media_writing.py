"""Turning a section's attached photos and videos into AI evidence (OpenAPI 0.12.0).

Shared by REVIEW_MEDIA_WRITING (app.interview.tasks) and DRAFT_MEDIA_WRITING
(app.manual_corrections). Runs in a task's `execute`: one short read of the media rows, then
storage reads, video decoding (`app.media.video.digest_video`, media-A) and speech-to-text of a
video's sound track (`AiProvider.transcribe`) with no transaction open. The model call itself is
`AiProvider.write_section_from_media(MediaWritingRequest)` (media-C).

Evidence, in display order (app.manual_attachments item shape):
* photo  -> PHOTO "media:<photoId>" with its title/caption, re-encoded as a JPEG of at most
  PHOTO_MAX_SIDE px on the long side (stored photos may be up to 10 MiB);
* video  -> VIDEO_FRAME "media:<videoId>@<t_ms>" per sampled frame, plus VIDEO_TRANSCRIPT
  "media:<videoId>#transcript" when the sound track was recognized. A failed or empty
  transcription is left out (frames alone still go in). A video whose original is gone
  (retention, decoding refused) falls back to its poster as a PHOTO "media:<posterId>".
At most MAX_DIGESTED_VIDEOS videos are decoded and transcribed per task (the lease budget,
`PROVIDER_CALLS`); further videos use their poster. The totals stay within MAX_MEDIA items,
MAX_MEDIA_IMAGES images and MAX_MEDIA_IMAGE_BYTES of image data (the provider refuses more as
INPUT_REJECTED); what does not fit is left out in display order. A file that disappeared is skipped; no evidence at all is a
non-retryable INPUT_REJECTED (the section has nothing left to read).
"""

import io
import logging
from collections.abc import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from sqlalchemy import select

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
from app.db import session_scope
from app.db.models import ManualMedia
from app.media import video as video_layer
from app.media.errors import MediaRejected
from app.media.storage import get_media_storage
from app.tasks import TaskContext

logger = logging.getLogger("jidan.media_writing")

MAX_DIGESTED_VIDEOS = 2
# Sequential provider calls of one media-writing execute: a transcription per decoded video and
# the writing call (app.tasks.validate_task_leases checks the handlers' leases against it).
PROVIDER_CALLS = MAX_DIGESTED_VIDEOS + 1
LEASE_SECONDS = 900  # PROVIDER_CALLS x a fallback provider's 2 x 120 s + margin, with headroom
TITLE_LIMIT, CAPTION_LIMIT = 100, 1000
PHOTO_MAX_SIDE = 2048
PHOTO_QUALITY = 85


def shrink_photo(data: bytes) -> bytes | None:
    """A stored photo as a JPEG of at most PHOTO_MAX_SIDE px (the provider's image budget).
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


def _media_rows(ids: Iterable[str]) -> dict[str, tuple[str, str, str, bool]]:
    """id -> (kind, mime type, object key, bytes available)."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return {}
    with session_scope() as db:
        return {
            row.id: (row.kind, row.mime_type, row.object_key,
                     row.deleted_at is None and row.content_deleted_at is None)
            for row in db.scalars(select(ManualMedia).where(ManualMedia.id.in_(wanted)))
        }


def _read(row: tuple[str, str, str, bool] | None) -> bytes | None:
    if row is None or not row[3]:
        return None
    try:
        return get_media_storage().read(row[2])
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


def gather_media(items: list[dict], ctx: TaskContext | None = None) -> tuple[MediaEvidence, ...]:
    """The evidence for `items` (one section's attachments); see the module docstring."""
    rows = _media_rows([i["mediaId"] for i in items] + [i["posterMediaId"] for i in items if i.get("posterMediaId")])
    evidence: list[MediaEvidence] = []
    images = digested = image_bytes = 0

    def labels(item: dict) -> dict:
        return {"title": (item.get("title") or None) and item["title"][:TITLE_LIMIT],
                "caption": (item.get("caption") or None) and item["caption"][:CAPTION_LIMIT]}

    def add_image(media_id: str, kind: str, data: bytes | None, item: dict) -> None:
        nonlocal images, image_bytes
        if data is None or images >= MAX_MEDIA_IMAGES or len(evidence) >= MAX_MEDIA:
            return
        if image_bytes + len(data) > MAX_MEDIA_IMAGE_BYTES:
            return
        evidence.append(MediaEvidence(id=media_id, kind=kind, image=ImageInput(mime_type="image/jpeg", data=data),
                                      **labels(item)))
        images += 1
        image_bytes += len(data)

    for item in items:
        if len(evidence) >= MAX_MEDIA:
            break
        if item.get("kind") != "VIDEO":
            row = rows.get(item["mediaId"])
            data = _read(row)
            if data is not None and row[0] == "IMAGE":
                add_image(f"media:{item['mediaId']}", "PHOTO", shrink_photo(data), item)
            continue
        digest = None
        if digested < MAX_DIGESTED_VIDEOS and images < MAX_MEDIA_IMAGES:
            data = _read(rows.get(item["mediaId"]))
            if data is not None:
                try:
                    digest = video_layer.digest_video(data)
                except MediaRejected as rejected:
                    logger.info("video digest refused: %s", rejected.code)
        if digest is None:  # no original (purged) or over the budget: the poster speaks for it
            poster = rows.get(item.get("posterMediaId") or "")
            data = _read(poster)
            if data is not None and poster[0] == "IMAGE":
                add_image(f"media:{item['posterMediaId']}", "PHOTO", shrink_photo(data), item)
            continue
        digested += 1
        for frame in sorted(digest.frames, key=lambda f: f.t_ms):  # already <= 1024 px JPEG (media-A)
            add_image(f"media:{item['mediaId']}@{frame.t_ms}", "VIDEO_FRAME", frame.jpeg, item)
        text = _transcript(ctx, digest.audio, digest.audio_mime)
        if text and len(evidence) < MAX_MEDIA:
            evidence.append(MediaEvidence(id=f"media:{item['mediaId']}#transcript", kind="VIDEO_TRANSCRIPT",
                                          text=text, **labels(item)))
    if not evidence:
        raise AiError(AiErrorCode.INPUT_REJECTED, detail="no_media")
    return tuple(evidence)


def evidence_query(section: dict, items: Iterable[dict]) -> str:
    """Retrieval query for the owner's words about a section (title, steps, media labels)."""
    parts = [section.get("title")]
    parts.extend(step.get("instruction") for step in section.get("steps", []))
    for item in items:
        parts.extend((item.get("title"), item.get("caption")))
    return "\n".join(part for part in parts if part)

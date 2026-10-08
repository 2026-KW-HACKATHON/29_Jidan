"""Photos and videos attached to manual sections (OpenAPI 0.12.0, docs/manual-interview-design.md
"사진·영상 기반 작성").

A video is AI input only. At upload the server derives one representative frame and stores it
as an ordinary IMAGE row (`ManualMedia.poster_media_id` on the video); workers only ever see
that poster. One attachment item, in the API and in the stored review JSON:

    photo   {"mediaId": <image>, "title", "caption"}                      (the 0.11.0 shape)
    video   {"mediaId": <video>, "kind": "VIDEO", "posterMediaId": <poster>, "title", "caption"}

Owner views (review, draft, publication response) show the item as is. Reader views (published
manual, draft preview) show `reader_item`: the poster as a plain photo. In a version's rows a
video is the attachment of its poster (`media_id`) remembering the video (`video_media_id`), so
the worker, Q&A and photo-bytes paths are unchanged. Clients may echo `kind`/`posterMediaId`
back; the server re-derives both from the file (`canonical_items`).

Linking locks the media rows (`lock_attachable`, the protocol of app.media.references): the
requested rows in ascending ID order, then their posters. A poster cannot be linked on its own
(it belongs to its video), and a video's original may already be purged by retention: the link
stays valid while the video row and its poster are alive.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.keyed import lock_by_key
from app.db.models import ManualMedia

VIDEO = "VIDEO"
VIDEO_PROCESSING_GRACE = timedelta(hours=24)  # a video original is kept this long after a writing request


class AttachError(Exception):
    """A file cannot be attached. `reason`: not_found (other store, unknown, deleted, purged, a
    poster on its own), not_image (a recording, or a video where only photos are allowed)."""

    def __init__(self, reason: str, media_id: str):
        self.reason = reason
        self.media_id = media_id
        super().__init__(reason)


def is_poster(db: Session, media_id: str) -> bool:
    return db.scalar(select(ManualMedia.id).where(ManualMedia.poster_media_id == media_id).limit(1)) is not None


def lock_attachable(db: Session, store_id: str, media_ids: Iterable[str], *,
                    videos: bool = True) -> dict[str, ManualMedia]:
    """Lock the store's attachable files (and the posters of videos) and return them by ID.

    Photos: IMAGE, same store, not deleted, bytes kept, not some video's poster. Videos (only
    with `videos=True`): VIDEO, same store, not deleted, with a live poster; the video's own
    bytes may be gone. Raises AttachError for the first file that is not attachable."""
    ordered = list(dict.fromkeys(media_ids))
    if not ordered:
        return {}
    rows = lock_by_key(db, ManualMedia, ordered)
    for media_id in ordered:
        row = rows.get(media_id)
        if row is None or row.store_id != store_id or row.deleted_at is not None:
            raise AttachError("not_found", media_id)
        if row.kind == "IMAGE":
            if row.content_deleted_at is not None or is_poster(db, media_id):
                raise AttachError("not_found", media_id)
        elif row.kind != VIDEO or not videos:
            raise AttachError("not_image", media_id)
        elif row.poster_media_id is None:
            raise AttachError("not_found", media_id)
    posters = lock_by_key(db, ManualMedia, [rows[m].poster_media_id for m in ordered if rows[m].kind == VIDEO])
    for media_id in ordered:
        row = rows[media_id]
        if row.kind == VIDEO:
            poster = posters.get(row.poster_media_id)
            if poster is None or poster.deleted_at is not None or poster.content_deleted_at is not None:
                raise AttachError("not_found", media_id)
    return {**rows, **posters}


def canonical_item(item: dict, rows: dict[str, ManualMedia]) -> dict:
    """The stored/owner shape of one item, from the locked file (client kind/poster ignored)."""
    row = rows[item["mediaId"]]
    body = {"mediaId": row.id, "title": item["title"], "caption": item["caption"]}
    if row.kind == VIDEO:
        body = {"mediaId": row.id, "kind": VIDEO, "posterMediaId": row.poster_media_id,
                "title": item["title"], "caption": item["caption"]}
    return body


def canonical_items(items: list[dict], rows: dict[str, ManualMedia]) -> list[dict]:
    return [canonical_item(item, rows) for item in items]


def reader_item(item: dict) -> dict:
    """What a worker (or the owner's worker preview) sees: the poster stands for a video."""
    return {"mediaId": item.get("posterMediaId") or item["mediaId"], "title": item["title"],
            "caption": item["caption"]}


def referenced_ids(items: Iterable[dict]) -> list[str]:
    """Every file an item list keeps alive: photos, videos and their posters."""
    ids: list[str] = []
    for item in items:
        ids.append(item["mediaId"])
        if item.get("posterMediaId"):
            ids.append(item["posterMediaId"])
    return list(dict.fromkeys(ids))


def video_ids(items: Iterable[dict]) -> list[str]:
    return [item["mediaId"] for item in items if item.get("kind") == VIDEO]


def keep_videos_for_processing(db: Session, media_ids: Iterable[str], *, now: datetime | None = None) -> None:
    """A writing request needs the video originals: keep them for VIDEO_PROCESSING_GRACE from
    now (retention purges a video original at `expires_at`, links or not; app.media.retention)."""
    now = now or utcnow()
    for media_id in sorted(set(media_ids)):
        db.execute(
            update(ManualMedia)
            .where(ManualMedia.id == media_id, ManualMedia.kind == VIDEO,
                   ManualMedia.expires_at < now + VIDEO_PROCESSING_GRACE)
            .values(expires_at=now + VIDEO_PROCESSING_GRACE)
            .execution_options(synchronize_session=False)
        )

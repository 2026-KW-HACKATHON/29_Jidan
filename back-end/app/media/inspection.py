"""What an uploaded file really is, judged from its bytes only.

`inspect_media(data, kind)` ignores the client's file name and Content-Type: it sniffs the
signature, requires a format accepted for the purpose, fully validates it (image decode or audio
container walk), enforces byte/pixel/duration limits and returns what to store.
Order of checks: empty (422) -> byte limit (413) -> format (415) -> content (422/413/415).
"""

import math
import struct
from dataclasses import dataclass, field

from app.db.models import (
    AUDIO_MIME_TYPES,
    IMAGE_MIME_TYPES,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_MILLISECONDS,
    MAX_IMAGE_BYTES,
)
from app.media.audio import DURATION_PARSERS
from app.media.errors import MediaInvalid, MediaTooLarge, MediaUnsupported
from app.media.images import normalize_image

MAX_BYTES = {"IMAGE": MAX_IMAGE_BYTES, "AUDIO": MAX_AUDIO_BYTES}
ACCEPTED = {"IMAGE": IMAGE_MIME_TYPES, "AUDIO": AUDIO_MIME_TYPES}


@dataclass(frozen=True)
class InspectedMedia:
    kind: str
    mime_type: str
    data: bytes = field(repr=False)  # what to store (re-encoded for photos, original for audio)
    duration_ms: int | None = None


def sniff(data: bytes) -> str | None:
    """The real type from leading bytes (also known-but-unsupported types, for clear 415s)."""
    head = data[:64]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and len(head) >= 12:
        return {b"WEBP": "image/webp", b"WAVE": "audio/wav", b"AVI ": "video/x-msvideo"}.get(head[8:12], "application/riff")
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif", b"avis"):
            return "image/heif"
        if brand == b"qt  ":
            return "video/quicktime"
        return "audio/mp4"  # M4A/isom/mp42/dash...; a video track is rejected later
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"  # DocType and video tracks are checked by the parser
    if head.startswith(b"ID3"):
        return "audio/mpeg"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "audio/mpeg" if head[1] & 0x06 else "audio/aac"  # layer bits 00 = ADTS AAC
    for signature, mime in (
        (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"), (b"BM", "image/bmp"),
        (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff"), (b"%PDF", "application/pdf"),
        (b"OggS", "audio/ogg"), (b"fLaC", "audio/flac"), (b"PK\x03\x04", "application/zip"),
    ):
        if head.startswith(signature):
            return mime
    if head.lstrip()[:1] == b"<":
        return "text/markup"  # SVG, HTML, XML
    return None


def inspect_media(data: bytes, kind: str) -> InspectedMedia:
    if not data:
        raise MediaInvalid("empty")
    if len(data) > MAX_BYTES[kind]:
        raise MediaTooLarge("bytes")
    mime_type = sniff(data)
    if mime_type not in ACCEPTED[kind]:
        raise MediaUnsupported(f"type:{mime_type or 'unknown'}")
    if kind == "IMAGE":
        return InspectedMedia(kind, mime_type, normalize_image(data, mime_type, MAX_IMAGE_BYTES))
    try:
        duration = DURATION_PARSERS[mime_type](data)
    except (struct.error, IndexError, ValueError, OverflowError, ZeroDivisionError) as error:
        # The parsers reject damage themselves (fuzz-tested); this keeps a missed case of
        # untrusted bytes a 422 instead of a 500.
        raise MediaInvalid(f"audio_parse:{type(error).__name__}") from error
    if duration * 1000 > MAX_AUDIO_MILLISECONDS:
        raise MediaTooLarge("duration")
    if duration <= 0:
        raise MediaInvalid("silent_container")
    return InspectedMedia(kind, mime_type, data, max(1, math.ceil(duration * 1000)))

"""STUB(media-B) — placeholder for the video layer owned by media-A; REPLACE ON MERGE.

Only the interface agreed in the orchestrator brief (BRIEF-MEDIA.md, "미디어 계층") so that the
upload route and the media-writing tasks (media-B) import and type-check. The real module
decodes with PyAV; this stub decodes nothing: both functions raise `MediaUnsupported`, so a
MANUAL_VIDEO upload is a 415 until A's implementation lands. Tests replace the functions with
monkeypatch. Do not add behaviour here.
"""

from dataclasses import dataclass

from app.media.errors import MediaUnsupported
from app.media.inspection import InspectedMedia

MAX_VIDEO_FRAMES = 8


@dataclass(frozen=True)
class VideoFrame:
    t_ms: int
    jpeg: bytes  # <= 1024 px long side, no EXIF


@dataclass(frozen=True)
class VideoDigest:
    duration_ms: int
    frames: tuple[VideoFrame, ...]  # evenly sampled, at most MAX_VIDEO_FRAMES
    poster: VideoFrame  # representative frame, stored as the photo workers see
    audio: bytes | None  # a format gpt-transcribe accepts, None without (audible) audio
    audio_mime: str | None


def inspect_video(data: bytes) -> InspectedMedia:  # pragma: no cover - replaced by media-A
    raise MediaUnsupported("video:not_implemented")


def digest_video(data: bytes) -> VideoDigest:  # pragma: no cover - replaced by media-A
    raise MediaUnsupported("video:not_implemented")

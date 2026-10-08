"""Owner manual videos (purpose MANUAL_VIDEO): upload checks and the digest the AI reads.

The language model takes text and images only, so a video is never sent as is. `digest_video`
turns it into evenly spaced still frames, a poster frame that workers see as the section photo
(stored as a derived MANUAL_PHOTO by `store_video_poster`) and the sound track as 16 kHz mono
PCM WAV for transcription. The original bytes are only AI input and are purged by retention.

Formats: MP4 (H.264/HEVC), QuickTime MOV (H.264/HEVC) and WebM (VP8/VP9/AV1); at most
`MAX_VIDEO_BYTES` (100 MiB), `MAX_VIDEO_MILLISECONDS` (60 s) and `MAX_VIDEO_PIXELS` per frame.
The type is judged from the bytes (signature, then the demuxer), never from the file name or
Content-Type. A file without a video track (an M4A/WebM recording) is not a video: 415.

Decoding uses PyAV (FFmpeg). Untrusted input is opened with an explicit demuxer ("mov" or
"matroska") from memory only, so FFmpeg never probes other formats (HLS/concat playlists could
open URLs or files) and the mov demuxer's external data references stay disabled (its default).
The duration is measured from the demuxed packets, not the header (a forged header cannot
pass a 10 minute clip as 10 s), and the packet count bounds decoding work.

Poster heuristic: among the sampled frames, ignore frames that are almost black (mean luma
< 24) or almost flat (luma standard deviation < 8, e.g. a lens cap or a white wall); of the
rest take the sharpest one (largest standard deviation of an edge-filtered 160 px grayscale
copy), the earliest on a tie. If every frame is dark/flat the brightest, then most varied, wins.
"""

import io
import math
import threading
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction

import av
from PIL import Image, ImageFilter, ImageStat
from sqlalchemy.orm import Session

from app.ai.silence import pcm_wav_is_silent
from app.db import new_uuid, utcnow
from app.db.models import MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, MAX_VIDEO_MILLISECONDS, ManualMedia
from app.media.audio import DOC_TYPE, EBML_HEADER, SEGMENT, _element_id, _element_size, _elements
from app.media.errors import MediaInvalid, MediaTooLarge, MediaUnsupported
from app.media.inspection import InspectedMedia, sniff
from app.media.retention import UNATTACHED_TTL
from app.media.storage import get_media_storage, object_key

MAX_VIDEO_FRAMES = 8           # frames handed to the model per video
SECONDS_PER_FRAME = 2          # one sample per started 2 s (a 5 s clip gets 3), capped above
MAX_FRAME_SIDE = 1024          # long side of every sampled frame and of the poster
FRAME_JPEG_QUALITY = 85
MAX_VIDEO_PIXELS = 40_000_000  # same budget as photos (app.media.images.MAX_PIXELS)
MAX_VIDEO_PACKETS = 60 * 240   # 60 s at 240 fps (slow motion); more is a decoding bomb
AUDIO_RATE = 16_000
AUDIO_MIME = "audio/wav"
DECODE_THREADS = 2
DECODE_CONCURRENCY = 1         # whole-video decodes per process (memory and CPU heavy)
_digest_slots = threading.BoundedSemaphore(DECODE_CONCURRENCY)

# Real container (from the signature) -> FFmpeg demuxer, accepted video codecs.
DEMUXERS = {"video/mp4": "mov", "video/quicktime": "mov", "video/webm": "matroska"}
VIDEO_CODECS = {
    "video/mp4": {"h264", "hevc"},
    "video/quicktime": {"h264", "hevc"},
    "video/webm": {"vp8", "vp9", "av1"},
}
# Codec of the demuxed stream (codec parameters, not the decoder implementation's name).
_DECODER_ALIASES = {"libdav1d": "av1", "libvpx": "vp8", "libvpx-vp9": "vp9"}

# Below these a frame is "black" / "flat" for poster selection (0-255 luma).
POSTER_MIN_MEAN = 24
POSTER_MIN_STDDEV = 8
_SCORE_SIDE = 160


@dataclass(frozen=True)
class VideoFrame:
    t_ms: int                         # from the start of the video
    jpeg: bytes = field(repr=False)   # <= MAX_FRAME_SIDE long side, no EXIF/metadata


@dataclass(frozen=True)
class VideoDigest:
    duration_ms: int
    frames: tuple[VideoFrame, ...]    # evenly spaced, 1..MAX_VIDEO_FRAMES, in time order
    poster: VideoFrame                # one of `frames`; workers see it as the section photo
    audio: bytes | None = field(default=None, repr=False)   # 16 kHz mono PCM WAV
    audio_mime: str | None = None     # AUDIO_MIME, or None: no sound track or digital silence


def video_container(data: bytes) -> str | None:
    """The accepted video container this signature announces, or None. ISO BMFF other than
    QuickTime (isom, mp42, M4A, 3gp...) is MP4 here; whether it has video is checked later."""
    sniffed = sniff(data)
    if sniffed == "video/quicktime":
        return sniffed
    if sniffed == "audio/mp4":
        return "video/mp4"
    if sniffed == "audio/webm":
        return "video/webm" if _ebml_doc_type(data) == b"webm" else None
    return None


def _ebml_doc_type(data: bytes) -> bytes | None:
    """DocType of an EBML header ("webm" vs "matroska"); only the first 4 KiB are read."""
    head = data[:4096]
    try:
        header = next(_elements(head, 0, len(head)), None)
        if header is None or header[0] != EBML_HEADER:
            return None
        for element_id, body, end in _elements(head, header[1], header[2]):
            if element_id == DOC_TYPE:
                return head[body:end].rstrip(b"\x00")
    except (MediaInvalid, IndexError, ValueError):
        return None
    return None


def _webm_truncated(data: bytes) -> bool:
    """A Segment of known size that ends past the file. (Browser recordings write "unknown
    size"; a cut one of those just plays shorter, which the packet timeline measures.)"""
    try:
        header = next(_elements(data, 0, min(len(data), 4096)))
        position = header[2]
        segment_id, id_length = _element_id(data, position)
        size, _length = _element_size(data, position + id_length)
    except (MediaInvalid, StopIteration, IndexError, ValueError):
        return True
    return segment_id == SEGMENT and size is not None and position + id_length + _length + size > len(data)


@contextmanager
def _open(data: bytes, mime_type: str) -> Iterator[av.container.InputContainer]:
    try:
        container = av.open(io.BytesIO(data), "r", format=DEMUXERS[mime_type])
    except av.FFmpegError:
        raise MediaInvalid("video_open") from None
    try:
        yield container
    finally:
        container.close()


def _codec_name(stream) -> str:
    context = stream.codec_context  # None when FFmpeg has no decoder for the tag (forged fourcc)
    name = context.name if context is not None else "unknown"
    return _DECODER_ALIASES.get(name, name)


def _video_stream(container, mime_type: str):
    streams = container.streams.video
    if not streams:
        raise MediaUnsupported("no_video_track")
    stream = streams[0]
    if _codec_name(stream) not in VIDEO_CODECS[mime_type]:
        raise MediaUnsupported(f"video_codec:{_codec_name(stream)}")
    width, height = stream.codec_context.width, stream.codec_context.height
    if width < 1 or height < 1:
        raise MediaInvalid("video_dimensions")
    if width * height > MAX_VIDEO_PIXELS:
        raise MediaTooLarge("video_pixels")
    return stream


@dataclass(frozen=True)
class _Timeline:
    start: Fraction        # earliest timestamp of any packet (seconds)
    duration: Fraction     # last packet end - start (seconds)
    video_packets: int


def _timeline(container, video_stream) -> _Timeline:
    """Measure the real length of the video track from its packets; the header is not trusted.
    (The sound track is cut to this length when digested; AAC priming does not count.)"""
    first: Fraction | None = None
    last: Fraction | None = None
    video_packets = 0
    for packet in container.demux(video_stream):
        if packet.size == 0 or packet.pts is None or packet.time_base is None:
            continue  # flush packet / no timestamp
        video_packets += 1
        if video_packets > MAX_VIDEO_PACKETS:
            raise MediaTooLarge("video_packets")
        begin = packet.pts * packet.time_base
        end = (packet.pts + max(packet.duration or 0, 0)) * packet.time_base
        first = begin if first is None else min(first, begin)
        last = end if last is None else max(last, end)
    if not video_packets or first is None or last is None or last <= first:
        raise MediaInvalid("video_empty")
    declared = video_stream.frames  # MP4/MOV sample tables list every frame; WebM says 0
    if declared and video_packets < declared:
        raise MediaInvalid("video_truncated")
    return _Timeline(first, last - first, video_packets)


def _samples_past_end(container, size: int) -> bool:
    """MP4/MOV: a sample table entry pointing past the end of the file (a cut upload whose
    index sits in front; the demuxer would hand out a short last packet without complaint)."""
    for stream in (*container.streams.video[:1], *container.streams.audio[:1]):
        for entry in stream.index_entries:
            if entry.pos + entry.size > size:
                return True
    return False


def _check_frame_size(frame) -> None:
    """The decoded size, not only the declared one (a forged header could claim a small frame)."""
    if frame.width * frame.height > MAX_VIDEO_PIXELS:
        raise MediaTooLarge("video_pixels")


def _first_frame(container, stream):
    for frame in container.decode(stream):
        _check_frame_size(frame)
        return frame
    raise MediaInvalid("video_undecodable")


def _configure(stream) -> None:
    stream.codec_context.thread_count = DECODE_THREADS
    stream.codec_context.thread_type = "AUTO"


def _milliseconds(seconds: Fraction) -> int:
    return max(1, math.ceil(seconds * 1000))


_PARSE_ERRORS = (EOFError, ValueError, OverflowError, ZeroDivisionError, MemoryError)


def _container_type(data: bytes) -> str:
    """Checks that need no demuxer: empty 422 -> bytes 413 -> real format 415 -> cut WebM 422."""
    if not data:
        raise MediaInvalid("empty")
    if len(data) > MAX_VIDEO_BYTES:
        raise MediaTooLarge("bytes")
    mime_type = video_container(data)
    if mime_type is None:
        sniffed = sniff(data)
        raise MediaUnsupported(f"type:{'matroska' if sniffed == 'audio/webm' else sniffed or 'unknown'}")
    if mime_type == "video/webm" and _webm_truncated(data):
        raise MediaInvalid("video_truncated")
    return mime_type


def _checked_stream(container, data: bytes, mime_type: str):
    """The video stream and its measured timeline, after every content check of an upload."""
    stream = _video_stream(container, mime_type)
    if mime_type != "video/webm" and _samples_past_end(container, len(data)):
        raise MediaInvalid("video_truncated")
    timeline = _timeline(container, stream)
    if timeline.duration * 1000 > MAX_VIDEO_MILLISECONDS:
        raise MediaTooLarge("duration")
    _configure(stream)
    return stream, timeline


def inspect_video(data: bytes) -> InspectedMedia:
    """Validate an upload for MANUAL_VIDEO. Same order and error codes as `inspect_media`:
    empty 422 -> bytes 413 -> real format 415 -> content (corrupt 422, length/pixels 413,
    no video track or another codec 415). The original bytes are what gets stored."""
    mime_type = _container_type(data)
    try:
        with _open(data, mime_type) as container:
            stream, timeline = _checked_stream(container, data, mime_type)
            container.seek(0)
            _first_frame(container, stream)
    except av.FFmpegError:
        raise MediaInvalid("video_decode") from None
    except _PARSE_ERRORS as error:
        raise MediaInvalid(f"video_parse:{type(error).__name__}") from None
    return InspectedMedia("VIDEO", mime_type, data, _milliseconds(timeline.duration))


# --- digest -------------------------------------------------------------------------------------


def sample_times(duration_ms: int) -> list[int]:
    """Evenly spaced sample times: the middles of `count` equal parts (never the very first
    or last frame, which are often a finger on the button or a fade)."""
    count = max(1, min(MAX_VIDEO_FRAMES, math.ceil(duration_ms / (SECONDS_PER_FRAME * 1000))))
    return [round(duration_ms * (2 * index + 1) / (2 * count)) for index in range(count)]


def _image(frame) -> Image.Image:
    image = frame.to_image()  # RGB, pixel data only: container/stream metadata never comes along
    rotation = (frame.rotation or 0) % 360  # display matrix, counter-clockwise degrees
    if rotation:
        image = image.rotate(rotation, expand=True)
    if max(image.size) > MAX_FRAME_SIDE:
        image.thumbnail((MAX_FRAME_SIDE, MAX_FRAME_SIDE), Image.Resampling.LANCZOS)
    return image


def _jpeg(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "JPEG", quality=FRAME_JPEG_QUALITY, optimize=True)
    return output.getvalue()


def _pick_nearest(data: bytes, mime_type: str, start: Fraction, targets: list[int], *,
                  keyframes_only: bool) -> tuple[list[tuple[int, Image.Image]], int]:
    """One decoding pass over a freshly opened container (some decoders, e.g. libdav1d, apply
    `skip_frame` only when they are opened, so a pass never reuses another pass's decoder).
    For each target time the decoded frame closest to it: ([(t_ms, picture)], largest
    distance to its target in ms). At most len(targets) frames stay decoded meanwhile."""
    with _open(data, mime_type) as container:
        stream = container.streams.video[0]
        _configure(stream)
        stream.codec_context.skip_frame = "NONKEY" if keyframes_only else "DEFAULT"
        best = _nearest_frames(container, stream, start, targets)
        chosen: dict[int, object] = {}
        for item in best:
            if item is not None:
                chosen.setdefault(item[1], item[2])
        worst = max((item[0] for item in best if item is not None), default=0)
        return [(t_ms, _image(frame)) for t_ms, frame in sorted(chosen.items())], worst


def _nearest_frames(container, stream, start: Fraction, targets: list[int]):
    best: list[tuple[int, int, object] | None] = [None] * len(targets)  # (distance, t_ms, frame)
    for frame in container.decode(stream):
        if frame.pts is None or frame.time_base is None:
            continue
        _check_frame_size(frame)
        t_ms = max(0, round((frame.pts * frame.time_base - start) * 1000))
        for index, target in enumerate(targets):
            distance = abs(t_ms - target)
            if best[index] is None or distance < best[index][0]:
                best[index] = (distance, t_ms, frame)
        if t_ms > targets[-1] and all(best):
            break  # every later frame is farther from every target
    return best


def _sample_frames(data: bytes, mime_type: str, start: Fraction,
                   duration_ms: int) -> list[tuple[int, Image.Image]]:
    """Keyframes first (cheap: phones write one every 1-2 s). When a keyframe is more than a
    quarter of a sample interval away from its target (long GOP: screen recordings, short clips
    with one keyframe at 0 s) or two targets share one, every frame is decoded instead."""
    targets = sample_times(duration_ms)
    tolerance = duration_ms / len(targets) / 4
    chosen, worst = _pick_nearest(data, mime_type, start, targets, keyframes_only=True)
    if len(chosen) < len(targets) or worst > tolerance:
        chosen, _worst = _pick_nearest(data, mime_type, start, targets, keyframes_only=False)
    if not chosen:
        raise MediaInvalid("video_undecodable")
    return chosen


def _frame_scores(image: Image.Image) -> tuple[float, float, float]:
    """(mean luma, luma stddev, edge stddev) of a small grayscale copy."""
    gray = image.convert("L")
    gray.thumbnail((_SCORE_SIDE, _SCORE_SIDE))
    stats = ImageStat.Stat(gray)
    edges = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
    return stats.mean[0], stats.stddev[0], edges.stddev[0]


def choose_poster(images: list[Image.Image]) -> int:
    """Index of the poster frame (module docstring: not black, not flat, then sharpest)."""
    scores = [_frame_scores(image) for image in images]
    usable = [i for i, (mean, stddev, _edge) in enumerate(scores)
              if mean >= POSTER_MIN_MEAN and stddev >= POSTER_MIN_STDDEV]
    if usable:
        return max(usable, key=lambda i: (scores[i][2], -i))
    return max(range(len(images)), key=lambda i: (scores[i][0], scores[i][1], -i))


def _audio_wav(data: bytes, mime_type: str, duration: Fraction) -> bytes | None:
    """The first sound track as 16 kHz mono 16-bit PCM WAV, cut at the video's length; None when
    there is no sound track, it cannot be decoded, or it is digital silence (nothing to transcribe)."""
    with _open(data, mime_type) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=AUDIO_RATE)
        limit = int(duration * AUDIO_RATE)
        pcm = bytearray()
        if stream.codec_context is None:
            return None  # no decoder for this sound track: frames only
        try:
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    pcm += bytes(out.planes[0])[: out.samples * 2]
                if len(pcm) >= limit * 2:
                    break
            for out in resampler.resample(None):
                pcm += bytes(out.planes[0])[: out.samples * 2]
        except (av.FFmpegError, ValueError):
            if not pcm:
                return None  # a broken sound track does not stop the frames
    del pcm[limit * 2:]
    if not pcm:
        return None
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(AUDIO_RATE)
        wav.writeframes(bytes(pcm))
    wav_bytes = output.getvalue()
    return None if pcm_wav_is_silent(wav_bytes) else wav_bytes


def digest_video(data: bytes) -> VideoDigest:
    """Frames, poster and sound of a video that passed `inspect_video` (run it in a background
    task, outside a request transaction: a 60 s 4K clip takes seconds of CPU). Runs the same
    checks as `inspect_video` (same MediaRejected reasons) and raises MediaInvalid when no
    frame can be decoded. A damaged or undecodable sound track only drops `audio`."""
    mime_type = _container_type(data)
    with _digest_slots:
        try:
            with _open(data, mime_type) as container:
                _stream, timeline = _checked_stream(container, data, mime_type)
            duration_ms = _milliseconds(timeline.duration)
            sampled = _sample_frames(data, mime_type, timeline.start, duration_ms)
            audio = _audio_wav(data, mime_type, timeline.duration)
        except av.FFmpegError:
            raise MediaInvalid("video_decode") from None
        except _PARSE_ERRORS as error:
            raise MediaInvalid(f"video_parse:{type(error).__name__}") from None
    frames = tuple(VideoFrame(t_ms, _jpeg(image)) for t_ms, image in sampled)
    poster = frames[choose_poster([image for _t, image in sampled])]
    return VideoDigest(duration_ms, frames, poster, audio, AUDIO_MIME if audio else None)


# --- poster photo -------------------------------------------------------------------------------


def store_video_poster(db: Session, video: ManualMedia, poster: VideoFrame, *,
                       now: datetime | None = None) -> ManualMedia:
    """Store `poster` as a new owner photo (ManualMedia kind IMAGE, image/jpeg) of the video's
    store and owner, and return the flushed row. From then on it is an ordinary MANUAL_PHOTO:
    link it with `lock_photos_for_link` like any photo; unlinked, it is purged after 24 h.

    Call it in the transaction that records the link from the video to its poster. The file is
    written before the row commits; if that transaction rolls back, the orphan sweep removes
    the file (or delete `row.object_key` yourself)."""
    if not poster.jpeg or len(poster.jpeg) > MAX_IMAGE_BYTES:  # 1024 px JPEG: never near 10 MiB
        raise MediaInvalid("poster_bytes")
    now = now or utcnow()
    media_id = new_uuid()
    location = object_key("manual", video.store_id, media_id)
    get_media_storage().write(location, poster.jpeg)
    row = ManualMedia(
        id=media_id, store_id=video.store_id, uploaded_by_owner_id=video.uploaded_by_owner_id,
        kind="IMAGE", object_key=location, mime_type="image/jpeg", byte_size=len(poster.jpeg),
        duration_ms=None, created_at=now, expires_at=now + UNATTACHED_TTL,
    )
    db.add(row)
    db.flush()
    return row

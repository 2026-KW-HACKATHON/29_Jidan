"""Owner manual videos (MANUAL_VIDEO): upload checks, digest (frames, poster, sound), poster
photo and retention hold. Every clip is encoded in the test with PyAV (tests/video_samples.py)."""

import io
import struct
import uuid
import wave
from datetime import timedelta

import pytest
from PIL import Image, ImageStat
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import MAX_VIDEO_BYTES, ManualMedia
from app.media import retention, video
from app.media.errors import MediaInvalid, MediaRejected, MediaTooLarge, MediaUnsupported
from app.media.inspection import inspect_media
from app.media.references import lock_photos_for_link
from app.media.storage import LocalMediaStorage, set_media_storage
from app.media.video import (
    AUDIO_MIME,
    AUDIO_RATE,
    MAX_FRAME_SIDE,
    MAX_VIDEO_FRAMES,
    VideoDigest,
    VideoFrame,
    choose_poster,
    digest_video,
    inspect_video,
    sample_times,
    store_video_poster,
)
from tests import media_samples
from tests import video_samples as samples
from tests.factories import NOW, make_store, make_user


def reject(data, error, reason=None):
    with pytest.raises(error) as caught:
        inspect_video(data)
    if reason:
        assert caught.value.reason == reason
    return caught.value


def picture(jpeg: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(jpeg))
    image.load()
    return image


def stripe_colour(jpeg: bytes) -> int:
    """Red value of the first stripe: video_samples paints it (frame index * 37) % 256."""
    return picture(jpeg).convert("RGB").getpixel((1, 1))[0]


# --- upload checks ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind,mime", [
    ("mp4", "video/mp4"), ("mp4-hevc", "video/mp4"), ("mov", "video/quicktime"),
    ("mov-hevc", "video/quicktime"), ("webm", "video/webm"), ("webm-vp8", "video/webm"),
    ("webm-av1", "video/webm"),
])
@pytest.mark.parametrize("audio", ["tone", None])
def test_supported_videos_are_accepted_by_content_and_stored_as_uploaded(kind, mime, audio):
    data = samples.video(kind, seconds=2, audio=audio)
    for result in (inspect_video(data), inspect_media(data, "VIDEO")):
        assert (result.kind, result.mime_type, result.duration_ms) == ("VIDEO", mime, 2000)
        assert result.data == data


def test_exactly_sixty_seconds_is_accepted_and_longer_is_too_large():
    tiny = {"width": 16, "height": 16, "audio": None}
    assert inspect_video(samples.video(seconds=60, fps=1, **tiny)).duration_ms == 60_000
    reject(samples.video(seconds=60.25, fps=4, **tiny), MediaTooLarge, "duration")
    reject(samples.video(seconds=61, fps=1, **tiny), MediaTooLarge, "duration")
    reject(samples.video("webm", seconds=61, fps=1, **tiny), MediaTooLarge, "duration")


def _forge_mp4_durations(data: bytes, value: int) -> bytes:
    """Rewrite every mvhd/mdhd/tkhd duration (version 0) to `value` timescale units."""
    forged = bytearray(data)
    for box, offset in ((b"mvhd", 16), (b"mdhd", 16), (b"tkhd", 20)):
        position = forged.find(box)
        while position != -1:
            assert forged[position + 4] == 0  # version 0 boxes from the muxer
            struct.pack_into(">I", forged, position + 4 + offset, value)
            position = forged.find(box, position + 4)
    return bytes(forged)


def test_a_forged_short_header_duration_does_not_hide_a_long_video():
    data = samples.video(seconds=61, fps=1, width=16, height=16, audio=None)
    forged = _forge_mp4_durations(data, 1)
    assert forged != data
    reject(forged, MediaTooLarge, "duration")


def test_byte_limit_is_exact():
    base = samples.video(seconds=1, audio=None)
    padding = MAX_VIDEO_BYTES - len(base)
    exact = base + struct.pack(">I", padding) + b"free" + b"\x00" * (padding - 8)  # trailing free box
    assert len(exact) == MAX_VIDEO_BYTES
    assert inspect_video(exact).duration_ms == 1000
    reject(exact + b"\x00", MediaTooLarge, "bytes")


def test_empty_file_is_invalid():
    reject(b"", MediaInvalid, "empty")
    with pytest.raises(MediaInvalid):
        inspect_media(b"", "VIDEO")


@pytest.mark.parametrize("data,reason", [
    (samples.audio_only("mp4"), "no_video_track"),           # M4A recording
    (samples.audio_only("webm"), "no_video_track"),          # browser voice recording
    (media_samples.mp4(), "no_video_track"),                 # hand-built M4A header
    (samples.other_container("avi", "mpeg4"), "type:video/x-msvideo"),
    (samples.other_container("matroska", "h264"), "type:matroska"),
    (samples.other_container("mpegts", "h264"), "type:unknown"),
    (samples.other_container("mp4", "mpeg4"), "video_codec:mpeg4"),  # MP4 Part 2, not H.264/HEVC
    (media_samples.gif(), "type:image/gif"),
    (media_samples.jpeg(), "type:image/jpeg"),
    (media_samples.wav_seconds(1), "type:audio/wav"),
    (b"plain text named clip.mp4", "type:unknown"),
])
def test_other_formats_and_files_without_video_are_unsupported(data, reason):
    reject(data, MediaUnsupported, reason)


def test_heif_photo_is_not_a_video():
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
    reject(heic, MediaUnsupported, "type:image/heif")


def test_videos_are_rejected_for_photo_and_recording_purposes():
    data = samples.video(seconds=1)
    for kind in ("IMAGE", "AUDIO"):
        with pytest.raises(MediaRejected) as caught:
            inspect_media(data, kind)
        assert caught.value.status_code == 415
    with pytest.raises(MediaUnsupported) as caught:
        inspect_media(samples.video("webm", seconds=1), "AUDIO")
    assert caught.value.reason == "video_track"


def test_quicktime_without_ftyp_is_not_accepted():
    # Pre-2001 QuickTime files start with moov/mdat/wide; phones always write ftyp.
    data = samples.video("mov", seconds=1)
    assert data[4:8] == b"ftyp"
    reject(data[data.find(b"wide") - 4:], MediaUnsupported, "type:unknown")


@pytest.mark.parametrize("kind,fourcc", [("mp4", b"avc1"), ("mov", b"avc1"), ("mp4-hevc", b"hev1")])
def test_a_codec_tag_ffmpeg_cannot_decode_is_unsupported_not_a_crash(kind, fourcc):
    data = samples.video(kind, seconds=1, audio=None)
    if fourcc not in data:
        pytest.fail(f"{fourcc!r} not written by the muxer")
    forged = data.replace(fourcc, b"zzzz")
    reject(forged, MediaUnsupported, "video_codec:unknown")
    with pytest.raises(MediaUnsupported):
        digest_video(forged)


def test_truncated_and_corrupt_videos_are_invalid(tmp_path):
    moov_last = samples.video(seconds=2)
    reject(moov_last[: len(moov_last) // 2], MediaInvalid)               # index at the end is gone
    reject(moov_last[:40] + b"\x00" * 2000, MediaInvalid)                  # signature, garbage body
    faststart = samples.video(seconds=2, options={"movflags": "faststart"}, path=tmp_path / "a.mp4")
    assert faststart.find(b"moov") < faststart.find(b"mdat")
    reject(faststart[:-10], MediaInvalid, "video_truncated")               # last frame cut short
    reject(faststart[: len(faststart) // 2], MediaInvalid, "video_truncated")
    webm = samples.video("webm", seconds=2)
    reject(webm[: len(webm) // 2], MediaInvalid, "video_truncated")
    reject(webm[:60], MediaInvalid)
    corrupt = bytearray(moov_last)
    middle = moov_last.find(b"mdat") + 200
    corrupt[middle:middle + 4000] = b"\xff" * 4000
    try:
        inspect_video(bytes(corrupt))
    except MediaRejected as rejected:
        assert rejected.status_code == 422
    else:  # the first frame survived: the digest may conceal the damage, but never crash
        try:
            digest_video(bytes(corrupt))
        except MediaRejected as rejected:
            assert rejected.status_code == 422


def test_pixel_and_frame_count_budgets(monkeypatch):
    data = samples.video(seconds=2, audio=None)
    monkeypatch.setattr(video, "MAX_VIDEO_PIXELS", 64 * 48 - 1)
    reject(data, MediaTooLarge, "video_pixels")
    monkeypatch.setattr(video, "MAX_VIDEO_PIXELS", 64 * 48)
    assert inspect_video(data).duration_ms == 2000
    monkeypatch.setattr(video, "MAX_VIDEO_PACKETS", 7)  # 2 s at 4 fps = 8 frames
    reject(data, MediaTooLarge, "video_packets")


# --- digest -------------------------------------------------------------------------------------


@pytest.mark.parametrize("duration_ms,expected", [
    (1, [0]), (1000, [500]), (2000, [1000]), (2001, [500, 1501]), (5000, [833, 2500, 4167]),
    (60_000, [3750, 11250, 18750, 26250, 33750, 41250, 48750, 56250]),
])
def test_sample_times_are_evenly_spaced_and_capped(duration_ms, expected):
    assert sample_times(duration_ms) == expected
    assert len(sample_times(duration_ms)) <= MAX_VIDEO_FRAMES


@pytest.mark.parametrize("kind", ["mp4", "mov", "webm", "webm-vp8", "webm-av1", "mp4-hevc", "mov-hevc"])
def test_digest_samples_frames_poster_and_sound(kind):
    data = samples.video(kind, seconds=5, fps=4)
    digest = digest_video(data)
    assert isinstance(digest, VideoDigest) and digest.duration_ms == 5000
    assert [frame.t_ms for frame in digest.frames] == [750, 2500, 4250]  # nearest 4 fps frames
    assert digest.poster in digest.frames
    for frame in digest.frames:
        assert isinstance(frame, VideoFrame)
        image = picture(frame.jpeg)
        assert image.format == "JPEG" and image.size == (64, 48)
        assert not image.getexif() and b"Exif" not in frame.jpeg
    assert digest.audio_mime == AUDIO_MIME
    with wave.open(io.BytesIO(digest.audio)) as sound:
        assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate()) == (1, 2, AUDIO_RATE)
        assert sound.getnframes() == 5 * AUDIO_RATE  # cut at the video's length


@pytest.mark.parametrize("gop", [None, 4])  # one keyframe (full decode) / one per second
def test_sampled_frames_show_the_picture_at_their_time(gop):
    digest = digest_video(samples.video(seconds=16, fps=4, gop=gop, audio=None))
    times = [frame.t_ms for frame in digest.frames]
    assert len(times) == MAX_VIDEO_FRAMES and times == sorted(times)
    for frame in digest.frames:
        index = frame.t_ms * 4 // 1000
        assert abs(stripe_colour(frame.jpeg) - (index * 37) % 256) <= 12, frame.t_ms
    if gop:
        assert all(frame.t_ms % 1000 == 0 for frame in digest.frames)  # keyframes only


@pytest.mark.parametrize("seconds,gop,expected", [
    (2, 8, [1000]),                   # one keyframe at 0 s: the middle frame, not the first
    (4, 8, [1000, 3000]),             # keyframes every 2 s are 1 s off target: full decode
    (8, 4, [1000, 3000, 5000, 7000]),  # keyframe every second: exact
])
def test_far_keyframes_fall_back_to_the_exact_frames(seconds, gop, expected):
    digest = digest_video(samples.video(seconds=seconds, fps=4, gop=gop, audio=None))
    assert [frame.t_ms for frame in digest.frames] == expected


def test_few_long_frames_give_fewer_samples_without_duplicates():
    digest = digest_video(samples.video(seconds=16, fps=1, gop=100, audio=None))
    assert [frame.t_ms for frame in digest.frames] == [1000, 3000, 5000, 7000, 9000, 11000, 13000, 15000]
    one = digest_video(samples.video(seconds=1, fps=1, audio=None))
    assert len(one.frames) == 1 and one.poster == one.frames[0] and one.duration_ms == 1000


@pytest.mark.parametrize("audio", [None, "silent"])
def test_no_sound_track_or_digital_silence_has_no_audio(audio):
    digest = digest_video(samples.video(seconds=2, audio=audio))
    assert (digest.audio, digest.audio_mime) == (None, None)


def test_webm_opus_sound_is_converted_to_wav():
    digest = digest_video(samples.video("webm", seconds=2))
    assert digest.audio.startswith(b"RIFF") and digest.audio_mime == "audio/wav"


def test_poster_skips_black_frames():
    digest = digest_video(samples.video(seconds=8, fps=4, pattern="fade", audio=None))
    assert [frame.t_ms for frame in digest.frames] == [1000, 3000, 5000, 7000]
    assert digest.poster.t_ms >= 4000
    assert ImageStat.Stat(picture(digest.poster.jpeg).convert("L")).mean[0] > 100


def test_all_black_video_still_has_a_poster():
    digest = digest_video(samples.video(seconds=4, pattern="black", audio=None))
    assert digest.poster == digest.frames[0]


def test_choose_poster_prefers_sharp_over_flat_dark_or_blurred():
    sharp = picture(digest_video(samples.video(seconds=1, fps=1, audio=None)).frames[0].jpeg)
    black = Image.new("RGB", sharp.size)
    grey = Image.new("RGB", sharp.size, (128, 128, 128))
    blurred = sharp.resize((8, 6)).resize(sharp.size)
    assert choose_poster([black, grey, blurred, sharp]) == 3
    assert choose_poster([sharp, blurred]) == 0
    assert choose_poster([black, Image.new("RGB", sharp.size, (20, 20, 20))]) == 1  # brightest


def _green_corner(jpeg: bytes) -> str:
    """Where the green square (painted in the upper-left quarter of the 64x48 source) ended up."""
    image = picture(jpeg).convert("RGB")
    width, height = image.size
    corners = {"upper-left": (0.375, 0.375), "upper-right": (0.625, 0.375),
               "lower-left": (0.375, 0.625), "lower-right": (0.625, 0.625)}
    for name, (x, y) in corners.items():
        r, g, b = image.getpixel((int(width * x), int(height * y)))
        if g > 150 and r < 100 and b < 100:
            return name
    return "none"


@pytest.mark.parametrize("rotation,size,corner", [
    (None, (64, 48), "upper-left"),
    (90, (48, 64), "lower-left"),     # display matrix: 90 degrees counter-clockwise
    (-90, (48, 64), "upper-right"),   # iPhone portrait (clockwise)
    (180, (64, 48), "lower-right"),
])
def test_rotation_is_applied_in_the_right_direction(rotation, size, corner):
    digest = digest_video(samples.video(seconds=1, fps=2, rotation=rotation, audio=None))
    assert picture(digest.poster.jpeg).size == size
    assert _green_corner(digest.poster.jpeg) == corner


def test_large_frames_are_scaled_down():
    large = digest_video(samples.video(seconds=1, fps=2, width=2000, height=1200, audio=None))
    assert picture(large.poster.jpeg).size == (MAX_FRAME_SIDE, 614)


def test_container_metadata_never_reaches_the_frames():
    data = samples.video("mov", seconds=1, metadata={"title": "secret title", "comment": "secret note"})
    assert b"secret title" in data
    digest = digest_video(data)
    for frame in (*digest.frames, digest.poster):
        assert b"secret" not in frame.jpeg and not picture(frame.jpeg).getexif()


@pytest.mark.parametrize("data,error", [
    (b"", MediaInvalid),
    (samples.audio_only("mp4"), MediaUnsupported),
    (samples.video(seconds=61, fps=1, width=16, height=16, audio=None), MediaTooLarge),
    (samples.video(seconds=2)[:3000], MediaInvalid),
])
def test_digest_rejects_what_upload_would(data, error):
    with pytest.raises(error):
        digest_video(data)


# --- poster photo and retention -----------------------------------------------------------------


@pytest.fixture
def media_env(db_engine, tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    with Session(db_engine) as db:
        store = make_store(db, owner=make_user(db, "OWNER"), approval_status="APPROVED", approved_at=NOW)
        db.commit()
        ids = store.id, store.owner_id
    yield db_engine, storage, *ids
    set_media_storage(None)


def test_poster_is_stored_as_an_ordinary_linkable_photo(media_env):
    engine, storage, store_id, owner_id = media_env
    poster = digest_video(samples.video(seconds=1, audio=None)).poster
    clip = ManualMedia(id=str(uuid.uuid4()), store_id=store_id, uploaded_by_owner_id=owner_id, kind="VIDEO")
    now = utcnow()
    with Session(engine) as db:
        row = store_video_poster(db, clip, poster, now=now)
        db.commit()
        poster_id, key = row.id, row.object_key
    with Session(engine) as db:
        stored = db.get(ManualMedia, poster_id)
        assert (stored.kind, stored.mime_type, stored.byte_size, stored.duration_ms) == (
            "IMAGE", "image/jpeg", len(poster.jpeg), None)
        assert (stored.store_id, stored.uploaded_by_owner_id) == (store_id, owner_id)
        assert abs(stored.expires_at - (now + retention.UNATTACHED_TTL)) < timedelta(seconds=1)
        assert storage.read(key) == poster.jpeg and key.startswith(f"manual/{store_id}/")
        assert [photo.id for photo in lock_photos_for_link(db, store_id, [poster_id])] == [poster_id]


def test_poster_rolled_back_leaves_only_an_orphan_the_sweep_removes(media_env):
    engine, storage, store_id, owner_id = media_env
    picture_bytes = media_samples.jpeg(gps=False)
    poster = VideoFrame(0, picture_bytes)
    clip = ManualMedia(id=str(uuid.uuid4()), store_id=store_id, uploaded_by_owner_id=owner_id, kind="VIDEO")
    with Session(engine) as db:
        key = store_video_poster(db, clip, poster).object_key
        db.rollback()
    assert storage.read(key) == picture_bytes
    assert retention.sweep_orphan_files(min_age_seconds=0) == 1 and not storage.exists(key)


def test_empty_poster_is_refused(media_env):
    engine, _storage, store_id, owner_id = media_env
    clip = ManualMedia(id=str(uuid.uuid4()), store_id=store_id, uploaded_by_owner_id=owner_id, kind="VIDEO")
    with Session(engine) as db, pytest.raises(MediaInvalid):
        store_video_poster(db, clip, VideoFrame(0, b""))


def test_video_hold_extends_only_videos_and_never_shortens():
    now = utcnow()
    clip = ManualMedia(kind="VIDEO", expires_at=now + timedelta(hours=1))
    retention.hold_video_bytes(clip, now)
    assert clip.expires_at == now + retention.VIDEO_HOLD_TTL
    later = now + timedelta(hours=48)
    clip.expires_at = later
    retention.hold_video_bytes(clip, now)
    assert clip.expires_at == later
    photo = ManualMedia(kind="IMAGE", expires_at=now)
    retention.hold_video_bytes(photo, now)
    assert photo.expires_at == now


def test_a_video_is_never_kept_by_references():
    # Unit level only: kind VIDEO rows need the migration (owned by the contract work) before
    # purge_media_content can be exercised end to end.
    # Purged at its hold even while attached; the derived poster photo is what stays.
    assert retention._in_use(None, ManualMedia(kind="VIDEO")) is None

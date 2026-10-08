"""Real type, limits and metadata stripping of uploads, judged from bytes only."""

import io

import pytest
from PIL import Image

from app.db.models import MAX_AUDIO_BYTES, MAX_IMAGE_BYTES
from app.media.errors import MediaInvalid, MediaTooLarge, MediaUnsupported
from app.media.inspection import inspect_media, sniff
from tests import media_samples as samples


def reject(kind, data, error, reason=None):
    with pytest.raises(error) as caught:
        inspect_media(data, kind)
    if reason:
        assert caught.value.reason == reason
    return caught.value


# --- photos -----------------------------------------------------------------------------------


@pytest.mark.parametrize("build,mime", [
    (samples.jpeg, "image/jpeg"), (samples.png, "image/png"), (samples.webp, "image/webp"),
])
def test_supported_photos_are_accepted_by_content(build, mime):
    result = inspect_media(build(), "IMAGE")
    assert (result.kind, result.mime_type, result.duration_ms) == ("IMAGE", mime, None)
    Image.open(io.BytesIO(result.data)).load()


def test_jpeg_location_exif_and_comments_are_removed_and_orientation_applied():
    original = samples.jpeg(width=60, height=20, gps=True, orientation=6)
    assert Image.open(io.BytesIO(original)).getexif().get_ifd(0x8825)
    stored = inspect_media(original, "IMAGE").data
    image = Image.open(io.BytesIO(stored))
    assert not image.getexif() and b"secret note" not in stored and b"PhoneMaker" not in stored
    assert image.size == (20, 60)  # rotated by the orientation instead of keeping the tag


def test_png_text_chunks_and_webp_exif_are_removed():
    assert b"GPS 37.6" not in inspect_media(samples.png(), "IMAGE").data
    stored = inspect_media(samples.webp(), "IMAGE").data
    assert not Image.open(io.BytesIO(stored)).getexif()


def test_exactly_ten_mebibytes_is_accepted_and_one_more_byte_is_too_large():
    accepted = inspect_media(samples.padded_jpeg(MAX_IMAGE_BYTES), "IMAGE")
    assert accepted.mime_type == "image/jpeg" and len(accepted.data) <= MAX_IMAGE_BYTES
    reject("IMAGE", samples.padded_jpeg(MAX_IMAGE_BYTES + 1), MediaTooLarge, "bytes")


def test_empty_file_is_invalid_not_unsupported():
    reject("IMAGE", b"", MediaInvalid, "empty")
    reject("AUDIO", b"", MediaInvalid, "empty")


@pytest.mark.parametrize("data", [
    samples.gif(), b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"  <html><script></script>",
    b"%PDF-1.7 ...", b"plain text pretending to be a jpg", samples.wav_seconds(1),
    b"\x00\x00\x01\xba video",
])
def test_unsupported_or_foreign_content_is_415(data):
    reject("IMAGE", data, MediaUnsupported)


def test_extension_and_declared_type_are_ignored():
    # The same PNG bytes are a PNG whatever the client claims; the stored type follows content.
    assert inspect_media(samples.png(), "IMAGE").mime_type == "image/png"


@pytest.mark.parametrize("data", [
    samples.jpeg()[:200],  # truncated
    b"\xff\xd8\xff\xe0" + b"\x00" * 300,  # JPEG signature, garbage body
    samples.png()[:40],
    b"RIFF\x10\x00\x00\x00WEBPVP8 garbage",
])
def test_corrupt_photos_are_invalid(data):
    reject("IMAGE", data, MediaInvalid, "image_decode")


def test_animated_photo_is_unsupported_and_pixel_bombs_are_too_large():
    reject("IMAGE", samples.webp(animated=True), MediaUnsupported, "animated_image")
    reject("IMAGE", samples.bomb_png(), MediaTooLarge, "image_pixels")


# --- audio ------------------------------------------------------------------------------------


@pytest.mark.parametrize("data,mime,milliseconds", [
    (samples.wav_seconds(3), "audio/wav", 3000),
    (samples.mp3(samples.mp3_frames_for(2)), "audio/mpeg", 2012),  # 77 frames = 2.0114 s
    (samples.mp3(10, id3=False), "audio/mpeg", 262),
    (samples.mp4(96000, 48000), "audio/mp4", 2000),
    (samples.mp4(fragments=[(50, 960), (50, 960)]), "audio/mp4", 2000),
    (samples.webm((0, 1000, 2000, 2500)), "audio/webm", 2500),
    (samples.webm((0, 500), duration_ms=1800), "audio/webm", 1800),
    (samples.webm((0, 1500), unknown_sizes=False), "audio/webm", 1500),
])
def test_supported_audio_reports_its_exact_duration(data, mime, milliseconds):
    result = inspect_media(data, "AUDIO")
    assert (result.mime_type, result.duration_ms) == (mime, milliseconds)
    assert result.data == data  # audio is stored as uploaded


def test_exactly_120_seconds_is_accepted_and_longer_is_too_large():
    assert inspect_media(samples.wav_seconds(120.0), "AUDIO").duration_ms == 120_000
    reject("AUDIO", samples.wav_seconds(120.1), MediaTooLarge, "duration")
    reject("AUDIO", samples.wav(120 * 8000 + 1), MediaTooLarge, "duration")  # 120.000125 s
    reject("AUDIO", samples.mp4(120_001, 1000), MediaTooLarge, "duration")
    reject("AUDIO", samples.webm((0, 120_100)), MediaTooLarge, "duration")


def test_audio_byte_limit_is_exact():
    exact = samples.wav(MAX_AUDIO_BYTES - 44)
    assert len(exact) == MAX_AUDIO_BYTES
    reject("AUDIO", exact, MediaTooLarge, "duration")  # 20 MiB of 8 kHz audio is far over 120 s
    reject("AUDIO", samples.wav(MAX_AUDIO_BYTES - 43), MediaTooLarge, "bytes")


@pytest.mark.parametrize("data,reason", [
    (samples.wav_seconds(1)[:30], "wav_fmt"),
    (samples.wav(0), "wav_empty"),
    (samples.wav_seconds(1)[:-100] + b"", None),  # declared size runs past the end
    (samples.mp3(5)[:-500] + b"\x00" * 2000, "mp3_damaged"),
    (b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10, "mp3_frames"),
    (samples.mp4()[:60], "mp4_box"),
    (samples.mp4(0), "mp4_empty"),
    (samples.webm(())[:-3] + b"\xff", None),
    (samples.webm(()), "webm_empty"),
])
def test_corrupt_audio_is_invalid(data, reason):
    error = reject("AUDIO", data, MediaInvalid)
    if reason:
        assert error.reason == reason


@pytest.mark.parametrize("data", [
    samples.mp4(video=True), samples.webm(video=True), samples.webm(doc_type=b"matroska"),
    samples.mp4(brand=b"qt  "), b"OggS\x00\x02" + b"\x00" * 40, b"fLaC" + b"\x00" * 40,
    b"\xff\xf1\x50\x80" + b"\x00" * 40,  # ADTS AAC is not an accepted container
    samples.png(),
])
def test_video_and_other_audio_containers_are_unsupported(data):
    reject("AUDIO", data, MediaUnsupported)


def test_sniffing_reads_bytes_not_names():
    assert sniff(samples.wav_seconds(1)) == "audio/wav"
    assert sniff(samples.webp()) == "image/webp"
    assert sniff(b"\x00\x00\x00\x18ftypheic") == "image/heif"
    assert sniff(b"random") is None

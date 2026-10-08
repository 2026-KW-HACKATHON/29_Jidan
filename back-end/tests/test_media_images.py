"""Photo normalization: multi-picture JPEGs, XMP/ICC handling and the decode concurrency bound."""
import io
import threading
import time

import pytest
from PIL import Image, ImageCms, PngImagePlugin

from app.media import images
from app.media.errors import MediaUnsupported
from app.media.inspection import inspect_media

XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF>'
       b"<exif:GPSLatitude>37,37.5N</exif:GPSLatitude>SECRET-XMP</rdf:RDF></x:xmpmeta>")
ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _exif_with_location() -> bytes:
    exif = Image.new("RGB", (1, 1)).getexif()
    exif[0x8825] = {1: "N", 2: (37.0, 37.0, 30.0)}  # GPSInfo
    exif[0x010F] = "SECRET-MAKE"
    return exif.tobytes()


def _reopen(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_multi_picture_jpeg_keeps_only_the_primary_still_image():
    # Ultra HDR / depth / stereo JPEGs carry extra MPF images; Pillow reports them as MPO frames.
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(
        buffer, "MPO", save_all=True, append_images=[Image.new("RGB", (8, 6), "blue")],
        exif=_exif_with_location(),
    )
    inspected = inspect_media(buffer.getvalue(), "IMAGE")
    assert inspected.mime_type == "image/jpeg"
    photo = _reopen(inspected.data)
    assert (photo.format, photo.size, getattr(photo, "n_frames", 1)) == ("JPEG", (32, 24), 1)
    assert photo.getpixel((0, 0))[0] > 200  # the red primary, not the blue extra image
    assert b"MPF" not in inspected.data and b"SECRET-MAKE" not in inspected.data
    assert len(photo.getexif()) == 0


def test_animated_png_and_webp_are_still_refused():
    frames = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]
    for fmt in ("PNG", "WEBP"):
        buffer = io.BytesIO()
        frames[0].save(buffer, fmt, save_all=True, append_images=frames[1:], duration=100)
        with pytest.raises(MediaUnsupported):
            inspect_media(buffer.getvalue(), "IMAGE")


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_xmp_exif_and_text_are_removed_and_icc_is_kept(fmt):
    buffer = io.BytesIO()
    options = {"exif": _exif_with_location(), "icc_profile": ICC}
    if fmt == "PNG":
        info = PngImagePlugin.PngInfo()
        info.add_itxt("XML:com.adobe.xmp", XMP.decode())
        info.add_text("Comment", "SECRET-TEXT")
        options["pnginfo"] = info
    else:
        options["xmp"] = XMP
    if fmt == "JPEG":
        options["comment"] = b"SECRET-COMMENT"
    Image.new("RGB", (16, 16), "red").save(buffer, fmt, **options)
    data = inspect_media(buffer.getvalue(), "IMAGE").data
    for secret in (b"SECRET-XMP", b"SECRET-MAKE", b"SECRET-TEXT", b"SECRET-COMMENT", b"GPSLatitude"):
        assert secret not in data
    photo = _reopen(data)
    assert len(photo.getexif()) == 0 and "xmp" not in photo.info
    assert photo.info.get("icc_profile") == ICC  # colour rendering is kept


def test_decoding_runs_at_most_the_configured_number_at_once(monkeypatch):
    active = 0
    peak = 0
    lock = threading.Lock()

    def slow(data, mime_type, max_bytes):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return data

    monkeypatch.setattr(images, "_normalize", slow)
    threads = [threading.Thread(target=images.normalize_image, args=(b"x", "image/png", 10)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert peak == images.DECODE_CONCURRENCY


def test_a_failed_decode_releases_its_slot():
    for _ in range(images.DECODE_CONCURRENCY + 2):
        with pytest.raises(Exception):  # noqa: B017 - any rejection; the slot must come back
            images.normalize_image(b"\x89PNG\r\n\x1a\nbroken", "image/png", 10)
    assert images._decode_slots.acquire(timeout=1)
    images._decode_slots.release()

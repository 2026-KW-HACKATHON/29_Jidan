"""Decode, normalize and re-encode uploaded photos.

Every photo is fully decoded (corrupt or truncated data fails here), checked against a pixel
budget before decoding (decompression bombs), rejected when animated (only still photos), and
re-encoded in its own format after applying the EXIF orientation. Re-encoding drops EXIF
(including GPS location), XMP, comments and text chunks; only the ICC colour profile is kept.

A JPEG carrying extra MPF images (Ultra HDR gain maps, depth maps, stereo views; Pillow opens
it as MPO) is a still photo: only the primary image is kept, the extra images are dropped.

Decoding is memory-heavy: a 40 MP photo needs ~120-240 MB while it is decoded and re-encoded,
even when the upload itself is under 1 MB. At most `DECODE_CONCURRENCY` photos are processed
at once per process, so concurrent uploads cannot exhaust a small server's memory; the others
wait for a slot.
"""

import io
import threading
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.media.errors import MediaInvalid, MediaTooLarge, MediaUnsupported

MAX_PIXELS = 40_000_000  # e.g. 8000 x 5000; above this a photo is "too large" (413)
DECODE_CONCURRENCY = 2
_decode_slots = threading.BoundedSemaphore(DECODE_CONCURRENCY)
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def _strip_metadata(image: Image.Image) -> Image.Image:
    """Keep only what rendering needs. Pillow's encoders fall back to `image.info` for EXIF,
    comments and XMP, so leaving it in place would silently carry them into the new file."""
    kept = {key: image.info[key] for key in ("icc_profile", "transparency") if key in image.info}
    image.info = kept
    return image


def _encode(image: Image.Image, fmt: str, attempt: int) -> bytes:
    output = io.BytesIO()
    options: dict = {}
    icc = image.info.get("icc_profile")
    if icc:
        options["icc_profile"] = icc
    if fmt == "JPEG":
        if image.mode not in ("RGB", "L", "CMYK"):
            image = image.convert("RGB")
        options.update(quality=(90, 80, 70)[attempt], optimize=True)
    elif fmt == "WEBP":
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
        options.update(quality=(90, 80, 70)[attempt], method=4)
    else:
        options.update(optimize=attempt > 0, compress_level=(6, 9, 9)[attempt])
        if "transparency" in image.info:
            options["transparency"] = image.info["transparency"]
    image.save(output, format=fmt, **options)
    return output.getvalue()


def normalize_image(data: bytes, mime_type: str, max_bytes: int) -> bytes:
    """The stored bytes of a photo of `mime_type`, without metadata, at most `max_bytes`."""
    with _decode_slots:
        return _normalize(data, mime_type, max_bytes)


def _normalize(data: bytes, mime_type: str, max_bytes: int) -> bytes:
    fmt = FORMATS[mime_type]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data), formats=[fmt])
            width, height = image.size
            if width < 1 or height < 1:
                raise MediaInvalid("image_dimensions")
            if width * height > MAX_PIXELS:
                raise MediaTooLarge("image_pixels")
            if image.format == "MPO":
                image.seek(0)  # the primary picture; MPF extras are not frames of an animation
            elif getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
                raise MediaUnsupported("animated_image")
            image.load()
            image = _strip_metadata(ImageOps.exif_transpose(image))
    except (MediaInvalid, MediaTooLarge, MediaUnsupported):
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MediaTooLarge("image_pixels") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError):
        raise MediaInvalid("image_decode") from None
    for attempt in range(3):
        encoded = _encode(image, fmt, attempt)
        if len(encoded) <= max_bytes:
            return encoded
    raise MediaTooLarge("image_reencoded_bytes")

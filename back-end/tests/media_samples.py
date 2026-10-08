"""Byte-exact media samples for upload tests, built in-process (no external tools)."""

import io
import struct
import wave

from PIL import Image

GPS_LATITUDE = 37.6


def jpeg(width=64, height=48, *, gps=True, orientation=None, color=(200, 30, 30)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    if orientation:
        exif[0x0112] = orientation
    if gps:
        exif[0x8825] = {1: "N", 2: (37.0, 36.0, 0.0), 3: "E", 4: (127.0, 3.0, 0.0)}  # GPS IFD
    output = io.BytesIO()
    image.save(output, "JPEG", exif=exif.tobytes(), comment=b"secret note")
    return output.getvalue()


def padded_jpeg(total: int) -> bytes:
    """A valid JPEG of exactly `total` bytes, padded with COM segments after SOI."""
    base = jpeg(gps=False)
    padding = total - len(base)
    segments = []
    while padding > 0:
        if padding < 4:
            raise ValueError("cannot pad by fewer than 4 bytes")
        payload = min(padding - 4, 65533)
        if 0 < padding - (payload + 4) < 4:
            payload -= 4
        segments.append(b"\xff\xfe" + struct.pack(">H", payload + 2) + b"\x00" * payload)
        padding -= payload + 4
    data = base[:2] + b"".join(segments) + base[2:]
    assert len(data) == total
    return data


def png(width=32, height=32, mode="RGBA", text=True) -> bytes:
    from PIL import PngImagePlugin

    image = Image.new(mode, (width, height), (0, 128, 255, 128) if mode == "RGBA" else 128)
    info = PngImagePlugin.PngInfo()
    if text:
        info.add_text("Comment", "GPS 37.6,127.0")
    output = io.BytesIO()
    image.save(output, "PNG", pnginfo=info)
    return output.getvalue()


def webp(animated=False) -> bytes:
    frames = [Image.new("RGB", (16, 16), color) for color in ((255, 0, 0), (0, 255, 0))]
    output = io.BytesIO()
    if animated:
        frames[0].save(output, "WEBP", save_all=True, append_images=frames[1:], duration=100)
    else:
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (37.0, 36.0, 0.0)}
        frames[0].save(output, "WEBP", exif=exif.tobytes())
    return output.getvalue()


def gif() -> bytes:
    output = io.BytesIO()
    Image.new("P", (8, 8)).save(output, "GIF")
    return output.getvalue()


def bomb_png(width=10000, height=10000) -> bytes:
    """A tiny file that would decode to `width * height` pixels."""
    output = io.BytesIO()
    Image.new("1", (width, height)).save(output, "PNG")
    return output.getvalue()


def wav(data_bytes: int, *, rate=8000, width=1, channels=1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b"\x80" * data_bytes)
    return output.getvalue()


def wav_seconds(seconds: float) -> bytes:
    """8 kHz 8-bit mono: one byte per 1/8000 s, so durations are exact."""
    return wav(round(seconds * 8000))


MP3_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413  # MPEG-1 Layer III, 128 kbit/s, 44.1 kHz: 417 bytes


def mp3(frames: int, *, id3=True) -> bytes:
    tag = b"ID3\x04\x00\x00" + bytes([0, 0, 0, 10]) + b"\x00" * 10 if id3 else b""
    return tag + MP3_FRAME * frames


def mp3_frames_for(seconds: float) -> int:
    return round(seconds * 44100 / 1152)


# --- ISO BMFF -----------------------------------------------------------------------------------


def box(kind: bytes, *parts: bytes) -> bytes:
    body = b"".join(parts)
    return struct.pack(">I4s", 8 + len(body), kind) + body


def full_box(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _trak(track_id: int, handler: bytes, scale: int, duration: int) -> bytes:
    tkhd = full_box(b"tkhd", 0, 7, struct.pack(">III", 0, 0, track_id) + b"\x00" * 68)
    mdhd = full_box(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, scale, duration) + b"\x00" * 4)
    hdlr = full_box(b"hdlr", 0, 0, b"\x00" * 4 + handler + b"\x00" * 12 + b"sound\x00")
    return box(b"trak", tkhd, box(b"mdia", mdhd, hdlr))


def mp4(duration: int = 48000, scale: int = 48000, *, video=False, fragments=None, brand=b"M4A ") -> bytes:
    """An audio MP4. `fragments=[(sample_count, sample_duration), ...]` makes it fragmented
    (mdhd duration 0, durations in moof/trun as browsers record)."""
    ftyp = box(b"ftyp", brand + b"\x00\x00\x00\x00" + b"isom")
    mvhd = full_box(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 0) + b"\x00" * 80)
    tracks = [_trak(1, b"soun", scale, 0 if fragments else duration)]
    if video:
        tracks.append(_trak(2, b"vide", 90000, 90000))
    moov_parts = [mvhd, *tracks]
    tail = [box(b"mdat", b"\x00" * 64)]
    if fragments:
        moov_parts.append(box(b"mvex", full_box(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 0, 0, 0))))
        tail = []
        for sequence, (count, sample_duration) in enumerate(fragments, start=1):
            tfhd = full_box(b"tfhd", 0, 0x08, struct.pack(">II", 1, sample_duration))
            trun = full_box(b"trun", 0, 0x200, struct.pack(">I", count) + struct.pack(">I", 10) * count)
            mfhd = full_box(b"mfhd", 0, 0, struct.pack(">I", sequence))
            tail += [box(b"moof", mfhd, box(b"traf", tfhd, trun)), box(b"mdat", b"\x00" * 10 * count)]
    return ftyp + box(b"moov", *moov_parts) + b"".join(tail)


# --- WebM / EBML ---------------------------------------------------------------------------------


def _vint(value: int) -> bytes:
    for length in range(1, 9):
        if value < (1 << (7 * length)) - 1:
            return ((1 << (7 * length)) | value).to_bytes(length, "big")
    raise ValueError("too large")


def ebml(element_id: int, *parts: bytes, unknown_size=False) -> bytes:
    body = b"".join(parts)
    identifier = element_id.to_bytes((element_id.bit_length() + 7) // 8, "big")
    size = b"\x01\xff\xff\xff\xff\xff\xff\xff" if unknown_size else _vint(len(body))
    return identifier + size + body


def uint(element_id: int, value: int) -> bytes:
    return ebml(element_id, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def webm(block_times=(0, 1000, 2000), *, duration_ms=None, video=False, doc_type=b"webm",
         unknown_sizes=True) -> bytes:
    """MediaRecorder-style WebM: unknown-size Segment and Clusters, usually no Duration."""
    header = ebml(0x1A45DFA3, uint(0x4286, 1), ebml(0x4282, doc_type))
    info = [uint(0x2AD7B1, 1_000_000)]
    if duration_ms is not None:
        info.append(ebml(0x4489, struct.pack(">d", float(duration_ms))))
    entries = [ebml(0xAE, uint(0xD7, 1), uint(0x83, 2), ebml(0x86, b"A_OPUS"))]
    if video:
        entries.append(ebml(0xAE, uint(0xD7, 2), uint(0x83, 1), ebml(0x86, b"V_VP8")))
    clusters = b""
    for start in range(len(block_times)):  # one cluster per block keeps int16 offsets small
        chunk = block_times[start:start + 1]
        base = chunk[0]
        blocks = b"".join(
            ebml(0xA3, b"\x81" + struct.pack(">h", time - base) + b"\x80" + b"\x00" * 8) for time in chunk
        )
        clusters += ebml(0x1F43B675, uint(0xE7, base), blocks, unknown_size=unknown_sizes)
    segment = ebml(0x18538067, ebml(0x1549A966, *info), ebml(0x1654AE6B, *entries), clusters,
                   unknown_size=unknown_sizes)
    return header + segment

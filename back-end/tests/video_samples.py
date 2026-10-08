"""Small synthetic videos for upload/digest tests, encoded in-process with PyAV (no fixtures).

Frames are tiny and the frame rate low so even a 61 s clip encodes in well under a second.
`pattern` decides what each frame shows: "bars" (sharp coloured stripes), "black", "flat" grey
or "fade" (black for the first half, then bars), so poster selection can be tested.
"""

import io
import math
from fractions import Fraction
from pathlib import Path

import av
from PIL import Image, ImageDraw

CONTAINERS = {"mp4": ("mp4", "libx264"), "mov": ("mov", "libx264"), "webm": ("webm", "libvpx-vp9"),
              "webm-vp8": ("webm", "libvpx"), "webm-av1": ("webm", "libsvtav1"),
              "mp4-hevc": ("mp4", "libx265"), "mov-hevc": ("mov", "libx265")}


def _picture(width: int, height: int, pattern: str, index: int, total: int) -> Image.Image:
    if pattern == "black" or (pattern == "fade" and index < total // 2):
        return Image.new("RGB", (width, height), (0, 0, 0))
    if pattern == "flat":
        return Image.new("RGB", (width, height), (128, 128, 128))
    image = Image.new("RGB", (width, height), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    stripe = max(2, width // 8)
    for x in range(0, width, stripe * 2):
        draw.rectangle([x, 0, x + stripe - 1, height], fill=((index * 37) % 256, 40, 200))
    draw.rectangle([width // 4, height // 4, width // 2, height // 2], fill=(10, 200, 10))
    return image


def video(kind: str = "mp4", *, seconds: float = 2.0, fps: int = 4, width: int = 64, height: int = 48,
          audio: str | None = "tone", pattern: str = "bars", gop: int | None = None,
          rotation: int | None = None, metadata: dict | None = None, options: dict | None = None,
          path=None) -> bytes:
    """An encoded clip. `audio`: "tone" (440 Hz), "silent" (digital zeros) or None (no track).
    `path`: write to that file instead of memory (needed for muxer options such as faststart)."""
    container_format, codec = CONTAINERS[kind]
    output = str(path) if path else io.BytesIO()
    with av.open(output, "w", format=container_format, options=options or {}) as container:
        container.metadata.update(metadata or {})
        stream = container.add_stream(codec, rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        if gop:
            stream.codec_context.gop_size = gop
        if codec == "libx265":
            stream.codec_context.options = {"x265-params": "log-level=none"}
        if rotation is not None:
            stream.set_display_rotation(rotation)
        sound = None
        if audio:
            sound = container.add_stream("libopus" if container_format == "webm" else "aac", rate=48000)
            sound.layout = "mono"
        total = max(1, round(seconds * fps))
        for index in range(total):
            frame = av.VideoFrame.from_image(_picture(width, height, pattern, index, total))
            frame.pts, frame.time_base = index, Fraction(1, fps)
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
        if sound is not None:
            _write_audio(container, sound, seconds, silent=audio == "silent")
    return Path(path).read_bytes() if path else output.getvalue()


def _write_audio(container, stream, seconds: float, *, silent: bool) -> None:
    rate, chunk = 48000, 960
    total = int(seconds * rate)
    fmt = stream.codec_context.format.name
    for start in range(0, total, chunk):
        count = min(chunk, total - start)
        values = [0.0 if silent else 0.5 * math.sin(2 * math.pi * 440 * (start + i) / rate) for i in range(count)]
        frame = av.AudioFrame(format="flt" if fmt == "flt" else "fltp", layout="mono", samples=count)
        frame.planes[0].update(_floats(values, count, frame.planes[0].buffer_size))
        frame.sample_rate, frame.pts, frame.time_base = rate, start, Fraction(1, rate)
        container.mux(stream.encode(frame))
    container.mux(stream.encode())


def _floats(values: list[float], count: int, size: int) -> bytes:
    from array import array

    data = array("f", values).tobytes()
    return data + b"\x00" * (size - len(data))


def audio_only(fmt: str = "mp4", seconds: float = 1.0) -> bytes:
    """A recording (AAC in MP4 or Opus in WebM): a valid container without a video track."""
    output = io.BytesIO()
    with av.open(output, "w", format=fmt) as container:
        stream = container.add_stream("libopus" if fmt == "webm" else "aac", rate=48000)
        stream.layout = "mono"
        _write_audio(container, stream, seconds, silent=False)
    return output.getvalue()


def other_container(fmt: str = "avi", codec: str = "mpeg4") -> bytes:
    """A real video in a container the purpose does not accept (AVI, Matroska, MPEG-TS)."""
    output = io.BytesIO()
    with av.open(output, "w", format=fmt) as container:
        stream = container.add_stream(codec, rate=4)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for index in range(4):
            frame = av.VideoFrame.from_image(_picture(64, 48, "bars", index, 4))
            frame.pts, frame.time_base = index, Fraction(1, 4)
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
    return output.getvalue()

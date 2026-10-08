"""Container-level validation and exact duration of the accepted audio formats.

No codec library is needed: each parser walks the whole container structure (WAV chunks, every
MPEG audio frame, MP4 boxes including fragments, WebM/EBML elements including unknown-size
segments and clusters written by browser MediaRecorder) and returns the duration as an exact
`Fraction` of seconds, so 120.0 s passes and 120.001 s does not. A declared length (WebM
Duration, MP4 mdhd/edit list/mehd) can only make a recording longer than its measured timestamps or
samples, never shorter, so a forged header cannot slip past the limit. Structural damage raises
MediaInvalid (422); a video track raises MediaUnsupported (415). Payload samples are not
decoded; undecodable audio inside a valid container is reported by the transcription task.
"""

import math
import struct
from fractions import Fraction

from app.media.errors import MediaInvalid, MediaUnsupported

# --- WAV --------------------------------------------------------------------------------------

_WAV_FORMATS = {1, 3, 6, 7, 0xFFFE}  # PCM, IEEE float, A-law, mu-law, extensible


def wav_duration(data: bytes) -> Fraction:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise MediaInvalid("wav_header")
    position = 12
    fmt = None
    data_size = None
    while position + 8 <= len(data):
        chunk_id = data[position:position + 4]
        size = struct.unpack_from("<I", data, position + 4)[0]
        body = position + 8
        available = len(data) - body
        if chunk_id == b"fmt ":
            if size < 16 or available < 16:
                raise MediaInvalid("wav_fmt")
            fmt = struct.unpack_from("<HHIIHH", data, body)
        elif chunk_id == b"data":
            # Streaming writers leave 0 or 0xFFFFFFFF until they finish; anything else that runs
            # past the end of the file is a truncated recording.
            if size in (0, 0xFFFFFFFF):
                size = available
            elif size > available:
                raise MediaInvalid("wav_truncated")
            data_size = size
            break
        position = body + size + (size & 1)
    if fmt is None or data_size is None:
        raise MediaInvalid("wav_chunks")
    audio_format, channels, sample_rate, byte_rate, block_align, bits = fmt
    if (
        audio_format not in _WAV_FORMATS or not 1 <= channels <= 8
        or not 1000 <= sample_rate <= 384000 or byte_rate == 0 or block_align == 0
        or byte_rate != sample_rate * block_align
        or (audio_format == 1 and block_align != channels * ((bits + 7) // 8))
    ):
        raise MediaInvalid("wav_format")
    if data_size < block_align:
        raise MediaInvalid("wav_empty")
    return Fraction(data_size - data_size % block_align, byte_rate)


# --- MPEG audio (MP3) -------------------------------------------------------------------------

_BITRATES = {  # kbit/s by (MPEG-1?, layer)
    (True, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (True, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (True, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (False, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (False, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (False, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_MAX_TRAILING_JUNK = 1024


def _mp3_frame(header: bytes) -> tuple[int, int, int] | None:
    """(frame length, samples, sample rate) of a valid MPEG audio frame header, else None."""
    if header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
        return None
    version = (header[1] >> 3) & 3
    layer = 4 - ((header[1] >> 1) & 3)  # 1, 2, 3 (4 means reserved)
    bitrate_index = header[2] >> 4
    rate_index = (header[2] >> 2) & 3
    padding = (header[2] >> 1) & 1
    if version == 1 or layer == 4 or bitrate_index in (0, 15) or rate_index == 3:
        return None  # reserved values; free-format bitrate is not supported
    mpeg1 = version == 3
    bitrate = _BITRATES[(mpeg1, layer)][bitrate_index] * 1000
    sample_rate = _SAMPLE_RATES[version][rate_index]
    if layer == 1:
        return (12 * bitrate // sample_rate + padding) * 4, 384, sample_rate
    if layer == 2 or mpeg1:
        return 144 * bitrate // sample_rate + padding, 1152, sample_rate
    return 72 * bitrate // sample_rate + padding, 576, sample_rate


def mp3_duration(data: bytes) -> Fraction:
    position = 0
    end = len(data)
    if data[:3] == b"ID3" and len(data) >= 10:
        size = 0
        for byte in data[6:10]:
            if byte & 0x80:
                raise MediaInvalid("mp3_id3")
            size = (size << 7) | byte
        position = 10 + size + (10 if data[5] & 0x10 else 0)
    if end - 128 >= position and data[end - 128:end - 125] == b"TAG":
        end -= 128  # ID3v1
    if end - 32 >= position and data[end - 32:end - 24] == b"APETAGEX":
        tag_size, flags = struct.unpack_from("<II", data, end - 20)
        end -= tag_size + (32 if flags & 0x80000000 else 0)
    frames = samples = 0
    rate = None
    while position + 4 <= end:
        frame = _mp3_frame(data[position:position + 4])
        if frame is None:
            break
        length, frame_samples, sample_rate = frame
        if rate is None:
            rate = sample_rate
        elif sample_rate != rate:
            raise MediaInvalid("mp3_rate_change")
        if position + length > end:
            break  # a cut-off last frame: the decoders drop it, so do we
        frames += 1
        samples += frame_samples
        position += length
    if frames == 0:
        raise MediaInvalid("mp3_frames")
    if end - position > _MAX_TRAILING_JUNK:
        raise MediaInvalid("mp3_damaged")  # frames stop long before the end of the file
    return Fraction(samples, rate)


# --- MP4 / ISO BMFF ---------------------------------------------------------------------------


def _boxes(data: bytes, start: int, end: int):
    position = start
    while position < end:
        if position + 8 > end:
            raise MediaInvalid("mp4_box")
        size, kind = struct.unpack_from(">I4s", data, position)
        header = 8
        if size == 1:
            if position + 16 > end:
                raise MediaInvalid("mp4_box")
            size = struct.unpack_from(">Q", data, position + 8)[0]
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            raise MediaInvalid("mp4_box")
        yield kind, position + header, position + size
        position += size


def _children(data: bytes, start: int, end: int) -> dict[bytes, list[tuple[int, int]]]:
    found: dict[bytes, list[tuple[int, int]]] = {}
    for kind, body, box_end in _boxes(data, start, end):
        found.setdefault(kind, []).append((body, box_end))
    return found


def _full_box_values(data: bytes, body: int, end: int, v0: str, v1: str, offset0: int, offset1: int):
    if body + 4 > end or data[body] not in (0, 1):
        raise MediaInvalid("mp4_full_box")
    version = data[body]
    fmt, offset = (v1, offset1) if version == 1 else (v0, offset0)
    if body + offset + struct.calcsize(fmt) > end:
        raise MediaInvalid("mp4_full_box")
    return struct.unpack_from(fmt, data, body + offset)


def mp4_duration(data: bytes) -> Fraction:
    top = list(_boxes(data, 0, len(data)))
    if not top or top[0][0] != b"ftyp":
        raise MediaInvalid("mp4_ftyp")
    kinds = {kind for kind, _body, _end in top}
    if b"moov" not in kinds or b"mdat" not in kinds:
        raise MediaInvalid("mp4_structure")
    moov_body, moov_end = next((body, end) for kind, body, end in top if kind == b"moov")
    moov = _children(data, moov_body, moov_end)
    if b"mvhd" not in moov:
        raise MediaInvalid("mp4_mvhd")
    mvhd_body, mvhd_end = moov[b"mvhd"][0]
    movie_scale, movie_duration = _full_box_values(data, mvhd_body, mvhd_end, ">II", ">IQ", 12, 20)

    sound: dict[int, tuple[Fraction, int]] = {}  # track id -> (duration, media timescale)
    for trak_body, trak_end in moov.get(b"trak", []):
        trak = _children(data, trak_body, trak_end)
        if b"tkhd" not in trak or b"mdia" not in trak:
            raise MediaInvalid("mp4_trak")
        tkhd_body, tkhd_end = trak[b"tkhd"][0]
        (track_id,) = _full_box_values(data, tkhd_body, tkhd_end, ">I", ">I", 12, 20)
        mdia = _children(data, *trak[b"mdia"][0])
        if b"hdlr" not in mdia or b"mdhd" not in mdia:
            raise MediaInvalid("mp4_mdia")
        hdlr_body, hdlr_end = mdia[b"hdlr"][0]
        if hdlr_body + 12 > hdlr_end:
            raise MediaInvalid("mp4_hdlr")
        handler = data[hdlr_body + 8:hdlr_body + 12]
        if handler == b"vide":
            raise MediaUnsupported("video_track")
        if handler != b"soun":
            continue
        mdhd_body, mdhd_end = mdia[b"mdhd"][0]
        scale, duration = _full_box_values(data, mdhd_body, mdhd_end, ">II", ">IQ", 12, 20)
        if scale == 0:
            raise MediaInvalid("mp4_timescale")
        media = max(Fraction(duration, scale), _sample_table_duration(data, mdia, scale))
        presented = _edit_list_duration(data, trak, movie_scale)
        if presented is not None and presented >= media - MAX_EDIT_TRIM:
            media = presented
        sound[track_id] = (media, scale)
    if not sound:
        raise MediaInvalid("mp4_no_audio")
    fragmented = _fragment_durations(data, top, moov, {tid: scale for tid, (_d, scale) in sound.items()})
    durations = [max(duration, fragmented.get(tid, 0)) for tid, (duration, _scale) in sound.items()]
    if max(durations) > 0:
        return max(durations)
    if b"mvex" in moov:
        mvex = _children(data, *moov[b"mvex"][0])
        if b"mehd" in mvex and movie_scale:
            mehd_body, mehd_end = mvex[b"mehd"][0]
            (fragment_duration,) = _full_box_values(data, mehd_body, mehd_end, ">I", ">Q", 4, 4)
            if fragment_duration:
                return Fraction(fragment_duration, movie_scale)
    elif movie_duration and movie_scale:
        return Fraction(movie_duration, movie_scale)
    raise MediaInvalid("mp4_empty")


# An edit list may trim AAC encoder delay and padding (a few thousand samples: under 0.4 s even
# at 8 kHz). A larger cut is not trimming, and the transcriber may still decode the whole media,
# so the media length then counts.
MAX_EDIT_TRIM = Fraction(1, 2)


def _sample_table_duration(data: bytes, mdia, scale: int) -> Fraction:
    """Sum of the `stbl/stts` sample deltas (0 when the track keeps its samples in fragments)."""
    if b"minf" not in mdia:
        return Fraction(0)
    minf = _children(data, *mdia[b"minf"][0])
    if b"stbl" not in minf:
        return Fraction(0)
    stbl = _children(data, *minf[b"stbl"][0])
    if b"stts" not in stbl:
        return Fraction(0)
    body, end = stbl[b"stts"][0]
    if body + 8 > end or data[body] != 0:
        raise MediaInvalid("mp4_stts")
    (count,) = struct.unpack_from(">I", data, body + 4)
    if body + 8 + count * 8 > end:
        raise MediaInvalid("mp4_stts")
    total = sum(
        samples * delta
        for samples, delta in struct.iter_unpack(">II", data[body + 8:body + 8 + count * 8])
    )
    return Fraction(total, scale)


def _edit_list_duration(data: bytes, trak, movie_scale: int) -> Fraction | None:
    """Presentation length from `edts/elst` (it trims encoder delay/padding of AAC)."""
    if b"edts" not in trak or not movie_scale:
        return None
    edts = _children(data, *trak[b"edts"][0])
    if b"elst" not in edts:
        return None
    body, end = edts[b"elst"][0]
    if body + 8 > end or data[body] not in (0, 1):
        raise MediaInvalid("mp4_elst")
    version = data[body]
    (count,) = struct.unpack_from(">I", data, body + 4)
    entry = 20 if version == 1 else 12
    if body + 8 + count * entry > end:
        raise MediaInvalid("mp4_elst")
    total = 0
    for index in range(count):
        offset = body + 8 + index * entry
        if version == 1:
            segment, media_time = struct.unpack_from(">Qq", data, offset)
        else:
            segment, media_time = struct.unpack_from(">Ii", data, offset)
        if media_time != -1:  # -1 is an empty edit (a delay), not presented media
            total += segment
    return Fraction(total, movie_scale) if total else None


def _fragment_durations(data, top, moov, scales: dict[int, int]) -> dict[int, Fraction]:
    defaults: dict[int, int] = {}
    if b"mvex" in moov:
        for trex_body, trex_end in _children(data, *moov[b"mvex"][0]).get(b"trex", []):
            if trex_body + 20 > trex_end:
                raise MediaInvalid("mp4_trex")
            track_id, _index, duration = struct.unpack_from(">III", data, trex_body + 4)
            defaults[track_id] = duration
    totals: dict[int, int] = {}
    for kind, body, end in top:
        if kind != b"moof":
            continue
        for traf_body, traf_end in _children(data, body, end).get(b"traf", []):
            traf = _children(data, traf_body, traf_end)
            if b"tfhd" not in traf:
                raise MediaInvalid("mp4_tfhd")
            tfhd_body, tfhd_end = traf[b"tfhd"][0]
            if tfhd_body + 8 > tfhd_end:
                raise MediaInvalid("mp4_tfhd")
            flags = int.from_bytes(data[tfhd_body + 1:tfhd_body + 4], "big")
            (track_id,) = struct.unpack_from(">I", data, tfhd_body + 4)
            offset = tfhd_body + 8 + (8 if flags & 0x01 else 0) + (4 if flags & 0x02 else 0)
            default = defaults.get(track_id, 0)
            if flags & 0x08:
                if offset + 4 > tfhd_end:
                    raise MediaInvalid("mp4_tfhd")
                (default,) = struct.unpack_from(">I", data, offset)
            for trun_body, trun_end in traf.get(b"trun", []):
                if trun_body + 8 > trun_end:
                    raise MediaInvalid("mp4_trun")
                trun_flags = int.from_bytes(data[trun_body + 1:trun_body + 4], "big")
                (count,) = struct.unpack_from(">I", data, trun_body + 4)
                position = trun_body + 8 + (4 if trun_flags & 0x01 else 0) + (4 if trun_flags & 0x04 else 0)
                fields = [bit for bit in (0x100, 0x200, 0x400, 0x800) if trun_flags & bit]
                if position + count * 4 * len(fields) > trun_end:
                    raise MediaInvalid("mp4_trun")
                if trun_flags & 0x100:
                    for sample in range(count):
                        totals[track_id] = totals.get(track_id, 0) + struct.unpack_from(
                            ">I", data, position + sample * 4 * len(fields))[0]
                else:
                    totals[track_id] = totals.get(track_id, 0) + count * default
    return {tid: Fraction(total, scales[tid]) for tid, total in totals.items() if tid in scales and total}


# --- WebM / EBML ------------------------------------------------------------------------------

EBML_HEADER, DOC_TYPE = 0x1A45DFA3, 0x4282
SEGMENT, INFO, TRACKS, CLUSTER = 0x18538067, 0x1549A966, 0x1654AE6B, 0x1F43B675
TIMECODE_SCALE, DURATION = 0x2AD7B1, 0x4489
TRACK_ENTRY, TRACK_TYPE = 0xAE, 0x83
CLUSTER_TIMECODE, SIMPLE_BLOCK, BLOCK_GROUP, BLOCK, BLOCK_DURATION = 0xE7, 0xA3, 0xA0, 0xA1, 0x9B
LEVEL_ONE = {INFO, TRACKS, CLUSTER, 0x1C53BB6B, 0x1254C367, 0x1043A770, 0x1941A469, 0x114D9B74}


def _element_id(data: bytes, position: int) -> tuple[int, int]:
    if position >= len(data):
        raise MediaInvalid("ebml_id")
    first = data[position]
    length = next((n for n in range(1, 5) if first & (0x80 >> (n - 1))), 0)
    if not length or position + length > len(data):
        raise MediaInvalid("ebml_id")
    return int.from_bytes(data[position:position + length], "big"), length


def _element_size(data: bytes, position: int) -> tuple[int | None, int]:
    """(size or None for "unknown size", bytes used)."""
    if position >= len(data):
        raise MediaInvalid("ebml_size")
    first = data[position]
    length = next((n for n in range(1, 9) if first & (0x80 >> (n - 1))), 0)
    if not length or position + length > len(data):
        raise MediaInvalid("ebml_size")
    value = first & (0xFF >> length)
    for byte in data[position + 1:position + length]:
        value = (value << 8) | byte
    unknown = value == (1 << (7 * length)) - 1
    return (None if unknown else value), length


def _elements(data: bytes, start: int, end: int, *, stop_at: set[int] = frozenset()):
    """Yield (id, body start, body end) children of a sized master element.

    Matroska allows "unknown size" only for Segment and Cluster, which `webm_duration` and
    `_scan_cluster` walk themselves. Every caller of this function reads elements that must be
    sized (EBML header, Info, Tracks, TrackEntry, BlockGroup), so an unknown size here is
    invalid content (422), never an end of None for the caller to compute with.
    """
    position = start
    while position < end:
        element_id, id_length = _element_id(data, position)
        if element_id in stop_at:
            return
        size, size_length = _element_size(data, position + id_length)
        body = position + id_length + size_length
        if size is None:
            raise MediaInvalid("ebml_unknown_size")
        if body + size > end:
            raise MediaInvalid("ebml_truncated")
        yield element_id, body, body + size
        position = body + size


def _uint(data: bytes, body: int, end: int) -> int:
    if end - body > 8:
        raise MediaInvalid("ebml_uint")
    return int.from_bytes(data[body:end], "big")


def webm_duration(data: bytes) -> Fraction:
    header = next(_elements(data, 0, len(data)), None)
    if header is None or header[0] != EBML_HEADER or header[2] is None:
        raise MediaInvalid("ebml_header")
    doc_type = None
    for element_id, body, end in _elements(data, header[1], header[2]):
        if element_id == DOC_TYPE:
            doc_type = data[body:end].rstrip(b"\x00")
    if doc_type != b"webm":
        raise MediaUnsupported("matroska_not_webm")
    position = header[2]
    segment_id, id_length = _element_id(data, position)
    if segment_id != SEGMENT:
        raise MediaInvalid("ebml_segment")
    size, size_length = _element_size(data, position + id_length)
    segment_body = position + id_length + size_length
    segment_end = len(data) if size is None else segment_body + size
    if segment_end > len(data):
        raise MediaInvalid("ebml_truncated")

    scale = 1_000_000
    declared = None
    audio = video = False
    latest = None
    position = segment_body
    while position < segment_end:
        element_id, id_length = _element_id(data, position)
        size, size_length = _element_size(data, position + id_length)
        body = position + id_length + size_length
        end = segment_end if size is None else body + size
        if end > segment_end:
            raise MediaInvalid("ebml_truncated")
        if element_id == INFO:
            for child, child_body, child_end in _elements(data, body, end):
                if child == TIMECODE_SCALE:
                    scale = _uint(data, child_body, child_end)
                elif child == DURATION:
                    width = child_end - child_body
                    if width not in (4, 8):
                        raise MediaInvalid("ebml_duration")
                    declared = struct.unpack(">f" if width == 4 else ">d", data[child_body:child_end])[0]
        elif element_id == TRACKS:
            for child, child_body, child_end in _elements(data, body, end):
                if child != TRACK_ENTRY:
                    continue
                for field, field_body, field_end in _elements(data, child_body, child_end):
                    if field == TRACK_TYPE:
                        kind = _uint(data, field_body, field_end)
                        video |= kind == 1
                        audio |= kind == 2
        elif element_id == CLUSTER:
            cluster_end, last = _scan_cluster(data, body, end if size is not None else segment_end)
            if last is not None:
                latest = last if latest is None else max(latest, last)
            end = cluster_end
        elif size is None:
            raise MediaInvalid("ebml_unknown_size")
        position = end
    if video:
        raise MediaUnsupported("video_track")
    if not audio:
        raise MediaInvalid("webm_no_audio")
    if scale <= 0:
        raise MediaInvalid("ebml_scale")
    if declared is not None:
        # A present Duration must be a positive finite length (NaN, +-inf, 0 or negative is a
        # damaged header, rejected rather than silently ignored). It may lengthen what the block
        # timestamps show, never shorten it.
        if not math.isfinite(declared) or declared <= 0:
            raise MediaInvalid("ebml_duration")
        return max(Fraction(declared), Fraction(max(latest or 0, 0))) * scale / 1_000_000_000
    if latest is None or latest <= 0:
        raise MediaInvalid("webm_empty")
    return Fraction(latest * scale, 1_000_000_000)


def _scan_cluster(data: bytes, start: int, limit: int) -> tuple[int, int | None]:
    """End of the cluster and its latest block end time (in timecode units)."""
    base = 0
    latest = None
    position = start
    while position < limit:
        element_id, id_length = _element_id(data, position)
        if element_id in LEVEL_ONE:
            break  # an unknown-size cluster ends where the next level-1 element starts
        size, size_length = _element_size(data, position + id_length)
        if size is None:
            raise MediaInvalid("ebml_unknown_size")
        body = position + id_length + size_length
        end = body + size
        if end > limit:
            raise MediaInvalid("ebml_truncated")
        if element_id == CLUSTER_TIMECODE:
            base = _uint(data, body, end)
        elif element_id in (SIMPLE_BLOCK, BLOCK_GROUP):
            time = _block_time(data, body, end, element_id == BLOCK_GROUP)
            if time is not None:
                latest = base + time if latest is None else max(latest, base + time)
        position = end
    return position, latest


def _block_time(data: bytes, body: int, end: int, group: bool) -> int | None:
    duration = 0
    if group:
        block = None
        for child, child_body, child_end in _elements(data, body, end):
            if child == BLOCK:
                block = (child_body, child_end)
            elif child == BLOCK_DURATION:
                duration = _uint(data, child_body, child_end)
        if block is None:
            return None
        body, end = block
    _track, track_length = _element_size(data, body)
    if body + track_length + 2 > end:
        raise MediaInvalid("ebml_block")
    relative = struct.unpack_from(">h", data, body + track_length)[0]
    return relative + duration


DURATION_PARSERS = {
    "audio/wav": wav_duration,
    "audio/mpeg": mp3_duration,
    "audio/mp4": mp4_duration,
    "audio/webm": webm_duration,
}

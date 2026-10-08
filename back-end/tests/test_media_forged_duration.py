"""Declared container durations must not shorten the real timestamps/samples (B02), and damaged
containers must end as a media rejection, never a 500 (B03). Parser and upload API, both DBs."""

import random
import struct
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ManualMedia
from app.media.audio import DURATION_PARSERS
from app.media.errors import MediaInvalid, MediaRejected, MediaTooLarge
from app.media.inspection import inspect_media
from app.media.storage import LocalMediaStorage, set_media_storage
from tests import media_samples as samples
from tests.api_contract import login
from tests.factories import NOW, make_store, make_user
from tests.media_samples import box, full_box


def mp4_with_mdhd(data: bytes, duration: int) -> bytes:
    """Overwrite the (version 0) sound track mdhd duration."""
    patched = bytearray(data)
    mdhd = patched.index(b"mdhd")
    struct.pack_into(">I", patched, mdhd + 20, duration)
    return bytes(patched)


def mp4_sample_table(sample_seconds: int, *, mdhd_duration: int, elst_ms: int | None = None,
                     scale: int = 1000) -> bytes:
    """A progressive (non-fragmented) MP4 whose stts says `sample_seconds` of audio."""
    ftyp = box(b"ftyp", b"M4A \x00\x00\x00\x00isom")
    mvhd = full_box(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 0) + b"\x00" * 80)
    tkhd = full_box(b"tkhd", 0, 7, struct.pack(">III", 0, 0, 1) + b"\x00" * 68)
    mdhd = full_box(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, scale, mdhd_duration) + b"\x00" * 4)
    hdlr = full_box(b"hdlr", 0, 0, b"\x00" * 4 + b"soun" + b"\x00" * 12 + b"sound\x00")
    stts = full_box(b"stts", 0, 0, struct.pack(">III", 1, sample_seconds, scale))
    minf = box(b"minf", box(b"stbl", stts))
    parts = [tkhd]
    if elst_ms is not None:
        parts.append(box(b"edts", full_box(b"elst", 0, 0, struct.pack(">IIiI", 1, elst_ms, 0, 0x10000))))
    trak = box(b"trak", *parts, box(b"mdia", mdhd, hdlr, minf))
    return ftyp + box(b"moov", mvhd, trak) + box(b"mdat", b"\x00" * 64)


FORGED_TOO_LONG = {
    "webm_121s_timestamps_1s_duration": samples.webm((0, 121000), duration_ms=1000),
    "mp4_121s_fragments_1s_mdhd": mp4_with_mdhd(samples.mp4(fragments=[(121, 1000)], scale=1000), 1000),
    "mp4_121s_stts_1s_mdhd": mp4_sample_table(121, mdhd_duration=1000),
    "mp4_121s_stts_1s_edit_list": mp4_sample_table(121, mdhd_duration=121000, elst_ms=1000),
    "webm_300s_timestamps_119s_duration": samples.webm((0, 300000), duration_ms=119000),
    "mp4_600s_fragments_60s_mdhd": mp4_with_mdhd(samples.mp4(fragments=[(600, 1000)], scale=1000), 60000),
}

DAMAGED = {
    "webm_infinite_duration": samples.webm((0, 1000), duration_ms=float("inf")),
    "webm_nan_duration": samples.webm((0, 1000), duration_ms=float("nan")),
    "webm_negative_infinite_duration": samples.webm((0, 1000), duration_ms=float("-inf")),
    "webm_negative_duration": samples.webm((0, 1000), duration_ms=-5.0),
    "webm_zero_duration": samples.webm((0, 1000), duration_ms=0.0),
    "mp4_empty_mvhd_last_box": box(b"ftyp", b"M4A \0\0\0\0isom") + box(b"mdat", b"x") + box(b"moov", box(b"mvhd")),
    "mp4_mvhd_unknown_version": box(b"ftyp", b"M4A \0\0\0\0isom") + box(b"mdat", b"x")
    + box(b"moov", full_box(b"mvhd", 7, 0, b"\x00" * 96)),
    "mp4_empty_tfhd": samples.mp4(fragments=[(10, 1000)], scale=1000).replace(
        full_box(b"tfhd", 0, 0x08, struct.pack(">II", 1, 1000)), box(b"tfhd")),
}
DAMAGED["mp4_empty_elst"] = (
    box(b"ftyp", b"M4A \x00\x00\x00\x00isom")
    + box(b"moov",
          full_box(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 0) + b"\x00" * 80),
          box(b"trak",
              full_box(b"tkhd", 0, 7, struct.pack(">III", 0, 0, 1) + b"\x00" * 68),
              box(b"edts", box(b"elst")),
              box(b"mdia",
                  full_box(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 1000) + b"\x00" * 4),
                  full_box(b"hdlr", 0, 0, b"\x00" * 4 + b"soun" + b"\x00" * 12 + b"sound\x00"))))
    + box(b"mdat", b"\x00" * 8)
)


# --- parser -----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FORGED_TOO_LONG))
def test_short_declared_duration_does_not_hide_long_audio(name):
    with pytest.raises(MediaTooLarge):
        inspect_media(FORGED_TOO_LONG[name], "AUDIO")


@pytest.mark.parametrize("name", sorted(DAMAGED))
def test_damaged_audio_is_invalid_not_an_unhandled_error(name):
    with pytest.raises(MediaInvalid):
        inspect_media(DAMAGED[name], "AUDIO")


def test_longer_declared_duration_still_counts():
    # A declared length above the timestamps is honoured (it only ever makes the limit stricter).
    assert inspect_media(samples.webm((0, 1000), duration_ms=5000), "AUDIO").duration_ms == 5000
    with pytest.raises(MediaTooLarge):
        inspect_media(samples.webm((0, 1000), duration_ms=120_001), "AUDIO")


def test_boundary_and_encoder_delay_trim_are_kept():
    # Exactly 120 s by timestamps, by fragments and by sample table stays accepted.
    assert inspect_media(samples.webm((0, 120000), duration_ms=1000), "AUDIO").duration_ms == 120000
    fragments = mp4_with_mdhd(samples.mp4(fragments=[(120, 1000)], scale=1000), 1000)
    assert inspect_media(fragments, "AUDIO").duration_ms == 120000
    assert inspect_media(mp4_sample_table(120, mdhd_duration=0), "AUDIO").duration_ms == 120000
    # An edit list that trims AAC priming/padding (well under a second) still sets the length.
    trimmed = mp4_sample_table(121, mdhd_duration=121000, elst_ms=120_000)
    with pytest.raises(MediaTooLarge):
        inspect_media(trimmed, "AUDIO")  # a 1 s "trim" is not encoder delay
    delay = mp4_sample_table(120, mdhd_duration=120_046, elst_ms=120_000)
    assert inspect_media(delay, "AUDIO").duration_ms == 120000


SEEDS = [
    samples.webm(), samples.webm((0, 1000, 2000), duration_ms=2000.0), samples.webm(unknown_sizes=False),
    samples.mp4(), samples.mp4(fragments=[(48, 1000), (12, 1000)], scale=48000),
    mp4_sample_table(3, mdhd_duration=3000, elst_ms=2990), samples.mp3(40), samples.wav_seconds(0.5),
]
SPECIALS = [b"\xff\xff\xff\xff", b"\x00\x00\x00\x00", b"\x01\xff\xff\xff", b"\x7f\xf0\x00\x00\x00\x00\x00\x00",
            b"\xff\xf8\x00\x00\x00\x00\x00\x01", b"\x00\x00\x00\x08", b"\x00\x00\x00\x01"]


def _mutate(rng: random.Random, seed: bytes) -> bytes:
    data = bytearray(seed)
    action = rng.random()
    if action < 0.25:
        return bytes(data[:rng.randrange(len(data))])  # truncation anywhere
    if action < 0.4:
        cut = rng.randrange(len(data))
        return bytes(data[:cut] + data[cut + rng.randint(1, 16):])  # a hole
    for _ in range(rng.randint(1, 6)):
        index = rng.randrange(len(data))
        if rng.random() < 0.5:
            data[index] = rng.randrange(256)
        else:
            data[index:index + 8] = rng.choice(SPECIALS)
    return bytes(data)


def test_fuzzed_containers_raise_only_media_rejections():
    """Truncated and mutated recordings: each parser itself ends in MediaRejected (no safety net)."""
    rng = random.Random(119_0203)
    parsers = {"webm": DURATION_PARSERS["audio/webm"], "mp4": DURATION_PARSERS["audio/mp4"],
               "mp3": DURATION_PARSERS["audio/mpeg"], "wav": DURATION_PARSERS["audio/wav"]}
    kinds = ["webm"] * 3 + ["mp4"] * 3 + ["mp3", "wav"]
    for _ in range(4000):
        index = rng.randrange(len(SEEDS))
        data = _mutate(rng, SEEDS[index])
        try:
            parsers[kinds[index]](data)
        except MediaRejected:
            pass
        try:
            inspect_media(data, "AUDIO")
        except MediaRejected:
            pass


# --- upload API -------------------------------------------------------------------------------


@pytest.fixture
def world(api, db_engine, tmp_path):
    storage = LocalMediaStorage(tmp_path / "forged-media")
    set_media_storage(storage)
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW)
        db.commit()
        owner_id, store_id = owner.id, store.id
    yield api, login(api, owner_id), db_engine, store_id, storage
    set_media_storage(None)


def _upload(world, data):
    api, auth, engine, store_id, storage = world
    response = api.post(
        f"/api/stores/{store_id}/manual/media",
        headers=auth.headers(str(uuid.uuid4())),
        data={"purpose": "INTERVIEW_AUDIO"},
        files={"file": ("answer.bin", data, "application/octet-stream")},
    )
    with Session(engine) as db:
        rows = db.scalar(select(func.count()).select_from(ManualMedia))
    return response, rows, list(storage.iter_files("manual"))


@pytest.mark.parametrize("name", sorted(FORGED_TOO_LONG))
def test_upload_of_forged_short_duration_is_413(world, name):
    response, rows, files = _upload(world, FORGED_TOO_LONG[name])
    assert (response.status_code, response.json()["code"]) == (413, "MEDIA_TOO_LARGE"), response.text
    assert rows == 0 and files == []


@pytest.mark.parametrize("name", sorted(DAMAGED))
def test_upload_of_damaged_audio_is_422_without_row_or_file(world, name):
    response, rows, files = _upload(world, DAMAGED[name])
    assert (response.status_code, response.json()["code"]) == (422, "MEDIA_INVALID"), response.text
    assert rows == 0 and files == []


def test_every_truncation_and_field_overwrite_is_a_rejection_or_a_valid_length():
    """Exhaustive over small seeds: cut at every byte, and write special values at every offset
    (infinite/NaN floats, zero/max sizes, short box sizes). Found the B03 crashes on 50948e3."""
    seeds = [samples.webm((0, 1000), duration_ms=1000.0), samples.mp4(), mp4_sample_table(2, mdhd_duration=2000,
             elst_ms=1990), samples.mp4(fragments=[(2, 1000)], scale=1000)]
    for seed in seeds:
        parser = DURATION_PARSERS["audio/webm" if seed[:4] == b"\x1a\x45\xdf\xa3" else "audio/mp4"]
        variants = [seed[:cut] for cut in range(len(seed))]
        for offset in range(len(seed)):
            for special in SPECIALS:
                data = bytearray(seed)
                data[offset:offset + len(special)] = special
                variants.append(bytes(data[:len(seed)]))
        for data in variants:
            try:
                duration = parser(data)
            except MediaRejected:
                continue
            assert duration >= 0

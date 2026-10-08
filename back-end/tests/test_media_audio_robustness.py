"""Malformed audio containers must end as a media rejection (4xx), never an unhandled error."""
import random
import struct

import pytest

from app.media.errors import MediaInvalid, MediaRejected
from app.media.inspection import inspect_media
from tests import media_samples as samples
from tests.media_samples import ebml, uint

HEADER = ebml(0x1A45DFA3, uint(0x4286, 1), ebml(0x4282, b"webm"))
AUDIO_TRACK = ebml(0x1654AE6B, ebml(0xAE, uint(0xD7, 1), uint(0x83, 2)))
CLUSTER = ebml(0x1F43B675, uint(0xE7, 0), ebml(0xA3, b"\x81" + struct.pack(">h", 1000) + b"\x80" + b"\x00" * 8))


@pytest.mark.parametrize("segment_body", [
    # Unknown size is allowed for Segment and Cluster only; anywhere below it used to reach
    # integer arithmetic as None (TypeError -> 500).
    ebml(0x1549A966, ebml(0x2AD7B1, b"\x0f\x42\x40", unknown_size=True)) + AUDIO_TRACK + CLUSTER,
    ebml(0x1549A966, ebml(0x4489, struct.pack(">d", 1000.0), unknown_size=True)) + AUDIO_TRACK + CLUSTER,
    ebml(0x1654AE6B, ebml(0xAE, uint(0x83, 2), unknown_size=True)) + CLUSTER,
    ebml(0x1654AE6B, ebml(0xAE, ebml(0x83, b"\x02", unknown_size=True))) + CLUSTER,
])
def test_unknown_size_below_segment_and_cluster_is_invalid(segment_body):
    data = HEADER + ebml(0x18538067, segment_body, unknown_size=True)
    with pytest.raises(MediaInvalid):
        inspect_media(data, "AUDIO")


def test_mutated_recordings_never_raise_anything_but_a_rejection():
    rng = random.Random(20261006)
    seeds = [samples.webm(), samples.mp4(), samples.mp4(fragments=[(48000, 1)]), samples.mp3(40),
             samples.wav_seconds(1.0)]
    for _ in range(3000):
        data = bytearray(rng.choice(seeds))
        for _ in range(rng.randint(1, 6)):
            index = rng.randrange(len(data))
            if rng.random() < 0.6:
                data[index] = rng.randrange(256)
            else:
                data[index:index + 4] = rng.choice([b"\xff\xff\xff\xff", b"\x00\x00\x00\x00", b"\x01\xff\xff\xff"])
        try:
            inspect_media(bytes(data), "AUDIO")
        except MediaRejected:
            pass

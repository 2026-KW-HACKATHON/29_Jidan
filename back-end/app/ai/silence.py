"""Signal-level check for recordings that contain no sound at all (before paying for STT).

Only uncompressed integer PCM WAV can be measured without a codec library; every other format
(and anything the `wave` module cannot read) is "unknown" and goes to the provider, whose own
speech signal decides (see `app.ai.openai_provider`). The threshold is a peak, not an average:
one audible syllable anywhere in the file keeps the recording, so real speech is never dropped
here, only digital silence or a muted microphone's floor.
"""

import io
import sys
import wave
from array import array

# Peak at or below -60 dBFS: well under any microphone picking up a voice.
SILENCE_PEAK_RATIO = 0.001


def pcm_wav_is_silent(data: bytes) -> bool | None:
    """True when every sample of a PCM WAV stays within -60 dBFS, False when one does not,
    None when the file cannot be measured here (not WAV, compressed, 24-bit, damaged)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            width = audio.getsampwidth()
            frames = audio.readframes(audio.getnframes())
    except (wave.Error, EOFError, ValueError):
        return None
    if not frames:
        return None
    if width == 1:  # unsigned, centred on 128
        peak = max(abs(max(frames) - 128), abs(min(frames) - 128))
        return peak <= 128 * SILENCE_PEAK_RATIO
    typecode = {2: "h", 4: "i"}.get(width)
    if typecode is None:
        return None
    samples = array(typecode)
    samples.frombytes(frames[: len(frames) - len(frames) % width])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return None
    full_scale = 2 ** (8 * width - 1)
    peak = max(abs(max(samples)), abs(min(samples)))
    return peak <= full_scale * SILENCE_PEAK_RATIO

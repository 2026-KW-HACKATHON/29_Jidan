"""Silence policy for speech-to-text: speech presence, text and the business status, separately.

* speech presence: a PCM WAV under -60 dBFS is never sent (`app.ai.silence`); otherwise the
  provider's own signal (gpt-transcribe `languages: []`) marks "no speech" and its text is not used;
* text: kept as recognised when speech is present (or the backend gives no signal);
* business status: no speech -> ERROR TRANSCRIPTION_FAILED at once (not retried, no empty or
  invented answer is stored); speech -> READY with the text.
"""

import io
import math
import random
import struct
import uuid
import wave
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.ai import set_ai_provider
from app.ai.contracts import TranscriptionRequest
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.ai.openai_provider import OpenAiProvider
from app.ai.provider import RawTranscript
from app.ai.silence import pcm_wav_is_silent
from app.db.models import BackgroundTask
from app.media.storage import LocalMediaStorage, set_media_storage
from app.tasks import drain
from tests import media_samples as samples
from tests.api_contract import login
from tests.factories import NOW, make_store, make_user

HALLUCINATION = "시청해 주셔서 감사합니다."  # a typical sentence invented from silence or noise


def pcm16(samples_: list[float], rate=16000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"".join(struct.pack("<h", round(max(-1, min(1, s)) * 32767)) for s in samples_))
    return output.getvalue()


def silence(seconds=1.0) -> bytes:
    return pcm16([0.0] * int(16000 * seconds))


def noise(seconds=1.0, level=0.03) -> bytes:  # background noise about -30 dBFS
    rnd = random.Random(7)
    return pcm16([rnd.uniform(-level, level) for _ in range(int(16000 * seconds))])


def tone(seconds=1.0, level=0.3) -> bytes:  # stands in for a voice: loud and periodic
    return pcm16([level * math.sin(2 * math.pi * 220 * n / 16000) for n in range(int(16000 * seconds))])


# --- signal level ------------------------------------------------------------------------------

def test_digital_silence_and_a_muted_floor_are_silent():
    assert pcm_wav_is_silent(silence()) is True
    assert pcm_wav_is_silent(pcm16([0.0005, -0.0009] * 8000)) is True  # under -60 dBFS
    assert pcm_wav_is_silent(samples.wav_seconds(1)) is True  # 8-bit unsigned at its centre


def test_one_audible_sample_keeps_the_recording():
    quiet = [0.0] * 16000
    quiet[8000] = 0.01  # -40 dBFS: a single click or a distant syllable
    assert pcm_wav_is_silent(pcm16(quiet)) is False
    assert pcm_wav_is_silent(noise()) is False and pcm_wav_is_silent(tone()) is False
    eight_bit = bytearray(samples.wav_seconds(1))
    eight_bit[-1] = 0x90
    assert pcm_wav_is_silent(bytes(eight_bit)) is False


@pytest.mark.parametrize("data", [
    samples.mp3(40), samples.webm(), samples.mp4(), b"RIFF\x00\x00", b"",
    samples.wav(3 * 100, width=3),  # 24-bit: not measured here
    samples.wav(0),  # no frames
])
def test_unmeasurable_audio_is_unknown(data):
    assert pcm_wav_is_silent(data) is None


# --- provider backend --------------------------------------------------------------------------

class SttClient:
    def __init__(self, text, languages=None, has_languages=True):
        self.calls = 0
        self._text, self._languages, self._has = text, languages, has_languages
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        self.calls += 1
        if not self._has:
            return SimpleNamespace(text=self._text)
        return SimpleNamespace(text=self._text, languages=self._languages)


def openai_with(client, model="gpt-transcribe") -> OpenAiProvider:
    return OpenAiProvider(api_key="", model="gpt-6-luna", transcribe_model=model, client=client)


def transcribe(provider, audio, mime="audio/wav"):
    return provider.transcribe(TranscriptionRequest(audio=audio, mime_type=mime))


def test_silent_wav_is_rejected_without_a_paid_call():
    client = SttClient(HALLUCINATION, languages=[])
    with pytest.raises(AiError) as caught:
        transcribe(openai_with(client), silence())
    assert (caught.value.code, caught.value.detail, caught.value.retryable) == (
        AiErrorCode.EMPTY_TRANSCRIPT, "silent_audio", False)
    assert client.calls == 0


@pytest.mark.parametrize("audio,mime", [(noise(), "audio/wav"), (samples.webm(), "audio/webm")])
def test_text_without_a_detected_language_is_no_speech(audio, mime):
    client = SttClient(HALLUCINATION, languages=[])
    with pytest.raises(AiError) as caught:
        transcribe(openai_with(client), audio, mime)
    assert (caught.value.code, caught.value.detail) == (AiErrorCode.EMPTY_TRANSCRIPT, "no_speech")
    assert client.calls == 1


def test_speech_with_a_detected_language_is_kept_as_recognised():
    client = SttClient(" 네. ", languages=[{"code": "ko"}])
    assert transcribe(openai_with(client), tone()).text == "네."


def test_models_without_a_speech_signal_keep_their_text():
    """whisper-1 / gpt-4o-* return no `languages`: no signal, so nothing is dropped."""
    client = SttClient("마감은 열 시예요.", has_languages=False)
    assert transcribe(openai_with(client, "whisper-1"), tone()).text == "마감은 열 시예요."
    explicit_none = SttClient("마감은 열 시예요.", languages=None)
    assert transcribe(openai_with(explicit_none), tone()).text == "마감은 열 시예요."


def test_empty_text_is_empty_whatever_the_language():
    with pytest.raises(AiError) as caught:
        transcribe(openai_with(SttClient("  ", languages=[{"code": "ko"}])), tone())
    assert caught.value.code == AiErrorCode.EMPTY_TRANSCRIPT


def test_fake_backend_can_report_no_speech():
    fake = FakeAiProvider().script("transcribe", FakeOutcome.ok(RawTranscript(HALLUCINATION, speech_detected=False)),
                                   FakeOutcome.ok(RawTranscript("네.", speech_detected=True)))
    with pytest.raises(AiError) as caught:
        transcribe(fake, b"x")
    assert caught.value.detail == "no_speech"
    assert transcribe(fake, b"x").text == "네."


# --- business status through the API ----------------------------------------------------------

@pytest.fixture
def owner_store(api, db_engine, tmp_path):
    set_media_storage(LocalMediaStorage(tmp_path / "media"))
    with Session(db_engine) as db:
        store = make_store(db, owner=make_user(db, "OWNER"), approval_status="APPROVED", approved_at=NOW)
        db.commit()
        store_id, owner_id = store.id, store.owner_id
    yield store_id, login(api, owner_id)
    set_media_storage(None)
    set_ai_provider(None)


@pytest.mark.parametrize("audio,reply,status,text,calls", [
    (silence(2), (HALLUCINATION, []), "ERROR", None, 0),
    (noise(2), (HALLUCINATION, []), "ERROR", None, 1),
    (tone(2), ("야간조는 밤 10시부터예요.", [{"code": "ko"}]), "READY", "야간조는 밤 10시부터예요.", 1),
], ids=["silence", "noise", "speech"])
def test_interview_audio_status_follows_speech_presence(api, db_engine, owner_store, audio, reply, status,
                                                        text, calls):
    client = SttClient(*reply)
    store_id, auth = owner_store
    set_ai_provider(openai_with(client))
    uploaded = api.post(f"/api/stores/{store_id}/manual/media", headers=auth.headers(str(uuid.uuid4())),
                        data={"purpose": "INTERVIEW_AUDIO"}, files={"file": ("a.wav", audio, "audio/wav")})
    assert uploaded.status_code == 201, uploaded.text
    started = api.post(f"/api/stores/{store_id}/manual/transcriptions", headers=auth.headers(str(uuid.uuid4())),
                       json={"mediaId": uploaded.json()["id"]})
    assert started.status_code == 202
    runs = drain()
    assert [run.outcome for run in runs] == ["succeeded" if status == "READY" else "failed"]  # never retried
    body = api.get(f"/api/stores/{store_id}/manual/transcriptions/{started.json()['id']}").json()
    assert (body["status"], body["text"]) == (status, text)
    if status == "ERROR":
        assert body["error"]["code"] == "TRANSCRIPTION_FAILED" and HALLUCINATION not in str(body)
    assert client.calls == calls
    with Session(db_engine) as db:
        assert db.get(BackgroundTask, runs[0].task_id).tries == 1

"""Worker Q&A operations that reserve a task or write media answer 503 JOB_QUEUE_UNAVAILABLE on
an availability failure (openapi 503, ddc9459), roll everything back and accept the same
Idempotency-Key afterwards (team/reviews/manual-503-conditions.md)."""

import errno
import os
import uuid

import pytest

from app.ai.fake import FakeOutcome
from app.media import storage as storage_module
from app.tasks import drain
from tests import media_samples as samples
from tests.test_manual_503_operations import all_counts
from tests.test_qa_conversations_api import ask, asked, conversation, read_question, retry
from tests.test_qa_media_api import retry_transcription, transcribe, upload, uploaded
from tests.test_qa_questions_api import ctx, media_root  # noqa: F401 (fixtures)
from tests.test_task_enqueue_unavailable import failing_insert, unavailable


def files(ctx) -> list[str]:  # noqa: F811
    return [os.path.join(top, name) for top, _dirs, names in os.walk(ctx.storage.root) for name in names]


@pytest.mark.parametrize("purpose,data", [("QUESTION_IMAGE", samples.jpeg()), ("QUESTION_AUDIO", samples.wav_seconds(1))],
                         ids=["photo", "audio"])
def test_qa_upload_storage_failure_is_503_and_retryable(ctx, monkeypatch, purpose, data):  # noqa: F811
    full, real = [True], os.fsync

    def fsync(fd):
        if full[0]:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(fd)

    monkeypatch.setattr(storage_module.os, "fsync", fsync)
    key = str(uuid.uuid4())
    before, files_before = all_counts(ctx.engine), files(ctx)
    response = upload(ctx, data, purpose, key=key)
    assert (response.status_code, response.json()["code"]) == (503, "JOB_QUEUE_UNAVAILABLE"), response.text
    assert all_counts(ctx.engine) == before and files(ctx) == files_before
    full[0] = False
    retried = upload(ctx, data, purpose, key=key)
    assert retried.status_code == 201, retried.text
    assert all_counts(ctx.engine)["qa_media"] == before["qa_media"] + 1
    assert len(files(ctx)) == len(files_before) + 1
    assert upload(ctx, data, purpose, key=key).headers.get("Idempotent-Replayed") == "true"
    assert len(files(ctx)) == len(files_before) + 1


def ask_question(ctx, fake_ai):  # noqa: F811
    conversation_id = conversation(ctx)
    return lambda key: ask(ctx, conversation_id, key=key)


def retry_question(ctx, fake_ai):  # noqa: F811
    conversation_id = conversation(ctx)
    fake_ai.script("answer_question", FakeOutcome.fail("refused"))
    question = asked(ctx, conversation_id)["id"]
    drain()
    assert read_question(ctx, conversation_id, question).json()["status"] == "ERROR"
    return lambda key: retry(ctx, conversation_id, question, key=key)


def transcribe_audio(ctx, fake_ai):  # noqa: F811
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    return lambda key: transcribe(ctx, media_id, key=key)


def retry_audio(ctx, fake_ai):  # noqa: F811
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    fake_ai.script("transcribe", FakeOutcome.fail("input_rejected"))
    transcription = transcribe(ctx, media_id).json()["id"]
    drain()
    return lambda key: retry_transcription(ctx, transcription, key=key)


SCENARIOS = {
    "askManualQuestion": ask_question,
    "retryManualQuestion": retry_question,
    "transcribeQAQuestionAudio": transcribe_audio,
    "retryQAQuestionTranscription": retry_audio,
}


@pytest.mark.parametrize("operation", sorted(SCENARIOS))
def test_qa_task_reservation_failure_is_503_and_rolls_back(ctx, fake_ai, operation):  # noqa: F811
    send = SCENARIOS[operation](ctx, fake_ai)
    key = str(uuid.uuid4())
    before = all_counts(ctx.engine)
    with failing_insert(ctx.engine, "background_tasks", unavailable()) as hits:
        response = send(key)
    assert (response.status_code, response.json()["code"]) == (503, "JOB_QUEUE_UNAVAILABLE"), response.text
    assert hits and all_counts(ctx.engine) == before
    retried = send(key)
    assert retried.status_code == 202, retried.text
    assert all_counts(ctx.engine)["background_tasks"] == before["background_tasks"] + 1
    assert send(key).headers.get("Idempotent-Replayed") == "true"

"""OpenAI backend wiring without network: request options, output parsing, error classification.
The real API is exercised only by `@pytest.mark.openai` tests (opt-in)."""

import json
import logging
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.ai.contracts import (
    DialogueTurn,
    IntentBrief,
    SufficiencyRequest,
    TranscriptionRequest,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.openai_provider import OpenAiProvider, classify

REQUEST = SufficiencyRequest(
    intent=IntentBrief(key="k", stage="COMMON_TASKS", base_question="q", coverage_criteria="c"),
    dialogue=(DialogueTurn(question="q", answer="비밀 답변 원문", depth=0),), depth=0,
)
VALID = json.dumps({"sufficient": True, "probability": 0.7, "missing_aspects": []})


def message(*parts):
    return SimpleNamespace(type="message", content=[SimpleNamespace(**part) for part in parts])


class StubClient:
    def __init__(self, output=None, error=None, status="completed", transcript="안녕하세요"):
        self.kwargs = None
        self.audio_kwargs = None
        self._output, self._error, self._status, self._transcript = output, error, status, transcript
        self.responses = SimpleNamespace(create=self._create)
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._transcribe))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self._error:
            raise self._error
        return SimpleNamespace(output=self._output, status=self._status)

    def _transcribe(self, **kwargs):
        self.audio_kwargs = kwargs
        if self._error:
            raise self._error
        return SimpleNamespace(text=self._transcript)


def provider(client) -> OpenAiProvider:
    """Jev on the Responses path (the structured-output wiring these tests pin); the Decisions
    path has its own tests in test_ai_decisions.py."""
    return OpenAiProvider(api_key="", model="gpt-6-luna", transcribe_model="gpt-transcribe",
                          reasoning_effort="low", timeout_seconds=30, judge_backend="responses",
                          client=client)


def test_request_uses_strict_schema_no_storage_and_the_timeout():
    client = StubClient(output=[message({"type": "output_text", "text": VALID})])
    assert provider(client).judge_sufficiency(REQUEST).probability == 0.7
    kwargs = client.kwargs
    fmt = kwargs["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "sufficiency_judgement"
    assert kwargs["store"] is False and kwargs["timeout"] == 30 and kwargs["model"] == "gpt-6-luna"
    assert kwargs["reasoning"] == {"effort": "low"}
    assert kwargs["service_tier"] == "fast"
    assert "비밀 답변 원문" not in kwargs["instructions"]
    assert "비밀 답변 원문" in kwargs["input"][0]["content"][0]["text"]


def test_extra_message_items_and_reasoning_items_are_skipped():
    client = StubClient(output=[
        SimpleNamespace(type="reasoning"),
        message({"type": "output_text", "text": "</assistant:commentary>"}),
        message({"type": "output_text", "text": VALID}),
    ])
    assert provider(client).judge_sufficiency(REQUEST).sufficient


def test_refusal_and_empty_output_are_classified():
    refusal = StubClient(output=[message({"type": "refusal", "refusal": "no"})])
    with pytest.raises(AiError, match="refused"):
        provider(refusal).judge_sufficiency(REQUEST)
    incomplete = StubClient(output=[], status="incomplete")
    with pytest.raises(AiError, match="invalid_output") as caught:
        provider(incomplete).judge_sufficiency(REQUEST)
    assert caught.value.retryable


def status_error(cls, status, body=None):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/x"),
                              json=body or {"error": {"message": "secret prompt echo sk-abc"}})
    return cls("secret prompt echo sk-abc", response=response, body=body)


@pytest.mark.parametrize("error,code", [
    (openai.APITimeoutError(request=httpx.Request("POST", "https://x")), AiErrorCode.TIMEOUT),
    (openai.APIConnectionError(request=httpx.Request("POST", "https://x")), AiErrorCode.UNAVAILABLE),
    (status_error(openai.RateLimitError, 429), AiErrorCode.RATE_LIMITED),
    (status_error(openai.AuthenticationError, 401), AiErrorCode.NOT_CONFIGURED),
    (status_error(openai.PermissionDeniedError, 403), AiErrorCode.NOT_CONFIGURED),
    (status_error(openai.NotFoundError, 404), AiErrorCode.NOT_CONFIGURED),
    (status_error(openai.BadRequestError, 400), AiErrorCode.INPUT_REJECTED),
    (status_error(openai.InternalServerError, 500), AiErrorCode.UNAVAILABLE),
    (status_error(openai.APIStatusError, 413), AiErrorCode.INPUT_REJECTED),
    (status_error(openai.APIStatusError, 529), AiErrorCode.UNAVAILABLE),
])
def test_sdk_errors_are_classified(error, code):
    assert classify(error).code == code


def test_quota_exhaustion_is_not_retryable():
    error = status_error(openai.RateLimitError, 429, body={"code": "insufficient_quota"})
    error.code = "insufficient_quota"
    assert not classify(error).retryable


def test_provider_text_and_traceback_never_reach_the_error_or_logs(caplog):
    client = StubClient(error=status_error(openai.BadRequestError, 400))
    caplog.set_level(logging.INFO, logger="jidan.ai")
    with pytest.raises(AiError) as caught:
        provider(client).judge_sufficiency(REQUEST)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert "secret" not in str(caught.value) and "sk-abc" not in caplog.text
    assert "비밀 답변 원문" not in caplog.text and "outcome=input_rejected" in caplog.text


def test_transcription_sends_the_audio_with_language_and_mime():
    client = StubClient(transcript=" 야간조는 7시에 끝나요 ")
    result = provider(client).transcribe(TranscriptionRequest(audio=b"abc", mime_type="audio/webm"))
    assert result.text == "야간조는 7시에 끝나요" and result.meta.model == "gpt-transcribe"
    kwargs = client.audio_kwargs
    assert kwargs["file"] == ("answer.webm", b"abc", "audio/webm") and kwargs["languages"] == ["ko"]
    assert kwargs["model"] == "gpt-transcribe" and kwargs["response_format"] == "json"


def test_older_transcription_models_get_language_and_prompt():
    client = StubClient()
    legacy = OpenAiProvider(api_key="", model="gpt-6-luna", transcribe_model="whisper-1", client=client)
    legacy.transcribe(TranscriptionRequest(audio=b"abc", mime_type="audio/wav", keywords=("마감",)))
    assert client.audio_kwargs["language"] == "ko" and "마감" in client.audio_kwargs["prompt"]


def test_transcription_errors_are_classified():
    client = StubClient(error=openai.APITimeoutError(request=httpx.Request("POST", "https://x")))
    with pytest.raises(AiError, match="timeout"):
        provider(client).transcribe(TranscriptionRequest(audio=b"abc", mime_type="audio/wav"))



@pytest.mark.parametrize("tier", ["auto", "default", "fast", "priority"])
def test_service_tier_is_sent_only_to_responses(tier):
    client = StubClient(output=[message({"type": "output_text", "text": VALID})])
    instance = OpenAiProvider(api_key="", model="gpt-6-luna", transcribe_model="gpt-transcribe",
                              service_tier=tier, judge_backend="responses", client=client)
    instance.judge_sufficiency(REQUEST)
    assert client.kwargs["service_tier"] == tier
    instance.transcribe(TranscriptionRequest(audio=b"audio", mime_type="audio/mpeg"))
    assert "service_tier" not in client.audio_kwargs


def test_invalid_service_tier_rejected_before_client_creation():
    with pytest.raises(ValueError, match="OPENAI_SERVICE_TIER"):
        OpenAiProvider(api_key="sk-test-not-used", model="gpt-6-luna", transcribe_model="gpt-transcribe",
                       service_tier="invalid")

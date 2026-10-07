"""OpenAI backend: Responses API with strict JSON Schema output, the Decisions API for Jev, and
audio transcriptions.

* `store=False`: interview answers and worker questions are not kept by the provider. The
  Decisions request schema has no `store` (or any other retention) option at all
  (`additionalProperties: false` over model/input/questions/safety_identifier), so nothing is
  sent for it there.
* Reasoning effort per operation group: questions `question_effort` (default low), manual writing
  (`summarize_intent`, `compose_draft`, `revise_structure`) `writing_effort` (default medium,
  with the longer `writing_timeout_seconds`), everything else `reasoning_effort`. Decisions takes
  no effort.
* The installed SDK has no Decisions method, so `_decide` uses the client's low-level `post`:
  same auth, base URL, timeout handling and exception types, hence the same `classify`.
* `max_retries=0` on the client: retries and backoff belong to the task runner, which records
  every attempt; the SDK must not multiply calls behind its back.
* Every SDK exception is classified into an `AiError` and re-raised `from None`; the provider's
  message (which can echo input) never reaches our logs or responses.
* gpt-6-luna sometimes returns more than one message item for structured output; every
  `output_text` item is offered to the parser and the first valid one wins.
* Speech presence (silence policy): a PCM WAV whose peak stays under -60 dBFS is not sent at all
  (`app.ai.silence`). Otherwise gpt-transcribe reports the detected `languages`; an explicit
  empty list means "no reliable language prediction" (the guide's wording), which is what it
  returns for silence and background noise, so a non-empty text with `languages: []` is treated
  as no speech instead of being stored. Models without that field (whisper-1, gpt-4o-*) give no
  signal and their text is kept. gpt-transcribe offers no logprobs or no_speech_prob
  (API reference, `include` and `verbose_json` are limited to other models).
"""

import base64
import json
from collections.abc import Sequence
from typing import Any

import httpx
import openai

from app.ai.contracts import ImageInput, TranscriptionRequest
from app.ai.decisions import Thresholds
from app.ai.errors import AiError, AiErrorCode
from app.ai.provider import JUDGE_BACKENDS, AiProvider, RawTranscript
from app.ai.schemas import OUTPUTS
from app.ai.silence import pcm_wav_is_silent

# Output budget per operation (reasoning tokens count against it as well).
MAX_OUTPUT_TOKENS = {
    "judge_sufficiency": 4000,
    # A question with guidance and examples is a few hundred tokens at low effort. The cap keeps
    # gpt-6-luna's rare whitespace run-away inside the JSON (seen live, ~5% of calls, 39 s at
    # 4000) short: it ends as INVALID_OUTPUT in seconds and the runner retries.
    "generate_question": 1500,
    "summarize_intent": 24000,
    "revise_structure": 32000,
    "compose_draft": 32000,
    "answer_question": 8000,
}
WRITING_OPERATIONS = frozenset({"summarize_intent", "compose_draft", "revise_structure"})
QUESTION_OPERATIONS = frozenset({"generate_question"})
AUDIO_EXTENSIONS = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/webm": "webm", "audio/wav": "wav"}


def classify(error: Exception) -> AiError:
    """Map an SDK exception to an AiError without looking at (or keeping) its message."""
    if isinstance(error, openai.APITimeoutError):
        return AiError(AiErrorCode.TIMEOUT)
    if isinstance(error, openai.APIConnectionError):
        return AiError(AiErrorCode.UNAVAILABLE)
    if isinstance(error, openai.RateLimitError):
        # An exhausted quota is also a 429 but waiting will not fix it.
        if getattr(error, "code", None) == "insufficient_quota":
            return AiError(AiErrorCode.NOT_CONFIGURED, detail="insufficient_quota")
        return AiError(AiErrorCode.RATE_LIMITED)
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError,
                          openai.NotFoundError)):
        return AiError(AiErrorCode.NOT_CONFIGURED)
    if isinstance(error, (openai.BadRequestError, openai.UnprocessableEntityError)):
        return AiError(AiErrorCode.INPUT_REJECTED)
    if isinstance(error, openai.APIStatusError):
        if error.status_code == 413:
            return AiError(AiErrorCode.INPUT_REJECTED)
        return AiError(AiErrorCode.UNAVAILABLE)
    return AiError(AiErrorCode.UNAVAILABLE)


class OpenAiProvider(AiProvider):
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        transcribe_model: str,
        reasoning_effort: str | None = "low",
        question_effort: str | None = "low",
        writing_effort: str | None = "medium",
        timeout_seconds: float = 60.0,
        writing_timeout_seconds: float | None = None,
        transcribe_timeout_seconds: float = 120.0,
        judge_backend: str = "decisions",
        judge_thresholds: Thresholds | None = None,
        client: openai.OpenAI | None = None,
    ):
        if not api_key and client is None:
            raise ValueError("OpenAI API key is required")
        if judge_backend not in JUDGE_BACKENDS:
            raise ValueError("judge_backend must be decisions or responses")
        self.model = model
        self.transcribe_model = transcribe_model
        self.reasoning_effort = reasoning_effort
        self.question_effort = question_effort
        self.writing_effort = writing_effort
        self.timeout_seconds = timeout_seconds
        self.writing_timeout_seconds = writing_timeout_seconds or timeout_seconds
        self.transcribe_timeout_seconds = transcribe_timeout_seconds
        self.judge_backend = judge_backend
        self.judge_thresholds = judge_thresholds or Thresholds()
        self._client = client or openai.OpenAI(api_key=api_key, max_retries=0, timeout=timeout_seconds)

    @property
    def max_call_seconds(self) -> float:
        return max(self.timeout_seconds, self.writing_timeout_seconds, self.transcribe_timeout_seconds)

    def __repr__(self) -> str:  # never show the client (it holds the key)
        return f"OpenAiProvider(model={self.model!r}, transcribe_model={self.transcribe_model!r})"

    def effort_for(self, operation: str) -> str | None:
        if operation in QUESTION_OPERATIONS:
            return self.question_effort
        if operation in WRITING_OPERATIONS:
            return self.writing_effort
        return self.reasoning_effort

    def _timeout_for(self, operation: str) -> float:
        return self.writing_timeout_seconds if operation in WRITING_OPERATIONS else self.timeout_seconds

    def _complete(
        self, operation: str, instructions: str, message: str, images: Sequence[ImageInput],
    ) -> list[str]:
        name, schema, _parser = OUTPUTS[operation]
        content = [{"type": "input_text", "text": message}]
        for image in images:
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append({
                "type": "input_image", "image_url": f"data:{image.mime_type};base64,{encoded}",
                "detail": "auto",
            })
        options = {}
        effort = self.effort_for(operation)
        if effort:
            options["reasoning"] = {"effort": effort}
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=instructions,
                input=[{"role": "user", "content": content}],
                text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
                max_output_tokens=MAX_OUTPUT_TOKENS[operation],
                store=False,
                timeout=self._timeout_for(operation),
                **options,
            )
        except openai.OpenAIError as error:
            raise classify(error) from None
        candidates: list[str] = []
        refused = False
        for item in response.output or ():
            if getattr(item, "type", None) != "message":
                continue
            for part in getattr(item, "content", None) or ():
                kind = getattr(part, "type", None)
                if kind == "output_text" and getattr(part, "text", None):
                    candidates.append(part.text)
                elif kind == "refusal":
                    refused = True
        if not candidates:
            if refused:
                raise AiError(AiErrorCode.REFUSED)
            raise AiError(AiErrorCode.INVALID_OUTPUT, detail=f"no_output:{response.status}")
        return candidates

    def _decide(self, body: dict[str, Any]) -> Any:
        try:
            response = self._client.post(
                "/decisions", cast_to=httpx.Response, body=body,
                options={"timeout": self.timeout_seconds},
            )
        except openai.OpenAIError as error:
            raise classify(error) from None
        try:
            return json.loads(response.content)
        except ValueError:
            raise AiError(AiErrorCode.INVALID_OUTPUT, detail="decision_not_json") from None

    def _transcribe(self, request: TranscriptionRequest) -> str | RawTranscript:
        if request.mime_type == "audio/wav" and pcm_wav_is_silent(request.audio):
            raise AiError(AiErrorCode.EMPTY_TRANSCRIPT, detail="silent_audio")
        filename = f"answer.{AUDIO_EXTENSIONS[request.mime_type]}"
        # gpt-transcribe takes `languages` and `keywords`; older models take `language`/`prompt`
        # (OpenAI speech-to-text guide, parameter table per model).
        if self.transcribe_model.startswith("gpt-transcribe"):
            options = {"languages": [request.language]}
            if request.keywords:
                options["keywords"] = list(request.keywords)
        else:
            options = {"language": request.language}
            if request.keywords:
                options["prompt"] = "다음 용어가 나올 수 있어요: " + ", ".join(request.keywords)
        try:
            result = self._client.audio.transcriptions.create(
                model=self.transcribe_model,
                file=(filename, request.audio, request.mime_type),
                response_format="json",
                timeout=self.transcribe_timeout_seconds,
                **options,
            )
        except openai.OpenAIError as error:
            raise classify(error) from None
        if isinstance(result, str):
            return result
        languages = getattr(result, "languages", None)
        if languages is None and getattr(result, "model_extra", None):
            languages = result.model_extra.get("languages")
        return RawTranscript(
            text=getattr(result, "text", None) or "",
            speech_detected=False if isinstance(languages, list) and not languages else None,
        )

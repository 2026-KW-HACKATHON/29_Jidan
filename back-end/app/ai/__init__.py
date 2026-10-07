"""AI and speech-to-text for manual interviews, drafts and worker Q&A.

Use `get_ai_provider()` from task-runner handlers (never inside a request transaction) and call the
domain operations on it; see `app.ai.provider.AiProvider` and `back-end/docs/ai-foundation.md`.

Configuration (environment):
    AI_PROVIDER                      openai (default) | fake (local/dev demos only)
    OPENAI_API_KEY                   required for openai; read from the environment only
    OPENAI_MODEL                     default gpt-6-luna ("ChatGPT 6 Luna")
    OPENAI_FALLBACK_MODEL            optional second model tried after a retryable failure
    OPENAI_TRANSCRIBE_MODEL          default gpt-transcribe
    OPENAI_REASONING_EFFORT          default low (none|low|medium|high|xhigh|max, or empty);
                                     answer_question, and Jev on the responses backend
    OPENAI_QUESTION_REASONING_EFFORT default low: generate_question
    OPENAI_WRITING_REASONING_EFFORT  default medium: summarize_intent, compose_draft, revise_structure
    OPENAI_JUDGE_BACKEND             decisions (default; POST /v1/decisions) | responses
    OPENAI_JUDGE_ASPECT_THRESHOLD    default 0.7 (0.5 <= x < 1): an aspect counts as covered
    OPENAI_JUDGE_NOT_APPLICABLE_THRESHOLD default 0.8: the intent counts as not applicable
    OPENAI_TIMEOUT_SECONDS           default 60 (per LLM call)
    OPENAI_WRITING_TIMEOUT_SECONDS   default 120 (per manual-writing call, medium effort)
    OPENAI_TRANSCRIBE_TIMEOUT_SECONDS default 120

Every operation uses OPENAI_MODEL (and OPENAI_FALLBACK_MODEL after a retryable failure); there is
no per-operation model, so a fallback always replaces one model with one other.
"""

import logging
import math
import os
import threading

from app.ai.decisions import thresholds_from_env
from app.ai.errors import AiError, AiErrorCode
from app.ai.provider import JUDGE_BACKENDS, AiProvider, FallbackAiProvider, UnconfiguredAiProvider

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_TRANSCRIBE_MODEL = "gpt-transcribe"
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")

logger = logging.getLogger("jidan.ai")
_lock = threading.Lock()
_provider: AiProvider | None = None


def _seconds(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if not math.isfinite(value) or not 1 <= value <= 600:
        raise ValueError(f"{name} must be between 1 and 600 seconds")
    return value


def _effort(name: str, default: str) -> str | None:
    effort = os.getenv(name, default).strip().lower() or None
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ValueError(f"{name} is not a supported value")
    return effort


def _judge_backend() -> str:
    backend = os.getenv("OPENAI_JUDGE_BACKEND", "").strip().lower() or "decisions"
    if backend not in JUDGE_BACKENDS:
        raise ValueError("OPENAI_JUDGE_BACKEND must be decisions or responses")
    return backend


def build_provider_from_env() -> AiProvider:
    kind = os.getenv("AI_PROVIDER", "openai").strip().lower() or "openai"
    judge_backend = _judge_backend()
    thresholds = thresholds_from_env()
    if kind == "fake":
        if os.getenv("APP_ENV", "local") == "production":
            raise ValueError("AI_PROVIDER=fake is not allowed in production")
        from app.ai.fake import FakeAiProvider

        fake = FakeAiProvider(timeout_seconds=_seconds("OPENAI_TIMEOUT_SECONDS", 60.0),
                              judge_backend=judge_backend)
        fake.judge_thresholds = thresholds
        return fake
    if kind != "openai":
        raise ValueError("AI_PROVIDER must be openai or fake")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY is not set; AI and transcription requests will fail")
        return UnconfiguredAiProvider()
    effort = _effort("OPENAI_REASONING_EFFORT", "low")
    question_effort = _effort("OPENAI_QUESTION_REASONING_EFFORT", "low")
    writing_effort = _effort("OPENAI_WRITING_REASONING_EFFORT", "medium")
    from app.ai.openai_provider import OpenAiProvider

    def make(model: str) -> AiProvider:
        return OpenAiProvider(
            api_key=api_key, model=model,
            transcribe_model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "").strip() or DEFAULT_TRANSCRIBE_MODEL,
            reasoning_effort=effort, question_effort=question_effort, writing_effort=writing_effort,
            timeout_seconds=_seconds("OPENAI_TIMEOUT_SECONDS", 60.0),
            writing_timeout_seconds=_seconds("OPENAI_WRITING_TIMEOUT_SECONDS", 120.0),
            transcribe_timeout_seconds=_seconds("OPENAI_TRANSCRIBE_TIMEOUT_SECONDS", 120.0),
            judge_backend=judge_backend, judge_thresholds=thresholds,
        )

    primary = make(os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_MODEL)
    fallback_model = os.getenv("OPENAI_FALLBACK_MODEL", "").strip()
    if fallback_model and fallback_model != primary.model:
        return FallbackAiProvider(primary, make(fallback_model))
    return primary


def get_ai_provider() -> AiProvider:
    """The process-wide provider, built from the environment on first use."""
    global _provider
    with _lock:
        if _provider is None:
            _provider = build_provider_from_env()
        return _provider


def set_ai_provider(provider: AiProvider | None) -> None:
    """Replace the provider (tests install a FakeAiProvider); None rebuilds from env next time."""
    global _provider
    with _lock:
        _provider = provider


__all__ = [
    "AiError", "AiErrorCode", "AiProvider", "build_provider_from_env", "get_ai_provider",
    "set_ai_provider",
]

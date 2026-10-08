"""AI and speech-to-text for manual interviews, drafts and worker Q&A.

Use `get_ai_provider()` from task-runner handlers (never inside a request transaction) and call the
domain operations on it; see `app.ai.provider.AiProvider` and `back-end/docs/ai-foundation.md`.

Configuration (environment):
    AI_PROVIDER                      openai (default) | fake (local/dev demos only)
    OPENAI_API_KEY                   required for openai; read from the environment only
    OPENAI_MODEL                     default gpt-6-luna ("ChatGPT 6 Luna")
    OPENAI_FALLBACK_MODEL            optional second model tried after a retryable failure
    OPENAI_TRANSCRIBE_MODEL          default gpt-transcribe
    OPENAI_SERVICE_TIER              default fast (auto|default|fast|priority)
    OPENAI_REASONING_EFFORT          default low (none|low|medium|high|xhigh|max, or empty)
    OPENAI_TIMEOUT_SECONDS           default 60 (per LLM call)
    OPENAI_TRANSCRIBE_TIMEOUT_SECONDS default 120
"""

import logging
import math
import os
import threading

from app.ai.errors import AiError, AiErrorCode
from app.ai.provider import AiProvider, FallbackAiProvider, UnconfiguredAiProvider

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


def build_provider_from_env() -> AiProvider:
    kind = os.getenv("AI_PROVIDER", "openai").strip().lower() or "openai"
    if kind == "fake":
        if os.getenv("APP_ENV", "local") == "production":
            raise ValueError("AI_PROVIDER=fake is not allowed in production")
        from app.ai.fake import FakeAiProvider

        return FakeAiProvider(timeout_seconds=_seconds("OPENAI_TIMEOUT_SECONDS", 60.0))
    if kind != "openai":
        raise ValueError("AI_PROVIDER must be openai or fake")
    from app.ai.openai_provider import SERVICE_TIERS, OpenAiProvider

    service_tier = os.getenv("OPENAI_SERVICE_TIER", "fast").strip().lower() or "fast"
    if service_tier not in SERVICE_TIERS:
        raise ValueError("OPENAI_SERVICE_TIER is not a supported value")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY is not set; AI and transcription requests will fail")
        return UnconfiguredAiProvider()
    effort = os.getenv("OPENAI_REASONING_EFFORT", "low").strip().lower() or None
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ValueError("OPENAI_REASONING_EFFORT is not a supported value")
    def make(model: str) -> AiProvider:
        return OpenAiProvider(
            api_key=api_key, model=model,
            transcribe_model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "").strip() or DEFAULT_TRANSCRIBE_MODEL,
            reasoning_effort=effort, service_tier=service_tier,
            timeout_seconds=_seconds("OPENAI_TIMEOUT_SECONDS", 60.0),
            transcribe_timeout_seconds=_seconds("OPENAI_TRANSCRIBE_TIMEOUT_SECONDS", 120.0),
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

"""Failure classification for AI/STT calls.

Provider errors are reduced to a small set of internal codes before they leave `app.ai`.
The original provider message, the prompt and the API key never become part of an `AiError`
(its `str()` is the code only), so callers may log it and map it to a public API error safely.
"""

from enum import StrEnum


class AiErrorCode(StrEnum):
    # Retryable: the same input may succeed later.
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"  # connection failure, 5xx, overloaded
    INVALID_OUTPUT = "invalid_output"  # not JSON, schema mismatch, failed server re-validation
    # Not retryable: repeating the same input will fail the same way.
    REFUSED = "refused"
    INPUT_REJECTED = "input_rejected"  # 400/413/422 from the provider: our request or the media
    NOT_CONFIGURED = "not_configured"  # no key, unknown provider, auth/permission failure
    EMPTY_TRANSCRIPT = "empty_transcript"  # silence or whitespace-only recognition


RETRYABLE_CODES = frozenset({
    AiErrorCode.TIMEOUT, AiErrorCode.RATE_LIMITED, AiErrorCode.UNAVAILABLE,
    AiErrorCode.INVALID_OUTPUT,
})


class AiError(Exception):
    """A classified AI/STT failure. Safe to log: carries no provider text, prompt or secret."""

    def __init__(self, code: AiErrorCode, *, detail: str | None = None) -> None:
        self.code = AiErrorCode(code)
        # `detail` is for tests and server logs only, and only ever set from our own constants
        # (e.g. which validation rule failed); never from provider responses or user content.
        self.detail = detail
        super().__init__(self.code.value)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    def __repr__(self) -> str:
        return f"AiError({self.code.value!r}, retryable={self.retryable})"

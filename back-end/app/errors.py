"""Error contract shared by every endpoint (openapi.yaml `Error` schema).

Every failure under /api is `{"code", "message", "requestId", "fieldErrors"}`. Messages are
safe to show to users and never contain internal details, SQL, stack traces or input values.
"""

import logging
import uuid
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("jidan.errors")

MAX_FIELD_ERRORS = 100


class ErrorCode(StrEnum):
    """Every `code` defined in openapi.yaml. Compare and serialize them as plain strings."""

    # Common
    INVALID_REQUEST = "INVALID_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    REGISTRATION_REQUIRED = "REGISTRATION_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
    CSRF_INVALID = "CSRF_INVALID"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # Authentication and registration
    OAUTH_STATE_INVALID = "OAUTH_STATE_INVALID"
    OAUTH_CODE_INVALID = "OAUTH_CODE_INVALID"
    GOOGLE_IDENTITY_INVALID = "GOOGLE_IDENTITY_INVALID"
    GOOGLE_UNAVAILABLE = "GOOGLE_UNAVAILABLE"
    GOOGLE_ACCESS_DENIED = "GOOGLE_ACCESS_DENIED"  # redirect-only: the user declined consent
    ALREADY_REGISTERED = "ALREADY_REGISTERED"
    REGISTRATION_CONFLICT = "REGISTRATION_CONFLICT"
    ADMIN_PASSWORD_INVALID = "ADMIN_PASSWORD_INVALID"
    # Stores and approval
    STORE_NOT_FOUND = "STORE_NOT_FOUND"
    STORE_ALREADY_REGISTERED = "STORE_ALREADY_REGISTERED"
    STORE_OUTSIDE_SERVICE_AREA = "STORE_OUTSIDE_SERVICE_AREA"
    STORE_APPROVAL_REQUEST_NOT_FOUND = "STORE_APPROVAL_REQUEST_NOT_FOUND"
    STORE_APPROVAL_NOT_ALLOWED = "STORE_APPROVAL_NOT_ALLOWED"
    STORE_APPROVAL_REQUIRED = "STORE_APPROVAL_REQUIRED"
    OWNER_APPROVAL_PENDING = "OWNER_APPROVAL_PENDING"
    ONBOARDING_NOT_FOUND = "ONBOARDING_NOT_FOUND"
    HOME_STORE_LIMIT = "HOME_STORE_LIMIT"
    # Invitations and worker access
    INVITATION_NOT_FOUND = "INVITATION_NOT_FOUND"
    INVITATION_ALREADY_PENDING = "INVITATION_ALREADY_PENDING"
    INVITATION_NOT_PENDING = "INVITATION_NOT_PENDING"
    INVITATION_EXPIRED = "INVITATION_EXPIRED"
    INVITATION_STATE_CONFLICT = "INVITATION_STATE_CONFLICT"
    INVITATION_EMAIL_MISMATCH = "INVITATION_EMAIL_MISMATCH"
    ACCESS_PERIOD_ENDED = "ACCESS_PERIOD_ENDED"
    STORE_WORKER_NOT_FOUND = "STORE_WORKER_NOT_FOUND"
    WORKER_ALREADY_HAS_ACCESS = "WORKER_ALREADY_HAS_ACCESS"
    # Jobs, applications and work requests
    JOB_ALREADY_STARTED = "JOB_ALREADY_STARTED"
    JOB_REVISION_CONFLICT = "JOB_REVISION_CONFLICT"
    APPLICATION_ALREADY_ACTIVE = "APPLICATION_ALREADY_ACTIVE"
    APPLICATION_NOT_WITHDRAWABLE = "APPLICATION_NOT_WITHDRAWABLE"
    APPLICATION_REVISION_CONFLICT = "APPLICATION_REVISION_CONFLICT"
    WORK_REQUEST_PENDING = "WORK_REQUEST_PENDING"
    WORK_REQUEST_NOT_PENDING = "WORK_REQUEST_NOT_PENDING"
    WORK_REQUEST_EXPIRED = "WORK_REQUEST_EXPIRED"
    WORK_REQUEST_REVISION_CONFLICT = "WORK_REQUEST_REVISION_CONFLICT"
    WORK_REQUEST_WITHDRAWAL_REQUIRED = "WORK_REQUEST_WITHDRAWAL_REQUIRED"
    WORK_CONFIRMATION_NOT_ACTIVE = "WORK_CONFIRMATION_NOT_ACTIVE"
    WORK_INTERVAL_CONFLICT = "WORK_INTERVAL_CONFLICT"
    INVALID_WORK_INTERVAL = "INVALID_WORK_INTERVAL"
    CALENDAR_RANGE_TOO_LARGE = "CALENDAR_RANGE_TOO_LARGE"
    DELIVERY_UNAVAILABLE = "DELIVERY_UNAVAILABLE"
    # Manuals, AI interview and Q&A
    MANUAL_RESOURCE_NOT_FOUND = "MANUAL_RESOURCE_NOT_FOUND"
    MANUAL_NOT_READY = "MANUAL_NOT_READY"
    MANUAL_NOT_PUBLISHED = "MANUAL_NOT_PUBLISHED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    MANUAL_STATE_CONFLICT = "MANUAL_STATE_CONFLICT"
    MANUAL_VERSION_CONFLICT = "MANUAL_VERSION_CONFLICT"
    MANUAL_VERSION_CHANGED = "MANUAL_VERSION_CHANGED"
    MANUAL_REFERENCE_CONFLICT = "MANUAL_REFERENCE_CONFLICT"
    INTERVIEW_ALREADY_EXISTS = "INTERVIEW_ALREADY_EXISTS"
    INTERVIEW_INCOMPLETE = "INTERVIEW_INCOMPLETE"
    INTERVIEW_STATE_CONFLICT = "INTERVIEW_STATE_CONFLICT"
    QUESTION_ALREADY_ANSWERED = "QUESTION_ALREADY_ANSWERED"
    REVIEW_NOT_READY = "REVIEW_NOT_READY"
    REVIEW_PROCESSING = "REVIEW_PROCESSING"
    TRANSCRIPTION_NOT_READY = "TRANSCRIPTION_NOT_READY"
    TRANSCRIPTION_NOT_RETRYABLE = "TRANSCRIPTION_NOT_RETRYABLE"
    AI_PROCESSING_FAILED = "AI_PROCESSING_FAILED"
    JOB_QUEUE_UNAVAILABLE = "JOB_QUEUE_UNAVAILABLE"
    QA_BUSY = "QA_BUSY"
    QA_INPUT_EXPIRED = "QA_INPUT_EXPIRED"
    QA_MEDIA_EXPIRED = "QA_MEDIA_EXPIRED"
    # Media
    MEDIA_INVALID = "MEDIA_INVALID"
    MEDIA_IN_USE = "MEDIA_IN_USE"
    MEDIA_PURPOSE_INVALID = "MEDIA_PURPOSE_INVALID"
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"
    MEDIA_TYPE_UNSUPPORTED = "MEDIA_TYPE_UNSUPPORTED"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"


DEFAULT_MESSAGES: dict[str, str] = {
    ErrorCode.INVALID_REQUEST: "요청 형식을 확인해 주세요.",
    ErrorCode.VALIDATION_ERROR: "입력한 정보를 확인해 주세요.",
    ErrorCode.SESSION_EXPIRED: "Google 로그인부터 다시 시작해 주세요.",
    ErrorCode.REGISTRATION_REQUIRED: "가입을 먼저 완료해 주세요.",
    ErrorCode.FORBIDDEN: "이 기능을 사용할 권한이 없습니다.",
    ErrorCode.ACCOUNT_SUSPENDED: "이용이 정지된 계정입니다.",
    ErrorCode.CSRF_INVALID: "요청을 확인할 수 없습니다. 새로고침 후 다시 시도해 주세요.",
    ErrorCode.RESOURCE_NOT_FOUND: "요청한 리소스를 찾을 수 없습니다.",
    ErrorCode.STATE_CONFLICT: "현재 상태에서는 요청을 처리할 수 없습니다.",
    ErrorCode.IDEMPOTENCY_KEY_REUSED: "같은 Idempotency-Key로 다른 요청을 보낼 수 없습니다.",
    ErrorCode.RATE_LIMITED: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
    ErrorCode.ADMIN_PASSWORD_INVALID: "관리자 인증에 실패했습니다.",
    ErrorCode.INTERNAL_ERROR: "처리에 실패했습니다.",
}
FALLBACK_MESSAGE = "요청을 처리할 수 없습니다."

# Starlette/FastAPI raise plain HTTPException for routing failures (404/405) and misc 4xx.
_STATUS_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.INVALID_REQUEST,
    401: ErrorCode.SESSION_EXPIRED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.RESOURCE_NOT_FOUND,
    409: ErrorCode.STATE_CONFLICT,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}


class ApiError(Exception):
    """Raise anywhere in a request to produce the contract's error response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str | None = None,
        *,
        field_errors: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = str(code)
        self.message = message or DEFAULT_MESSAGES.get(code, FALLBACK_MESSAGE)
        self.field_errors = field_errors or []
        self.headers = headers
        super().__init__(f"{status_code} {self.code}")


class UnstructuredHTTPException(StarletteHTTPException):
    """Keeps FastAPI's `{"detail": ...}` body for contracts that predate this error format.

    `/api/health` is such a contract (deploy scripts read it), so it must not be reshaped.
    """


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    field_errors: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {
        "code": str(code),
        "message": message,
        "requestId": new_request_id(),
        "fieldErrors": (field_errors or [])[:MAX_FIELD_ERRORS],
    }
    response_headers = {"Cache-Control": "no-store", **(headers or {})}
    return JSONResponse(body, status_code=status_code, headers=response_headers)


def _field_error(error: dict) -> dict[str, str]:
    """Describe a pydantic error without echoing the submitted value (it may be a secret)."""
    location = [str(part) for part in error["loc"]]
    if location and location[0] in {"body", "query", "path", "header", "cookie"}:
        location = location[1:]
    kind = error["type"]
    if kind == "missing":
        code, message = "REQUIRED", "필수 항목입니다."
    elif kind.startswith(("greater", "less", "too_short", "too_long", "string_too")):
        code, message = "OUT_OF_RANGE", "허용 범위를 벗어났습니다."
    else:
        code, message = "INVALID_FORMAT", "형식을 확인해 주세요."
    return {"field": ".".join(location), "code": code, "message": message}


async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return error_response(
        exc.status_code, exc.code, exc.message, field_errors=exc.field_errors, headers=exc.headers,
    )


async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    # Malformed JSON is a 400 (the request itself is unreadable); anything that parsed but
    # failed field rules is a 422 with per-field details.
    if any(error["type"] == "json_invalid" for error in errors):
        return error_response(400, ErrorCode.INVALID_REQUEST, "JSON 요청 형식을 확인해 주세요.")
    return error_response(
        422, ErrorCode.VALIDATION_ERROR, DEFAULT_MESSAGES[ErrorCode.VALIDATION_ERROR],
        field_errors=[_field_error(error) for error in errors],
    )


async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code >= 500:
        return error_response(
            exc.status_code, ErrorCode.INTERNAL_ERROR, DEFAULT_MESSAGES[ErrorCode.INTERNAL_ERROR],
        )
    code = _STATUS_CODES.get(exc.status_code, ErrorCode.INVALID_REQUEST)
    if exc.status_code == 405:
        message = "허용되지 않는 요청 방식입니다."
    else:
        message = DEFAULT_MESSAGES[code]
    # Keep protocol headers such as Allow; the detail text is never forwarded.
    return error_response(exc.status_code, code, message, headers=dict(exc.headers or {}))


async def handle_unstructured_http_exception(
    _request: Request, exc: UnstructuredHTTPException,
) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    # The traceback goes to the server log only; the client sees a generic message.
    logger.error("Unhandled error", exc_info=(type(exc), exc, exc.__traceback__))
    return error_response(500, ErrorCode.INTERNAL_ERROR, DEFAULT_MESSAGES[ErrorCode.INTERNAL_ERROR])


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(UnstructuredHTTPException, handle_unstructured_http_exception)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)

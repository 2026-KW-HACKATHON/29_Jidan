"""One server-generated request ID per HTTP request, shared by error bodies and logs.

The `requestId` of an error response (openapi `Error.requestId`) is the same value attached to
every application log record of that request, so a reported ID finds its server records
(docs/auth-design.md: 500 -> "requestId 문의"; admin approval operation records).

* The ID is always generated here. A client supplied header is never read: it would let
  callers forge or collide log entries.
* Every response carries it as `X-Request-ID` (openapi common rule), so a success can be
  traced too. Error bodies repeat the same value as `requestId`. The 500 rendered outside this
  middleware gets the header from `error_response`.
"""

import logging
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_current: ContextVar[str | None] = ContextVar("jidan_request_id", default=None)

LOGGER_NAME = "jidan"
HEADER = "X-Request-ID"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def current_request_id() -> str:
    """The current request's ID; outside a request (or without the middleware) a fresh one."""
    return _current.get() or new_request_id()


class RequestIdMiddleware:
    """Assigns the request ID before anything else in the app runs.

    Deliberately not reset when the call returns: Starlette's ServerErrorMiddleware, which
    renders unhandled exceptions as 500, sits outside every user middleware and must still see
    this request's ID. Each request runs in its own task with a copied context, so the value
    never reaches another request, and every request overwrites it on entry.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_request_id()
        _current.set(request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Replaces, never appends: one value, the same one error_response already set.
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_header)


class RequestIdLogFilter(logging.Filter):
    """Adds `record.request_id` ("-" outside a request) for the log format."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _current.get() or "-"
        return True


def install_request_logging() -> None:
    """Emit `jidan.*` records at INFO with time and request ID (idempotent).

    Uvicorn configures only its own loggers; without this, application INFO records such as
    admin operation records reach no handler and are dropped.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if any(isinstance(f, RequestIdLogFilter) for h in logger.handlers for f in h.filters):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdLogFilter())
    formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
    formatter.converter = time.gmtime  # server times are UTC
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)

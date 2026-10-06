"""Response headers applied to every request."""

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.request_id import request_id_for

NO_STORE = "no-store"
API_PREFIX = "/api"


class NoStoreMiddleware:
    """Marks every /api response (success, error, 404, 405, ...) as uncacheable.

    API responses carry personal data and session state, so neither browsers nor proxies may
    keep them. Written as plain ASGI so streamed responses are not buffered.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not (path == API_PREFIX or path.startswith(f"{API_PREFIX}/")):
            await self.app(scope, receive, send)
            return

        request_id = request_id_for(Request(scope))

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = NO_STORE
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_header)


def install_middleware(app: FastAPI) -> None:
    app.add_middleware(NoStoreMiddleware)

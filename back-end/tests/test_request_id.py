import logging
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.errors import ApiError, ErrorCode, UnstructuredHTTPException, install_error_handlers
from app.middleware import install_middleware
from app.request_id import HEADER, LOG_FORMAT, RequestIdLogFilter, current_request_id

log = logging.getLogger("jidan.test")


class Body(BaseModel):
    n: int


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app)

    @app.get("/api/conflict")
    def conflict() -> None:
        log.info("about to conflict")
        raise ApiError(409, ErrorCode.IDEMPOTENCY_KEY_REUSED)

    @app.get("/api/boom")
    def boom() -> None:
        log.info("about to fail")
        raise RuntimeError("secret payload")

    @app.get("/api/async-boom")
    async def async_boom() -> None:
        log.info("about to fail")
        raise RuntimeError("secret payload")

    @app.post("/api/echo")
    def echo(body: Body) -> dict:
        return {"n": body.n}

    @app.get("/api/ok")
    def ok() -> dict:
        log.info("ok")
        return {"id": current_request_id()}

    @app.get("/api/unstructured")
    def unstructured() -> None:
        raise UnstructuredHTTPException(status_code=503, detail="Service unavailable")

    @app.get("/outside")
    def outside() -> dict:
        return {"id": current_request_id()}

    return app


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.addFilter(RequestIdLogFilter())

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def records():
    handler = Records()
    logger = logging.getLogger("jidan")
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)


@pytest.fixture
def client():
    return TestClient(build_app(), raise_server_exceptions=False)


def ids_of(records, message):
    return {r.request_id for r in records if r.getMessage().startswith(message)}


def header_id(response):
    # Exactly one header value: the middleware replaces the one error_response set.
    values = response.headers.get_list(HEADER)
    assert len(values) == 1, values
    return values[0]


def test_error_body_header_and_handler_logs_share_the_request_id(client, records):
    response = client.get("/api/conflict")
    body = response.json()
    assert body["requestId"].startswith("req_")
    assert header_id(response) == body["requestId"]
    assert ids_of(records, "about to conflict") == {body["requestId"]}


def test_success_response_carries_the_logged_request_id(client, records):
    response = client.get("/api/ok")
    assert response.status_code == 200
    assert header_id(response) == response.json()["id"]
    assert ids_of(records, "ok") == {response.json()["id"]}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(("method", "path", "status"), [
    ("GET", "/api/missing", 404), ("DELETE", "/api/ok", 405),
])
def test_routing_errors_carry_the_body_request_id(client, method, path, status):
    response = client.request(method, path)
    assert response.status_code == status
    assert header_id(response) == response.json()["requestId"]


def test_unstructured_and_non_api_responses_carry_a_request_id(client):
    # /api/health keeps {"detail": ...}; the ID is still on the response for tracing.
    response = client.get("/api/unstructured")
    assert response.status_code == 503 and response.json() == {"detail": "Service unavailable"}
    assert header_id(response).startswith("req_")
    response = client.get("/outside")
    assert header_id(response) == response.json()["id"]


@pytest.mark.parametrize("path", ["/api/boom", "/api/async-boom"])
def test_unhandled_500_keeps_the_request_id(client, records, path):
    # The 500 is rendered by ServerErrorMiddleware, outside every user middleware.
    response = client.get(path)
    assert response.status_code == 500
    request_id = response.json()["requestId"]
    assert header_id(response) == request_id
    assert response.headers["Cache-Control"] == "no-store"
    assert ids_of(records, "about to fail") == {request_id}
    assert ids_of(records, "Unhandled server error") == {request_id}
    assert "secret payload" not in "".join(r.getMessage() for r in records)


def test_validation_and_malformed_json_errors_use_the_request_id(client, records):
    for kwargs in ({"json": {"n": "x"}}, {"content": b"{", "headers": {"Content-Type": "application/json"}}):
        response = client.post("/api/echo", **kwargs)
        body = response.json()
        assert body["requestId"].startswith("req_")
        assert header_id(response) == body["requestId"]
    ids = [client.post("/api/echo", json={"n": "x"}).json()["requestId"] for _ in range(3)]
    assert len(set(ids)) == 3


@pytest.mark.parametrize("path", ["/api/conflict", "/api/ok", "/api/boom"])
def test_client_supplied_request_id_is_ignored(client, records, path):
    forged = "req_forged0000000000"
    headers = {"X-Request-ID": forged, "Request-Id": forged, "X-Correlation-Id": forged}
    response = client.get(path, headers=headers)
    assert forged not in response.text
    assert header_id(response) != forged and header_id(response).startswith("req_")
    assert forged not in " ".join(response.headers.values())
    assert forged not in {r.request_id for r in records}


def test_concurrent_requests_keep_their_own_ids(client, records):
    barrier = threading.Barrier(8)
    seen = []

    def call():
        barrier.wait(5)
        response = client.get("/api/ok")
        assert header_id(response) == response.json()["id"]
        seen.append(response.json()["id"])

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(seen) == 8 and len(set(seen)) == 8
    assert ids_of(records, "ok") == set(seen)


def test_records_outside_a_request_are_marked(records):
    # A fresh context, as a background task or startup code has.
    import contextvars

    contextvars.Context().run(log.info, "background")
    assert ids_of(records, "background") == {"-"}


def test_installed_handler_formats_time_level_and_request_id():
    logger = logging.getLogger("jidan")
    handlers = [h for h in logger.handlers if any(isinstance(f, RequestIdLogFilter) for f in h.filters)]
    assert len(handlers) == 1  # installing twice does not duplicate output
    assert handlers[0].formatter._fmt == LOG_FORMAT
    record = logging.LogRecord("jidan.test", logging.INFO, "", 1, "m", (), None)
    record.created = 0  # 1970-01-01T00:00:00Z regardless of the host time zone
    handlers[0].filter(record)
    assert handlers[0].format(record).startswith("1970-01-01T00:00:00Z INFO jidan.test request_id=- m")
    assert logger.getEffectiveLevel() <= logging.INFO


def test_concurrent_errors_keep_header_body_and_logs_together(client, records):
    barrier = threading.Barrier(6)
    pairs = []

    def call(path):
        barrier.wait(5)
        response = client.get(path)
        pairs.append((path, header_id(response), response.json()["requestId"]))

    paths = ["/api/conflict", "/api/boom", "/api/async-boom"] * 2
    threads = [threading.Thread(target=call, args=(path,)) for path in paths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(pairs) == 6
    assert all(header == body for _, header, body in pairs)
    assert len({body for _, _, body in pairs}) == 6
    assert ids_of(records, "about to conflict") == {b for p, _, b in pairs if p == "/api/conflict"}
    assert ids_of(records, "about to fail") == {b for p, _, b in pairs if p != "/api/conflict"}

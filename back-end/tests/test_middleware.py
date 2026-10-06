from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.errors import ApiError, ErrorCode, install_error_handlers
from app.main import app as main_app
from app.middleware import install_middleware
from app.request_id import request_id_for


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app)

    @app.get("/api/ok")
    def ok() -> dict:
        return {"ok": True}

    @app.get("/api/context")
    def context(request: Request):
        return {"first": request_id_for(request), "second": request_id_for(request)}

    @app.get("/api/forbidden")
    def forbidden() -> None:
        raise ApiError(403, ErrorCode.FORBIDDEN)

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("internal")

    @app.get("/api/stream")
    def stream() -> StreamingResponse:
        return StreamingResponse(iter([b"a", b"b"]), media_type="text/plain")

    @app.get("/api/own-cache")
    def own_cache() -> StreamingResponse:
        return StreamingResponse(iter([b"x"]), headers={"Cache-Control": "public, max-age=60"})

    @app.get("/apix")
    def outside_prefix() -> dict:
        return {}

    @app.get("/other")
    def other() -> dict:
        return {}

    return app


@pytest.fixture
def client():
    return TestClient(build_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/api/ok", "/api/forbidden", "/api/boom", "/api/missing"])
def test_api_responses_are_never_cacheable(client, path):
    assert client.get(path).headers["Cache-Control"] == "no-store"


def test_wrong_method_and_validation_failures_are_not_cacheable(client):
    assert client.post("/api/ok").headers["Cache-Control"] == "no-store"  # 405


def test_streamed_responses_get_the_header_and_keep_their_body(client):
    response = client.get("/api/stream")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.content == b"ab"


def test_handler_set_cache_control_is_replaced_not_duplicated(client):
    response = client.get("/api/own-cache")
    assert response.headers.get_list("cache-control") == ["no-store"]


def test_paths_outside_api_are_untouched(client):
    assert "Cache-Control" not in client.get("/other").headers
    assert "Cache-Control" not in client.get("/apix").headers


def test_real_app_covers_health_and_unknown_api():
    client = TestClient(main_app)
    assert client.get("/api/health").headers["Cache-Control"] == "no-store"
    assert client.get("/api/nope").headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/forbidden"), ("GET", "/api/boom"),
    ("GET", "/api/missing"), ("POST", "/api/ok"),
])
def test_error_body_and_header_share_request_id(client, method, path):
    response = client.request(method, path)
    assert response.headers["X-Request-ID"] == response.json()["requestId"]


def test_concurrent_requests_have_distinct_server_generated_ids(client):
    def run(_):
        return client.get("/api/context", headers={"X-Request-ID": "untrusted-id"})
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(run, range(10)))
    ids = set()
    for response in responses:
        request_id = response.headers["X-Request-ID"]
        assert request_id.startswith("req_") and request_id != "untrusted-id"
        assert response.json() == {"first": request_id, "second": request_id}
        ids.add(request_id)
    assert len(ids) == 10


def test_stream_and_success_carry_request_id_and_non_api_stays_untouched(client):
    for path in ("/api/ok", "/api/stream"):
        assert client.get(path).headers["X-Request-ID"].startswith("req_")
    assert "X-Request-ID" not in client.get("/other").headers

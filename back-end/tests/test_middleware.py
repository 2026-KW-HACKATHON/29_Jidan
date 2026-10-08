import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.errors import ApiError, ErrorCode, install_error_handlers
from app.main import app as main_app
from app.middleware import install_middleware


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app)

    @app.get("/api/ok")
    def ok() -> dict:
        return {"ok": True}

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

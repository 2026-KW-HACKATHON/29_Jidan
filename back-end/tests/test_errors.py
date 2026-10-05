import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.errors import ApiError, ErrorCode, install_error_handlers
from app.main import app as main_app

OPENAPI = Path(__file__).resolve().parent.parent / "openapi.yaml"
ERROR_KEYS = {"code", "message", "requestId", "fieldErrors"}


class Payload(BaseModel):
    name: str = Field(min_length=1, max_length=5)
    age: int


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/api/echo")
    def echo(payload: Payload) -> dict:
        return payload.model_dump()

    @app.get("/api/items")
    def items(page: int = Query(0, ge=0)) -> dict:
        return {"page": page}

    @app.get("/api/api-error")
    def api_error() -> None:
        raise ApiError(
            409, ErrorCode.IDEMPOTENCY_KEY_REUSED, headers={"Retry-After": "3"},
            field_errors=[{"field": "x", "code": "C", "message": "m"}],
        )

    @app.get("/api/http-error")
    def http_error() -> None:
        raise HTTPException(status_code=418, detail="secret internal detail")

    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("password=hunter2 host=db.internal")

    return app


@pytest.fixture
def client():
    return TestClient(build_app(), raise_server_exceptions=False)


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == code
    assert body["requestId"].startswith("req_")
    assert isinstance(body["fieldErrors"], list)
    return body


def test_api_error_uses_contract_shape_and_headers(client):
    response = client.get("/api/api-error")
    body = assert_error(response, 409, "IDEMPOTENCY_KEY_REUSED")
    assert response.headers["Retry-After"] == "3"
    assert body["fieldErrors"] == [{"field": "x", "code": "C", "message": "m"}]
    assert body["message"]


def test_request_ids_are_unique(client):
    ids = {client.get("/api/api-error").json()["requestId"] for _ in range(5)}
    assert len(ids) == 5


def test_validation_failure_is_422_with_field_errors_and_no_echoed_input(client):
    response = client.post("/api/echo", json={"name": "way-too-long-secret", "age": "x"})
    body = assert_error(response, 422, "VALIDATION_ERROR")
    fields = {error["field"]: error["code"] for error in body["fieldErrors"]}
    assert fields == {"name": "OUT_OF_RANGE", "age": "INVALID_FORMAT"}
    assert "way-too-long-secret" not in response.text


def test_missing_field_is_reported_as_required(client):
    body = assert_error(client.post("/api/echo", json={}), 422, "VALIDATION_ERROR")
    assert {error["code"] for error in body["fieldErrors"]} == {"REQUIRED"}


def test_malformed_json_is_400_invalid_request(client):
    response = client.post(
        "/api/echo", content=b'{"name": ', headers={"Content-Type": "application/json"},
    )
    body = assert_error(response, 400, "INVALID_REQUEST")
    assert body["fieldErrors"] == []


def test_query_validation_failure_names_the_parameter(client):
    body = assert_error(client.get("/api/items?page=-1"), 422, "VALIDATION_ERROR")
    assert body["fieldErrors"][0]["field"] == "page"


def test_http_exception_never_forwards_its_detail(client):
    response = client.get("/api/http-error")
    assert_error(response, 418, "INVALID_REQUEST")
    assert "secret internal detail" not in response.text


def test_unknown_route_is_404_resource_not_found(client):
    assert_error(client.get("/api/missing"), 404, "RESOURCE_NOT_FOUND")


def test_wrong_method_keeps_allow_header(client):
    response = client.delete("/api/items")
    assert_error(response, 405, "INVALID_REQUEST")
    assert "GET" in response.headers["Allow"]


def test_unhandled_exception_is_generic_500_without_internals(client, caplog):
    response = client.get("/api/boom")
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "hunter2" not in response.text and "db.internal" not in response.text
    assert "RuntimeError" not in response.text
    assert any(record.exc_info for record in caplog.records)  # kept in the server log only


def test_health_failure_keeps_its_legacy_detail_body(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    response = TestClient(main_app).get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}


def test_main_app_unknown_api_uses_contract_error():
    assert_error(TestClient(main_app).get("/api/missing"), 404, "RESOURCE_NOT_FOUND")


def test_every_openapi_error_code_has_a_constant():
    text = OPENAPI.read_text(encoding="utf-8")
    documented = set(re.findall(r"^\s+code: ([A-Z][A-Z_]+)$", text, flags=re.MULTILINE))
    # Field-level codes such as INVALID_FORMAT live in fieldErrors, not in the error code list.
    documented -= {"INVALID_FORMAT"}
    missing = documented - {code.value for code in ErrorCode}
    assert not missing


def test_error_codes_are_unique_and_match_their_names():
    assert all(code.name == code.value for code in ErrorCode)

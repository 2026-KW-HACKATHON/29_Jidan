import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.design_docs import PUBLIC_FILES, install_design_docs


@pytest.fixture
def artifact(tmp_path):
    for name in PUBLIC_FILES:
        (tmp_path / name).write_text("test asset", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<html lang="ko"><script src="./init.js"></script></html>', encoding="utf-8",
    )
    (tmp_path / "openapi.json").write_text(json.dumps({
        "openapi": "3.1.0", "info": {"title": "지단", "version": "0.7.0"},
        "paths": {"/api/health": {"get": {}}},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def client_for(environment, directory):
    app = FastAPI()
    install_design_docs(app, environment=environment, directory=directory)
    return TestClient(app)


def test_development_serves_index_and_every_public_asset(artifact):
    client = client_for("dev", artifact)
    response = client.get("/api/swagger", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/api/swagger/")
    for name in ["", *PUBLIC_FILES]:
        response = client.get("/api/swagger/" + name)
        assert response.status_code == 200, name
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/swagger/openapi.json").json()["info"]["title"] == "지단"


@pytest.mark.parametrize("environment", ["production", "local", "staging", ""])
def test_non_development_never_registers_swagger_even_if_artifacts_exist(artifact, environment):
    client = client_for(environment, artifact)
    for name in ["", "openapi.json", "openapi.yaml", "swagger-ui-bundle.js"]:
        assert client.get("/api/swagger/" + name).status_code == 404


def test_development_fails_startup_with_missing_or_incomplete_build(tmp_path):
    for directory in [tmp_path / "missing", tmp_path]:
        with pytest.raises(RuntimeError, match="missing or incomplete"):
            client_for("dev", directory)


def test_environment_is_taken_from_runtime_not_build(artifact, monkeypatch):
    for environment, expected in [("dev", 200), ("production", 404)]:
        monkeypatch.setenv("APP_ENV", environment)
        app = FastAPI()
        install_design_docs(app, directory=artifact)
        assert TestClient(app).get("/api/swagger/").status_code == expected


def test_writes_traversal_and_unrelated_files_are_rejected(artifact):
    (artifact / "private.txt").write_text("private data", encoding="utf-8")
    client = client_for("dev", artifact)
    for method in ["POST", "PUT", "PATCH", "DELETE"]:
        assert client.request(method, "/api/swagger/openapi.json").status_code == 405
    for path in ["package.json", "server.mjs", "private.txt", "%2e%2e%2foutside.txt", "missing.js"]:
        assert client.get("/api/swagger/" + path).status_code == 404
    response = client.head("/api/swagger/openapi.json")
    assert response.status_code == 200
    assert response.content == b""

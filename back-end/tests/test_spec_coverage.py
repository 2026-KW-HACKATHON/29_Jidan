"""The OpenAPI coverage tool (tests/spec_coverage.py) records the right hits and reports gaps."""
import json

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tests import spec_coverage
from tests.spec_coverage import Collector, analyze, plugin_from_env, render


def test_disabled_without_the_setting(monkeypatch):
    monkeypatch.delenv(spec_coverage.ENV, raising=False)
    assert plugin_from_env() is None
    monkeypatch.setenv(spec_coverage.ENV, "  ")
    assert plugin_from_env() is None


def test_collector_records_real_handlers_and_restores_the_client(db_engine, tmp_path, monkeypatch):
    from app import notification_views
    from app.errors import install_error_handlers
    from app.main import app

    monkeypatch.setenv("APP_ENV", "local")
    original = TestClient.request
    collector = Collector(str(tmp_path / "hits.json"))
    collector.pytest_sessionstart(None)
    try:
        TestClient(app).post(f"/api/users/me/notifications/{'0' * 8}-0000-4000-8000-{'0' * 12}/read")  # 403
        mounted = FastAPI()
        install_error_handlers(mounted)
        mounted.include_router(notification_views.router)
        TestClient(mounted).get("/api/users/me/notifications/")  # not a spec path on any app
        stub = FastAPI()
        stub_router = APIRouter()

        @stub_router.get("/api/users/me/notifications")
        def fake():
            return {}

        stub.include_router(stub_router)
        TestClient(stub).get("/api/users/me/notifications")  # a stub at a spec path: ignored
        TestClient(mounted).get("/api/users/me/notifications")  # real router on a test app: counted
        TestClient(app).get("/api/health")  # deployment check: ignored
    finally:
        collector.pytest_sessionfinish(None, 0)
    assert TestClient.request is original
    data = json.loads((tmp_path / "hits.json").read_text())
    assert data["hits"] == [
        ["GET", "/api/users/me/notifications", "401"],  # only from the real router on a test app
        ["POST", "/api/users/me/notifications/{notificationId}/read", "403"],  # main app, no Origin
    ]
    assert ["GET", "/api/users/me/notifications/", "404"] not in data["unmatched"]


def test_report_groups_gaps_by_area():
    hits = [
        ["GET", "/api/users/me/notifications", "200"],
        ["GET", "/api/users/me/notifications", "401"],
        ["GET", "/api/users/me/notifications", "500"],
    ]
    analysis = analyze({"hits": hits, "unmatched": [["GET", "/api/nowhere", "404"]]})
    notify = analysis["areas"]["notify"]
    assert notify["operations"] == 5 and notify["implemented"] == 5 and notify["called"] == 1
    assert "POST /api/users/me/notifications/{notificationId}/read" in notify["uncalled"]
    [(label, missing)] = notify["missing"]
    assert label == "GET /api/users/me/notifications" and "422" in missing and "401" not in missing
    assert "500" not in missing and ("GET /api/users/me/notifications", ["429"]) in notify["missing_common"]
    manual = analysis["areas"]["manual·AI"]
    # Areas are built in waves: every operation is either implemented or listed as a gap.
    assert manual["implemented"] + len(manual["unimplemented"]) == manual["operations"] > 0
    text = render(analysis)
    assert "## notify" in text and "`GET /api/nowhere` → 404" in text


def test_every_spec_operation_belongs_to_an_area():
    operations = spec_coverage.spec_operations()
    assert len(operations) == 102
    assert all(spec_coverage.area_of(op) != "other" for op in operations.values())


def test_application_routes_are_all_in_the_spec():
    assert analyze({"hits": []})["undocumented_routes"] == []

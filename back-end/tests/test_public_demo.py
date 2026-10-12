from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import AuthSession, Store, User
from app.demo import DemoBoundary, allowed_path, configuration
from app.operator_cli import operator_cli

ORIGIN = "http://demo.test"


@pytest.fixture
def demo_client(db_engine, monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan_public_demo_test")
    monkeypatch.setenv("DEMO_ROLE", "OWNER")
    monkeypatch.setenv("DEMO_HOST", "demo.test")
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "demo-media"))
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    from app.main import app

    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def start(client):
    response = client.post("/api/demo/session", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()


def test_session_resume_expiry_and_reset(demo_client, db_engine):
    first = start(demo_client)
    assert first["role"] == "OWNER"
    assert start(demo_client) == first
    cookie = demo_client.cookies[auth.SESSION_COOKIE_NAME]
    with Session(db_engine) as db:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(cookie)))
        assert row is not None
        assert timedelta(hours=2) - timedelta(seconds=2) < row.expires_at - row.created_at <= timedelta(hours=2)
        assert row.expires_at.isoformat() == first["expiresAt"]
        assert db.get(Store, first["storeId"]).owner_id == row.user_id
        assert db.scalar(select(func.count()).select_from(User)) == 1
    assert demo_client.post("/api/demo/reset", headers={"Origin": ORIGIN}).status_code == 403
    reset = demo_client.post("/api/demo/reset", headers={"Origin": ORIGIN, "X-CSRF-Token": first["csrfToken"]})
    assert reset.status_code == 200
    second = reset.json()
    assert second["storeId"] != first["storeId"]
    assert demo_client.get(f"/api/stores/{first['storeId']}/manual").status_code == 404
    with Session(db_engine) as db:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(cookie)))
        assert row.revoked_at is not None
        fresh = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(
            demo_client.cookies[auth.SESSION_COOKIE_NAME])))
        fresh.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    third = start(demo_client)
    assert third["storeId"] not in {first["storeId"], second["storeId"]}


def test_role_host_scope_and_origin_are_fixed(demo_client, db_engine, monkeypatch):
    assert demo_client.post("/api/demo/session").status_code == 403
    assert demo_client.post("/api/demo/session", headers={"Origin": "http://evil.test"}).status_code == 403
    first = start(demo_client)
    response = demo_client.post("/api/demo/session", json={"role": "WORKER"}, headers={"Origin": ORIGIN})
    assert response.json() == first
    for path in ("/api/home", "/api/auth/session", "/api/auth/google", "/api/stores", "/docs", "/openapi.json"):
        assert demo_client.get(path).status_code == 403
    assert demo_client.post("/api/demo/session", headers={"Origin": ORIGIN, "Host": "evil.test"}).status_code == 403
    assert demo_client.get(f"/api/stores/{first['storeId']}/manual/qa/conversations").status_code == 403
    assert demo_client.get(f"/api/stores/{first['storeId']}/manual").status_code == 200
    monkeypatch.delenv("DEMO_ROLE")
    assert demo_client.get(f"/api/stores/{first['storeId']}/manual").status_code == 401
    assert demo_client.post("/api/demo/session", headers={"Origin": ORIGIN}).status_code == 404


def test_worker_sample_and_visitor_isolation(demo_client, db_engine, monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "WORKER")
    from app.demo_seed_public import seed

    with operator_cli(), Session(db_engine) as db:
        seed(db)
        seed(db)
        db.commit()
    first = start(demo_client)
    cookie = demo_client.cookies[auth.SESSION_COOKIE_NAME]
    listing = demo_client.get(f"/api/stores/{first['storeId']}/manual/published")
    assert listing.status_code == 200
    assert len(listing.json()["sections"]) == 5
    assert demo_client.get(f"/api/stores/{first['storeId']}/manual").status_code == 403
    assert demo_client.post(f"/api/stores/{first['storeId']}/manual/interviews").status_code == 403
    demo_client.cookies.clear()
    second = start(demo_client)
    assert second["storeId"] == first["storeId"]
    assert second["csrfToken"] != first["csrfToken"]
    with Session(db_engine) as db:
        rows = list(db.scalars(select(AuthSession)))
        assert len(rows) == 2
        assert len({row.user_id for row in rows}) == 2
    monkeypatch.setenv("DEMO_ROLE", "OWNER")
    demo_client.cookies.set(auth.SESSION_COOKIE_NAME, cookie)
    assert demo_client.get(f"/api/stores/{first['storeId']}/manual").status_code == 401


@pytest.mark.parametrize("role,db,environment", [("admin", "jidan_demo", "local"),
    ("OWNER", "jidan_dev", "local"), ("WORKER", "jidan_demo", "production")])
def test_unsafe_demo_configuration(monkeypatch, role, db, environment):
    monkeypatch.setenv("DEMO_ROLE", role)
    monkeypatch.setenv("DB_NAME", db)
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/demo-media")
    with pytest.raises(ValueError):
        configuration()


def test_path_and_quota_boundaries():
    path = "/api/stores/00000000-0000-0000-0000-000000000001/manual"
    assert allowed_path(path, "GET", "OWNER")
    assert not allowed_path(path, "GET", "WORKER")
    assert allowed_path(path + "/published", "GET", "WORKER")
    assert not allowed_path(path + "/published", "POST", "WORKER")
    assert not allowed_path(path + "s", "GET", "OWNER")
    boundary = DemoBoundary(None)
    assert boundary.consume("one", 2, 1)
    assert boundary.consume("one", 2, 1)
    assert not boundary.consume("one", 2, 1)
    assert boundary.consume("one", 2, 2)
    assert boundary.consume("two", 2, 1)


@pytest.mark.parametrize("name,value", [("MEDIA_ROOT", ""), ("MEDIA_ROOT", " "),
                                       ("DEMO_HOST", "evil.test/path"), ("DEMO_HOST", "")])
def test_required_configuration(monkeypatch, name, value):
    monkeypatch.setenv("DEMO_ROLE", "OWNER")
    monkeypatch.setenv("DB_NAME", "jidan_demo")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/demo-media")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        configuration()


def test_quota_parallel_limit():
    from concurrent.futures import ThreadPoolExecutor

    boundary = DemoBoundary(None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: boundary.consume("global", 3, 0), range(20)))
    assert sum(results) == 3


def test_unseeded_worker_failure_rolls_back(demo_client, db_engine, monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "WORKER")
    response = demo_client.post("/api/demo/session", headers={"Origin": ORIGIN})
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_cookie_contract_and_http_quota(demo_client):
    response = demo_client.post("/api/demo/session", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "domain=" not in cookie
    assert any(f"max-age={seconds}" in cookie for seconds in (7198, 7199, 7200))
    for _ in range(151):
        response = demo_client.post("/api/demo/session", headers={"Origin": ORIGIN})
        if response.status_code == 429:
            break
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMITED"
    assert demo_client.get("/api/auth/csrf").status_code == 200

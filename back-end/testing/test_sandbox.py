"""Sandbox guard tests run without importing the wrapper into production app.main."""
import pymysql
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app import auth
from app.auth_views import router
from app.db.models import AuthSession, User, WorkerProfile
from app.errors import install_error_handlers
from app.middleware import install_middleware
from testing.sandbox import create_app, install_tools, validate_environment
from tests.conftest import migrated_sqlite_engine


@pytest.fixture
def environment(monkeypatch):
    for key, value in {"APP_ENV": "local", "DB_NAME": "jidan_sandbox", "DB_HOST": "mysql",
                       "COOKIE_SECURE": "false", "ALLOWED_ORIGINS": "http://testserver"}.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("key,value", [
    ("APP_ENV", "dev"), ("APP_ENV", "production"), ("APP_ENV", ""),
    ("DB_NAME", "jidan_dev"), ("DB_NAME", "jidan_production"), ("DB_NAME", "jidan_e2e_test"),
    ("DB_HOST", "shared-mysql"), ("COOKIE_SECURE", "true"),
])
def test_wrapper_rejects_other_environments(environment, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="dedicated local"):
        create_app()


def test_guard_accepts_only_sandbox(environment):
    validate_environment()


@pytest.fixture
def api(environment, monkeypatch):
    engine = migrated_sqlite_engine()
    monkeypatch.setattr("app.db.session.get_session_factory",
                        lambda: sessionmaker(engine, expire_on_commit=False))
    app = FastAPI()
    install_middleware(app)
    install_error_handlers(app)
    app.include_router(router)
    install_tools(app)
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_login_reuses_account_and_commits_before_cookie(api):
    client, engine = api
    for _ in range(2):
        response = client.post("/sandbox/login/worker", headers={"Origin": "http://testserver"})
        assert response.status_code == 204 and "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/api/auth/session").status_code == 200
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(WorkerProfile)) == 1
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 2


@pytest.mark.parametrize("origin", [None, "http://evil.test", "http://testserver.evil.test"])
def test_login_requires_exact_origin(api, origin):
    client, engine = api
    response = client.post("/sandbox/login/owner", headers={"Origin": origin} if origin else {})
    assert response.status_code == 403
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_role_validation_and_registration_switch(api):
    client, _ = api
    headers = {"Origin": "http://testserver"}
    assert client.post("/sandbox/login/admin", headers=headers).status_code == 422
    assert client.post("/sandbox/login/owner", headers=headers).status_code == 204
    assert client.post("/sandbox/registration", headers=headers).status_code == 204
    assert auth.SESSION_COOKIE_NAME not in client.cookies
    assert auth.REGISTRATION_COOKIE_NAME in client.cookies
    assert client.get("/api/auth/csrf").status_code == 200
    assert client.post("/sandbox/login/worker", headers=headers).status_code == 204
    assert auth.REGISTRATION_COOKIE_NAME not in client.cookies


def test_production_app_has_no_sandbox_routes(environment):
    from app.main import app

    assert not any(getattr(route, "path", "").startswith("/sandbox") for route in app.routes)
    # No lifespan is needed for 404 routing; do not start DB cleanup in this guard test.
    client = TestClient(app)
    try:
        assert client.get("/sandbox").status_code == 404
        assert client.post("/sandbox/login/worker").status_code == 404
    finally:
        client.close()


def test_actual_factory_serves_page_and_real_profile_routes(environment, monkeypatch):
    # This test shares one SQLite connection; background sweepers are unrelated to routing.
    monkeypatch.setenv("BACKGROUND_JOBS", "off")
    from app.main import app

    engine = migrated_sqlite_engine()
    monkeypatch.setattr("app.db.session.get_session_factory",
                        lambda: sessionmaker(engine, expire_on_commit=False))
    routes = list(app.router.routes)
    schema = app.openapi_schema
    try:
        wrapped = create_app()
        assert wrapped is app
        with TestClient(wrapped) as client:
            page = client.get("/sandbox")
            assert page.status_code == 200 and "Jidan API 테스트" in page.text
            assert page.headers["cache-control"] == "no-store"
            login = client.post("/sandbox/login/worker", headers={"Origin": "http://testserver"})
            assert login.status_code == 204
            profile = client.get("/api/users/me/profile")
            assert profile.status_code == 200 and profile.json()["name"] == "테스트 worker"
    finally:
        app.router.routes[:] = routes
        app.openapi_schema = schema
        engine.dispose()


@pytest.mark.parametrize("code", [1062, 1205, 1213])
def test_fixture_login_retries_the_whole_transaction_after_race(api, code):
    client, engine = api
    attempts = []

    def fail_once(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO AUTH_SESSIONS"):
            attempts.append(1)
            if len(attempts) == 1:
                if code == 1062:
                    raise IntegrityError("INSERT", {}, pymysql.err.IntegrityError(code, "fixture race"))
                raise OperationalError("INSERT", {}, pymysql.err.OperationalError(code, "fixture race"))

    event.listen(engine, "before_cursor_execute", fail_once)
    try:
        response = client.post("/sandbox/login/worker", headers={"Origin": "http://testserver"})
    finally:
        event.remove(engine, "before_cursor_execute", fail_once)
    assert response.status_code == 204 and len(attempts) == 2
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(WorkerProfile)) == 1
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
    assert client.get("/api/auth/session").status_code == 200


@pytest.mark.parametrize("code,expected", [(1213, 3), (3819, 1), (2013, 1)])
def test_fixture_retry_is_bounded_and_does_not_hide_other_db_failures(api, code, expected):
    client, engine = api
    attempts = []

    def fail(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO AUTH_SESSIONS"):
            attempts.append(1)
            raise OperationalError("INSERT", {}, pymysql.err.OperationalError(code, "fixture failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(OperationalError):
            client.post("/sandbox/login/worker", headers={"Origin": "http://testserver"})
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert len(attempts) == expected
    with Session(engine) as db:
        for model in (User, WorkerProfile, AuthSession):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_sandbox_checker_refuses_registration_only_data_without_writing(api):
    from testing.check_sandbox import require_fresh_fixtures

    client, engine = api
    require_fresh_fixtures(engine)
    assert client.post("/sandbox/registration", headers={"Origin": "http://testserver"}).status_code == 204
    with pytest.raises(AssertionError, match="fresh disposable"):
        require_fresh_fixtures(engine)
    from app.db.models import RegistrationSession
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(RegistrationSession)) == 1

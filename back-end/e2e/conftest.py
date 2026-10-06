"""Real socket HTTP against app.main, with an independent MySQL verification connection.

Only verified Google identity is seeded: registration, cookies, CSRF, writes and reads
use the actual server. These fixtures never replace app dependencies or API handlers.
"""
import os
import uuid
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import auth
from app.db import get_engine, reset_engine
from app.db.models import (
    AvailabilityDay,
    AvailabilityRule,
    RegistrationSession,
    User,
    WorkerCareer,
    WorkerProfile,
)

WORKER = {
    "name": "HTTP 테스트", "phoneNumber": "01012345678", "birthDate": "2001-03-14",
    "gender": "FEMALE", "experienceLevel": "NEW", "careers": [],
    "availabilities": [{"days": ["MON"], "startTime": "09:00", "endTime": "14:00",
                        "endsNextDay": False}],
}


@pytest.fixture(scope="session")
def base_url():
    url = os.environ.get("JIDAN_E2E_BASE_URL", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"e2e-api", "127.0.0.1", "localhost"}
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.username or parsed.password):
        pytest.fail("JIDAN_E2E_BASE_URL must point to the local HTTP test server")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def real_db(base_url):
    if os.getenv("DB_NAME") != "jidan_e2e_test" or os.getenv("APP_ENV") != "local":
        pytest.fail("HTTP E2E requires the dedicated jidan_e2e_test database and APP_ENV=local")
    reset_engine()
    engine = get_engine()
    with engine.connect() as connection:
        assert connection.dialect.name == "mysql"
        assert connection.scalar(text("SELECT DATABASE()")) == "jidan_e2e_test"
        expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == expected
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "environment": "local", "database": "ok"}
        assert client.post("/sandbox/login/worker").status_code == 404
        assert client.get("/sandbox").status_code == 404
    yield engine
    reset_engine()


@dataclass
class RegistrationCase:
    client: httpx.Client
    subject: str
    registration_token: str = field(repr=False)
    worker: dict = field(default_factory=lambda: deepcopy(WORKER))

    def headers(self, *, key=None):
        response = self.client.get("/api/auth/csrf")
        assert response.status_code == 200, response.text
        return {"Origin": str(self.client.base_url).rstrip("/"),
                "X-CSRF-Token": response.json()["csrfToken"],
                "Idempotency-Key": key or str(uuid.uuid4())}

    def register(self, *, body=None, key=None):
        return self.client.post("/api/auth/registrations/workers",
                                json=self.worker if body is None else body,
                                headers=self.headers(key=key))


@pytest.fixture
def registrations(base_url, real_db):
    with ExitStack() as clients:
        def create():
            subject = f"http-e2e-{uuid.uuid4()}"
            with Session(real_db) as db:
                issued = auth.create_registration_session(subject, f"{subject}@e2e.test", db=db)
                db.commit()
            client = clients.enter_context(httpx.Client(base_url=base_url, timeout=10, trust_env=False))
            # Match CookieJar's host-only domain so the server's deletion removes the seed.
            host = urlsplit(base_url).hostname
            domain = host if "." in host else f"{host}.local"
            client.cookies.set(auth.REGISTRATION_COOKIE_NAME, issued.token, domain=domain, path="/api/auth")
            return RegistrationCase(client, subject, issued.token)
        yield create


@pytest.fixture
def registration(registrations):
    return registrations()


@pytest.fixture
def member(registration):
    response = registration.register()
    assert response.status_code == 201, response.text
    return registration, response.json()["user"]["id"]


def registration_row(db, case):
    return db.scalar(select(RegistrationSession).where(
        RegistrationSession.token_hash == auth.hash_token(case.registration_token),
    ))


def worker_snapshot(db, user_id):
    """All persisted aggregate columns, independent of response serialization."""
    snapshot = {}
    for model, field_name in ((User, "id"), (WorkerProfile, "user_id"),
                              (WorkerCareer, "worker_id"), (AvailabilityRule, "worker_id")):
        table = model.__table__
        snapshot[table.name] = [tuple(row) for row in db.execute(
            select(table).where(getattr(model, field_name) == user_id)
            .order_by(*table.primary_key.columns))]
    snapshot[AvailabilityDay.__tablename__] = [tuple(row) for row in db.execute(
        select(AvailabilityDay.__table__).join(AvailabilityRule)
        .where(AvailabilityRule.worker_id == user_id)
        .order_by(AvailabilityDay.rule_id, AvailabilityDay.weekday))]
    return snapshot

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.auth_views import router as view_router
from app.db.models import RegistrationSession, User, WorkerProfile
from app.errors import install_error_handlers
from app.middleware import install_middleware
from app.registration import router
from app.registration_inputs import WorkerInput

WORKER = {
    "name": "김지수", "phoneNumber": "01012345678", "birthDate": "2001-03-14",
    "gender": "FEMALE", "experienceLevel": "NEW", "careers": [],
    "availabilities": [{"days": ["MON"], "startTime": "09:00", "endTime": "14:00", "endsNextDay": False}],
}


@pytest.fixture
def worker_api(db_engine, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://frontend.test")
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app)
    app.include_router(router)
    app.include_router(view_router)
    api = TestClient(app, raise_server_exceptions=False)
    with Session(db_engine) as db:
        issued = auth.create_registration_session("worker-sub", "worker@test.org", db=db)
        db.commit()
    api.cookies.set(auth.REGISTRATION_COOKIE_NAME, issued.token)
    return api


def headers(api, key=None):
    return {"Origin": "http://frontend.test", "Idempotency-Key": key or str(uuid.uuid4()),
            "X-CSRF-Token": api.get("/api/auth/csrf").json()["csrfToken"]}


def test_worker_atomic_success_and_retry(worker_api, db_engine):
    key = str(uuid.uuid4())
    first = worker_api.post("/api/auth/registrations/workers", json=WORKER, headers=headers(worker_api, key))
    assert first.status_code == 201, first.text
    assert first.json()["nextAction"] == "WORKER_HOME"
    assert first.json()["user"]["identity"]["email"] == "worker@test.org"
    replay = worker_api.post("/api/auth/registrations/workers", json=WORKER, headers=headers(worker_api, key))
    assert replay.status_code == 201 and replay.json() == first.json()
    assert "set-cookie" not in replay.headers
    changed = {**WORKER, "name": "변경"}
    assert worker_api.post("/api/auth/registrations/workers", json=changed, headers=headers(worker_api, key)).json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert worker_api.post("/api/auth/registrations/workers", json=WORKER, headers=headers(worker_api)).json()["code"] == "ALREADY_REGISTERED"
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(WorkerProfile)) == 1
        assert db.scalar(select(RegistrationSession)).consumed_at


def test_failure_rolls_back_everything(worker_api, db_engine, monkeypatch):
    from app import registration
    def fail(*args, **kwargs):
        raise RuntimeError("injected")
    monkeypatch.setattr(registration.auth, "create_session", fail)
    r = worker_api.post("/api/auth/registrations/workers", json=WORKER, headers=headers(worker_api))
    assert r.status_code == 500 and "set-cookie" not in r.headers
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(WorkerProfile)) == 0
        assert db.scalar(select(RegistrationSession)).consumed_at is None


@pytest.mark.parametrize("change", [
    {"email": "injected@test.org"}, {"role": "OWNER"}, {"birthDate": "2999-01-01"},
    {"birthDate": "2001-02-29"}, {"birthDate": 100}, {"experienceLevel": "EXPERIENCED"},
    {"phoneNumber": "010１２３４５６７８"}, {"name": " "}, {"availabilities": []},
])
def test_invalid_inputs(worker_api, change):
    r = worker_api.post("/api/auth/registrations/workers", json={**WORKER, **change}, headers=headers(worker_api))
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("groups", [
    [{"days": ["MON", "MON"], "startTime": "09:00", "endTime": "10:00", "endsNextDay": False}],
    [{"days": ["MON"], "startTime": "09:00", "endTime": "09:00", "endsNextDay": False}],
    [{"days": ["MON"], "startTime": "09:00", "endTime": "10:00", "endsNextDay": True}],
    [{"days": ["SUN"], "startTime": "22:00", "endTime": "02:00", "endsNextDay": True},
     {"days": ["MON"], "startTime": "01:30", "endTime": "03:00", "endsNextDay": False}],
])
def test_weekly_overlap_and_duration(groups):
    with pytest.raises(ValidationError):
        WorkerInput.model_validate({**WORKER, "availabilities": groups})


def test_adjacent_week_boundary_is_allowed():
    model = WorkerInput.model_validate({**WORKER, "availabilities": [
        {"days": ["SUN"], "startTime": "22:00", "endTime": "02:00", "endsNextDay": True},
        {"days": ["MON"], "startTime": "02:00", "endTime": "03:00", "endsNextDay": False},
    ]})
    assert len(model.availabilities) == 2


@pytest.mark.parametrize("kind", ["origin", "csrf", "key"])
def test_write_guards(worker_api, kind):
    values = headers(worker_api)
    if kind == "origin": values["Origin"] = "http://evil.test"
    if kind == "csrf": values.pop("X-CSRF-Token")
    if kind == "key": values.pop("Idempotency-Key")
    r = worker_api.post("/api/auth/registrations/workers", json=WORKER, headers=values)
    assert r.status_code == (422 if kind == "key" else 403)

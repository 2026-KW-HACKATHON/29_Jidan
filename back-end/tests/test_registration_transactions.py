import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db.models import User, WorkerProfile
from app.errors import install_error_handlers
from app.registration import router
from tests.auth_contract import ContractClient
from tests.test_worker_registration import WORKER, headers
from tests.test_worker_registration import worker_api as _worker_api

worker_api = _worker_api


@pytest.mark.mysql
@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_registration_creates_one_account(db_engine, monkeypatch, same_key):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app import idempotency
    from app.db.models import AuthSession, IdempotencyRecord

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://frontend.test")
    monkeypatch.setattr(idempotency, "POLL_INTERVAL_SECONDS", 0.01)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    with Session(db_engine) as db:
        issued = auth.create_registration_session("concurrent", "worker@test.org", db=db)
        db.commit()
    key = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    ready = threading.Barrier(2)
    from app import registration
    original = registration.run_idempotent
    def synchronize(**kwargs):
        ready.wait(timeout=5)  # both requests authenticated before either consumes the session
        return original(**kwargs)
    monkeypatch.setattr(registration, "run_idempotent", synchronize)
    def submit(index):
        with ContractClient(app, raise_server_exceptions=False) as api:
            barrier.wait(timeout=5)
            return api.post("/api/auth/registrations/workers", json=WORKER, headers={
                "Origin": "http://frontend.test", "X-CSRF-Token": issued.csrf_token,
                "Idempotency-Key": key if same_key or index == 0 else str(uuid.uuid4()),
                "Cookie": f"{auth.REGISTRATION_COOKIE_NAME}={issued.token}",
            })
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, [0, 1]))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201, 201] if same_key else statuses in ([201, 409], [201, 401])
    if same_key:
        assert responses[0].json() == responses[1].json()
        assert sum("set-cookie" in r.headers for r in responses) == 1
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(WorkerProfile)) == 1
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert db.scalar(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.state == "COMPLETED")) == 1


def test_commit_failure_does_not_issue_cookie(worker_api, db_engine, monkeypatch):
    from app.db.models import IdempotencyRecord
    original = Session.commit
    def failing_commit(self):
        if any(isinstance(row, WorkerProfile) for row in self.identity_map.values()):
            raise RuntimeError("commit failed")
        if self.scalar(select(func.count()).select_from(WorkerProfile)):
            raise RuntimeError("commit failed")
        return original(self)
    monkeypatch.setattr(Session, "commit", failing_commit)
    r = worker_api.post("/api/auth/registrations/workers", json=WORKER, headers=headers(worker_api))
    assert r.status_code == 500 and "set-cookie" not in r.headers
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(WorkerProfile)) == 0
        assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0

"""Public demo API over real TCP HTTP, using a separate verification MySQL connection.

Two real app.main processes fix the role per host. Only AI is fake and background tasks
are manual; no handler, session dependency or DB access is replaced. All initial visitor
and workspace writes happen through the public API. Sample initialization is an operator
step. This suite must run exclusively in the disposable jidan_e2e_test database.
"""
import os
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import auth
from app.db import get_engine, reset_engine
from app.db.models import (
    AuthSession,
    ManualQa,
    ManualQaConversation,
    Store,
    StoreAccessGrant,
    StoreInvitation,
    User,
)
from e2e.conftest import require_local_test_database


@pytest.fixture
def public_demo(tmp_path, monkeypatch):
    require_local_test_database()
    reset_engine()
    engine = get_engine()
    with engine.connect() as connection:
        assert connection.dialect.name == "mysql"
        assert connection.scalar(text("SELECT DATABASE()")) == "jidan_e2e_test"
    monkeypatch.setenv("DEMO_ROLE", "WORKER")
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    from app.demo_seed_public import seed

    with Session(engine) as db:
        seed(db)
        db.commit()
    with ExitStack() as stack:
        clients = {}
        for role in ("OWNER", "WORKER"):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            origin = f"http://127.0.0.1:{port}"
            environment = {**os.environ, "DEMO_ROLE": role, "DEMO_HOST": "127.0.0.1",
                           "ALLOWED_ORIGINS": origin, "BACKGROUND_JOBS": "off",
                           "TASK_RUNNER_MODE": "manual", "AI_PROVIDER": "fake",
                           "COOKIE_SECURE": "false"}
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                                        "--host", "127.0.0.1", "--port", str(port),
                                        "--log-level", "warning"], env=environment)

            def stop(p=process):
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)
            stack.callback(stop)
            client = stack.enter_context(httpx.Client(base_url=origin, timeout=10, trust_env=False))
            deadline = time.monotonic() + 20
            while True:
                try:
                    response = client.get("/api/health")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert process.poll() is None, "demo HTTP server exited"
                assert time.monotonic() < deadline, "demo HTTP server did not become healthy"
                time.sleep(.05)
            clients[role] = client
        yield clients, engine


def start(client):
    response = client.post("/api/demo/session", headers={"Origin": str(client.base_url).rstrip("/")})
    assert response.status_code == 200, response.text
    return response.json()


def headers(client, session):
    return {"Origin": str(client.base_url).rstrip("/"), "X-CSRF-Token": session["csrfToken"]}


def test_owner_resume_isolation_reset_and_concurrent_reset(public_demo):
    clients, engine = public_demo
    owner = clients["OWNER"]
    first = start(owner)
    assert start(owner) == first
    cookie = owner.cookies[auth.SESSION_COOKIE_NAME]
    with httpx.Client(base_url=owner.base_url, timeout=10, trust_env=False) as other:
        second = start(other)
        assert first["storeId"] != second["storeId"]
        assert owner.get(f"/api/stores/{second['storeId']}/manual").status_code == 404
        assert other.get(f"/api/stores/{first['storeId']}/manual").status_code == 404
        mutation = other.post(f"/api/stores/{first['storeId']}/manual/interviews", json={},
                              headers={**headers(other, second), "Idempotency-Key": str(uuid.uuid4())})
        assert mutation.status_code == 404
        with Session(engine) as db:
            before = db.scalar(select(func.count()).select_from(Store))
        assert owner.post("/api/demo/reset", headers=headers(owner, second)).status_code == 403
        assert owner.post("/api/demo/reset", headers={**headers(owner, first), "Origin": "http://evil.test"}).status_code == 403
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Store)) == before
        def reset(_):
            with httpx.Client(base_url=owner.base_url, timeout=10, trust_env=False,
                              cookies={auth.SESSION_COOKIE_NAME: cookie}) as client:
                return client.post("/api/demo/reset", headers=headers(client, first))
        # Hold the old session lock until both HTTP requests reach their locking write/read.
        # This makes the regression fail with the old unconditional revoke implementation.
        with Session(engine) as blocker, ThreadPoolExecutor(max_workers=2) as pool:
            blocker.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(cookie)).with_for_update())
            futures = [pool.submit(reset, index) for index in range(2)]
            deadline = time.monotonic() + 5
            while True:
                with engine.connect() as connection:
                    queries = connection.execute(text("SHOW FULL PROCESSLIST")).mappings().all()
                    waiting = [row for row in queries if row["Info"] and "auth_sessions" in row["Info"]
                               and ("FOR UPDATE" in row["Info"].upper() or row["Info"].upper().startswith("UPDATE"))]
                if len(waiting) >= 2:
                    break
                if time.monotonic() >= deadline:
                    blocker.rollback()
                    pytest.fail("both reset requests did not reach the session lock")
                time.sleep(.02)
            blocker.rollback()
            responses = [future.result() for future in futures]
        assert sorted(response.status_code for response in responses) == [200, 401]
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Store)) == before + 1
            assert db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(cookie))).revoked_at is not None
        assert owner.post("/api/demo/reset", headers=headers(owner, first)).status_code == 401
        assert other.get(f"/api/stores/{second['storeId']}/manual").status_code == 200


def test_worker_conversations_are_private_and_seed_preserves_them(public_demo):
    clients, engine = public_demo
    worker = clients["WORKER"]
    first = start(worker)
    path = f"/api/stores/{first['storeId']}/manual/qa/conversations"
    key = str(uuid.uuid4())
    response = worker.post(path, json={}, headers={**headers(worker, first), "Idempotency-Key": key})
    assert response.status_code == 201, response.text
    conversation = response.json()["id"]
    assert worker.post(path, json={}, headers={**headers(worker, first), "Idempotency-Key": key}).json()["id"] == conversation
    with httpx.Client(base_url=worker.base_url, timeout=10, trust_env=False) as other:
        second = start(other)
        assert other.get(path + "/" + conversation).status_code == 404
        assert other.get(path).json()["items"] == []
        question_body = {"kind": "TEXT", "text": "포스 마감 방법", "transcriptionId": None, "imageMediaIds": []}
        assert other.post(path + f"/{conversation}/questions", json=question_body,
                          headers={**headers(other, second), "Idempotency-Key": str(uuid.uuid4())}).status_code == 404
        question = worker.post(path + f"/{conversation}/questions", json=question_body,
                               headers={**headers(worker, first), "Idempotency-Key": str(uuid.uuid4())})
        assert question.status_code == 202, question.text
        assert other.get(path + f"/{conversation}/questions/{question.json()['id']}").status_code == 404
        with Session(engine) as db:
            saved = db.get(ManualQaConversation, conversation)
            assert saved.worker_id == db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(
                worker.cookies[auth.SESSION_COOKIE_NAME]))).user_id
            assert db.get(ManualQa, question.json()["id"]).conversation_id == conversation
    from app.demo_seed_public import seed
    with Session(engine) as db:
        seed(db)
        db.commit()
    assert worker.get(path + "/" + conversation).status_code == 200
    assert worker.get(path).json()["items"][0]["id"] == conversation
    old_cookie = worker.cookies[auth.SESSION_COOKIE_NAME]
    response = worker.post("/api/demo/reset", headers=headers(worker, first))
    assert response.status_code == 200
    assert response.json()["storeId"] == first["storeId"]
    assert response.json()["csrfToken"] != first["csrfToken"]
    assert worker.get(path + "/" + conversation).status_code == 404
    with Session(engine) as db:
        retired = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(old_cookie)))
        fresh = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(
            worker.cookies[auth.SESSION_COOKIE_NAME])))
        assert retired.revoked_at is not None
        assert retired.user_id != fresh.user_id
        assert db.get(ManualQaConversation, conversation).worker_id == retired.user_id
        assert db.scalar(select(StoreAccessGrant.id).where(StoreAccessGrant.worker_id == fresh.user_id))
        assert db.scalar(select(StoreInvitation.id).where(StoreInvitation.accepted_by_worker_id == fresh.user_id))


def test_real_member_is_not_accepted(public_demo):
    clients, engine = public_demo
    owner = clients["OWNER"]
    with Session(engine) as db:
        user = User(google_sub=f"real-member-{uuid.uuid4()}", google_email="real@example.test",
                    email_verified=True, role="OWNER", name="기존 점주", phone_number="01012345678")
        db.add(user)
        db.flush()
        token = auth.create_session(user.id, db=db).token
        db.commit()
    owner.cookies.set(auth.SESSION_COOKIE_NAME, token)
    assert owner.get("/api/auth/csrf").status_code == 401
    before = None
    with Session(engine) as db:
        before = db.scalar(select(func.count()).select_from(Store))
    visitor = start(owner)
    assert visitor["role"] == "OWNER"
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Store)) == before + 1
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(token))).revoked_at is None


def test_worker_sample_and_role_host_scope(public_demo):
    clients, engine = public_demo
    worker, owner = clients["WORKER"], clients["OWNER"]
    visitor = start(worker)
    result = worker.get(f"/api/stores/{visitor['storeId']}/manual/published")
    assert result.status_code == 200
    assert len(result.json()["sections"]) == 5
    for path in ("/api/home", "/api/stores", "/api/auth/google", "/api/auth/session", "/docs", "/openapi.json"):
        assert worker.get(path).status_code == 403
    assert worker.post(f"/api/stores/{visitor['storeId']}/manual/interviews").status_code == 403
    assert worker.get("/api/auth/csrf", headers={"Host": "evil.test"}).status_code == 403
    owner.cookies.set(auth.SESSION_COOKIE_NAME, worker.cookies[auth.SESSION_COOKIE_NAME])
    assert owner.get(f"/api/stores/{visitor['storeId']}/manual").status_code == 401
    with Session(engine) as db:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(
            worker.cookies[auth.SESSION_COOKIE_NAME])))
        assert db.get(User, row.user_id).role == "WORKER"

"""Server requests contend on real MySQL locks and recover from actual DB write errors."""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import (
    AuthSession,
    AvailabilityDay,
    AvailabilityRule,
    IdempotencyRecord,
    RegistrationSession,
    User,
    WorkerCareer,
    WorkerProfile,
)
from app.idempotency import body_hash
from app.registration_inputs import WorkerInput
from e2e.conftest import registration_row, worker_snapshot


@contextmanager
def reserve_attempts(engine, keys):
    """Nontransactional evidence survives a duplicate-key rollback in the contender."""
    table = f"e2e_attempts_{uuid.uuid4().hex}"
    trigger = f"e2e_attempt_trigger_{uuid.uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE TABLE `{table}` (attempt_key VARCHAR(36) NOT NULL) ENGINE=MEMORY")
            connection.exec_driver_sql(f"""CREATE TRIGGER `{trigger}` BEFORE INSERT ON idempotency_records
                FOR EACH ROW BEGIN
                  IF NEW.idempotency_key IN (%s, %s) THEN
                    INSERT INTO `{table}` VALUES (NEW.idempotency_key);
                  END IF;
                END""", tuple(keys))
        yield table
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS `{trigger}`")
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS `{table}`")


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_registration_creates_exactly_one_aggregate(registration, real_db, base_url, same_key):
    case = registration
    common = case.headers()
    keys = [common["Idempotency-Key"], common["Idempotency-Key"] if same_key else str(uuid.uuid4())]
    barrier = Barrier(2)

    def submit(index):
        with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
            headers = {**common, "Idempotency-Key": keys[index],
                       "Cookie": f"{auth.REGISTRATION_COOKIE_NAME}={case.registration_token}"}
            barrier.wait(timeout=5)
            return client.post("/api/auth/registrations/workers", json=case.worker, headers=headers)

    with reserve_attempts(real_db, keys) as audit_table, ThreadPoolExecutor(max_workers=2) as pool:
        with Session(real_db) as blocker:
            blocker.scalar(select(RegistrationSession).where(
                RegistrationSession.token_hash == auth.hash_token(case.registration_token)).with_for_update())
            futures = [pool.submit(submit, index) for index in range(2)]
            try:
                deadline = time.monotonic() + 2
                ready = False
                while time.monotonic() < deadline:
                    with real_db.connect() as observer:
                        attempts = observer.exec_driver_sql(f"SELECT COUNT(*) FROM `{audit_table}`").scalar_one()
                    with Session(real_db) as observer:
                        processing = observer.scalar(select(func.count()).select_from(IdempotencyRecord).where(
                            IdempotencyRecord.idempotency_key.in_(keys), IdempotencyRecord.state == "PROCESSING"))
                    ready = attempts >= 2 and processing == (1 if same_key else 2)
                    if ready:
                        break
                    time.sleep(0.005)
                assert ready, "both HTTP requests did not reach the contention boundary"
                assert not any(future.done() for future in futures)
            finally:
                blocker.rollback()
        responses = [future.result(timeout=15) for future in futures]
    winner = next(response for response in responses if response.status_code == 201 and "set-cookie" in response.headers)
    assert sum(response.status_code == 201 for response in responses) == (2 if same_key else 1)
    if same_key:
        assert responses[0].json() == responses[1].json()
        assert sum(response.headers.get("Idempotent-Replayed") == "true" for response in responses) == 1
    assert all(response.status_code in {201, 401, 409} for response in responses)
    if not same_key:
        loser = next(response for response in responses if response.status_code != 201)
        assert (loser.status_code, loser.json()["code"]) in {
            (401, "SESSION_EXPIRED"), (409, "ALREADY_REGISTERED")}
    assert sum("set-cookie" in response.headers for response in responses) == 1
    user_id = winner.json()["user"]["id"]
    case.client.cookies.extract_cookies(winner)
    with Session(real_db) as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.google_sub == case.subject)) == 1
        assert db.get(WorkerProfile, user_id) is not None
        assert db.scalar(select(func.count()).select_from(AvailabilityRule).where(AvailabilityRule.worker_id == user_id)) == 1
        assert db.scalar(select(func.count()).select_from(AvailabilityDay).join(AvailabilityRule).where(AvailabilityRule.worker_id == user_id)) == 1
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user_id)) == 1
        records = db.scalars(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key.in_(keys))).all()
        assert len(records) == 1 and records[0].state == "COMPLETED"
        assert registration_row(db, case).consumed_at is not None
        saved = worker_snapshot(db, user_id)
        winning_key = records[0].idempotency_key
    replay = case.register(key=winning_key)
    assert replay.status_code == 201 and replay.json() == winner.json()
    assert replay.headers["Idempotent-Replayed"] == "true" and "set-cookie" not in replay.headers
    assert case.client.get("/api/users/me/profile").json()["id"] == user_id
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == saved


def test_registration_db_error_rolls_back_and_same_key_retries(registration, real_db):
    case = registration
    case.worker.update(experienceLevel="EXPERIENCED", careers=[
        {"industry": "CAFE", "duties": "음료 제조", "storeName": "월계 카페", "startMonth": "2024-03",
         "endMonth": None, "isCurrent": True},
        {"industry": "OTHER", "duties": "매장 정리", "startMonth": "2022-01", "endMonth": "2023-12", "isCurrent": False},
    ], availabilities=[
        {"days": ["MON", "FRI"], "startTime": "09:00", "endTime": "14:00", "endsNextDay": False},
        {"days": ["SUN"], "startTime": "22:00", "endTime": "02:00", "endsNextDay": True},
    ])
    headers = case.headers()
    models = (User, WorkerProfile, WorkerCareer, AvailabilityRule, AvailabilityDay, AuthSession, IdempotencyRecord)
    with Session(real_db) as db:
        before = [db.scalar(select(func.count()).select_from(model)) for model in models]
    trigger = f"e2e_registration_{uuid.uuid4().hex}"
    with real_db.begin() as connection:
        connection.exec_driver_sql(f"""CREATE TRIGGER `{trigger}` BEFORE INSERT ON auth_sessions
            FOR EACH ROW BEGIN
              IF EXISTS (SELECT 1 FROM users WHERE id = NEW.user_id AND google_sub = %s) THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'E2E injected session write failure';
              END IF;
            END""", (case.subject,))
    try:
        failed = case.client.post("/api/auth/registrations/workers", json=case.worker, headers=headers)
        assert failed.status_code == 500 and failed.json()["code"] == "INTERNAL_ERROR"
        assert "set-cookie" not in failed.headers
        with Session(real_db) as db:
            assert [db.scalar(select(func.count()).select_from(model)) for model in models] == before
            assert registration_row(db, case).consumed_at is None
    finally:
        with real_db.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS `{trigger}`")
    retry = case.client.post("/api/auth/registrations/workers", json=case.worker, headers=headers)
    assert retry.status_code == 201
    user_id = retry.json()["user"]["id"]
    profile = case.client.get("/api/users/me/profile")
    assert profile.status_code == 200 and profile.json()["id"] == user_id
    assert {field_name: profile.json()[field_name] for field_name in case.worker} == case.worker
    with Session(real_db) as db:
        assert db.get(User, user_id).name == case.worker["name"]
        assert db.get(WorkerProfile, user_id) is not None
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)) is not None
        assert registration_row(db, case).consumed_at is not None
        record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == headers["Idempotency-Key"]))
        assert record.state == "COMPLETED"
        assert [db.scalar(select(func.count()).select_from(model)) for model in models] == [
            count + increment for count, increment in zip(before, (1, 1, 2, 2, 3, 1, 1), strict=True)]


def test_processing_key_conflict_preserves_registration_and_can_retry(registration, real_db):
    case = registration
    headers = case.headers()
    now = utcnow()
    with Session(real_db) as db:
        record = IdempotencyRecord(subject_id=auth.hash_token(case.subject), idempotency_key=headers["Idempotency-Key"],
                                   endpoint="POST /api/auth/registrations/workers",
                                   request_hash=body_hash(WorkerInput.model_validate(case.worker)), state="PROCESSING",
                                   lock_token=str(uuid.uuid4()), locked_until=now + timedelta(seconds=60),
                                   created_at=now, expires_at=now + timedelta(days=1))
        db.add(record)
        db.commit()
        record_id = record.id
    response = case.client.post("/api/auth/registrations/workers", json=case.worker, headers=headers)
    assert response.status_code == 409 and response.json()["code"] == "STATE_CONFLICT"
    assert response.headers["retry-after"] == "1" and "set-cookie" not in response.headers
    with Session(real_db) as db:
        assert db.scalar(select(User).where(User.google_sub == case.subject)) is None
        assert registration_row(db, case).consumed_at is None
        assert db.get(IdempotencyRecord, record_id).state == "PROCESSING"
        db.delete(db.get(IdempotencyRecord, record_id))
        db.commit()
    retry = case.client.post("/api/auth/registrations/workers", json=case.worker, headers=headers)
    assert retry.status_code == 201
    user_id = retry.json()["user"]["id"]
    assert case.client.get("/api/users/me/profile").json()["id"] == user_id
    with Session(real_db) as db:
        assert db.get(WorkerProfile, user_id) is not None
        assert registration_row(db, case).consumed_at is not None

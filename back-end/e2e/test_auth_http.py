"""Auth HTTP → MySQL scenarios; external Google verification is the seed boundary."""
from datetime import timedelta
from http.cookies import SimpleCookie

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import (
    WEEKDAYS,
    AuthSession,
    AvailabilityDay,
    AvailabilityRule,
    IdempotencyRecord,
    User,
    WorkerCareer,
    WorkerProfile,
)
from e2e.conftest import registration_row, worker_snapshot


@pytest.mark.parametrize("experienced", [False, True])
def test_worker_registration_persists_entire_aggregate_and_replays(registration, real_db, experienced):
    case = registration
    if experienced:
        case.worker.update(experienceLevel="EXPERIENCED", careers=[
            {"industry": "CAFE", "duties": "음료 제조", "storeName": "월계 카페",
             "startMonth": "2024-03", "endMonth": None, "isCurrent": True},
            {"industry": "OTHER", "duties": "매장 정리", "startMonth": "2022-01",
             "endMonth": "2023-12", "isCurrent": False},
        ], availabilities=[
            {"days": ["FRI", "MON"], "startTime": "09:30", "endTime": "14:00",
             "endsNextDay": False},
            {"days": ["SUN"], "startTime": "22:00", "endTime": "02:00",
             "endsNextDay": True},
        ])
    key = case.headers()["Idempotency-Key"]
    response = case.register(key=key)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    session_cookie = cookies[auth.SESSION_COOKIE_NAME]
    assert session_cookie["httponly"] and session_cookie["path"] == "/"
    assert session_cookie["samesite"].lower() == "lax" and not session_cookie["domain"]
    assert int(session_cookie["max-age"]) > 0
    assert not session_cookie["secure"]  # dedicated plain HTTP local environment
    deletion = cookies[auth.REGISTRATION_COOKIE_NAME]
    assert deletion["max-age"] == "0" and deletion["path"] == "/api/auth"
    assert deletion["httponly"] and deletion["samesite"].lower() == "lax"
    assert auth.REGISTRATION_COOKIE_NAME not in case.client.cookies
    user_id = response.json()["user"]["id"]
    token = case.client.cookies.get(auth.SESSION_COOKIE_NAME)
    assert token and response.json()["nextAction"] == "WORKER_HOME"
    with Session(real_db) as db:
        user = db.get(User, user_id)
        assert user.google_sub == case.subject and user.name == case.worker["name"]
        profile = db.get(WorkerProfile, user_id)
        assert (profile.birth_date.isoformat(), profile.gender, profile.experience_level) == (
            case.worker["birthDate"], case.worker["gender"], case.worker["experienceLevel"])
        careers = db.scalars(select(WorkerCareer).where(WorkerCareer.worker_id == user_id)
                             .order_by(WorkerCareer.sort_order)).all()
        assert len(careers) == len(case.worker["careers"])
        for index, (row, expected) in enumerate(zip(careers, case.worker["careers"], strict=True)):
            assert (row.sort_order, row.industry, row.duties, row.store_name, row.start_month,
                    row.end_month, row.is_current) == (
                index, expected["industry"], expected["duties"], expected.get("storeName"),
                expected["startMonth"], expected["endMonth"], expected["isCurrent"])
        rules = db.scalars(select(AvailabilityRule).where(AvailabilityRule.worker_id == user_id)
                           .order_by(AvailabilityRule.sort_order)).all()
        assert len(rules) == len(case.worker["availabilities"])
        for index, (row, expected) in enumerate(zip(rules, case.worker["availabilities"], strict=True)):
            assert (row.sort_order, row.start_time.strftime("%H:%M"), row.end_time.strftime("%H:%M"),
                    row.ends_next_day) == (
                index, expected["startTime"], expected["endTime"], expected["endsNextDay"])
            days = db.scalars(select(AvailabilityDay.weekday).where(
                AvailabilityDay.rule_id == row.id)).all()
            assert sorted(days, key=WEEKDAYS.index) == sorted(expected["days"], key=WEEKDAYS.index)
        assert registration_row(db, case).consumed_at is not None
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).token_hash == auth.hash_token(token)
        assert db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)) is not None
        persisted = worker_snapshot(db, user_id)
    session = case.client.get("/api/auth/session")
    assert session.status_code == 200 and session.json()["user"]["id"] == user_id
    profile = case.client.get("/api/users/me/profile")
    assert profile.status_code == 200
    assert profile.json()["careers"] == case.worker["careers"]
    for field_name in ("name", "phoneNumber", "birthDate", "gender", "experienceLevel"):
        assert profile.json()[field_name] == case.worker[field_name]
    assert len(profile.json()["availabilities"]) == len(case.worker["availabilities"])
    for actual, expected in zip(profile.json()["availabilities"], case.worker["availabilities"], strict=True):
        assert actual == {**expected, "days": sorted(expected["days"], key=WEEKDAYS.index)}
    replay = case.register(key=key)
    assert replay.status_code == 201 and replay.json() == response.json()
    assert replay.headers["Idempotent-Replayed"] == "true" and "set-cookie" not in replay.headers
    changed = case.register(body={**case.worker, "name": "다른 이름"}, key=key)
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    duplicate = case.register()
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "ALREADY_REGISTERED"
    with Session(real_db) as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.google_sub == case.subject)) == 1
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user_id)) == 1
        assert db.get(User, user_id).name == case.worker["name"]
        assert worker_snapshot(db, user_id) == persisted


@pytest.mark.parametrize("change", [
    {"name": " "}, {"birthDate": "2001-02-29"}, {"birthDate": "2999-01-01"},
    {"phoneNumber": "010１２３４５６７８"}, {"role": "OWNER"},
    {"experienceLevel": "EXPERIENCED"}, {"availabilities": []},
])
def test_invalid_registration_preserves_db_and_can_retry(registration, real_db, change):
    case = registration
    key = case.headers()["Idempotency-Key"]
    response = case.register(body={**case.worker, **change}, key=key)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert "set-cookie" not in response.headers
    with Session(real_db) as db:
        assert db.scalar(select(User).where(User.google_sub == case.subject)) is None
        assert registration_row(db, case).consumed_at is None
        assert db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)) is None
    assert case.register(key=key).status_code == 201


@pytest.mark.parametrize("guard,status", [("Origin", 403), ("X-CSRF-Token", 403),
                                         ("Idempotency-Key", 422), ("wrong-origin", 403),
                                         ("wrong-csrf", 403), ("malformed-key", 422)])
def test_registration_guards_do_not_consume_session(registration, real_db, guard, status):
    headers = registration.headers()
    if guard == "wrong-origin":
        headers["Origin"] = "http://evil.test"
    elif guard == "wrong-csrf":
        headers["X-CSRF-Token"] = "invalid"
    elif guard == "malformed-key":
        headers["Idempotency-Key"] = "invalid"
    else:
        headers.pop(guard)
    response = registration.client.post("/api/auth/registrations/workers",
                                        json=registration.worker, headers=headers)
    assert response.status_code == status
    with Session(real_db) as db:
        assert db.scalar(select(User).where(User.google_sub == registration.subject)) is None
        assert registration_row(db, registration).consumed_at is None


def test_logout_revokes_persisted_session_and_old_cookie(member, real_db, base_url):
    case, user_id = member
    token = case.client.cookies.get(auth.SESSION_COOKIE_NAME)
    headers = case.headers()
    response = case.client.post("/api/auth/logout", headers=headers)
    assert response.status_code == 204 and auth.SESSION_COOKIE_NAME not in case.client.cookies
    with Session(real_db) as db:
        assert db.scalar(select(AuthSession).where(AuthSession.user_id == user_id)).revoked_at is not None
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as stale:
        stale.cookies.set(auth.SESSION_COOKIE_NAME, token)
        rejected = stale.get("/api/auth/session")
        assert rejected.status_code == 401 and rejected.json()["code"] == "SESSION_EXPIRED"
    assert case.client.post("/api/auth/logout", headers={"Origin": base_url}).status_code == 204


@pytest.mark.parametrize("kind,code,status", [("expired", "SESSION_EXPIRED", 401),
                                              ("suspended", "ACCOUNT_SUSPENDED", 403)])
def test_db_session_state_is_enforced_over_http(member, real_db, kind, code, status):
    case, user_id = member
    with Session(real_db) as db:
        if kind == "expired":
            row = db.scalar(select(AuthSession).where(AuthSession.user_id == user_id))
            row.expires_at = utcnow() - timedelta(seconds=1)
        else:
            db.get(User, user_id).status = "SUSPENDED"
        db.commit()
    response = case.client.get("/api/auth/session")
    assert response.status_code == status and response.json()["code"] == code

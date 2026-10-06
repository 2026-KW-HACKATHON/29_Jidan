"""Exercise the PR #154 wrapper over real HTTP and an independent MySQL connection.

Run only in a new disposable manual Compose project. Existing sandbox data is rejected.
The wrapper supplies Google identity fixtures; business handlers and DB writes stay real.
"""
import argparse
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from threading import Barrier
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db import get_engine
from app.db.models import (
    AuthSession,
    AvailabilityDay,
    AvailabilityRule,
    RegistrationSession,
    User,
    WorkerProfile,
)
from testing.sandbox import validate_environment

WORKER = {
    "name": "Sandbox HTTP 가입", "phoneNumber": "01023456789", "birthDate": "2001-03-14",
    "gender": "FEMALE", "experienceLevel": "NEW", "careers": [],
    "availabilities": [{"days": ["MON"], "startTime": "09:00", "endTime": "14:00",
                        "endsNextDay": False}],
}


def require_fresh_fixtures(engine) -> None:
    with Session(engine) as db:
        for model in (User, WorkerProfile, AuthSession, RegistrationSession):
            assert db.scalar(select(func.count()).select_from(model)) == 0, "Use a fresh disposable project"


def check(base_url: str) -> list[str]:
    validate_environment()
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"api", "127.0.0.1", "localhost"}
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.username or parsed.password):
        raise ValueError("Use the local manual sandbox HTTP server")
    engine = get_engine()
    assert engine.dialect.name == "mysql", "This check requires real MySQL"
    require_fresh_fixtures(engine)
    origin = os.environ["ALLOWED_ORIGINS"]
    passed = []

    def counts():
        with Session(engine) as db:
            return tuple(db.scalar(select(func.count()).select_from(model))
                         for model in (User, WorkerProfile, AuthSession, RegistrationSession))

    def headers(client):
        response = client.get("/api/auth/csrf")
        assert response.status_code == 200
        return {"Origin": origin, "X-CSRF-Token": response.json()["csrfToken"],
                "Idempotency-Key": str(uuid.uuid4())}

    with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
        assert client.get("/api/health").json()["database"] == "ok"
        page = client.get("/sandbox")
        assert page.status_code == 200 and "Jidan API 테스트" in page.text
        assert page.headers["cache-control"] == "no-store"
        assert client.get("/docs").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/users/me/profile" in paths and "/sandbox/login/{role}" in paths
        passed.append("page, actual Swagger and real database health")

        before = counts()
        for rejected in (None, "http://evil.test", origin + ".evil.test"):
            response = client.post("/sandbox/login/owner",
                                   headers={"Origin": rejected} if rejected else {})
            assert response.status_code == 403
        assert client.post("/sandbox/login/admin", headers={"Origin": origin}).status_code == 422
        assert counts() == before
        passed.append("Origin and role rejection preserve all rows")

        barrier = Barrier(4)

        def first_login(role):
            with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as other:
                barrier.wait(timeout=10)
                response = other.post("/sandbox/login/" + role, headers={"Origin": origin})
                if response.status_code == 204:
                    assert other.get("/api/auth/session").status_code == 200
                return response.status_code

        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(first_login, ["worker"] * 4))
        assert statuses == [204] * 4, f"Concurrent first login statuses: {statuses}"
        assert counts()[:3] == (1, 1, 4)
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.google_sub == "sandbox-worker"))
            profile = db.get(WorkerProfile, user.id)
            assert user.role == "WORKER" and user.status == "ACTIVE" and user.email_verified
            assert (profile.birth_date, profile.gender, profile.experience_level) == (
                date(2001, 3, 14), "FEMALE", "NEW")
            rule = db.scalar(select(AvailabilityRule).where(AvailabilityRule.worker_id == user.id))
            assert rule is not None and (rule.start_time, rule.end_time, rule.ends_next_day) == (
                time(9), time(14), False)
            assert db.scalars(select(AvailabilityDay.weekday).where(
                AvailabilityDay.rule_id == rule.id)).all() == ["MON"]
        passed.append("concurrent first login creates one account/profile and four usable sessions")

        response = client.post("/sandbox/login/worker", headers={"Origin": origin})
        assert response.status_code == 204 and "HttpOnly" in response.headers["set-cookie"]
        assert counts()[:3] == (1, 1, 5)
        assert client.get("/api/users/me/profile").status_code == 200
        passed.append("repeat login reuses committed fixture account")

        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(first_login, ["owner"] * 4))
        assert statuses == [204] * 4, f"Concurrent first owner login statuses: {statuses}"
        assert counts()[:3] == (2, 1, 9)
        passed.append("concurrent first owner login creates one owner and four usable sessions")

        assert client.post("/sandbox/login/owner", headers={"Origin": origin}).status_code == 204
        before = counts()
        assert client.get("/api/users/me/profile").status_code == 403
        response = client.patch("/api/users/me/profile/basic", json={"name": "forbidden"},
                                headers=headers(client))
        assert response.status_code == 403 and counts() == before
        with Session(engine) as db:
            assert db.scalar(select(User.name).where(User.google_sub == "sandbox-worker")) == "테스트 worker"
        passed.append("owner cannot read or overwrite worker profile")

        before = counts()
        assert client.post("/sandbox/registration", headers={"Origin": origin}).status_code == 204
        assert auth.SESSION_COOKIE_NAME not in client.cookies
        assert auth.REGISTRATION_COOKIE_NAME in client.cookies
        assert counts()[3] == before[3] + 1
        assert client.get("/api/users/me/profile").status_code == 401
        passed.append("registration fixture replaces member cookie without granting member access")

        response = client.post("/api/auth/registrations/workers", json=WORKER, headers=headers(client))
        assert response.status_code == 201
        assert auth.REGISTRATION_COOKIE_NAME not in client.cookies
        assert auth.SESSION_COOKIE_NAME in client.cookies
        assert client.get("/api/users/me/profile").json()["name"] == WORKER["name"]
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.name == WORKER["name"]))
            assert user is not None and user.role == "WORKER"
            assert db.get(WorkerProfile, user.id) is not None
            new_user_id = user.id
        passed.append("real worker registration commits user and profile")

        response = client.patch("/api/users/me/profile/basic", json={"name": "Sandbox HTTP 저장"},
                                headers=headers(client))
        assert response.status_code == 200
        assert client.get("/api/users/me/profile").json()["name"] == "Sandbox HTTP 저장"
        with Session(engine) as db:
            assert db.get(User, new_user_id).name == "Sandbox HTTP 저장"
        passed.append("profile update matches independent DB and API reread")
    engine.dispose()
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://api:8000")
    args = parser.parse_args()
    print(json.dumps({"passed": check(args.base_url), "ai": "not called",
                      "google": "fixture", "database": "real MySQL", "transport": "real HTTP"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

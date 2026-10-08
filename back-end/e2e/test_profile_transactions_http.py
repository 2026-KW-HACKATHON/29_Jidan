"""Real concurrent HTTP writes, including stale no-op regression under actual DB contention."""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import AuthSession, AvailabilityDay, AvailabilityRule, User, WorkerCareer
from e2e.test_profile_http import CAREER, PATH, profile_headers

CAREERS_A = {"experienceLevel": "EXPERIENCED", "careers": [{**CAREER, "duties": "동시 경력 A"}]}
CAREERS_B = {"experienceLevel": "EXPERIENCED", "careers": [
    {**CAREER, "duties": "동시 경력 B"}, {**CAREER, "industry": "OTHER", "duties": "동시 경력 C"}]}
AVAIL_A = {"availabilities": [{"days": ["TUE"], "startTime": "10:00", "endTime": "11:00", "endsNextDay": False}]}
AVAIL_B = {"availabilities": [
    {"days": ["FRI"], "startTime": "08:00", "endTime": "10:00", "endsNextDay": False},
    {"days": ["SAT", "SUN"], "startTime": "20:00", "endTime": "23:00", "endsNextDay": False}]}


@contextmanager
def observe_auth_updates(engine, user_id):
    table, trigger = f"e2e_profile_attempts_{uuid.uuid4().hex}", f"e2e_profile_auth_{uuid.uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE TABLE `{table}` (session_id VARCHAR(36) NOT NULL) ENGINE=MEMORY")
            connection.exec_driver_sql(f"""CREATE TRIGGER `{trigger}` BEFORE UPDATE ON auth_sessions
                FOR EACH ROW BEGIN
                  IF NEW.user_id = %s AND NEW.last_seen_at > OLD.last_seen_at THEN
                    INSERT INTO `{table}` VALUES (NEW.id);
                  END IF;
                END""", (user_id,))
        yield table
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS `{trigger}`")
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS `{table}`")


def assert_persisted_profile(db, user_id, profile):
    assert db.get(User, user_id).name == profile["name"]
    assert db.get(User, user_id).phone_number == profile["phoneNumber"]
    careers = db.scalars(select(WorkerCareer).where(WorkerCareer.worker_id == user_id)
                         .order_by(WorkerCareer.sort_order)).all()
    assert len(careers) == len(profile["careers"])
    for index, (row, expected) in enumerate(zip(careers, profile["careers"], strict=True)):
        assert (row.sort_order, row.industry, row.duties, row.store_name, row.start_month, row.end_month, row.is_current) == (
            index, expected["industry"], expected["duties"], expected.get("storeName"), expected["startMonth"],
            expected["endMonth"], expected["isCurrent"])
    rules = db.scalars(select(AvailabilityRule).where(AvailabilityRule.worker_id == user_id)
                       .order_by(AvailabilityRule.sort_order)).all()
    assert len(rules) == len(profile["availabilities"])
    for index, (row, expected) in enumerate(zip(rules, profile["availabilities"], strict=True)):
        assert (row.sort_order, row.start_time.strftime("%H:%M"), row.end_time.strftime("%H:%M"), row.ends_next_day) == (
            index, expected["startTime"], expected["endTime"], expected["endsNextDay"])
        days = db.scalars(select(AvailabilityDay.weekday).where(AvailabilityDay.rule_id == row.id)).all()
        assert sorted(days) == sorted(expected["days"])


@pytest.mark.parametrize("left,right", [
    (("PATCH", "/basic", {"name": "동시 이름"}), ("PATCH", "/basic", {"phoneNumber": "01099998888"})),
    (("PATCH", "/basic", {"name": "동시 이름"}), ("PUT", "/careers", CAREERS_A)),
    (("PUT", "/careers", CAREERS_A), ("PUT", "/availabilities", AVAIL_A)),
    (("PUT", "/careers", CAREERS_A), ("PUT", "/careers", CAREERS_B)),
    (("PUT", "/availabilities", AVAIL_A), ("PUT", "/availabilities", AVAIL_B)),
])
def test_profile_contention_preserves_complete_updates(member, real_db, base_url, left, right):
    case, user_id = member
    baseline = case.client.get(PATH).json()
    first_csrf = profile_headers(case)["X-CSRF-Token"]
    first_token = case.client.cookies.get(auth.SESSION_COOKIE_NAME)
    with Session(real_db) as db:
        second = auth.create_session(user_id, db=db)
        db.commit()
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False,
                      headers={"Cookie": f"{auth.SESSION_COOKIE_NAME}={second.token}"}) as browser:
        second_csrf = browser.get("/api/auth/csrf").json()["csrfToken"]
    with Session(real_db) as db:
        for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user_id)):
            session.last_seen_at = utcnow() - timedelta(minutes=2)
        db.commit()
    barrier = Barrier(2)

    def write(index):
        method, suffix, body = (left, right)[index]
        token, csrf = ((first_token, first_csrf), (second.token, second_csrf))[index]
        with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
            barrier.wait(timeout=5)
            return client.request(method, PATH + suffix, json=body,
                                  headers={"Origin": base_url, "X-CSRF-Token": csrf,
                                           "Cookie": f"{auth.SESSION_COOKIE_NAME}={token}"})

    with observe_auth_updates(real_db, user_id) as audit_table, ThreadPoolExecutor(max_workers=2) as pool:
        with Session(real_db) as blocker:
            blocker.scalar(select(User).where(User.id == user_id).with_for_update())
            futures = [pool.submit(write, index) for index in range(2)]
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with real_db.connect() as observer:
                        ready = observer.exec_driver_sql(f"SELECT COUNT(DISTINCT session_id) FROM `{audit_table}`").scalar_one() == 2
                    if ready:
                        break
                    time.sleep(0.005)
                assert ready, "both HTTP requests did not reach authentication while the profile lock was held"
                assert not any(future.done() for future in futures)
            finally:
                blocker.rollback()
        responses = [future.result(timeout=15) for future in futures]
    assert [response.status_code for response in responses] == [200, 200]
    for response, request in zip(responses, (left, right), strict=True):
        assert {key: response.json()[key] for key in request[2]} == request[2]
    final = case.client.get(PATH)
    assert final.status_code == 200
    expected = {**baseline, **left[2], **right[2]}
    fields = {*left[2], *right[2]}
    if left[1] == right[1] and left[1] != "/basic":
        assert {key: final.json()[key] for key in fields} in (left[2], right[2])
    else:
        assert {key: final.json()[key] for key in fields} == {key: expected[key] for key in fields}
    assert {key: value for key, value in final.json().items() if key not in {*fields, "updatedAt"}} == {
        key: value for key, value in baseline.items() if key not in {*fields, "updatedAt"}}
    with Session(real_db) as db:
        assert_persisted_profile(db, user_id, final.json())


@pytest.mark.parametrize("suffix,changed", [("/careers", CAREERS_A), ("/availabilities", AVAIL_A)])
def test_recent_session_contention_restores_initial_list(member, real_db, base_url, suffix, changed):
    case, user_id = member
    baseline = case.client.get(PATH).json()
    fields = set(changed)
    restore = {key: baseline[key] for key in fields}
    first_token = case.client.cookies.get(auth.SESSION_COOKIE_NAME)
    first_csrf = profile_headers(case)["X-CSRF-Token"]
    with Session(real_db) as db:
        second = auth.create_session(user_id, db=db)
        db.commit()
    # Only the guarded disposable DB's root account reads lock metadata; requests use the normal app DB user.
    inspector = create_engine(real_db.url.set(username="root", password="e2e-root"))

    def write(token, csrf, body):
        with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
            return client.put(PATH + suffix, json=body, headers={"Origin": base_url, "X-CSRF-Token": csrf,
                              "Cookie": f"{auth.SESSION_COOKIE_NAME}={token}"})

    def wait_for_requests(count):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with inspector.connect() as observer:
                waiting = observer.exec_driver_sql("""SELECT COUNT(*) FROM performance_schema.data_locks
                    WHERE OBJECT_SCHEMA = 'jidan_e2e_test' AND OBJECT_NAME = 'users'
                      AND LOCK_STATUS = 'WAITING' AND LOCK_TYPE = 'RECORD'""").scalar_one()
            if waiting >= count:
                return
            time.sleep(0.005)
        raise AssertionError(f"{count} actual requests did not wait on the users lock")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            with Session(real_db) as blocker:
                blocker.scalar(select(User).where(User.id == user_id).with_for_update())
                try:
                    first = pool.submit(write, first_token, first_csrf, changed)
                    wait_for_requests(1)
                    last = pool.submit(write, second.token, second.csrf_token, restore)
                    wait_for_requests(2)
                    assert not first.done() and not last.done()
                finally:
                    blocker.rollback()
            responses = [first.result(timeout=15), last.result(timeout=15)]
        assert [response.status_code for response in responses] == [200, 200]
        assert {key: responses[0].json()[key] for key in fields} == changed
        assert {key: responses[1].json()[key] for key in fields} == restore
        final = case.client.get(PATH)
        assert final.status_code == 200
        assert {key: value for key, value in final.json().items() if key != "updatedAt"} == {
            key: value for key, value in baseline.items() if key != "updatedAt"}
        with Session(real_db) as db:
            assert_persisted_profile(db, user_id, final.json())
    finally:
        inspector.dispose()

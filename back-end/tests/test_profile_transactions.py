import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, worker_profile
from app.db.models import AuthSession, User
from tests.auth_contract import ContractClient
from tests.test_profile_availabilities import AVAILABLE, GROUP, NIGHT
from tests.test_profile_basic import BASIC
from tests.test_profile_careers import CAREER, CAREERS, career_body
from tests.test_worker_profile import PATH
from tests.test_worker_profile import profile_api as _profile_api
from tests.test_worker_registration import headers
from tests.test_worker_registration import worker_api as _worker_api

worker_api = _worker_api
profile_api = _profile_api
EDITS = [
    ("PATCH", BASIC, {"name": "변경"}),
    ("PUT", CAREERS, career_body([CAREER])),
    ("PUT", AVAILABLE, {"availabilities": [NIGHT]}),
]


@pytest.mark.parametrize("method,path,body", EDITS)
@pytest.mark.parametrize("kind,status,code", [
    ("missing", 401, "SESSION_EXPIRED"), ("registration", 401, "REGISTRATION_REQUIRED"),
    ("revoked", 401, "SESSION_EXPIRED"), ("owner", 403, "FORBIDDEN"),
    ("suspended", 403, "ACCOUNT_SUSPENDED"),
    ("csrf", 403, "CSRF_INVALID"), ("origin", 403, "CSRF_INVALID"),
])
def test_all_writes_require_active_worker_and_csrf(profile_api, db_engine, method, path, body, kind, status, code):
    before = profile_api.get(PATH).json()
    values = headers(profile_api)
    with Session(db_engine) as db:
        user = db.scalar(select(User))
        if kind == "owner": user.role = "OWNER"
        if kind == "suspended": user.status = "SUSPENDED"
        if kind == "revoked":
            from app.db import utcnow
            db.scalar(select(AuthSession)).revoked_at = utcnow()
        if kind == "registration":
            issued = auth.create_registration_session("other", "other@test.org", db=db)
        db.commit()
    if kind in {"missing", "registration"}: profile_api.cookies.clear()
    if kind == "registration":
        profile_api.cookies.set(auth.REGISTRATION_COOKIE_NAME, issued.token)
        values["X-CSRF-Token"] = issued.csrf_token
    if kind == "csrf": values.pop("X-CSRF-Token")
    if kind == "origin": values.pop("Origin")
    response = profile_api.request(method, path, json=body, headers=values)
    assert response.status_code == status and response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store"
    with Session(db_engine) as db:
        user = db.scalar(select(User))
        current = worker_profile.profile_body(db, user)
        # Account status/role changes are test setup; no requested profile field was saved.
        for key in before.keys() - {"updatedAt"}:
            assert current[key] == before[key]


@pytest.mark.parametrize("method,path,body", EDITS)
def test_commit_failure_rolls_back_all_areas(profile_api, monkeypatch, caplog, method, path, body):
    before = profile_api.get(PATH).json()
    values = headers(profile_api)
    def fail(self):
        raise RuntimeError("worker@test.org private submitted data token")
    monkeypatch.setattr(Session, "commit", fail)
    response = profile_api.request(method, path, json=body, headers=values)
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert "set-cookie" not in response.headers
    assert profile_api.get(PATH).json() == before
    assert "worker@test.org" not in response.text + caplog.text
    assert "private submitted data token" not in response.text + caplog.text


def ordered_edits(profile_api, monkeypatch, first_edit, second_edit):
    first_locked = threading.Event()
    second_ready = threading.Event()
    counter_lock = threading.Lock()
    calls = 0
    original = worker_profile.lock_worker
    def ordered(db, user_id):
        nonlocal calls
        with counter_lock:
            index = calls
            calls += 1
        if index == 0:
            user = original(db, user_id)
            first_locked.set()
            assert second_ready.wait(5)
            return user
        second_ready.set()
        return original(db, user_id)
    monkeypatch.setattr(worker_profile, "lock_worker", ordered)
    values = headers(profile_api)
    token = profile_api.cookies.get(auth.SESSION_COOKIE_NAME)
    def submit(edit):
        method, path, body = edit
        with ContractClient(profile_api.app, raise_server_exceptions=False) as api:
            api.cookies.set(auth.SESSION_COOKIE_NAME, token)
            return api.request(method, path, json=body, headers=values)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, first_edit)
        assert first_locked.wait(5)
        second = pool.submit(submit, second_edit)
        responses = [first.result(10), second.result(10)]
    assert [r.status_code for r in responses] == [200, 200]
    assert profile_api.get(PATH).json() == responses[1].json()
    return responses[1].json()


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
@pytest.mark.parametrize("first,second,expected", [
    (("PATCH", BASIC, {"name": "첫 저장"}), ("PATCH", BASIC, {"name": "마지막 저장"}), {"name": "마지막 저장"}),
    (("PUT", AVAILABLE, {"availabilities": [GROUP]}), ("PUT", AVAILABLE, {"availabilities": [NIGHT]}), {"availabilities": [NIGHT]}),
])
def test_same_area_last_save(profile_api, monkeypatch, first, second, expected):
    result = ordered_edits(profile_api, monkeypatch, first, second)
    for key, value in expected.items():
        assert result[key] == value


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
@pytest.mark.parametrize("first,second,expected", [
    (("PATCH", BASIC, {"name": "변경"}), ("PUT", CAREERS, career_body([CAREER])), {"name": "변경", "careers": [CAREER]}),
    (("PUT", CAREERS, career_body([CAREER])), ("PUT", AVAILABLE, {"availabilities": [NIGHT]}), {"careers": [CAREER], "availabilities": [NIGHT]}),
    (("PUT", AVAILABLE, {"availabilities": [NIGHT]}), ("PATCH", BASIC, {"phoneNumber": "01099999999"}), {"availabilities": [NIGHT], "phoneNumber": "01099999999"}),
    # Partial edits in the basic area also preserve each other's omitted fields.
    (("PATCH", BASIC, {"name": "변경"}), ("PATCH", BASIC, {"gender": "MALE"}), {"name": "변경", "gender": "MALE"}),
])
def test_concurrent_areas_preserve_other_values(profile_api, monkeypatch, first, second, expected):
    result = ordered_edits(profile_api, monkeypatch, first, second)
    for key, value in expected.items():
        assert result[key] == value

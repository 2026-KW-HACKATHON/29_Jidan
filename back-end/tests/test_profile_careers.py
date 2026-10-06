import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import WorkerCareer
from tests.test_worker_profile import PATH
from tests.test_worker_profile import profile_api as _profile_api
from tests.test_worker_registration import headers
from tests.test_worker_registration import worker_api as _worker_api

worker_api = _worker_api
profile_api = _profile_api
CAREERS = PATH + "/careers"
CAREER = {"industry": "CAFE", "duties": "음료 제조", "startMonth": "2024-03", "endMonth": None, "isCurrent": True}


def career_body(careers, level="EXPERIENCED"):
    return {"experienceLevel": level, "careers": careers}


def test_replace_order_normalization_noop_and_clear(profile_api, db_engine):
    before = profile_api.get(PATH).json()
    body = career_body([{**CAREER, "storeName": " 매장 ", "duties": " 음료 제조 "},
                        {**CAREER, "industry": "OTHER", "endMonth": "2024-03", "isCurrent": False}])
    response = profile_api.put(CAREERS, json=body, headers=headers(profile_api))
    assert response.status_code == 200
    after = response.json()
    assert after["careers"][0]["storeName"] == "매장"
    assert after["careers"][0]["duties"] == "음료 제조"
    assert after["careers"][1]["industry"] == "OTHER" and "storeName" not in after["careers"][1]
    assert after["updatedAt"] > before["updatedAt"]
    for key in before.keys() - {"careers", "experienceLevel", "updatedAt"}:
        assert after[key] == before[key]
    with Session(db_engine) as db:
        ids = db.scalars(select(WorkerCareer.id).order_by(WorkerCareer.sort_order)).all()
    assert profile_api.put(CAREERS, json=body, headers=headers(profile_api)).json() == after
    with Session(db_engine) as db:
        assert db.scalars(select(WorkerCareer.id).order_by(WorkerCareer.sort_order)).all() == ids
    # Omitting the optional store name removes it; dropped items are deleted.
    response = profile_api.put(CAREERS, json=career_body([CAREER]), headers=headers(profile_api))
    assert response.json()["careers"] == [CAREER]
    response = profile_api.put(CAREERS, json=career_body([], "NEW"), headers=headers(profile_api))
    assert response.json()["experienceLevel"] == "NEW" and response.json()["careers"] == []
    with Session(db_engine) as db:
        assert db.scalars(select(WorkerCareer)).all() == []


@pytest.mark.parametrize("body", [
    {}, {"careers": []}, {"experienceLevel": "NEW"}, career_body([]),
    career_body([CAREER], "NEW"), career_body([CAREER] * 21), career_body([], "new"),
    *[career_body([{**CAREER, **change}]) for change in [
        {"startMonth": "0000-01"}, {"startMonth": "2024-00"}, {"startMonth": "2024-13"},
        {"startMonth": "2999-01"}, {"endMonth": "2999-01", "isCurrent": False},
        {"endMonth": "2024-02", "isCurrent": False}, {"endMonth": "2024-04"},
        {"isCurrent": False}, {"isCurrent": 1}, {"storeName": None}, {"storeName": " "},
        {"storeName": "a" * 101}, {"duties": " "}, {"duties": "a" * 301},
        {"id": "injected"}, {"industry": "cafe"},
    ]],
    *[{**career_body([CAREER]), key: "injected"} for key in ["name", "identity", "id", "role", "availabilities", "unknown"]],
])
def test_invalid_careers_keep_saved_list(profile_api, body):
    assert profile_api.put(CAREERS, json=career_body([CAREER]), headers=headers(profile_api)).status_code == 200
    before = profile_api.get(PATH).json()
    response = profile_api.put(CAREERS, json=body, headers=headers(profile_api))
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["fieldErrors"]
    assert profile_api.get(PATH).json() == before


def test_twenty_careers_and_string_boundaries(profile_api):
    career = {**CAREER, "storeName": "가" * 100, "duties": "가" * 300}
    response = profile_api.put(CAREERS, json=career_body([career] * 20), headers=headers(profile_api))
    assert response.status_code == 200 and len(response.json()["careers"]) == 20


def test_career_calendar_uses_seoul_month(profile_api, monkeypatch):
    from datetime import date

    monkeypatch.setattr("app.registration_inputs.today", lambda: date(2026, 10, 1))
    assert profile_api.put(CAREERS, json=career_body([{**CAREER, "startMonth": "2026-10"}]), headers=headers(profile_api)).status_code == 200
    assert profile_api.put(CAREERS, json=career_body([{**CAREER, "startMonth": "2026-11"}]), headers=headers(profile_api)).status_code == 422


def test_failure_after_delete_is_atomic(profile_api, monkeypatch):
    from app import worker_profile

    assert profile_api.put(CAREERS, json=career_body([CAREER]), headers=headers(profile_api)).status_code == 200
    before = profile_api.get(PATH).json()
    def fail(*args, **kwargs):
        raise RuntimeError("private career details")
    original = worker_profile.WorkerCareer
    monkeypatch.setattr(worker_profile, "WorkerCareer", fail)
    response = profile_api.put(CAREERS, json=career_body([{**CAREER, "duties": "변경"}]), headers=headers(profile_api))
    assert response.status_code == 500 and "private career details" not in response.text
    monkeypatch.setattr(worker_profile, "WorkerCareer", original)
    assert profile_api.get(PATH).json() == before


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_career_last_save_after_stale_auth_snapshot(profile_api, db_engine, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app import worker_profile
    from tests.auth_contract import ContractClient

    first_locked = threading.Event()
    second_authenticated = threading.Event()
    original = worker_profile.lock_worker
    counter_lock = threading.Lock()
    calls = 0
    def synchronize(db, user_id):
        nonlocal calls
        with counter_lock:
            index = calls
            calls += 1
        if index == 0:
            user = original(db, user_id)
            first_locked.set()
            assert second_authenticated.wait(5)
            return user
        # Authentication already read the old NEW state; the first request has not saved yet.
        second_authenticated.set()
        return original(db, user_id)
    monkeypatch.setattr(worker_profile, "lock_worker", synchronize)
    values = headers(profile_api)
    token = profile_api.cookies.get("jidan_session")
    def submit(body):
        with ContractClient(profile_api.app, raise_server_exceptions=False) as api:
            api.cookies.set("jidan_session", token)
            return api.put(CAREERS, json=body, headers=values)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, career_body([CAREER]))
        assert first_locked.wait(5)
        second = pool.submit(submit, career_body([], "NEW"))
        first_response, second_response = first.result(10), second.result(10)
    assert first_response.status_code == second_response.status_code == 200
    final = profile_api.get(PATH).json()
    assert final == second_response.json()
    assert final["experienceLevel"] == "NEW" and final["careers"] == []

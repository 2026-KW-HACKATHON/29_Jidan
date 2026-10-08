import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AvailabilityDay, AvailabilityRule
from tests.test_worker_profile import PATH
from tests.test_worker_profile import profile_api as _profile_api
from tests.test_worker_registration import headers
from tests.test_worker_registration import worker_api as _worker_api

worker_api = _worker_api
profile_api = _profile_api
AVAILABLE = PATH + "/availabilities"
GROUP = {"days": ["MON"], "startTime": "09:00", "endTime": "10:00", "endsNextDay": False}
NIGHT = {"days": ["SUN"], "startTime": "22:00", "endTime": "02:00", "endsNextDay": True}


def test_replace_night_order_noop_and_old_list_not_checked(profile_api, db_engine):
    before = profile_api.get(PATH).json()
    body = {"availabilities": [NIGHT, {**GROUP, "startTime": "02:00", "endTime": "03:00"},
                               {**GROUP, "days": ["WED", "TUE"]}]}
    response = profile_api.put(AVAILABLE, json=body, headers=headers(profile_api))
    assert response.status_code == 200
    after = response.json()
    assert after["availabilities"][:2] == body["availabilities"][:2]
    assert after["availabilities"][2]["days"] == ["TUE", "WED"]
    for key in before.keys() - {"availabilities", "updatedAt"}:
        assert after[key] == before[key]
    assert after["updatedAt"] > before["updatedAt"]
    with Session(db_engine) as db:
        ids = db.scalars(select(AvailabilityRule.id).order_by(AvailabilityRule.sort_order)).all()
        day_ids = set(db.scalars(select(AvailabilityDay.id)))
    assert profile_api.put(AVAILABLE, json=body, headers=headers(profile_api)).json() == after
    # Day selection is a set; changing only its order is not a material edit.
    assert profile_api.put(AVAILABLE, json={"availabilities": after["availabilities"]}, headers=headers(profile_api)).json() == after
    with Session(db_engine) as db:
        assert db.scalars(select(AvailabilityRule.id).order_by(AvailabilityRule.sort_order)).all() == ids
        assert set(db.scalars(select(AvailabilityDay.id))) == day_ids
    response = profile_api.put(AVAILABLE, json={"availabilities": [GROUP]}, headers=headers(profile_api))
    assert response.status_code == 200 and response.json()["availabilities"] == [GROUP]
    with Session(db_engine) as db:
        assert len(db.scalars(select(AvailabilityRule)).all()) == 1
        assert len(db.scalars(select(AvailabilityDay)).all()) == 1


@pytest.mark.parametrize("groups", [
    [], [GROUP, GROUP], [NIGHT, {**GROUP, "startTime": "01:00", "endTime": "03:00"}],
    [{**GROUP, "days": ["MON", "MON"]}], [{**GROUP, "days": []}],
    [{**GROUP, "days": ["mon"]}], [{**GROUP, "startTime": "09:15"}],
    [{**GROUP, "endTime": "24:00"}], [{**GROUP, "endTime": "09:00"}],
    [{**GROUP, "endTime": "08:30"}], [{**GROUP, "endsNextDay": True}],
    [{**GROUP, "endsNextDay": 1}], [{**GROUP, "id": "injected"}],
    [{**GROUP, "days": ["MON", "TUE"], "startTime": "10:00", "endTime": "10:00", "endsNextDay": True},
     {**GROUP, "days": ["TUE"], "startTime": "09:00", "endTime": "09:30"}],
])
def test_invalid_availability_is_atomic(profile_api, groups):
    before = profile_api.get(PATH).json()
    response = profile_api.put(AVAILABLE, json={"availabilities": groups}, headers=headers(profile_api))
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["fieldErrors"]
    assert profile_api.get(PATH).json() == before


@pytest.mark.parametrize("body", [{}, {"availabilities": None},
    *[{"availabilities": [GROUP], key: "injected"} for key in ["name", "id", "role", "identity", "careers", "experienceLevel", "unknown"]],
])
def test_availability_readonly_injection(profile_api, body):
    assert profile_api.put(AVAILABLE, json=body, headers=headers(profile_api)).status_code == 422


def slots(count):
    days = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    def clock(value):
        return f"{(value // 60) % 24:02d}:{value % 60:02d}"
    return [{"days": [days[index // 48]], "startTime": clock(index % 48 * 30),
             "endTime": clock((index % 48 + 1) * 30), "endsNextDay": index % 48 == 47}
            for index in range(count)]


def test_100_101_boundary(profile_api):
    response = profile_api.put(AVAILABLE, json={"availabilities": slots(100)}, headers=headers(profile_api))
    assert response.status_code == 200 and len(response.json()["availabilities"]) == 100
    before = response.json()
    response = profile_api.put(AVAILABLE, json={"availabilities": slots(101)}, headers=headers(profile_api))
    assert response.status_code == 422
    assert profile_api.get(PATH).json() == before


@pytest.mark.parametrize("groups", [
    [{**GROUP, "startTime": "00:00", "endTime": "00:00", "endsNextDay": True}],
    [{**NIGHT, "endTime": "00:00"}, {**GROUP, "startTime": "00:00", "endTime": "00:30"}],
    [{**GROUP, "days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], "startTime": "00:00", "endTime": "00:00", "endsNextDay": True}],
])
def test_full_day_midnight_and_weekly_adjacent(profile_api, groups):
    assert profile_api.put(AVAILABLE, json={"availabilities": groups}, headers=headers(profile_api)).status_code == 200


def test_failure_after_rule_insert_rolls_back_children(profile_api, monkeypatch):
    from app import worker_profile

    before = profile_api.get(PATH).json()
    original = worker_profile.rewrite
    def fail(db, *args):
        original(db, *args)
        db.flush()  # stale days were deleted and the rules rewritten before the failure
        raise RuntimeError("private availability detail")
    monkeypatch.setattr(worker_profile, "rewrite", fail)
    response = profile_api.put(AVAILABLE, json={"availabilities": [NIGHT, GROUP | {"days": ["TUE"]}]},
                               headers=headers(profile_api))
    assert response.status_code == 500 and "private availability detail" not in response.text
    assert profile_api.get(PATH).json() == before

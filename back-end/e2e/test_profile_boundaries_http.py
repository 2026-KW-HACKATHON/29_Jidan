"""HTTP field/list limits, strict types and calendar boundaries preserve actual rows."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import AvailabilityDay, AvailabilityRule, User, WorkerCareer
from e2e.conftest import worker_snapshot
from e2e.test_profile_http import CAREER, PATH, profile_headers


def slots(count):
    weekdays = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    result = []
    for index in range(count):
        day, slot = divmod(index, 48)
        start, finish = slot * 30, (slot + 1) * 30 % 1440
        result.append({"days": [weekdays[day]], "startTime": f"{start // 60:02}:{start % 60:02}",
                       "endTime": f"{finish // 60:02}:{finish % 60:02}", "endsNextDay": slot == 47})
    return result


@pytest.mark.parametrize("length", [1, 50])
def test_name_bounds_advance_utc_timestamp_and_noop_preserves_db(member, real_db, length):
    case, user_id = member
    old = utcnow() - timedelta(minutes=2)
    with Session(real_db) as db:
        db.get(User, user_id).updated_at = old
        db.commit()
    response = case.client.patch(PATH + "/basic", json={"name": "  " + "가" * length + "  "}, headers=profile_headers(case))
    assert response.status_code == 200 and response.json()["name"] == "가" * length
    changed = datetime.fromisoformat(response.json()["updatedAt"])
    assert changed.utcoffset() == timedelta(0) and changed > old
    assert case.client.get(PATH).json() == response.json()
    with Session(real_db) as db:
        assert db.get(User, user_id).updated_at == changed
        saved = worker_snapshot(db, user_id)
    retry = case.client.patch(PATH + "/basic", json={"name": "가" * length}, headers=profile_headers(case))
    assert retry.status_code == 200 and retry.json() == response.json()
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == saved


@pytest.mark.parametrize("careers", [
    [{**CAREER, "duties": "가" * 300, "storeName": "나" * 100}],
    [{**CAREER, "duties": f"업무 {index}"} for index in range(20)],
    [{"industry": "OTHER", "duties": "  종료 업무  ", "startMonth": "2022-03", "endMonth": "2022-03", "isCurrent": False}],
    [{**CAREER, "duties": "  음료 제조  ", "storeName": "  월계 카페  "}],
])
def test_career_limits_and_normalization_persist(member, real_db, careers):
    case, user_id = member
    expected = [{**career, "duties": career["duties"].strip(),
                 **({"storeName": career["storeName"].strip()} if "storeName" in career else {})} for career in careers]
    old = utcnow() - timedelta(minutes=2)
    with Session(real_db) as db:
        db.get(User, user_id).updated_at = old
        db.commit()
    response = case.client.put(PATH + "/careers", json={"experienceLevel": "EXPERIENCED", "careers": careers},
                               headers=profile_headers(case))
    assert response.status_code == 200 and response.json()["careers"] == expected
    assert case.client.get(PATH).json() == response.json()
    changed = datetime.fromisoformat(response.json()["updatedAt"])
    assert changed.utcoffset() == timedelta(0) and changed > old
    with Session(real_db) as db:
        rows = db.scalars(select(WorkerCareer).where(WorkerCareer.worker_id == user_id)
                          .order_by(WorkerCareer.sort_order)).all()
        assert len(rows) == len(expected)
        assert [row.duties for row in rows] == [career["duties"] for career in expected]
        assert [row.store_name for row in rows] == [career.get("storeName") for career in expected]
        assert db.get(User, user_id).updated_at == changed
        saved = worker_snapshot(db, user_id)
    repeated = case.client.put(PATH + "/careers", json={"experienceLevel": "EXPERIENCED", "careers": expected},
                               headers=profile_headers(case))
    assert repeated.status_code == 200 and repeated.json() == response.json()
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == saved


@pytest.mark.parametrize("groups", [
    slots(100),
    [{"days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
      "startTime": "00:00", "endTime": "00:00", "endsNextDay": True}],
    [{"days": ["SUN"], "startTime": "23:30", "endTime": "00:00", "endsNextDay": True},
     {"days": ["MON"], "startTime": "00:00", "endTime": "00:30", "endsNextDay": False}],
])
def test_availability_limits_full_day_and_midnight_boundary(member, real_db, groups):
    case, user_id = member
    old = utcnow() - timedelta(minutes=2)
    with Session(real_db) as db:
        db.get(User, user_id).updated_at = old
        db.commit()
    response = case.client.put(PATH + "/availabilities", json={"availabilities": groups}, headers=profile_headers(case))
    assert response.status_code == 200 and response.json()["availabilities"] == groups
    assert case.client.get(PATH).json() == response.json()
    changed = datetime.fromisoformat(response.json()["updatedAt"])
    assert changed.utcoffset() == timedelta(0) and changed > old
    with Session(real_db) as db:
        rows = db.scalars(select(AvailabilityRule).where(AvailabilityRule.worker_id == user_id)
                          .order_by(AvailabilityRule.sort_order)).all()
        assert len(rows) == len(groups)
        assert db.scalar(select(func.count()).select_from(AvailabilityDay)
                         .join(AvailabilityRule).where(AvailabilityRule.worker_id == user_id)) == sum(len(g["days"]) for g in groups)
        for row, expected in zip(rows, groups, strict=True):
            assert (row.start_time.strftime("%H:%M"), row.end_time.strftime("%H:%M"), row.ends_next_day) == (
                expected["startTime"], expected["endTime"], expected["endsNextDay"])
        assert db.get(User, user_id).updated_at == changed
        saved = worker_snapshot(db, user_id)
    repeated = case.client.put(PATH + "/availabilities", json={"availabilities": groups}, headers=profile_headers(case))
    assert repeated.status_code == 200 and repeated.json() == response.json()
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == saved


INVALID = [("/basic", {"name": "가" * 51}), ("/basic", {"name": 1}), ("/basic", {"phoneNumber": 1012345678}),
           ("/basic", {"birthDate": 0}), ("/basic", {"gender": "OTHER"})]
INVALID += [("/basic", {field_name: None}) for field_name in ("phoneNumber", "birthDate", "gender")]
INVALID += [("/careers", {"experienceLevel": "EXPERIENCED", "careers": [{**CAREER, **change}]}) for change in (
    {"duties": "가" * 301}, {"storeName": "가" * 101}, {"storeName": None}, {"isCurrent": 1},
    {"endMonth": "2023-01", "isCurrent": False}, {"endMonth": "2024-04", "isCurrent": True},
)]
INVALID += [("/careers", {"experienceLevel": "EXPERIENCED", "careers": [CAREER] * 21})]
INVALID += [("/availabilities", {"availabilities": [group]}) for group in (
    {"days": [], "startTime": "09:00", "endTime": "10:00", "endsNextDay": False},
    {"days": ["MON"], "startTime": "24:00", "endTime": "10:00", "endsNextDay": False},
    {"days": ["MON"], "startTime": "09:00", "endTime": "10:00", "endsNextDay": 0},
)]
INVALID += [("/availabilities", {"availabilities": slots(101)})]
INVALID += [("/availabilities", {"availabilities": [slots(1)[0], slots(1)[0]]})]


@pytest.mark.parametrize("suffix,body", INVALID)
def test_invalid_boundaries_preserve_full_aggregate(member, real_db, suffix, body):
    case, user_id = member
    # Start with nonempty child rows so rejection cannot silently clear them.
    assert case.client.put(PATH + "/careers", json={"experienceLevel": "EXPERIENCED", "careers": [CAREER]},
                           headers=profile_headers(case)).status_code == 200
    prior = case.client.get(PATH).json()
    with Session(real_db) as db:
        before = worker_snapshot(db, user_id)
    response = case.client.request("PATCH" if suffix == "/basic" else "PUT", PATH + suffix,
                                    json=body, headers=profile_headers(case))
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["fieldErrors"] and case.client.get(PATH).json() == prior
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == before


def test_today_birthdate_allowed_but_tomorrow_rejected(member, real_db):
    case, user_id = member
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    accepted = case.client.patch(PATH + "/basic", json={"birthDate": today.isoformat()}, headers=profile_headers(case))
    assert accepted.status_code == 200 and accepted.json()["birthDate"] == today.isoformat()
    assert case.client.get(PATH).json() == accepted.json()
    with Session(real_db) as db:
        saved = worker_snapshot(db, user_id)
    rejected = case.client.patch(PATH + "/basic", json={"birthDate": (today + timedelta(days=1)).isoformat()},
                                  headers=profile_headers(case))
    assert rejected.status_code == 422 and rejected.json()["code"] == "VALIDATION_ERROR"
    assert case.client.get(PATH).json() == accepted.json()
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == saved


def test_current_month_career_allowed_but_next_month_rejected(member, real_db):
    case, user_id = member
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    current = today.strftime("%Y-%m")
    next_month = f"{today.year + (today.month == 12):04}-{today.month % 12 + 1:02}"
    body = {"experienceLevel": "EXPERIENCED", "careers": [{**CAREER, "startMonth": current}]}
    accepted = case.client.put(PATH + "/careers", json=body, headers=profile_headers(case))
    assert accepted.status_code == 200 and accepted.json()["careers"] == body["careers"]
    assert case.client.get(PATH).json() == accepted.json()
    with Session(real_db) as db:
        saved = worker_snapshot(db, user_id)
    rejected = case.client.put(PATH + "/careers", json={**body, "careers": [{**CAREER, "startMonth": next_month}]},
                                headers=profile_headers(case))
    assert rejected.status_code == 422 and rejected.json()["code"] == "VALIDATION_ERROR"
    assert case.client.get(PATH).json() == accepted.json()
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == saved

"""Worker profile HTTP writes are checked through reads and separate DB transactions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AvailabilityDay, AvailabilityRule, User, WorkerCareer, WorkerProfile
from e2e.conftest import worker_snapshot

PATH = "/api/users/me/profile"
CAREER = {"industry": "CAFE", "duties": "음료 제조", "storeName": "월계 카페",
          "startMonth": "2024-03", "endMonth": None, "isCurrent": True}
OVERNIGHT = {"days": ["SUN"], "startTime": "22:00", "endTime": "02:00", "endsNextDay": True}
ADJACENT = {"days": ["MON"], "startTime": "02:00", "endTime": "03:00", "endsNextDay": False}


def profile_headers(case):
    headers = case.headers()
    headers.pop("Idempotency-Key")
    return headers


def test_basic_patch_persists_and_noop_keeps_timestamp(member, real_db):
    case, user_id = member
    body = {"name": "수정한 이름", "phoneNumber": "01087654321",
            "birthDate": "2000-02-29", "gender": "MALE"}
    before = case.client.get(PATH).json()
    first = case.client.patch(PATH + "/basic", json=body, headers=profile_headers(case))
    assert first.status_code == 200, first.text
    assert {k: first.json()[k] for k in body} == body
    assert {k: v for k, v in first.json().items() if k not in {*body, "updatedAt"}} == {
        k: v for k, v in before.items() if k not in {*body, "updatedAt"}}
    assert case.client.get(PATH).json() == first.json()
    with Session(real_db) as db:
        user = db.get(User, user_id)
        profile = db.get(WorkerProfile, user_id)
        assert (user.name, user.phone_number, profile.birth_date.isoformat(), profile.gender) == tuple(body.values())
        saved = worker_snapshot(db, user_id)
    second = case.client.patch(PATH + "/basic", json=body, headers=profile_headers(case))
    assert second.status_code == 200 and second.json() == first.json()
    assert case.client.get(PATH).json() == first.json()
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == saved


@pytest.mark.parametrize("body", [{}, {"name": None}, {"name": " "},
                                 {"birthDate": "2001-02-29"}, {"birthDate": "2999-01-01"},
                                 {"role": "OWNER"}, {"name": "부분 저장 금지", "phoneNumber": "bad"}])
def test_invalid_basic_patch_is_atomic(member, real_db, body):
    case, user_id = member
    before = case.client.get(PATH).json()
    response = case.client.patch(PATH + "/basic", json=body, headers=profile_headers(case))
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert case.client.get(PATH).json() == before
    with Session(real_db) as db:
        user = db.get(User, user_id)
        assert user.name == before["name"] and user.phone_number == before["phoneNumber"]
        assert user.updated_at.isoformat() == before["updatedAt"]


def test_careers_replace_and_clear_persist_without_old_rows(member, real_db):
    case, user_id = member
    prior_ids = []
    for careers in ([CAREER], [{**CAREER, "duties": "홀 서빙"},
                               {"industry": "OTHER", "duties": "종료 경력", "startMonth": "2022-01",
                                "endMonth": "2022-01", "isCurrent": False}]):
        body = {"experienceLevel": "EXPERIENCED", "careers": careers}
        prior = case.client.get(PATH).json()
        response = case.client.put(PATH + "/careers", json=body, headers=profile_headers(case))
        assert response.status_code == 200, response.text
        assert response.json()["careers"] == careers
        assert {k: v for k, v in response.json().items() if k not in {"careers", "experienceLevel", "updatedAt"}} == {
            k: v for k, v in prior.items() if k not in {"careers", "experienceLevel", "updatedAt"}}
        assert case.client.get(PATH).json()["careers"] == careers
        with Session(real_db) as db:
            rows = db.scalars(select(WorkerCareer).where(WorkerCareer.worker_id == user_id)
                              .order_by(WorkerCareer.sort_order)).all()
            assert len(rows) == len(careers)
            for row, expected in zip(rows, careers, strict=True):
                assert (row.industry, row.duties, row.store_name, row.start_month,
                        row.end_month, row.is_current) == (
                    expected["industry"], expected["duties"], expected.get("storeName"),
                    expected["startMonth"], expected["endMonth"], expected["isCurrent"])
            assert [row.sort_order for row in rows] == list(range(len(careers)))
            assert all(db.get(WorkerCareer, row_id) is None for row_id in prior_ids)
            prior_ids = [row.id for row in rows]
            before = worker_snapshot(db, user_id)
        replay = case.client.put(PATH + "/careers", json=body, headers=profile_headers(case))
        assert replay.status_code == 200 and replay.json() == response.json()
        with Session(real_db) as db:
            assert worker_snapshot(db, user_id) == before
    cleared = case.client.put(PATH + "/careers", json={"experienceLevel": "NEW", "careers": []},
                              headers=profile_headers(case))
    assert cleared.status_code == 200 and cleared.json()["careers"] == []
    assert cleared.json()["experienceLevel"] == "NEW" and case.client.get(PATH).json() == cleared.json()
    assert {k: v for k, v in cleared.json().items() if k not in {"careers", "experienceLevel", "updatedAt"}} == {
        k: v for k, v in prior.items() if k not in {"careers", "experienceLevel", "updatedAt"}}
    with Session(real_db) as db:
        assert db.scalars(select(WorkerCareer).where(WorkerCareer.worker_id == user_id)).all() == []
        assert db.get(WorkerProfile, user_id).experience_level == "NEW"


@pytest.mark.parametrize("body", [
    {"experienceLevel": "NEW", "careers": [CAREER]},
    {"experienceLevel": "EXPERIENCED", "careers": []},
    {"experienceLevel": "EXPERIENCED", "careers": [CAREER, {**CAREER, "startMonth": "2999-01"}]},
    {"experienceLevel": "EXPERIENCED", "careers": [{**CAREER, "storeName": " "}]},
])
def test_invalid_career_replacement_preserves_prior_rows(member, real_db, body):
    case, user_id = member
    valid = {"experienceLevel": "EXPERIENCED", "careers": [CAREER]}
    assert case.client.put(PATH + "/careers", json=valid, headers=profile_headers(case)).status_code == 200
    before = case.client.get(PATH).json()
    with Session(real_db) as db:
        old_ids = list(db.scalars(select(WorkerCareer.id).where(WorkerCareer.worker_id == user_id)))
    response = case.client.put(PATH + "/careers", json=body, headers=profile_headers(case))
    assert response.status_code == 422
    assert case.client.get(PATH).json() == before
    with Session(real_db) as db:
        assert list(db.scalars(select(WorkerCareer.id).where(WorkerCareer.worker_id == user_id))) == old_ids


def test_overnight_week_boundary_and_full_replacement(member, real_db):
    case, user_id = member
    with Session(real_db) as db:
        old_id = db.scalar(select(AvailabilityRule.id).where(AvailabilityRule.worker_id == user_id))
    supplied = [{**OVERNIGHT, "days": ["SUN", "FRI"]}, ADJACENT]
    expected = [{**OVERNIGHT, "days": ["FRI", "SUN"]}, ADJACENT]
    prior = case.client.get(PATH).json()
    response = case.client.put(PATH + "/availabilities", json={"availabilities": supplied},
                               headers=profile_headers(case))
    assert response.status_code == 200, response.text
    assert response.json()["availabilities"] == expected
    assert {k: v for k, v in response.json().items() if k not in {"availabilities", "updatedAt"}} == {
        k: v for k, v in prior.items() if k not in {"availabilities", "updatedAt"}}
    assert case.client.get(PATH).json()["availabilities"] == expected
    with Session(real_db) as db:
        assert db.get(AvailabilityRule, old_id) is None
        assert db.scalars(select(AvailabilityDay).where(AvailabilityDay.rule_id == old_id)).all() == []
        rows = db.scalars(select(AvailabilityRule).where(AvailabilityRule.worker_id == user_id)
                          .order_by(AvailabilityRule.sort_order)).all()
        assert len(rows) == 2 and rows[0].ends_next_day and not rows[1].ends_next_day
        assert rows[0].start_time.hour == 22 and rows[0].end_time.hour == 2
        assert set(db.scalars(select(AvailabilityDay.weekday).where(
            AvailabilityDay.rule_id == rows[0].id))) == {"FRI", "SUN"}
        assert list(db.scalars(select(AvailabilityDay.weekday).where(
            AvailabilityDay.rule_id == rows[1].id))) == ["MON"]
        before = worker_snapshot(db, user_id)
    replay = case.client.put(PATH + "/availabilities", json={"availabilities": expected},
                             headers=profile_headers(case))
    assert replay.status_code == 200 and replay.json() == response.json()
    with Session(real_db) as db:
        assert worker_snapshot(db, user_id) == before


@pytest.mark.parametrize("groups", [[], [OVERNIGHT, {**ADJACENT, "startTime": "01:30"}],
                                   [{**OVERNIGHT, "days": ["SUN", "SUN"]}],
                                   [{**ADJACENT, "startTime": "02:15"}],
                                   [{**ADJACENT, "endsNextDay": True}],
                                   [OVERNIGHT, {**ADJACENT, "endTime": "02:00"}]])
def test_invalid_availability_is_atomic(member, real_db, groups):
    case, user_id = member
    before = case.client.get(PATH).json()
    with Session(real_db) as db:
        old_id = db.scalar(select(AvailabilityRule.id).where(AvailabilityRule.worker_id == user_id))
    response = case.client.put(PATH + "/availabilities", json={"availabilities": groups},
                               headers=profile_headers(case))
    assert response.status_code == 422
    assert case.client.get(PATH).json() == before
    with Session(real_db) as db:
        assert db.get(AvailabilityRule, old_id) is not None
        assert db.scalar(select(AvailabilityDay.weekday).where(AvailabilityDay.rule_id == old_id)) == "MON"


@pytest.mark.parametrize("guard", ["csrf", "origin", "owner"])
def test_profile_write_guards_preserve_db(member, real_db, guard):
    case, user_id = member
    headers = profile_headers(case)
    if guard == "csrf":
        headers.pop("X-CSRF-Token")
    if guard == "origin":
        headers["Origin"] = "http://evil.test"
    if guard == "owner":
        with Session(real_db) as db:
            db.get(User, user_id).role = "OWNER"
            db.commit()
    response = case.client.patch(PATH + "/basic", json={"name": "거부할 변경"}, headers=headers)
    assert response.status_code == 403
    with Session(real_db) as db:
        assert db.get(User, user_id).name == case.worker["name"]


def test_concurrent_disjoint_patches_do_not_lose_updates(member, real_db, base_url):
    case, user_id = member
    headers = profile_headers(case)
    cookies = dict(case.client.cookies)
    barrier = Barrier(2)

    def patch(body):
        with httpx.Client(base_url=base_url, cookies=cookies, timeout=15, trust_env=False) as client:
            barrier.wait(timeout=10)
            return client.patch(PATH + "/basic", json=body, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(patch, [{"name": "동시 저장"}, {"phoneNumber": "01099998888"}]))
    assert [r.status_code for r in responses] == [200, 200]
    after = case.client.get(PATH).json()
    assert (after["name"], after["phoneNumber"]) == ("동시 저장", "01099998888")
    with Session(real_db) as db:
        user = db.get(User, user_id)
        assert (user.name, user.phone_number) == ("동시 저장", "01099998888")

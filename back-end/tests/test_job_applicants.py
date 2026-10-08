"""Owner applicant review (#113), on SQLite and MySQL."""
from datetime import date, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.models import ApplicationCareer, JobApplication, JobPosting, Store, User
from tests.factories import make_application, make_job, make_request
from tests.jobs_support import NOW, as_user, key, pin_clock, seed_shop, seed_user

TODAY = date(2026, 10, 5)


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


def seed_job(db_engine, store_id, **overrides) -> str:
    with Session(db_engine) as db:
        job = make_job(db, db.get(Store, store_id), **overrides)
        db.commit()
        return job.id


def seed_application(db_engine, job_id, worker_id=None, **overrides) -> str:
    with Session(db_engine) as db:
        worker = db.get(User, worker_id or seed_user(db_engine))
        application = make_application(db, db.get(JobPosting, job_id), worker, **overrides)
        db.commit()
        return application.id


def applicants(store_id, job_id) -> str:
    return f"/api/stores/{store_id}/job-postings/{job_id}/applications"


@pytest.fixture
def world(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    as_user(api, shop.owner_id)
    return shop, job_id


def test_detail_returns_submission_snapshot_only(api, db_engine, world):
    shop, job_id = world
    worker = seed_user(db_engine)
    application = seed_application(
        db_engine, job_id, worker, applicant_name="김지단", age_at_submission=23,
        experience_level="EXPERIENCED", introduction="카페 경력 2년입니다.",
    )
    with Session(db_engine) as db:
        db.add_all([
            ApplicationCareer(application_id=application, sort_order=1, industry="CAFE", duties="바리스타",
                              store_name="월계 카페", start_month="2024-01", end_month=None, is_current=True),
            ApplicationCareer(application_id=application, sort_order=0, industry="RESTAURANT", duties="서빙",
                              store_name=None, start_month="2022-03", end_month="2023-02", is_current=False),
        ])
        user = db.get(User, worker)
        user.name, user.phone_number = "바뀐 이름", "01099998888"
        db.commit()
    response = api.get(f"{applicants(shop.store_id, job_id)}/{application}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "id": application, "jobId": job_id, "status": "APPLIED", "introduction": "카페 경력 2년입니다.",
        "applicant": {
            "workerId": worker, "name": "김지단", "ageAtSubmission": 23, "experienceLevel": "EXPERIENCED",
            "careers": [
                {"industry": "RESTAURANT", "duties": "서빙", "startMonth": "2022-03", "endMonth": "2023-02",
                 "isCurrent": False},
                {"industry": "CAFE", "duties": "바리스타", "storeName": "월계 카페", "startMonth": "2024-01",
                 "endMonth": None, "isCurrent": True},
            ],
        },
        "submittedAt": body["submittedAt"], "revision": 1,
    }
    assert "01099998888" not in response.text and "example.com" not in response.text


def test_list_filters_and_orders(api, db_engine, world, clock):
    shop, job_id = world
    first = seed_application(db_engine, job_id, applied_at=NOW - timedelta(hours=3))
    tied = sorted(seed_application(db_engine, job_id, applied_at=NOW - timedelta(hours=2)) for _ in range(2))
    requested = seed_application(db_engine, job_id, status="REQUESTED", applied_at=NOW - timedelta(hours=1))
    withdrawn = seed_application(db_engine, job_id, status="WITHDRAWN", withdrawn_at=NOW, applied_at=NOW)
    seed_application(db_engine, seed_job(db_engine, shop.store_id))  # another posting
    with Session(db_engine) as db:
        make_request(db, db.get(JobApplication, requested), shop.owner_id, requested_at=NOW - timedelta(hours=1),
                     expires_at=NOW)  # due: reads as APPLIED
        db.commit()
    body = api.get(applicants(shop.store_id, job_id)).json()
    assert [i["id"] for i in body["items"]] == [first, *tied, requested]
    assert body["items"][-1]["status"] == "APPLIED" and body["totalItems"] == 4
    everything = api.get(applicants(shop.store_id, job_id), params={"filter": "ALL"}).json()
    assert [i["id"] for i in everything["items"]] == [first, *tied, requested, withdrawn]
    page = api.get(applicants(shop.store_id, job_id), params={"filter": "ALL", "page": 2, "size": 2}).json()
    assert [i["id"] for i in page["items"]] == [withdrawn] and page["totalItems"] == 5
    assert everything["asOf"] == "2026-10-05T03:00:00+00:00"


def test_confirmed_applicant_leaves_active_list_when_shift_ends(api, db_engine, world, clock):
    shop, _ = world
    job_id = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW, work_date=TODAY,
                      start_time=time(9), end_time=time(13))
    confirmed = seed_application(db_engine, job_id, status="CONFIRMED")
    not_selected = seed_application(db_engine, job_id, status="NOT_SELECTED")
    assert [i["id"] for i in api.get(applicants(shop.store_id, job_id)).json()["items"]] == [confirmed]
    clock.at = NOW + timedelta(hours=1)  # 13:00 KST: the shift ended
    assert api.get(applicants(shop.store_id, job_id)).json()["items"] == []
    items = api.get(applicants(shop.store_id, job_id), params={"filter": "ALL"}).json()["items"]
    assert [(i["id"], i["status"]) for i in items] == [(confirmed, "COMPLETED"), (not_selected, "NOT_SELECTED")]


def test_empty_posting(api, db_engine, world):
    shop, job_id = world
    body = api.get(applicants(shop.store_id, job_id)).json()
    assert body["items"] == [] and body["totalItems"] == 0


def test_ownership_chain_is_checked(api, db_engine, world):
    shop, job_id = world
    other = seed_shop(db_engine)
    foreign_job = seed_job(db_engine, other.store_id)
    foreign_application = seed_application(db_engine, foreign_job)
    mine = seed_application(db_engine, job_id)
    sibling_job = seed_job(db_engine, shop.store_id)
    assert api.get(applicants(other.store_id, foreign_job)).status_code == 404
    assert api.get(applicants(shop.store_id, foreign_job)).status_code == 404
    assert api.get(f"{applicants(shop.store_id, job_id)}/{foreign_application}").status_code == 404
    assert api.get(f"{applicants(shop.store_id, sibling_job)}/{mine}").status_code == 404
    assert api.get(f"{applicants(shop.store_id, job_id)}/{key()}").json()["code"] == "RESOURCE_NOT_FOUND"
    assert api.get(f"{applicants(shop.store_id, job_id)}/nope").status_code == 422
    assert api.get(f"{applicants(shop.store_id, job_id)}/{mine}").status_code == 200


def test_auth_and_roles(api, db_engine, world):
    shop, job_id = world
    pending = seed_shop(db_engine, approved=False)
    pending_job = seed_job(db_engine, pending.store_id)
    as_user(api, pending.owner_id)
    assert api.get(applicants(pending.store_id, pending_job)).json()["code"] == "STORE_APPROVAL_REQUIRED"
    as_user(api, seed_user(db_engine))
    assert api.get(applicants(shop.store_id, job_id)).json()["code"] == "FORBIDDEN"
    api.cookies.clear()
    assert api.get(applicants(shop.store_id, job_id)).status_code == 401


@pytest.mark.parametrize("query", ["filter=ENDED", "filter=active", "filter=ALL&filter=ACTIVE", "size=101"])
def test_bad_query(api, db_engine, world, query):
    shop, job_id = world
    assert api.get(f"{applicants(shop.store_id, job_id)}?{query}").status_code == 422

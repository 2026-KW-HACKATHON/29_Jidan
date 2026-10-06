"""Owner job postings (#110): create, list, detail and closure, on SQLite and MySQL."""
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import JobApplication, JobPosting, ShiftAssignment, Store, WorkRequest
from tests.api_contract import ORIGIN
from tests.factories import make_application, make_job, make_request, make_shift
from tests.jobs_support import (
    NOW,
    as_user,
    client_factory,
    key,
    pin_clock,
    run_concurrently,
    seed_shop,
    seed_user,
)


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


BODY = {
    "title": "주말 오픈 대타",
    "description": "음료 제조와 고객 응대",
    "workPart": "WEEKEND_OPEN",
    "workDate": "2026-10-10",
    "startTime": "09:00",
    "endTime": "14:00",
    "endsNextDay": False,
    "minimumExperience": "ANY",
    "experienceNotes": "",
    "hourlyPay": 12000,
    "paymentTiming": "WORK_DAY",
    "paymentNotes": "",
}


def postings(store_id: str) -> str:
    return f"/api/stores/{store_id}/job-postings"


def create(api, me, store_id, idem=None, **changes):
    return api.post(postings(store_id), json={**BODY, **changes}, headers=me.headers(idem or key()))


def seed_job(db_engine, store_id, **overrides) -> str:
    with Session(db_engine) as db:
        job = make_job(db, db.get(Store, store_id), **overrides)
        db.commit()
        return job.id


# --- create ---------------------------------------------------------------------------------


def test_create_returns_recruiting_posting_with_seoul_times(api, db_engine, clock):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    response = create(api, me, shop.store_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "RECRUITING" and body["closedAt"] is None and body["revision"] == 1
    assert body["startAt"] == "2026-10-10T00:00:00+00:00" and body["endAt"] == "2026-10-10T05:00:00+00:00"
    assert body["estimatedPay"] == 60000
    assert body["applicantCount"] == 0 and body["recruitmentCount"] == 1
    assert body["createdAt"] == "2026-10-05T03:00:00+00:00"
    assert body["store"] == {
        "id": shop.store_id, "name": "월계 카페", "industry": "CAFE",
        "address": "서울 노원구 월계1동", "neighborhood": "월계1동",
    }
    assert {k: body[k] for k in BODY} == BODY
    with Session(db_engine) as db:
        job = db.get(JobPosting, body["id"])
        assert job.created_by_owner_id == shop.owner_id and job.store_id == shop.store_id
        assert job.min_experience_months == 0 and job.headcount == 1


def test_create_maps_experience_and_trims_text(api, db_engine, clock):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    response = create(api, me, shop.store_id, title="  마감 대타 ", minimumExperience="YEAR_1",
                      experienceNotes=" 포스 경험 ", paymentNotes="당일 지급")
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "마감 대타" and body["experienceNotes"] == "포스 경험"
    with Session(db_engine) as db:
        assert db.get(JobPosting, body["id"]).min_experience_months == 12
    detail = api.get(f"{postings(shop.store_id)}/{body['id']}").json()
    assert detail["minimumExperience"] == "YEAR_1" and detail["paymentNotes"] == "당일 지급"


@pytest.mark.parametrize(("start", "end", "minutes"), [("22:00", "02:00", 240), ("23:30", "00:00", 30)])
def test_overnight_shift_ends_next_seoul_day(api, db_engine, clock, start, end, minutes):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    response = create(api, me, shop.store_id, startTime=start, endTime=end, endsNextDay=True)
    assert response.status_code == 201, response.text
    body = response.json()
    start_at = datetime.fromisoformat(body["startAt"])
    end_at = datetime.fromisoformat(body["endAt"])
    assert end_at - start_at == timedelta(minutes=minutes)
    assert end_at.astimezone(UTC).date() == date(2026, 10, 10)  # 02:00/00:00 KST on the 11th
    assert body["estimatedPay"] == 12000 * minutes // 60


def test_estimated_pay_is_floored(api, db_engine, clock):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    body = create(api, me, shop.store_id, startTime="09:00", endTime="09:50", hourlyPay=10001).json()
    assert body["estimatedPay"] == 10001 * 50 // 60 == 8334


def test_max_shift_is_23h59(api, db_engine, clock):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    body = create(api, me, shop.store_id, startTime="09:00", endTime="08:59", endsNextDay=True).json()
    assert body["estimatedPay"] == 12000 * (24 * 60 - 1) // 60


@pytest.mark.parametrize(("changes", "reason"), [
    ({"startTime": "09:00", "endTime": "09:00"}, "zero minutes"),
    ({"startTime": "10:00", "endTime": "09:00"}, "end before start without next day"),
    ({"startTime": "09:00", "endTime": "10:00", "endsNextDay": True}, "over 24 hours"),
    ({"startTime": "09:00", "endTime": "09:00", "endsNextDay": True}, "exactly 24 hours"),
    ({"workDate": "2026-10-04"}, "yesterday"),
    ({"workDate": "2026-10-05", "startTime": "11:59", "endTime": "13:00"}, "started a minute ago"),
    ({"workDate": "2026-10-05", "startTime": "12:00", "endTime": "13:00"}, "starts exactly now"),
    ({"workDate": "0001-01-01", "startTime": "01:00", "endTime": "02:00"}, "before UTC epoch range"),
    ({"workDate": "9999-12-31", "startTime": "23:00", "endTime": "01:00", "endsNextDay": True}, "after calendar end"),
])
def test_invalid_work_interval(api, db_engine, clock, changes, reason):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    response = create(api, me, shop.store_id, **changes)
    assert response.status_code == 422, reason
    assert response.json()["code"] == "INVALID_WORK_INTERVAL", reason
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(JobPosting)) == 0


def test_shift_starting_one_minute_from_now_is_allowed(api, db_engine, clock):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    clock.at = NOW - timedelta(minutes=1)
    response = create(api, me, shop.store_id, workDate="2026-10-05", startTime="12:00", endTime="13:00")
    assert response.status_code == 201


@pytest.mark.parametrize("changes", [
    {"title": "   "}, {"title": "가" * 101}, {"description": ""}, {"description": "가" * 3001},
    {"experienceNotes": "가" * 1001}, {"paymentNotes": "가" * 1001},
    {"hourlyPay": 0}, {"hourlyPay": 1_000_001}, {"hourlyPay": "12000"}, {"hourlyPay": 1.5},
    {"hourlyPay": True}, {"endsNextDay": "false"}, {"endsNextDay": 0},
    {"startTime": "24:00"}, {"startTime": "9:00"}, {"endTime": "14:00:00"},
    {"workDate": "2026-02-30"}, {"workDate": "2026/10/10"}, {"workDate": 20261010},
    {"workPart": "weekend_open"}, {"minimumExperience": "MONTHS_12"}, {"paymentTiming": "LATER"},
    {"recruitmentCount": 2}, {"storeId": "51c1c743-d377-4e7a-8449-94377eecfce0"}, {"status": "CLOSED"},
    {"experienceNotes": None},
])
def test_create_rejects_invalid_fields(api, db_engine, clock, changes):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    response = create(api, me, shop.store_id, **changes)
    assert response.status_code == 422, (changes, response.text)
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("field", list(BODY))
def test_create_requires_every_field(api, db_engine, clock, field):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    body = {k: v for k, v in BODY.items() if k != field}
    response = api.post(postings(shop.store_id), json=body, headers=me.headers(key()))
    assert response.status_code == 422
    assert response.json()["fieldErrors"][0]["field"] == field


def test_create_access_rules(api, db_engine, clock):
    shop = seed_shop(db_engine)
    other = seed_shop(db_engine)
    pending = seed_shop(db_engine, approved=False)
    worker = seed_user(db_engine)

    assert api.post(postings(shop.store_id), json=BODY, headers={"Origin": ORIGIN}).status_code == 401
    me = as_user(api, worker)
    assert create(api, me, shop.store_id).json()["code"] == "FORBIDDEN"
    me = as_user(api, shop.owner_id)
    assert create(api, me, other.store_id).status_code == 404
    missing = create(api, me, "00000000-0000-4000-8000-000000000000")
    assert missing.status_code == 404 and missing.json()["code"] == "RESOURCE_NOT_FOUND"
    assert create(api, me, "not-a-uuid").status_code == 422
    me = as_user(api, pending.owner_id)
    denied = create(api, me, pending.store_id)
    assert denied.status_code == 403 and denied.json()["code"] == "STORE_APPROVAL_REQUIRED"
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(JobPosting)) == 0


def test_create_requires_csrf_origin_and_key(api, db_engine, clock):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    url = postings(shop.store_id)
    no_token = api.post(url, json=BODY, headers={"Origin": ORIGIN, "Idempotency-Key": key()})
    assert no_token.status_code == 403 and no_token.json()["code"] == "CSRF_INVALID"
    bad_origin = {**me.headers(key()), "Origin": "https://evil.test"}
    assert api.post(url, json=BODY, headers=bad_origin).json()["code"] == "CSRF_INVALID"
    no_key = api.post(url, json=BODY, headers=me.headers())
    assert no_key.status_code == 422
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(JobPosting)) == 0


def test_create_is_idempotent(api, db_engine, clock):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    idem = key()
    first = create(api, me, shop.store_id, idem)
    again = create(api, me, shop.store_id, idem)
    assert again.status_code == 201 and again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    reused = create(api, me, shop.store_id, idem, title="다른 공고")
    assert reused.status_code == 409 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(JobPosting)) == 1


# --- list and detail -------------------------------------------------------------------------


def test_list_tabs_sort_and_paginate(api, db_engine, clock):
    shop = seed_shop(db_engine)
    other = seed_shop(db_engine)
    same_time = NOW - timedelta(hours=1)
    ids = [seed_job(db_engine, shop.store_id, created_at=same_time) for _ in range(3)]
    newest = seed_job(db_engine, shop.store_id, created_at=NOW)
    oldest = seed_job(db_engine, shop.store_id, created_at=NOW - timedelta(days=1))
    closed = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW)
    seed_job(db_engine, other.store_id)
    as_user(api, shop.owner_id)

    body = api.get(postings(shop.store_id)).json()
    expected = [newest, *sorted(ids, reverse=True), oldest]  # createdAt DESC, then id DESC
    assert [item["id"] for item in body["items"]] == expected
    assert body["totalItems"] == 5 and body["page"] == 0 and body["size"] == 20
    assert body["asOf"] == "2026-10-05T03:00:00+00:00"

    second = api.get(postings(shop.store_id), params={"page": 1, "size": 2}).json()
    assert [item["id"] for item in second["items"]] == expected[2:4] and second["totalItems"] == 5
    beyond = api.get(postings(shop.store_id), params={"page": 3, "size": 2}).json()
    assert beyond["items"] == [] and beyond["totalItems"] == 5

    tab = api.get(postings(shop.store_id), params={"status": "CLOSED"}).json()
    assert [item["id"] for item in tab["items"]] == [closed]
    assert tab["items"][0]["closedAt"] == "2026-10-05T03:00:00+00:00"


def test_list_empty_tab(api, db_engine, clock):
    shop = seed_shop(db_engine)
    as_user(api, shop.owner_id)
    body = api.get(postings(shop.store_id), params={"status": "CLOSED"}).json()
    assert body["items"] == [] and body["totalItems"] == 0


@pytest.mark.parametrize("query", [
    "status=OPEN", "status=recruiting", "status=RECRUITING&status=CLOSED", "status=",
    "size=0", "size=101", "page=-1", "page=x",
])
def test_list_rejects_bad_query(api, db_engine, clock, query):
    shop = seed_shop(db_engine)
    as_user(api, shop.owner_id)
    response = api.get(f"{postings(shop.store_id)}?{query}")
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


def test_applicant_count_excludes_ended_applications(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    with Session(db_engine) as db:
        job = db.get(JobPosting, job_id)
        for status in ("APPLIED", "REQUESTED", "NOT_SELECTED", "COMPLETED"):
            make_application(db, job, status=status)
        make_application(db, job, status="WITHDRAWN", withdrawn_at=NOW)
        db.commit()
    as_user(api, shop.owner_id)
    assert api.get(f"{postings(shop.store_id)}/{job_id}").json()["applicantCount"] == 3
    assert api.get(postings(shop.store_id)).json()["items"][0]["applicantCount"] == 3


def test_read_access_rules(api, db_engine, clock):
    shop = seed_shop(db_engine)
    other = seed_shop(db_engine)
    pending = seed_shop(db_engine, approved=False)
    job_id = seed_job(db_engine, shop.store_id)
    foreign_job = seed_job(db_engine, other.store_id)
    worker = seed_user(db_engine)

    assert api.get(postings(shop.store_id)).status_code == 401
    assert api.get(f"{postings(shop.store_id)}/{job_id}").status_code == 401
    as_user(api, worker)
    assert api.get(postings(shop.store_id)).json()["code"] == "FORBIDDEN"
    assert api.get(f"{postings(shop.store_id)}/{job_id}").status_code == 403
    as_user(api, shop.owner_id)
    assert api.get(f"{postings(shop.store_id)}/{job_id}").status_code == 200
    # Another store's posting under my store, and another owner's store: both hidden.
    assert api.get(f"{postings(shop.store_id)}/{foreign_job}").status_code == 404
    assert api.get(f"{postings(other.store_id)}/{foreign_job}").status_code == 404
    assert api.get(postings(other.store_id)).status_code == 404
    assert api.get(f"{postings(shop.store_id)}/{key()}").status_code == 404
    assert api.get(f"{postings(shop.store_id)}/nope").status_code == 422
    as_user(api, pending.owner_id)
    assert api.get(postings(pending.store_id)).json()["code"] == "STORE_APPROVAL_REQUIRED"


def test_job_id_case_is_normalized(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    as_user(api, shop.owner_id)
    response = api.get(f"{postings(shop.store_id.upper())}/{job_id.upper()}")
    assert response.status_code == 200 and response.json()["id"] == job_id


# --- closure -------------------------------------------------------------------------------


def closure(api, me, store_id, job_id, revision, idem=None):
    return api.post(f"{postings(store_id)}/{job_id}/closure", json={"expectedRevision": revision},
                    headers=me.headers(idem or key()))


def test_closure_closes_and_ends_current_applications(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    with Session(db_engine) as db:
        job = db.get(JobPosting, job_id)
        applied = make_application(db, job).id
        withdrawn = make_application(db, job, status="WITHDRAWN", withdrawn_at=NOW).id
        db.commit()
    me = as_user(api, shop.owner_id)
    response = closure(api, me, shop.store_id, job_id, 1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "CLOSED" and body["closedAt"] == "2026-10-05T03:00:00+00:00"
    assert body["revision"] == 2 and body["applicantCount"] == 0
    with Session(db_engine) as db:
        assert db.get(JobApplication, applied).status == "NOT_SELECTED"
        assert db.get(JobApplication, applied).revision == 2
        untouched = db.get(JobApplication, withdrawn)
        assert untouched.status == "WITHDRAWN" and untouched.revision == 1


def test_closure_of_closed_posting_keeps_history(api, db_engine, clock):
    shop = seed_shop(db_engine)
    closed_at = NOW - timedelta(hours=2)
    job_id = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=closed_at, revision=3)
    with Session(db_engine) as db:
        job = db.get(JobPosting, job_id)
        application = make_application(db, job, status="CONFIRMED")
        request = make_request(db, application, shop.owner_id, status="ACCEPTED",
                               responded_at=closed_at, ended_at=closed_at)
        make_shift(db, job, request, application.worker_id, confirmed_at=closed_at)
        db.commit()
    me = as_user(api, shop.owner_id)
    for moment in (NOW, datetime(2026, 10, 10, 10, 0, tzinfo=UTC), datetime(2026, 10, 11, tzinfo=UTC)):
        clock.at = moment  # before, during and after the shift
        response = closure(api, me, shop.store_id, job_id, 3)
        assert response.status_code == 200 and "Idempotent-Replayed" not in response.headers
        assert response.json()["closedAt"] == "2026-10-05T01:00:00+00:00"
        assert response.json()["revision"] == 3
    with Session(db_engine) as db:
        job = db.get(JobPosting, job_id)
        assert (job.status, job.closed_at, job.revision) == ("CLOSED", closed_at, 3)
        assert db.scalar(select(WorkRequest.status)) == "ACCEPTED"
        assert db.scalar(select(JobApplication.status)) == "CONFIRMED"
        assert db.scalar(select(ShiftAssignment.withdrawn_at)) is None


def test_closure_revision_conflict(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id, revision=2)
    closed_id = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW, revision=2)
    me = as_user(api, shop.owner_id)
    for job, revision in ((job_id, 1), (job_id, 3), (closed_id, 1)):
        response = closure(api, me, shop.store_id, job, revision)
        assert response.status_code == 409 and response.json()["code"] == "JOB_REVISION_CONFLICT"
    with Session(db_engine) as db:
        assert db.get(JobPosting, job_id).status == "RECRUITING"


def test_closure_requires_withdrawing_live_request(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    with Session(db_engine) as db:
        application = make_application(db, db.get(JobPosting, job_id), status="REQUESTED")
        make_request(db, application, shop.owner_id, requested_at=NOW - timedelta(minutes=30),
                     expires_at=NOW + timedelta(microseconds=1))
        db.commit()
    me = as_user(api, shop.owner_id)
    response = closure(api, me, shop.store_id, job_id, 1)
    assert response.status_code == 409
    assert response.json()["code"] == "WORK_REQUEST_WITHDRAWAL_REQUIRED"
    with Session(db_engine) as db:
        assert db.get(JobPosting, job_id).status == "RECRUITING"
        assert db.scalar(select(WorkRequest.status)) == "PENDING"
        assert db.scalar(select(JobApplication.status)) == "REQUESTED"


def test_closure_treats_request_at_its_deadline_as_expired(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    deadline = NOW - timedelta(minutes=10)  # the sweep is late: it must not extend the deadline
    with Session(db_engine) as db:
        application = make_application(db, db.get(JobPosting, job_id), status="REQUESTED")
        request_id = make_request(db, application, shop.owner_id,
                                  requested_at=deadline - timedelta(hours=1), expires_at=deadline).id
        db.commit()
    me = as_user(api, shop.owner_id)
    clock.at = deadline + timedelta(minutes=10)
    assert closure(api, me, shop.store_id, job_id, 1).status_code == 200
    with Session(db_engine) as db:
        request = db.get(WorkRequest, request_id)
        assert (request.status, request.ended_at, request.revision) == ("EXPIRED", deadline, 1)
        assert db.scalar(select(JobApplication.status)) == "NOT_SELECTED"


def test_closure_at_exact_deadline(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    with Session(db_engine) as db:
        application = make_application(db, db.get(JobPosting, job_id), status="REQUESTED")
        make_request(db, application, shop.owner_id, requested_at=NOW - timedelta(hours=1), expires_at=NOW)
        db.commit()
    me = as_user(api, shop.owner_id)
    assert closure(api, me, shop.store_id, job_id, 1).status_code == 200


def test_closure_idempotency(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    me = as_user(api, shop.owner_id)
    idem = key()
    first = closure(api, me, shop.store_id, job_id, 1, idem)
    clock.at = NOW + timedelta(hours=1)
    replay = closure(api, me, shop.store_id, job_id, 1, idem)
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()
    reused = closure(api, me, shop.store_id, job_id, 2, idem)
    assert reused.status_code == 409 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    stale = closure(api, me, shop.store_id, job_id, 1)
    assert stale.json()["code"] == "JOB_REVISION_CONFLICT"


def test_closure_replay_rechecks_store_ownership(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    me = as_user(api, shop.owner_id)
    idem = key()
    assert closure(api, me, shop.store_id, job_id, 1, idem).status_code == 200
    successor = seed_user(db_engine, "OWNER")
    with Session(db_engine) as db:
        db.get(Store, shop.store_id).owner_id = successor
        db.commit()
    assert closure(api, me, shop.store_id, job_id, 1, idem).status_code == 404


@pytest.mark.parametrize("body", [{}, {"expectedRevision": 0}, {"expectedRevision": "1"},
                                  {"expectedRevision": 1, "extra": True}, {"expectedRevision": None}])
def test_closure_rejects_invalid_body(api, db_engine, clock, body):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    me = as_user(api, shop.owner_id)
    response = api.post(f"{postings(shop.store_id)}/{job_id}/closure", json=body, headers=me.headers(key()))
    assert response.status_code == 422


def test_closure_access_rules(api, db_engine, clock):
    shop = seed_shop(db_engine)
    other = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    foreign = seed_job(db_engine, other.store_id)
    worker = seed_user(db_engine)
    url = f"{postings(shop.store_id)}/{job_id}/closure"

    assert api.post(url, json={"expectedRevision": 1}, headers={"Origin": ORIGIN}).status_code == 401
    me = as_user(api, worker)
    assert closure(api, me, shop.store_id, job_id, 1).json()["code"] == "FORBIDDEN"
    me = as_user(api, shop.owner_id)
    no_csrf = api.post(url, json={"expectedRevision": 1}, headers={"Origin": ORIGIN, "Idempotency-Key": key()})
    assert no_csrf.json()["code"] == "CSRF_INVALID"
    assert closure(api, me, shop.store_id, foreign, 1).status_code == 404
    assert closure(api, me, other.store_id, foreign, 1).status_code == 404
    with Session(db_engine) as db:
        assert {job.status for job in db.scalars(select(JobPosting))} == {"RECRUITING"}


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_closures_close_once(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    me = as_user(api, shop.owner_id)
    url = f"{postings(shop.store_id)}/{job_id}/closure"
    calls = [
        (lambda client: client.post(url, json={"expectedRevision": 1}, headers=me.headers(key())))
        for _ in range(6)
    ]
    responses = run_concurrently(client_factory(me.token), calls)
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] + [409] * 5, [r.text for r in responses]
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"JOB_REVISION_CONFLICT"}
    with Session(db_engine) as db:
        assert db.get(JobPosting, job_id).revision == 2


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_creates_with_one_key_store_one_posting(api, db_engine, clock):
    shop = seed_shop(db_engine)
    me = as_user(api, shop.owner_id)
    idem = key()
    calls = [
        (lambda client: client.post(postings(shop.store_id), json=BODY, headers=me.headers(idem)))
        for _ in range(4)
    ]
    responses = run_concurrently(client_factory(me.token), calls)
    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(JobPosting)) == 1


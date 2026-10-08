"""Monthly substitute-shift calendars for workers and owners (#117), on SQLite and MySQL."""
from datetime import date, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.models import JobApplication, JobPosting, ShiftAssignment, Store, User, WorkRequest
from tests.factories import make_job, make_store_with_request, make_user, make_worker
from tests.jobs_support import NOW, as_user, key, pin_clock


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


def confirm(db, store, worker, *, withdrawn=False, name="확정자", **job):
    """A confirmed shift the way acceptance records it (or later withdrawn)."""
    job = make_job(db, store, status="CLOSED", closed_at=NOW, **job)
    application = JobApplication(job_id=job.id, worker_id=worker.id, introduction="지원", applicant_name=name,
                                 age_at_submission=25, experience_level="NEW",
                                 status="APPLIED" if withdrawn else "CONFIRMED", revision=2)
    db.add(application)
    db.flush()
    request = WorkRequest(application_id=application.id, requested_by_owner_id=store.owner_id,
                          status="CONFIRMATION_WITHDRAWN" if withdrawn else "ACCEPTED", requested_at=NOW,
                          expires_at=NOW + timedelta(hours=1), responded_at=NOW, ended_at=NOW,
                          revision=3 if withdrawn else 2)
    db.add(request)
    db.flush()
    shift = ShiftAssignment(job_id=job.id, work_request_id=request.id, worker_id=worker.id, confirmed_at=NOW,
                            withdrawn_at=NOW if withdrawn else None)
    db.add(shift)
    db.flush()
    return shift


@pytest.fixture
def world(db_engine, clock):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store, _ = make_store_with_request(db, owner, approved_at=NOW)
        second, _ = make_store_with_request(db, owner, approved_at=NOW, name="월계 식당", industry="RESTAURANT")
        pending, _ = make_store_with_request(db, owner)
        foreign, _ = make_store_with_request(db, approved_at=NOW)
        me, them = make_worker(db), make_worker(db)
        shifts = {
            # Seoul month 2026-10 = [2026-09-30T15:00Z, 2026-10-31T15:00Z)
            "carried_in": confirm(db, store, me, work_date=date(2026, 9, 30), start_time=time(22), end_time=time(2),
                                  ends_next_day=True),
            "touching_start": confirm(db, store, me, work_date=date(2026, 9, 30), start_time=time(20),
                                      end_time=time(0), ends_next_day=True),
            "completed": confirm(db, store, me, work_date=date(2026, 10, 1), start_time=time(9), end_time=time(13)),
            "future": confirm(db, second, me, work_date=date(2026, 10, 20)),
            "withdrawn": confirm(db, store, me, withdrawn=True, work_date=date(2026, 10, 21)),
            "carried_out": confirm(db, store, me, work_date=date(2026, 10, 31), start_time=time(23), end_time=time(1),
                                   ends_next_day=True),
            "touching_end": confirm(db, store, me, work_date=date(2026, 11, 1), start_time=time(0), end_time=time(4)),
            "theirs": confirm(db, store, them, name="다른 사람", work_date=date(2026, 10, 10)),
            "foreign": confirm(db, foreign, them, name="다른 사람", work_date=date(2026, 10, 11)),
            "pending_store": confirm(db, pending, them, name="다른 사람", work_date=date(2026, 10, 12)),
        }
        db.commit()
        return {
            "owner": owner.id, "store": store.id, "second": second.id, "pending": pending.id,
            "foreign": foreign.id, "me": me.id, "them": them.id, "shifts": {k: v.id for k, v in shifts.items()},
        }


def ids(body, world) -> list[str]:
    names = {v: k for k, v in world["shifts"].items()}
    return [names[event["id"]] for event in body["events"]]


def test_worker_month_follows_seoul_boundaries(api, db_engine, world):
    as_user(api, world["me"])
    response = api.get("/api/users/me/calendar/events", params={"month": "2026-10"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert ids(body, world) == ["carried_in", "completed", "future", "carried_out"]
    assert body["month"] == "2026-10" and body["timezone"] == "Asia/Seoul"
    assert body["asOf"] == "2026-10-05T03:00:00+00:00"
    first = body["events"][0]
    assert (first["startAt"], first["endAt"]) == ("2026-09-30T13:00:00+00:00", "2026-09-30T17:00:00+00:00")  # original times
    assert first == {
        "id": world["shifts"]["carried_in"], "store": first["store"], "kind": "TEMPORARY_WORK",
        "title": "저녁 대타", "startAt": first["startAt"], "endAt": first["endAt"], "allDay": False,
        "workerId": world["me"], "workerName": "확정자", "jobId": first["jobId"], "revision": 2,
        "editable": False,
    }
    assert first["store"]["neighborhood"] == "월계1동"
    # The late-night shift also belongs to the month it starts in and the one it ends in.
    september = api.get("/api/users/me/calendar/events", params={"month": "2026-09"}).json()
    assert ids(september, world) == ["touching_start", "carried_in"]
    november = api.get("/api/users/me/calendar/events", params={"month": "2026-11"}).json()
    assert ids(november, world) == ["carried_out", "touching_end"]


def test_worker_calendar_survives_ended_access_and_is_private(api, db_engine, world):
    as_user(api, world["them"])
    body = api.get("/api/users/me/calendar/events", params={"month": "2026-10"}).json()
    assert ids(body, world) == ["theirs", "foreign", "pending_store"]
    assert {e["workerName"] for e in body["events"]} == {"다른 사람"}
    as_user(api, world["me"])
    assert api.get("/api/users/me/calendar/events", params={"month": "2027-01"}).json()["events"] == []


def test_owner_calendar_covers_approved_owned_stores(api, db_engine, world):
    as_user(api, world["owner"])
    body = api.get("/api/owners/me/calendar/events", params={"month": "2026-10"}).json()
    assert ids(body, world) == ["carried_in", "completed", "theirs", "future", "carried_out"]
    only = api.get("/api/owners/me/calendar/events", params={"month": "2026-10", "storeId": world["second"]}).json()
    assert ids(only, world) == ["future"]
    pending = api.get("/api/owners/me/calendar/events", params={"month": "2026-10", "storeId": world["pending"]})
    assert pending.status_code == 403 and pending.json()["code"] == "STORE_APPROVAL_REQUIRED"
    foreign = api.get("/api/owners/me/calendar/events", params={"month": "2026-10", "storeId": world["foreign"]})
    assert foreign.status_code == 404 and foreign.json()["code"] == "RESOURCE_NOT_FOUND"
    assert api.get("/api/owners/me/calendar/events", params={"month": "2026-10", "storeId": key()}).status_code == 404


def test_owner_without_approved_stores_gets_empty_month(api, db_engine, clock):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        make_store_with_request(db, owner)
        db.commit()
        owner_id = owner.id
    as_user(api, owner_id)
    body = api.get("/api/owners/me/calendar/events", params={"month": "2026-10"}).json()
    assert body["events"] == []


@pytest.mark.parametrize("query", [
    "", "month=", "month=2026-13", "month=2026-00", "month=2026-1", "month=26-10", "month=2026/10",
    "month=2026-10-01", "month=2026-10&month=2026-11", "month=0000-01", "month=9999-12", "month=２０２６-10",
])
def test_month_validation(api, db_engine, world, query):
    for role, url in (("me", "/api/users/me/calendar/events"), ("owner", "/api/owners/me/calendar/events")):
        as_user(api, world[role])
        response = api.get(f"{url}?{query}")
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR", (url, query)


def test_owner_store_filter_validation(api, db_engine, world):
    as_user(api, world["owner"])
    assert api.get("/api/owners/me/calendar/events?month=2026-10&storeId=x").status_code == 422


def test_calendar_roles(api, db_engine, world):
    assert api.get("/api/users/me/calendar/events?month=2026-10").status_code == 401
    assert api.get("/api/owners/me/calendar/events?month=2026-10").status_code == 401
    as_user(api, world["me"])
    assert api.get("/api/owners/me/calendar/events?month=2026-10").json()["code"] == "FORBIDDEN"
    as_user(api, world["owner"])
    assert api.get("/api/users/me/calendar/events?month=2026-10").json()["code"] == "FORBIDDEN"


def test_more_than_1000_events_is_refused(api, db_engine, clock):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store, _ = make_store_with_request(db, owner, approved_at=NOW)
        worker = make_worker(db)
        for i in range(1000):
            confirm(db, store, worker, work_date=date(2026, 10, 1) + timedelta(days=i % 30),
                    start_time=time(i % 24, 0), end_time=time(i % 24, 30))
        db.commit()
        owner_id, worker_id, store_id = owner.id, worker.id, store.id
    as_user(api, worker_id)
    assert len(api.get("/api/users/me/calendar/events?month=2026-10").json()["events"]) == 1000
    with Session(db_engine) as db:
        confirm(db, db.get(Store, store_id), db.get(User, worker_id), work_date=date(2026, 10, 31))
        db.commit()
    for user, url in ((worker_id, "/api/users/me/calendar/events"), (owner_id, "/api/owners/me/calendar/events")):
        as_user(api, user)
        response = api.get(f"{url}?month=2026-10")
        assert response.status_code == 422 and response.json()["code"] == "CALENDAR_RANGE_TOO_LARGE"
    assert len(api.get(f"/api/owners/me/calendar/events?month=2026-11&storeId={store_id}").json()["events"]) == 0


def test_real_flow_confirmation_withdrawal_removes_event(api, db_engine, clock):
    """Accept through the API, then withdraw the confirmation: the event disappears."""
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store, _ = make_store_with_request(db, owner, approved_at=NOW)
        worker = make_worker(db)
        job = make_job(db, store)
        db.commit()
        owner_id, store_id, worker_id, job_id = owner.id, store.id, worker.id, job.id
    me = as_user(api, worker_id)
    application = api.post(f"/api/job-postings/{job_id}/applications", json={"introduction": "지원"},
                           headers=me.headers(key())).json()["id"]
    boss = as_user(api, owner_id)
    with Session(db_engine) as db:
        revision = db.get(JobPosting, job_id).revision
    request = api.post(f"/api/stores/{store_id}/job-postings/{job_id}/applications/{application}/work-requests",
                       json={"expectedJobRevision": revision}, headers=boss.headers(key())).json()
    me = as_user(api, worker_id)
    accepted = api.post(f"/api/users/me/work-requests/{request['id']}/response",
                        json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key())).json()
    events = api.get("/api/users/me/calendar/events?month=2026-10").json()["events"]
    assert [(e["jobId"], e["startAt"], e["revision"]) for e in events] == [(job_id, "2026-10-10T09:00:00+00:00", 2)]
    boss = as_user(api, owner_id)
    assert len(api.get("/api/owners/me/calendar/events?month=2026-10").json()["events"]) == 1
    withdrawn = api.post(
        f"/api/stores/{store_id}/job-postings/{job_id}/work-requests/{request['id']}/confirmation-withdrawal",
        json={"expectedRevision": accepted["revision"]}, headers=boss.headers(key()))
    assert withdrawn.status_code == 200
    assert api.get("/api/owners/me/calendar/events?month=2026-10").json()["events"] == []
    as_user(api, worker_id)
    assert api.get("/api/users/me/calendar/events?month=2026-10").json()["events"] == []

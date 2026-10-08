"""Work requests (#114): send, answer, withdraw, confirm, confirmation withdrawal, onboarding.

Every scenario goes through the real API on SQLite and MySQL; races run on MySQL only.
"""
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ApplicationSelectionEffect,
    JobApplication,
    JobPosting,
    ShiftAssignment,
    Store,
    StoreAccessGrant,
    WorkRequest,
)
from tests.api_contract import ORIGIN
from tests.factories import make_job
from tests.jobs_support import (
    NOW,
    as_user,
    client_factory,
    key,
    pin_clock,
    race,
    seed_shop,
    seed_user,
)

TODAY = date(2026, 10, 5)  # NOW = 12:00 KST
START = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)  # default posting: 10-10 18:00-22:00 KST
END = datetime(2026, 10, 10, 13, 0, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


@dataclass
class Scene:
    api: object
    db_engine: object
    clock: object
    store_id: str
    owner_id: str

    # -- data --------------------------------------------------------------------------------

    def job(self, **overrides) -> str:
        with Session(self.db_engine) as db:
            job = make_job(db, db.get(Store, self.store_id), **overrides)
            db.commit()
            return job.id

    def job_row(self, job_id) -> JobPosting:
        with Session(self.db_engine) as db:
            return db.get(JobPosting, job_id)

    def row(self, model, id_):
        with Session(self.db_engine) as db:
            return db.get(model, id_)

    def worker(self) -> str:
        return seed_user(self.db_engine)

    # -- sessions ----------------------------------------------------------------------------

    def owner(self):
        return as_user(self.api, self.owner_id)

    def as_worker(self, worker_id):
        return as_user(self.api, worker_id)

    # -- calls -------------------------------------------------------------------------------

    def apply(self, worker_id, job_id) -> str:
        me = self.as_worker(worker_id)
        response = self.api.post(f"/api/job-postings/{job_id}/applications", json={"introduction": "지원합니다"},
                                 headers=me.headers(key()))
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def send(self, job_id, application_id, revision=None, idem=None):
        me = self.owner()
        revision = revision if revision is not None else self.job_row(job_id).revision
        return self.api.post(
            f"/api/stores/{self.store_id}/job-postings/{job_id}/applications/{application_id}/work-requests",
            json={"expectedJobRevision": revision}, headers=me.headers(idem or key()))

    def request(self, job_id, application_id) -> dict:
        response = self.send(job_id, application_id)
        assert response.status_code == 201, response.text
        return response.json()

    def respond(self, worker_id, request_id, decision="ACCEPT", revision=1, idem=None):
        me = self.as_worker(worker_id)
        return self.api.post(f"/api/users/me/work-requests/{request_id}/response",
                             json={"expectedRevision": revision, "decision": decision}, headers=me.headers(idem or key()))

    def withdraw_request(self, job_id, request_id, revision=1, idem=None):
        me = self.owner()
        return self.api.post(f"/api/stores/{self.store_id}/job-postings/{job_id}/work-requests/{request_id}/withdrawal",
                             json={"expectedRevision": revision}, headers=me.headers(idem or key()))

    def withdraw_confirmation(self, job_id, request_id, revision=2, idem=None):
        me = self.owner()
        return self.api.post(
            f"/api/stores/{self.store_id}/job-postings/{job_id}/work-requests/{request_id}/confirmation-withdrawal",
            json={"expectedRevision": revision}, headers=me.headers(idem or key()))

    def confirmed(self, job_id=None, worker_id=None, **job) -> tuple[str, str, str, dict]:
        """A posting with an accepted request: (job, worker, application, request body)."""
        job_id = job_id or self.job(**job)
        worker_id = worker_id or self.worker()
        application = self.apply(worker_id, job_id)
        request = self.request(job_id, application)
        response = self.respond(worker_id, request["id"])
        assert response.status_code == 200, response.text
        return job_id, worker_id, application, response.json()


@pytest.fixture
def scene(api, db_engine, clock) -> Scene:
    shop = seed_shop(db_engine)
    return Scene(api, db_engine, clock, shop.store_id, shop.owner_id)


# --- send ----------------------------------------------------------------------------------


def test_send_creates_pending_request_with_one_hour_deadline(scene):
    job = scene.job()
    worker = scene.worker()
    application = scene.apply(worker, job)
    revision = scene.job_row(job).revision
    response = scene.send(job, application, revision)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING" and body["revision"] == 1 and body["accessGrant"] is None
    assert (body["jobId"], body["applicationId"], body["workerId"]) == (job, application, worker)
    assert body["workerName"] == "테스터"
    assert body["requestedAt"] == "2026-10-05T03:00:00+00:00" and body["expiresAt"] == "2026-10-05T04:00:00+00:00"
    assert body["respondedAt"] is None and body["endedAt"] is None
    assert scene.row(JobApplication, application).status == "REQUESTED"
    assert scene.job_row(job).revision == revision + 1


@pytest.mark.parametrize(("start", "expires"), [
    (time(12, 30), "2026-10-05T03:30:00+00:00"),  # starts in 30 minutes: expires at the start
    (time(13, 0), "2026-10-05T04:00:00+00:00"),   # exactly one hour: both bounds agree
    (time(13, 1), "2026-10-05T04:00:00+00:00"),
])
def test_deadline_is_min_of_one_hour_and_start(scene, start, expires):
    job = scene.job(work_date=TODAY, start_time=start, end_time=time(15))
    application = scene.apply(scene.worker(), job)
    assert scene.request(job, application)["expiresAt"] == expires


def test_send_conflicts(scene):
    job = scene.job()
    first, second = scene.worker(), scene.worker()
    a1, a2 = scene.apply(first, job), scene.apply(second, job)
    stale = scene.job_row(job).revision - 1
    assert scene.send(job, a1, stale).json()["code"] == "JOB_REVISION_CONFLICT"
    scene.request(job, a1)
    assert scene.send(job, a2).json()["code"] == "WORK_REQUEST_PENDING"
    assert scene.send(job, a1).json()["code"] == "WORK_REQUEST_PENDING"

    other = scene.job()
    withdrawn = scene.apply(scene.worker(), other)
    with Session(scene.db_engine) as db:
        row = db.get(JobApplication, withdrawn)
        row.status, row.withdrawn_at = "WITHDRAWN", NOW
        db.commit()
    assert scene.send(other, withdrawn).json()["code"] == "APPLICATION_NOT_ACTIVE"


def test_send_refuses_closed_filled_and_started_postings(scene):
    closed = scene.job()
    application = scene.apply(scene.worker(), closed)
    owner = scene.owner()
    scene.api.post(f"/api/stores/{scene.store_id}/job-postings/{closed}/closure",
                   json={"expectedRevision": scene.job_row(closed).revision}, headers=owner.headers(key()))
    assert scene.send(closed, application).json()["code"] == "JOB_NOT_RECRUITING"

    filled, _w, _a, _r = scene.confirmed()
    late = scene.worker()
    with Session(scene.db_engine) as db:  # an application that predates the confirmation
        db.add(JobApplication(job_id=filled, worker_id=late, introduction="늦게", applicant_name="늦음",
                              age_at_submission=20, experience_level="NEW", status="APPLIED"))
        db.commit()
        late_application = db.scalar(select(JobApplication.id).where(JobApplication.worker_id == late))
    assert scene.send(filled, late_application).json()["code"] == "JOB_FILLED"

    started = scene.job(work_date=TODAY, start_time=time(12, 30), end_time=time(14))
    application = scene.apply(scene.worker(), started)
    scene.clock.at = NOW + timedelta(minutes=30)
    assert scene.send(started, application).json()["code"] == "JOB_NOT_RECRUITING"


def test_send_scope_and_auth(scene):
    job = scene.job()
    application = scene.apply(scene.worker(), job)
    other_job = scene.job()
    other_shop = seed_shop(scene.db_engine)
    url = f"/api/stores/{scene.store_id}/job-postings/{other_job}/applications/{application}/work-requests"
    owner = scene.owner()
    assert scene.api.post(url, json={"expectedJobRevision": 1}, headers=owner.headers(key())).status_code == 404
    stranger = as_user(scene.api, other_shop.owner_id)
    url = f"/api/stores/{scene.store_id}/job-postings/{job}/applications/{application}/work-requests"
    assert scene.api.post(url, json={"expectedJobRevision": 2}, headers=stranger.headers(key())).status_code == 404
    worker = as_user(scene.api, scene.worker())
    assert scene.api.post(url, json={"expectedJobRevision": 2}, headers=worker.headers(key())).status_code == 403
    owner = scene.owner()
    no_csrf = scene.api.post(url, json={"expectedJobRevision": 2}, headers={"Origin": ORIGIN, "Idempotency-Key": key()})
    assert no_csrf.json()["code"] == "CSRF_INVALID"
    for body in ({}, {"expectedJobRevision": 0}, {"expectedJobRevision": "2"}, {"expectedRevision": 2}):
        assert scene.api.post(url, json=body, headers=owner.headers(key())).status_code == 422
    with Session(scene.db_engine) as db:
        assert db.scalar(select(func.count()).select_from(WorkRequest)) == 0


def test_send_is_idempotent(scene):
    job = scene.job()
    application = scene.apply(scene.worker(), job)
    revision = scene.job_row(job).revision
    idem = key()
    first = scene.send(job, application, revision, idem)
    replay = scene.send(job, application, revision, idem)
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()
    assert scene.send(job, application, revision + 5, idem).json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_expired_request_frees_the_posting_for_a_new_request(scene):
    job = scene.job()
    worker, other = scene.worker(), scene.worker()
    application, other_application = scene.apply(worker, job), scene.apply(other, job)
    old = scene.request(job, application)
    scene.clock.at = NOW + timedelta(hours=1) - timedelta(microseconds=1)
    assert scene.send(job, other_application).json()["code"] == "WORK_REQUEST_PENDING"
    scene.clock.at = NOW + timedelta(hours=1)  # exactly expiresAt
    again = scene.send(job, application)  # same applicant again: a new request row
    assert again.status_code == 201 and again.json()["id"] != old["id"]
    expired = scene.row(WorkRequest, old["id"])
    assert (expired.status, expired.ended_at, expired.revision) == ("EXPIRED", NOW + timedelta(hours=1), 1)
    assert scene.respond(worker, old["id"]).json()["code"] == "WORK_REQUEST_EXPIRED"


# --- respond -------------------------------------------------------------------------------


def test_accept_confirms_and_closes_atomically(scene):
    job = scene.job()
    chosen, rival, gone = scene.worker(), scene.worker(), scene.worker()
    application = scene.apply(chosen, job)
    rival_application = scene.apply(rival, job)
    gone_application = scene.apply(gone, job)
    me = scene.as_worker(gone)
    scene.api.post(f"/api/users/me/applications/{gone_application}/withdrawal", json={"expectedRevision": 1},
                   headers=me.headers(key()))
    request = scene.request(job, application)
    scene.clock.at = NOW + timedelta(minutes=10)
    response = scene.respond(chosen, request["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    at = "2026-10-05T03:10:00+00:00"
    assert (body["status"], body["respondedAt"], body["endedAt"], body["revision"]) == ("ACCEPTED", at, at, 2)
    grant = body["accessGrant"]
    assert (grant["type"], grant["status"], grant["startedAt"], grant["validUntil"]) == (
        "TEMPORARY", "ACTIVE", at, "2026-10-10T13:00:00+00:00")
    assert grant["dutyLabel"] == "저녁 대타" and grant["revokedAt"] is None and len(grant["permissions"]) == 3

    posting = scene.job_row(job)
    assert (posting.status, posting.closed_at) == ("CLOSED", NOW + timedelta(minutes=10))
    assert scene.row(JobApplication, application).status == "CONFIRMED"
    rival_row = scene.row(JobApplication, rival_application)
    assert rival_row.status == "NOT_SELECTED"
    gone_row = scene.row(JobApplication, gone_application)
    assert (gone_row.status, gone_row.revision) == ("WITHDRAWN", 2)
    with Session(scene.db_engine) as db:
        effects = list(db.scalars(select(ApplicationSelectionEffect)))
        assert [(e.request_id, e.application_id, e.previous_status, e.applied_revision) for e in effects] == [
            (request["id"], rival_application, "APPLIED", rival_row.revision)]
        shift = db.scalar(select(ShiftAssignment))
        assert (shift.job_id, shift.worker_id, shift.work_request_id) == (job, chosen, request["id"])
        access = db.scalar(select(StoreAccessGrant))
        assert (access.store_id, access.worker_id, access.assignment_id, access.invitation_id) == (
            scene.store_id, chosen, shift.id, None)
    scene.owner()
    detail = scene.api.get(f"/api/stores/{scene.store_id}/job-postings/{job}").json()
    assert detail["closedAt"] == at and detail["status"] == "CLOSED"


def test_accept_and_decline_deadline_boundary(scene):
    job = scene.job()
    worker = scene.worker()
    application = scene.apply(worker, job)
    request = scene.request(job, application)
    scene.clock.at = NOW + timedelta(hours=1)
    for decision in ("ACCEPT", "DECLINE"):
        response = scene.respond(worker, request["id"], decision)
        assert response.status_code == 409 and response.json()["code"] == "WORK_REQUEST_EXPIRED"
    scene.clock.at = NOW + timedelta(hours=1) - timedelta(microseconds=1)
    assert scene.respond(worker, request["id"]).status_code == 200


def test_decline_keeps_posting_open(scene):
    job = scene.job()
    worker = scene.worker()
    application = scene.apply(worker, job)
    request = scene.request(job, application)
    revision = scene.job_row(job).revision
    body = scene.respond(worker, request["id"], "DECLINE").json()
    assert (body["status"], body["respondedAt"], body["endedAt"], body["accessGrant"]) == (
        "DECLINED", "2026-10-05T03:00:00+00:00", "2026-10-05T03:00:00+00:00", None)
    posting = scene.job_row(job)
    assert (posting.status, posting.closed_at, posting.revision) == ("RECRUITING", None, revision + 1)
    assert scene.row(JobApplication, application).status == "APPLIED"
    assert scene.request(job, application)["id"] != request["id"]  # may be asked again


def test_respond_conflicts(scene):
    job = scene.job()
    worker = scene.worker()
    application = scene.apply(worker, job)
    request = scene.request(job, application)
    assert scene.respond(worker, request["id"], revision=2).json()["code"] == "WORK_REQUEST_REVISION_CONFLICT"
    idem = key()
    assert scene.respond(worker, request["id"], idem=idem).status_code == 200
    replay = scene.respond(worker, request["id"], idem=idem)
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    for revision in (1, 2):  # a new key for the same answer
        assert scene.respond(worker, request["id"], revision=revision).json()["code"] == "WORK_REQUEST_NOT_PENDING"
    assert scene.respond(worker, request["id"], "DECLINE", revision=2).json()["code"] == "WORK_REQUEST_NOT_PENDING"


def test_cancelled_and_withdrawn_requests_cannot_be_accepted(scene):
    job = scene.job()
    worker = scene.worker()
    application = scene.apply(worker, job)
    cancelled = scene.request(job, application)
    assert scene.withdraw_request(job, cancelled["id"]).status_code == 200
    assert scene.respond(worker, cancelled["id"], revision=2).json()["code"] == "WORK_REQUEST_NOT_PENDING"
    second = scene.request(job, application)
    assert scene.respond(worker, second["id"]).status_code == 200
    assert scene.withdraw_confirmation(job, second["id"]).status_code == 200
    assert scene.respond(worker, second["id"], revision=3).json()["code"] == "WORK_REQUEST_NOT_PENDING"


def test_respond_is_private_and_validated(scene):
    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))
    assert scene.respond(scene.worker(), request["id"]).status_code == 404
    me = scene.as_worker(worker)
    url = f"/api/users/me/work-requests/{request['id']}/response"
    for body in ({"expectedRevision": 1}, {"expectedRevision": 1, "decision": "accept"},
                 {"expectedRevision": 0, "decision": "ACCEPT"}, {"decision": "ACCEPT"},
                 {"expectedRevision": 1, "decision": "ACCEPT", "extra": 1}):
        assert scene.api.post(url, json=body, headers=me.headers(key())).status_code == 422
    owner = scene.owner()
    assert scene.api.post(url, json={"expectedRevision": 1, "decision": "ACCEPT"},
                          headers=owner.headers(key())).status_code == 403


@pytest.mark.parametrize(("other", "conflict"), [
    ({"work_date": date(2026, 10, 10), "start_time": time(21), "end_time": time(23)}, True),
    ({"work_date": date(2026, 10, 10), "start_time": time(14), "end_time": time(18)}, False),  # adjacent before
    ({"work_date": date(2026, 10, 10), "start_time": time(22), "end_time": time(23)}, False),  # adjacent after
    ({"work_date": date(2026, 10, 10), "start_time": time(17), "end_time": time(18, 1)}, True),
    ({"work_date": date(2026, 10, 9), "start_time": time(20), "end_time": time(19), "ends_next_day": True}, True),
    ({"work_date": date(2026, 10, 9), "start_time": time(22), "end_time": time(18), "ends_next_day": True}, False),
    ({"work_date": date(2026, 10, 11), "start_time": time(9), "end_time": time(10)}, False),
])
def test_overlapping_confirmations_are_refused(scene, other, conflict):
    worker = scene.worker()
    scene.confirmed(worker_id=worker, **other)
    job = scene.job()  # 10-10 18:00-22:00 KST
    request = scene.request(job, scene.apply(worker, job))
    response = scene.respond(worker, request["id"])
    if conflict:
        assert response.status_code == 409 and response.json()["code"] == "WORK_INTERVAL_CONFLICT"
        assert scene.job_row(job).status == "RECRUITING"
    else:
        assert response.status_code == 200, response.text


def test_overnight_overlap_across_midnight(scene):
    worker = scene.worker()
    scene.confirmed(worker_id=worker, work_date=date(2026, 10, 10), start_time=time(22), end_time=time(2),
                    ends_next_day=True)
    job = scene.job(work_date=date(2026, 10, 11), start_time=time(1), end_time=time(5))
    request = scene.request(job, scene.apply(worker, job))
    assert scene.respond(worker, request["id"]).json()["code"] == "WORK_INTERVAL_CONFLICT"


def test_overnight_posting_meets_a_shift_on_its_next_day(scene):
    # The new posting ends on the day after its work date; a shift starting that day is read.
    worker = scene.worker()
    scene.confirmed(worker_id=worker, work_date=date(2026, 10, 11), start_time=time(1), end_time=time(3))
    job = scene.job(work_date=date(2026, 10, 10), start_time=time(22), end_time=time(2), ends_next_day=True)
    request = scene.request(job, scene.apply(worker, job))
    assert scene.respond(worker, request["id"]).json()["code"] == "WORK_INTERVAL_CONFLICT"


def test_withdrawn_confirmation_does_not_block_another(scene):
    worker = scene.worker()
    first, _w, _a, accepted = scene.confirmed(worker_id=worker)
    assert scene.withdraw_confirmation(first, accepted["id"]).status_code == 200
    job = scene.job()  # same time as the withdrawn one
    request = scene.request(job, scene.apply(worker, job))
    assert scene.respond(worker, request["id"]).status_code == 200


# --- owner withdrawal ----------------------------------------------------------------------


def test_owner_withdraws_pending_request(scene):
    job = scene.job()
    worker = scene.worker()
    application = scene.apply(worker, job)
    request = scene.request(job, application)
    revision = scene.job_row(job).revision
    scene.clock.at = NOW + timedelta(minutes=59)
    response = scene.withdraw_request(job, request["id"])
    body = response.json()
    assert (body["status"], body["endedAt"], body["respondedAt"], body["revision"], body["accessGrant"]) == (
        "CANCELLED", "2026-10-05T03:59:00+00:00", None, 2, None)
    assert scene.row(JobApplication, application).status == "APPLIED"
    assert scene.job_row(job).revision == revision + 1


def test_owner_withdrawal_conflicts(scene):
    job = scene.job()
    worker = scene.worker()
    application = scene.apply(worker, job)
    request = scene.request(job, application)
    assert scene.withdraw_request(job, request["id"], revision=2).json()["code"] == "WORK_REQUEST_REVISION_CONFLICT"
    scene.clock.at = NOW + timedelta(hours=1)
    assert scene.withdraw_request(job, request["id"]).json()["code"] == "WORK_REQUEST_EXPIRED"
    scene.clock.at = NOW
    idem = key()
    assert scene.withdraw_request(job, request["id"], idem=idem).status_code == 200
    assert scene.withdraw_request(job, request["id"], idem=idem).headers["Idempotent-Replayed"] == "true"
    assert scene.withdraw_request(job, request["id"], revision=2).json()["code"] == "WORK_REQUEST_NOT_PENDING"
    accepted_job, _w, _a, accepted = scene.confirmed()
    assert scene.withdraw_request(accepted_job, accepted["id"], revision=2).json()["code"] == "WORK_REQUEST_NOT_PENDING"
    other_job = scene.job()
    assert scene.withdraw_request(other_job, accepted["id"]).status_code == 404


# --- confirmation withdrawal ---------------------------------------------------------------


def test_confirmation_withdrawal_reopens_and_restores_only_its_effects(scene):
    job = scene.job()
    chosen, rival, changed, gone = (scene.worker() for _ in range(4))
    application = scene.apply(chosen, job)
    rival_application = scene.apply(rival, job)
    changed_application = scene.apply(changed, job)
    gone_application = scene.apply(gone, job)
    me = scene.as_worker(gone)
    scene.api.post(f"/api/users/me/applications/{gone_application}/withdrawal", json={"expectedRevision": 1},
                   headers=me.headers(key()))
    request = scene.request(job, application)
    accepted = scene.respond(chosen, request["id"]).json()
    with Session(scene.db_engine) as db:  # changed separately after the acceptance
        db.get(JobApplication, changed_application).revision += 1
        db.commit()
    revision = scene.job_row(job).revision
    scene.clock.at = NOW + timedelta(hours=2)
    response = scene.withdraw_confirmation(job, request["id"], accepted["revision"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["respondedAt"], body["endedAt"], body["revision"]) == (
        "CONFIRMATION_WITHDRAWN", "2026-10-05T03:00:00+00:00", "2026-10-05T05:00:00+00:00", 3)
    assert (body["accessGrant"]["status"], body["accessGrant"]["revokedAt"], body["accessGrant"]["permissions"]) == (
        "REVOKED", "2026-10-05T05:00:00+00:00", [])
    posting = scene.job_row(job)
    assert (posting.status, posting.closed_at, posting.revision) == ("RECRUITING", None, revision + 1)
    assert scene.row(JobApplication, application).status == "APPLIED"
    assert scene.row(JobApplication, rival_application).status == "APPLIED"
    assert scene.row(JobApplication, changed_application).status == "NOT_SELECTED"
    assert scene.row(JobApplication, gone_application).status == "WITHDRAWN"
    with Session(scene.db_engine) as db:
        restored = {e.application_id: e.restored_at for e in db.scalars(select(ApplicationSelectionEffect))}
        assert restored == {rival_application: NOW + timedelta(hours=2), changed_application: None}
        shift = db.scalar(select(ShiftAssignment))
        assert shift.withdrawn_at == NOW + timedelta(hours=2)

    # The posting is open again: searchable, and another applicant can be asked and confirmed.
    as_user(scene.api, scene.worker())
    assert job in [item["id"] for item in scene.api.get("/api/job-postings").json()["items"]]
    second = scene.request(job, rival_application)
    assert scene.respond(rival, second["id"]).status_code == 200
    with Session(scene.db_engine) as db:
        assert db.scalar(select(func.count()).select_from(ShiftAssignment)) == 2


def test_confirmation_withdrawal_keeps_an_earlier_manual_revocation(scene):
    job, _worker, _application, accepted = scene.confirmed()
    revoked_at = NOW + timedelta(minutes=5)
    with Session(scene.db_engine) as db:
        db.scalar(select(StoreAccessGrant)).revoked_at = revoked_at
        db.commit()
    scene.clock.at = NOW + timedelta(hours=1)
    body = scene.withdraw_confirmation(job, accepted["id"]).json()
    assert body["accessGrant"]["revokedAt"] == "2026-10-05T03:05:00+00:00"
    assert scene.row(WorkRequest, accepted["id"]).ended_at == NOW + timedelta(hours=1)


def test_confirmation_withdrawal_until_start_only(scene):
    job, _worker, _application, accepted = scene.confirmed(work_date=TODAY, start_time=time(12, 30), end_time=time(14))
    scene.clock.at = NOW + timedelta(minutes=30)
    response = scene.withdraw_confirmation(job, accepted["id"])
    assert response.status_code == 409 and response.json()["code"] == "JOB_ALREADY_STARTED"
    scene.clock.at = NOW + timedelta(minutes=30) - timedelta(microseconds=1)
    assert scene.withdraw_confirmation(job, accepted["id"]).status_code == 200


def test_confirmation_withdrawal_conflicts(scene):
    job, _worker, _application, accepted = scene.confirmed()
    assert scene.withdraw_confirmation(job, accepted["id"], revision=1).json()["code"] == \
        "WORK_REQUEST_REVISION_CONFLICT"
    idem = key()
    assert scene.withdraw_confirmation(job, accepted["id"], idem=idem).status_code == 200
    replay = scene.withdraw_confirmation(job, accepted["id"], idem=idem)
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    for revision in (2, 3):
        assert scene.withdraw_confirmation(job, accepted["id"], revision=revision).json()["code"] == \
            "WORK_CONFIRMATION_NOT_ACTIVE"
    pending_job = scene.job()
    pending = scene.request(pending_job, scene.apply(scene.worker(), pending_job))
    assert scene.withdraw_confirmation(pending_job, pending["id"], revision=1).json()["code"] == \
        "WORK_CONFIRMATION_NOT_ACTIVE"
    assert scene.withdraw_confirmation(scene.job(), accepted["id"]).status_code == 404


def test_closure_after_reopen_and_closed_history_is_kept(scene):
    job, _worker, _application, accepted = scene.confirmed()
    owner = scene.owner()
    url = f"/api/stores/{scene.store_id}/job-postings/{job}/closure"
    closed = scene.job_row(job)
    again = scene.api.post(url, json={"expectedRevision": closed.revision}, headers=owner.headers(key()))
    assert again.status_code == 200 and again.json()["closedAt"] == "2026-10-05T03:00:00+00:00"
    assert scene.withdraw_confirmation(job, accepted["id"]).status_code == 200
    owner = scene.owner()
    scene.clock.at = NOW + timedelta(hours=3)
    reopened = scene.job_row(job)
    response = scene.api.post(url, json={"expectedRevision": reopened.revision}, headers=owner.headers(key()))
    assert response.json()["closedAt"] == "2026-10-05T06:00:00+00:00"
    with Session(scene.db_engine) as db:  # the first closing time stays in the confirmation history
        shift = db.scalar(select(ShiftAssignment).where(ShiftAssignment.work_request_id == accepted["id"]))
        assert shift.confirmed_at == NOW and db.get(WorkRequest, accepted["id"]).responded_at == NOW


# --- reads ---------------------------------------------------------------------------------


def test_owner_request_list(scene):
    job = scene.job()
    workers = [scene.worker() for _ in range(3)]
    applications = [scene.apply(w, job) for w in workers]
    expired = scene.request(job, applications[0])
    scene.clock.at = NOW + timedelta(hours=1)
    cancelled = scene.request(job, applications[1])
    scene.withdraw_request(job, cancelled["id"])
    scene.clock.at = NOW + timedelta(hours=2)
    accepted = scene.request(job, applications[2])
    scene.respond(workers[2], accepted["id"])
    scene.owner()
    body = scene.api.get(f"/api/stores/{scene.store_id}/job-postings/{job}/work-requests").json()
    assert [(i["id"], i["status"]) for i in body["items"]] == [
        (accepted["id"], "ACCEPTED"), (cancelled["id"], "CANCELLED"), (expired["id"], "EXPIRED")]
    assert body["items"][2]["endedAt"] == "2026-10-05T04:00:00+00:00" and body["totalItems"] == 3
    assert body["items"][0]["accessGrant"]["type"] == "TEMPORARY"
    page = scene.api.get(f"/api/stores/{scene.store_id}/job-postings/{job}/work-requests",
                         params={"page": 1, "size": 2}).json()
    assert [i["id"] for i in page["items"]] == [expired["id"]]


def test_owner_request_list_hides_foreign_and_unknown_postings(scene):
    """listOwnerWorkRequests 404: another store's posting under this store, an unknown posting,
    and this posting under another owner's store are the same RESOURCE_NOT_FOUND, never an empty
    list (an empty list would confirm the posting exists)."""
    job = scene.job()
    other = seed_shop(scene.db_engine)
    with Session(scene.db_engine) as db:
        foreign_job = make_job(db, db.get(Store, other.store_id)).id
        db.commit()
    scene.owner()
    for store_id, job_id in ((scene.store_id, foreign_job), (scene.store_id, str(uuid.uuid4())),
                             (other.store_id, job)):
        response = scene.api.get(f"/api/stores/{store_id}/job-postings/{job_id}/work-requests")
        assert (response.status_code, response.json()["code"]) == (404, "RESOURCE_NOT_FOUND"), response.text
    assert scene.api.get(f"/api/stores/{scene.store_id}/job-postings/{job}/work-requests").json()["items"] == []


def test_unmaterialized_expiry_is_projected(scene):
    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))
    scene.clock.at = NOW + timedelta(hours=1)
    scene.owner()
    item = scene.api.get(f"/api/stores/{scene.store_id}/job-postings/{job}/work-requests").json()["items"][0]
    assert (item["status"], item["endedAt"], item["revision"]) == ("EXPIRED", "2026-10-05T04:00:00+00:00", 1)
    assert scene.row(WorkRequest, request["id"]).status == "PENDING"  # reads never write
    scene.as_worker(worker)
    assert scene.api.get(f"/api/users/me/work-requests/{request['id']}").json()["status"] == "EXPIRED"
    assert scene.api.get("/api/users/me/work-requests").json()["items"] == []
    assert len(scene.api.get("/api/users/me/work-requests", params={"filter": "ALL"}).json()["items"]) == 1


def test_worker_request_reads(scene):
    worker, other = scene.worker(), scene.worker()
    job1, job2 = scene.job(), scene.job()
    pending = scene.request(job1, scene.apply(worker, job1))
    scene.clock.at = NOW + timedelta(minutes=1)
    declined = scene.request(job2, scene.apply(worker, job2))
    scene.respond(worker, declined["id"], "DECLINE")
    scene.request(job2, scene.apply(other, job2))
    scene.as_worker(worker)
    assert [i["id"] for i in scene.api.get("/api/users/me/work-requests").json()["items"]] == [pending["id"]]
    everything = scene.api.get("/api/users/me/work-requests", params={"filter": "ALL"}).json()
    assert [i["id"] for i in everything["items"]] == [declined["id"], pending["id"]]
    assert everything["asOf"] == "2026-10-05T03:01:00+00:00"
    detail = scene.api.get(f"/api/users/me/work-requests/{pending['id']}").json()
    assert detail["expiresAt"] == "2026-10-05T04:00:00+00:00" and detail["revision"] == 1
    scene.as_worker(other)
    assert scene.api.get(f"/api/users/me/work-requests/{pending['id']}").status_code == 404
    for query in ("filter=EXPIRED", "filter=pending", "filter=ALL&filter=PENDING"):
        assert scene.api.get(f"/api/users/me/work-requests?{query}").status_code == 422
    scene.owner()
    assert scene.api.get("/api/users/me/work-requests").status_code == 403


def test_onboarding(scene):
    job = scene.job()
    url = f"/api/stores/{scene.store_id}/job-postings/{job}/onboarding"
    scene.owner()
    missing = scene.api.get(url)
    assert missing.status_code == 404 and missing.json()["code"] == "ONBOARDING_NOT_FOUND"
    _job, worker, _application, accepted = scene.confirmed(job_id=job)
    scene.owner()
    assert scene.api.get(url).json() == {
        "jobId": job, "workerId": worker, "workerName": "테스터", "accessStatus": "ACTIVE",
        "manualStatus": "NOT_PUBLISHED", "manualVersionId": None,
    }
    scene.clock.at = END  # the shift ended: access ended, the confirmation stays
    assert scene.api.get(url).json()["accessStatus"] == "ENDED"
    scene.clock.at = NOW
    assert scene.withdraw_confirmation(job, accepted["id"]).status_code == 200
    assert scene.api.get(url).json()["code"] == "ONBOARDING_NOT_FOUND"
    other = seed_shop(scene.db_engine)
    as_user(scene.api, other.owner_id)
    assert scene.api.get(url).status_code == 404


# --- races (MySQL) -------------------------------------------------------------------------

def mysql_only(test):
    return pytest.mark.mysql(pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)(test))


@mysql_only
def test_race_double_accept_confirms_once(scene):
    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))
    me = scene.as_worker(worker)
    url = f"/api/users/me/work-requests/{request['id']}/response"
    responses = race([(client_factory(me.token), lambda c: c.post(
        url, json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key())))] * 6)
    assert sorted(r.status_code for r in responses) == [200] + [409] * 5
    with Session(scene.db_engine) as db:
        assert db.scalar(select(func.count()).select_from(ShiftAssignment)) == 1
        assert db.scalar(select(func.count()).select_from(StoreAccessGrant)) == 1


@mysql_only
def test_race_requests_to_different_applicants_leave_one_pending(scene):
    # Only the posting lock keeps one PENDING request per posting (the UNIQUE is per application).
    for _ in range(3):
        job = scene.job()
        applications = [scene.apply(scene.worker(), job) for _ in range(4)]
        owner = scene.owner()
        revision = scene.job_row(job).revision
        responses = race([(client_factory(owner.token), lambda c, a=a, j=job, rev=revision, owner=owner: c.post(
            f"/api/stores/{scene.store_id}/job-postings/{j}/applications/{a}/work-requests",
            json={"expectedJobRevision": rev}, headers=owner.headers(key()))) for a in applications])
        assert sorted(r.status_code for r in responses) == [201, 409, 409, 409]
        assert {r.json()["code"] for r in responses if r.status_code == 409} <= {
            "JOB_REVISION_CONFLICT", "WORK_REQUEST_PENDING"}
        with Session(scene.db_engine) as db:
            assert db.scalar(select(func.count()).select_from(WorkRequest)
                             .join(JobApplication, JobApplication.id == WorkRequest.application_id)
                             .where(JobApplication.job_id == job, WorkRequest.status == "PENDING")) == 1
            assert db.scalar(select(func.count()).select_from(JobApplication).where(
                JobApplication.job_id == job, JobApplication.status == "REQUESTED")) == 1


@mysql_only
def test_race_same_worker_overlapping_postings(scene):
    worker = scene.worker()
    jobs = [scene.job() for _ in range(3)]  # identical times
    requests = [scene.request(job, scene.apply(worker, job)) for job in jobs]
    me = scene.as_worker(worker)
    responses = race([(client_factory(me.token), lambda c, r=r: c.post(
        f"/api/users/me/work-requests/{r['id']}/response", json={"expectedRevision": 1, "decision": "ACCEPT"},
        headers=me.headers(key()))) for r in requests])
    assert sorted(r.status_code for r in responses) == [200, 409, 409]
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"WORK_INTERVAL_CONFLICT"}
    with Session(scene.db_engine) as db:
        assert db.scalar(select(func.count()).select_from(ShiftAssignment)) == 1
        assert sorted(db.scalars(select(JobPosting.status))) == ["CLOSED", "RECRUITING", "RECRUITING"]


@mysql_only
def test_race_owner_withdrawal_and_accept(scene):
    for _ in range(5):
        job = scene.job()
        worker = scene.worker()
        application = scene.apply(worker, job)
        request = scene.request(job, application)
        me, owner = scene.as_worker(worker), scene.owner()
        accepted, withdrawn = race([
            (client_factory(me.token), lambda c, r=request, me=me: c.post(
                f"/api/users/me/work-requests/{r['id']}/response",
                json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key()))),
            (client_factory(owner.token), lambda c, r=request, j=job, owner=owner: c.post(
                f"/api/stores/{scene.store_id}/job-postings/{j}/work-requests/{r['id']}/withdrawal",
                json={"expectedRevision": 1}, headers=owner.headers(key()))),
        ])
        assert sorted([accepted.status_code, withdrawn.status_code]) == [200, 409]
        final = scene.row(WorkRequest, request["id"]).status
        app_status = scene.row(JobApplication, application).status
        if accepted.status_code == 200:
            assert (final, app_status, scene.job_row(job).status) == ("ACCEPTED", "CONFIRMED", "CLOSED")
            assert withdrawn.json()["code"] == "WORK_REQUEST_NOT_PENDING"
        else:
            assert (final, app_status, scene.job_row(job).status) == ("CANCELLED", "APPLIED", "RECRUITING")
            assert accepted.json()["code"] == "WORK_REQUEST_NOT_PENDING"


@mysql_only
def test_race_application_withdrawal_and_accept(scene):
    for _ in range(5):
        job = scene.job()
        worker = scene.worker()
        application = scene.apply(worker, job)
        request = scene.request(job, application)
        me = scene.as_worker(worker)
        app_revision = scene.row(JobApplication, application).revision
        accepted, withdrawn = race([
            (client_factory(me.token), lambda c, r=request, me=me: c.post(
                f"/api/users/me/work-requests/{r['id']}/response",
                json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key()))),
            (client_factory(me.token), lambda c, a=application, rev=app_revision, me=me: c.post(
                f"/api/users/me/applications/{a}/withdrawal", json={"expectedRevision": rev},
                headers=me.headers(key()))),
        ])
        assert sorted([accepted.status_code, withdrawn.status_code]) == [200, 409]
        if accepted.status_code == 200:
            assert scene.row(JobApplication, application).status == "CONFIRMED"
        else:
            assert scene.row(WorkRequest, request["id"]).status == "CANCELLED"
            assert scene.row(JobApplication, application).status == "WITHDRAWN"
            assert scene.job_row(job).status == "RECRUITING"


@mysql_only
def test_race_closure_and_accept(scene):
    for _ in range(5):
        job = scene.job()
        worker = scene.worker()
        request = scene.request(job, scene.apply(worker, job))
        me, owner = scene.as_worker(worker), scene.owner()
        revision = scene.job_row(job).revision
        accepted, closed = race([
            (client_factory(me.token), lambda c, r=request, me=me: c.post(
                f"/api/users/me/work-requests/{r['id']}/response",
                json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key()))),
            (client_factory(owner.token), lambda c, j=job, rev=revision, owner=owner: c.post(
                f"/api/stores/{scene.store_id}/job-postings/{j}/closure", json={"expectedRevision": rev},
                headers=owner.headers(key()))),
        ])
        # A live request blocks the closure; an acceptance first makes its revision stale.
        assert accepted.status_code == 200 and closed.status_code == 409
        assert closed.json()["code"] in {"WORK_REQUEST_WITHDRAWAL_REQUIRED", "JOB_REVISION_CONFLICT"}
        posting = scene.job_row(job)
        assert (posting.status, posting.closed_at) == ("CLOSED", NOW)


@mysql_only
def test_race_concurrent_confirmation_withdrawals_apply_once(scene):
    job, _worker, _application, accepted = scene.confirmed()
    responses = race([
        (client_factory(scene.owner().token), lambda c: c.post(
            f"/api/stores/{scene.store_id}/job-postings/{job}/work-requests/{accepted['id']}/confirmation-withdrawal",
            json={"expectedRevision": 2}, headers=_headers(c))),
    ] * 4)
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409]
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"WORK_CONFIRMATION_NOT_ACTIVE"}
    with Session(scene.db_engine) as db:
        assert db.get(JobPosting, job).status == "RECRUITING"
        assert db.scalar(select(ShiftAssignment.withdrawn_at)) is not None


def _headers(client):
    from app.auth import SESSION_COOKIE_NAME, csrf_token_for

    token = client.cookies.get(SESSION_COOKIE_NAME)
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf_token_for(token), "Idempotency-Key": key()}


def test_sweep_records_due_requests_once(scene):
    from app.jobs.state import expire_due_requests

    due_job, live_job = scene.job(), scene.job()
    due_worker = scene.worker()
    due_application = scene.apply(due_worker, due_job)
    due = scene.request(due_job, due_application)
    scene.clock.at = NOW + timedelta(minutes=30)
    live = scene.request(live_job, scene.apply(scene.worker(), live_job))
    deadline = NOW + timedelta(hours=1)
    assert expire_due_requests(deadline) == 1
    assert expire_due_requests(deadline) == 0
    row = scene.row(WorkRequest, due["id"])
    assert (row.status, row.ended_at, row.revision) == ("EXPIRED", deadline, 1)
    assert scene.row(JobApplication, due_application).status == "APPLIED"
    assert scene.row(WorkRequest, live["id"]).status == "PENDING"
    scene.clock.at = deadline
    assert scene.respond(due_worker, due["id"]).json()["code"] == "WORK_REQUEST_EXPIRED"


@mysql_only
def test_race_accept_and_store_first_lock_holder_do_not_deadlock(scene):
    """An invitation acceptance (#108) locks the store row, then inserts a grant that checks the
    worker row (FK shared lock). The acceptance here must not hold the worker row while it waits
    for the store row, or the two deadlock and one of them fails."""
    import threading
    import time as clock_time

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))
    me = scene.as_worker(worker)
    results = {}
    with scene.db_engine.connect() as store_first:
        store_first.begin()
        store_first.execute(text("SELECT id FROM stores WHERE id = :id FOR UPDATE"), {"id": scene.store_id})

        def accept():
            client = client_factory(me.token)()
            try:
                results["accept"] = client.post(
                    f"/api/users/me/work-requests/{request['id']}/response",
                    json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key()))
            finally:
                client.close()

        thread = threading.Thread(target=accept)
        thread.start()
        clock_time.sleep(1.5)
        assert thread.is_alive()  # the acceptance is waiting on the store row
        try:
            store_first.execute(text("SELECT id FROM users WHERE id = :id FOR SHARE"), {"id": worker})
            store_first.commit()
        except OperationalError as error:
            results["store_first"] = error
            store_first.rollback()
        thread.join(timeout=60)
    assert "store_first" not in results, results.get("store_first")
    assert results["accept"].status_code == 200, results["accept"].text


def test_late_begin_transition_fails_under_tests(db_engine):
    """After a read the isolation level can no longer change; tests must not pass silently."""
    from sqlalchemy import text

    from app.jobs.state import LateTransitionWarning, begin_transition

    with Session(db_engine) as db:
        begin_transition(db)  # first statement: fine
        db.execute(text("SELECT 1"))
        db.commit()
        db.execute(text("SELECT 1"))
        with pytest.raises(LateTransitionWarning):
            begin_transition(db)


# --- lock scope ----------------------------------------------------------------------------

# Columns whose equality finds at most one row (or a primary key prefix): a FOR UPDATE on them
# locks the same rows whatever plan MySQL picks.
UNIQUE_LOCK_KEYS = {
    ("job_postings", "id"), ("job_applications", "id"), ("work_requests", "id"), ("users", "id"),
    ("stores", "id"), ("shift_assignments", "active_job_id"), ("shift_assignments", "work_request_id"),
    ("store_access_grants", "assignment_id"), ("application_selection_effects", "request_id"),
}


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_transitions_lock_rows_only_by_unique_keys(scene):
    """A FOR UPDATE on a non-unique condition locks whatever MySQL examines, and the plan follows
    table statistics: driving a posting's PENDING requests from the status index or a table scan
    locked every store's PENDING requests and deadlocked confirmations of unrelated postings
    (1213 -> 500). Every locking read of a transition must name one row (or a primary key
    prefix), so its lock scope does not depend on the plan."""
    import re

    from sqlalchemy import event

    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if re.search(r"\bFOR (UPDATE|SHARE)\b|LOCK IN SHARE MODE", statement):
            statements.append(" ".join(statement.split()))

    event.listen(scene.db_engine, "before_cursor_execute", capture)
    try:
        # apply, send, answer (accept with a rival and another pending request), confirmation
        # withdrawal, application withdrawal with a pending request, closure, expiry.
        job = scene.job()
        chosen, rival, other = scene.worker(), scene.worker(), scene.worker()
        application = scene.apply(chosen, job)
        scene.apply(rival, job)
        request = scene.request(job, application)
        assert scene.respond(chosen, request["id"]).status_code == 200
        assert scene.withdraw_confirmation(job, request["id"]).status_code == 200
        other_application = scene.apply(other, job)
        scene.request(job, other_application)
        me = scene.as_worker(other)
        withdrawn = scene.api.post(f"/api/users/me/applications/{other_application}/withdrawal",
                                   json={"expectedRevision": 2}, headers=me.headers(key()))
        assert withdrawn.status_code == 200, withdrawn.text
        closing = scene.job()
        scene.apply(scene.worker(), closing)
        scene.owner()
        closed = scene.api.post(f"/api/stores/{scene.store_id}/job-postings/{closing}/closure",
                                json={"expectedRevision": scene.job_row(closing).revision}, headers=scene.owner().headers(key()))
        assert closed.status_code == 200, closed.text
        expiring = scene.job()
        scene.request(expiring, scene.apply(scene.worker(), expiring))
        from app.jobs.state import expire_due_requests
        assert expire_due_requests(NOW + timedelta(hours=2)) == 1
    finally:
        event.remove(scene.db_engine, "before_cursor_execute", capture)

    job_statements = [s for s in statements if re.search(
        r"\bFROM (job_postings|job_applications|work_requests|shift_assignments|store_access_grants"
        r"|application_selection_effects)\b", s)]
    assert any("FROM work_requests" in s for s in job_statements)  # the capture saw the transitions
    for statement in job_statements:
        assert " JOIN " not in statement, statement
        found = re.search(r"\bWHERE (\w+)\.(\w+) = ", statement)
        assert found and found.groups() in UNIQUE_LOCK_KEYS, statement

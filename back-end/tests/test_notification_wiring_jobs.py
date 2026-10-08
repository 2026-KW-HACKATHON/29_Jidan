"""Jobs transitions record their notifications in the same transaction (#116 wiring).

Each site: right recipients and target, no personal names in the text, one row per event under
idempotent replays and races, nothing left behind when the transition fails.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import notification_sweeps
from app.db.models import JobApplication, Notification, ShiftAssignment, WorkRequest
from app.jobs.state import expire_due_requests
from tests.invitation_helpers import (
    configure_mail,
)
from tests.jobs_support import NOW, client_factory, key, pin_clock, race, seed_shop
from tests.test_work_requests import Scene, mysql_only


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


@pytest.fixture
def scene(api, db_engine, clock) -> Scene:
    shop = seed_shop(db_engine, name="월계 카페")
    return Scene(api, db_engine, clock, shop.store_id, shop.owner_id)


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


def notes(db_engine, kind=None) -> list[Notification]:
    with Session(db_engine) as db:
        statement = select(Notification).order_by(Notification.created_at, Notification.id)
        if kind is not None:
            statement = statement.where(Notification.event_type == kind)
        return list(db.scalars(statement))


def one(db_engine, kind) -> Notification:
    rows = notes(db_engine, kind)
    assert len(rows) == 1, [(r.event_type, r.recipient_user_id) for r in rows]
    return rows[0]


# --- jobs ---------------------------------------------------------------------------------------


def test_new_application_notifies_the_owner_once(scene):
    job = scene.job(title="주말 오픈 대타")
    worker = scene.worker()
    me = scene.as_worker(worker)
    idem = key()
    url = f"/api/job-postings/{job}/applications"
    first = scene.api.post(url, json={"introduction": "지원합니다"}, headers=me.headers(idem))
    replay = scene.api.post(url, json={"introduction": "지원합니다"}, headers=me.headers(idem))
    assert first.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    row = one(scene.db_engine, "NEW_APPLICATION")
    assert row.recipient_user_id == scene.owner_id
    assert row.target_context == {
        "type": "JOB_APPLICATION", "applicationId": first.json()["id"], "storeId": scene.store_id, "jobId": job,
    }
    assert row.body == "주말 오픈 대타 공고에 새 지원이 있어요" and "테스터" not in row.body
    assert row.created_at == NOW


def test_refused_application_leaves_no_notification(scene):
    job = scene.job()
    worker = scene.worker()
    scene.apply(worker, job)
    me = scene.as_worker(worker)
    again = scene.api.post(f"/api/job-postings/{job}/applications", json={"introduction": "다시"},
                           headers=me.headers(key()))
    assert again.status_code == 409
    assert len(notes(scene.db_engine, "NEW_APPLICATION")) == 1


def test_work_request_received_withdrawn_and_replays(scene):
    job = scene.job()
    worker = scene.worker()
    application = scene.apply(worker, job)
    idem = key()
    sent = scene.send(job, application, idem=idem)
    assert scene.send(job, application, revision=scene.job_row(job).revision - 1, idem=idem).status_code == 201
    received = one(scene.db_engine, "WORK_REQUEST_RECEIVED")
    assert received.recipient_user_id == worker
    assert received.target_context == {
        "type": "WORK_REQUEST", "requestId": sent.json()["id"], "storeId": scene.store_id, "jobId": job,
    }
    assert received.body == "월계 카페 10월 10일 18:00–22:00"
    withdraw_key = key()
    assert scene.withdraw_request(job, sent.json()["id"], idem=withdraw_key).status_code == 200
    assert scene.withdraw_request(job, sent.json()["id"], idem=withdraw_key).status_code == 200
    withdrawn = one(scene.db_engine, "WORK_REQUEST_WITHDRAWN")
    assert withdrawn.recipient_user_id == worker and withdrawn.target_id == sent.json()["id"]


def test_failed_send_records_nothing(scene):
    job = scene.job()
    application = scene.apply(scene.worker(), job)
    stale = scene.job_row(job).revision - 1
    assert scene.send(job, application, stale).status_code == 409
    assert notes(scene.db_engine, "WORK_REQUEST_RECEIVED") == []


def test_confirmation_notifies_worker_and_owner_with_the_schedule(scene):
    _job, worker, _application, accepted = scene.confirmed()
    rows = notes(scene.db_engine, "WORK_CONFIRMED")
    assert sorted(r.recipient_user_id for r in rows) == sorted([worker, scene.owner_id])
    with Session(scene.db_engine) as db:
        shift = db.scalar(select(ShiftAssignment).where(ShiftAssignment.work_request_id == accepted["id"]))
    for row in rows:
        assert row.target_context == {
            "type": "WORK_SCHEDULE", "eventId": shift.id, "storeId": scene.store_id, "workDate": "2026-10-10",
        }
        assert row.body == "월계 카페 10월 10일 18:00–22:00"


def test_decline_notifies_nobody(scene):
    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))
    assert scene.respond(worker, request["id"], decision="DECLINE").status_code == 200
    assert {r.event_type for r in notes(scene.db_engine)} == {"NEW_APPLICATION", "WORK_REQUEST_RECEIVED"}


def test_accept_replay_and_rejected_accept_add_nothing(scene):
    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))
    idem = key()
    assert scene.respond(worker, request["id"], idem=idem).status_code == 200
    assert scene.respond(worker, request["id"], idem=idem).headers["Idempotent-Replayed"] == "true"
    assert scene.respond(worker, request["id"]).status_code == 409  # a new key on an ended request
    assert len(notes(scene.db_engine, "WORK_CONFIRMED")) == 2


def test_confirmation_withdrawal_points_at_the_request(scene):
    job, worker, _application, accepted = scene.confirmed()
    idem = key()
    assert scene.withdraw_confirmation(job, accepted["id"], idem=idem).status_code == 200
    assert scene.withdraw_confirmation(job, accepted["id"], idem=idem).status_code == 200
    row = one(scene.db_engine, "WORK_CONFIRMATION_WITHDRAWN")
    assert row.recipient_user_id == worker
    assert row.target_context["type"] == "WORK_REQUEST" and row.target_id == accepted["id"]


def sweep(now):
    return notification_sweeps.run_notification_sweeps(now)["work-request-expiry"]


def test_no_response_sweep_materializes_the_expiry_with_its_notice(scene):
    job = scene.job()
    application = scene.apply(scene.worker(), job)
    request = scene.request(job, application)
    deadline = scene.row(WorkRequest, request["id"]).expires_at
    assert sweep(deadline - timedelta(microseconds=1)) == 0
    assert notes(scene.db_engine, "WORK_REQUEST_NO_RESPONSE") == []
    assert sweep(deadline) == 1
    assert sweep(deadline + timedelta(minutes=5)) == 0
    stored = scene.row(WorkRequest, request["id"])
    assert (stored.status, stored.ended_at) == ("EXPIRED", deadline)
    assert scene.row(JobApplication, application).status == "APPLIED"
    row = one(scene.db_engine, "WORK_REQUEST_NO_RESPONSE")
    assert row.recipient_user_id == scene.owner_id and row.created_at == deadline
    assert row.target_id == request["id"] and row.body.endswith("요청이 응답 없이 만료됐어요")


def test_no_response_is_recorded_when_another_write_settles_the_expiry(scene):
    """A posting write materializes due requests first; that transaction records the notice."""
    job = scene.job()
    first, second = scene.worker(), scene.worker()
    a1, a2 = scene.apply(first, job), scene.apply(second, job)
    request = scene.request(job, a1)
    scene.clock.at = NOW + timedelta(hours=1)
    assert scene.send(job, a2).status_code == 201  # settles the first request as EXPIRED
    assert scene.row(WorkRequest, request["id"]).status == "EXPIRED"
    row = one(scene.db_engine, "WORK_REQUEST_NO_RESPONSE")
    assert row.target_id == request["id"] and row.recipient_user_id == scene.owner_id
    assert sweep(NOW + timedelta(hours=1, minutes=1)) == 0  # the new request is still live


def test_sweep_skips_answered_and_ended_requests(scene):
    job_a, job_b, job_c, open_job = scene.job(), scene.job(), scene.job(), scene.job()
    declined_worker = scene.worker()
    declined = scene.request(job_a, scene.apply(declined_worker, job_a))
    assert scene.respond(declined_worker, declined["id"], decision="DECLINE").status_code == 200
    withdrawn = scene.request(job_b, scene.apply(scene.worker(), job_b))
    assert scene.withdraw_request(job_b, withdrawn["id"]).status_code == 200
    scene.confirmed(job_id=job_c)
    unanswered = scene.request(open_job, scene.apply(scene.worker(), open_job))
    assert sweep(NOW + timedelta(hours=1)) == 1  # only the unanswered one
    assert one(scene.db_engine, "WORK_REQUEST_NO_RESPONSE").target_id == unanswered["id"]
    statuses = {scene.row(WorkRequest, r["id"]).status for r in (declined, withdrawn)}
    assert statuses == {"DECLINED", "CANCELLED"}


def test_a_long_past_deadline_is_materialized_once_and_stamped_at_the_deadline(scene):
    """No lookback: expiry is state and must always be recorded. The notice carries the deadline
    as createdAt, so after an outage it sorts into the past instead of showing up as new."""
    job = scene.job()
    request = scene.request(job, scene.apply(scene.worker(), job))
    deadline = scene.row(WorkRequest, request["id"]).expires_at
    assert sweep(deadline + timedelta(days=30)) == 1
    assert sweep(deadline + timedelta(days=30)) == 0
    assert one(scene.db_engine, "WORK_REQUEST_NO_RESPONSE").created_at == deadline


def test_a_failing_notice_keeps_the_request_pending(scene, monkeypatch):
    """Atomicity: the expiry and its notice commit together or not at all."""
    from app import notification_events

    job = scene.job()
    request = scene.request(job, scene.apply(scene.worker(), job))
    deadline = scene.row(WorkRequest, request["id"]).expires_at

    def fail(*_args, **_kwargs):
        raise RuntimeError("notice cannot be recorded")

    original = notification_events.work_request_no_response
    monkeypatch.setattr(notification_events, "work_request_no_response", fail)
    assert sweep(deadline) is None  # logged by the registry, retried next run
    assert scene.row(WorkRequest, request["id"]).status == "PENDING"
    assert notes(scene.db_engine, "WORK_REQUEST_NO_RESPONSE") == []
    monkeypatch.setattr(notification_events, "work_request_no_response", original)
    assert expire_due_requests(deadline) == 1
    assert one(scene.db_engine, "WORK_REQUEST_NO_RESPONSE").target_id == request["id"]


def test_failure_after_recording_rolls_the_notifications_back(scene, monkeypatch):
    from app.jobs import work_requests

    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))

    def fail(*_args, **_kwargs):
        raise RuntimeError("fails after WORK_CONFIRMED was recorded")

    monkeypatch.setattr(work_requests, "request_body", fail)
    assert scene.respond(worker, request["id"]).status_code == 500
    assert notes(scene.db_engine, "WORK_CONFIRMED") == []
    assert scene.row(WorkRequest, request["id"]).status == "PENDING"


def test_sensitive_looking_store_name_falls_back_to_a_neutral_body(api, db_engine, clock):
    shop = seed_shop(db_engine, name="카페 01012345678")
    scene = Scene(api, db_engine, clock, shop.store_id, shop.owner_id)
    scene.confirmed()
    for row in notes(db_engine, "WORK_CONFIRMED"):
        assert row.body == "10월 10일 18:00–22:00 근무"


@mysql_only
def test_race_double_accept_notifies_once_per_recipient(scene):
    job = scene.job()
    worker = scene.worker()
    request = scene.request(job, scene.apply(worker, job))
    me = scene.as_worker(worker)
    url = f"/api/users/me/work-requests/{request['id']}/response"
    responses = race([(client_factory(me.token), lambda c: c.post(
        url, json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key())))] * 6)
    assert sorted(r.status_code for r in responses) == [200] + [409] * 5
    assert sorted(r.recipient_user_id for r in notes(scene.db_engine, "WORK_CONFIRMED")) == sorted(
        [worker, scene.owner_id])


@mysql_only
def test_race_withdrawal_and_accept_notify_only_the_winner(scene):
    for _ in range(3):
        job = scene.job()
        worker = scene.worker()
        request = scene.request(job, scene.apply(worker, job))
        me, owner = scene.as_worker(worker), scene.owner()
        accepted, withdrawn = race([
            (client_factory(me.token), lambda c, r=request, me=me: c.post(
                f"/api/users/me/work-requests/{r['id']}/response",
                json={"expectedRevision": 1, "decision": "ACCEPT"}, headers=me.headers(key()))),
            (client_factory(owner.token), lambda c, r=request, j=job, owner=owner: c.post(
                f"/api/stores/{scene.store_id}/job-postings/{j}/work-requests/{r['id']}/withdrawal",
                json={"expectedRevision": 1}, headers=owner.headers(key()))),
        ])
        with Session(scene.db_engine) as db:
            shift = db.scalar(select(ShiftAssignment.id).where(ShiftAssignment.work_request_id == request["id"]))
            confirmed = list(db.scalars(select(Notification.recipient_user_id).where(
                Notification.event_type == "WORK_CONFIRMED", Notification.target_id == shift))) if shift else []
            withdrawals = list(db.scalars(select(Notification.recipient_user_id).where(
                Notification.event_type == "WORK_REQUEST_WITHDRAWN", Notification.target_id == request["id"])))
        if accepted.status_code == 200:
            assert withdrawn.status_code == 409 and withdrawals == []
            assert sorted(confirmed) == sorted([worker, scene.owner_id])
        else:
            assert withdrawn.status_code == 200 and confirmed == [] and withdrawals == [worker]


@mysql_only
def test_race_sweeps_and_settling_write_record_no_response_once(scene):
    for _ in range(3):
        job = scene.job()
        first, second = scene.worker(), scene.worker()
        a1, a2 = scene.apply(first, job), scene.apply(second, job)
        request = scene.request(job, a1)
        scene.clock.at = NOW + timedelta(hours=1)
        owner = scene.owner()
        results = race([
            (client_factory(owner.token), lambda c, j=job, a=a2, owner=owner: c.post(
                f"/api/stores/{scene.store_id}/job-postings/{j}/applications/{a}/work-requests",
                json={"expectedJobRevision": scene.job_row(j).revision}, headers=owner.headers(key()))),
            (client_factory(owner.token), lambda _c: sweep(NOW + timedelta(hours=1))),
            (client_factory(owner.token), lambda _c: sweep(NOW + timedelta(hours=1))),
        ])
        assert results[0].status_code == 201, results[0].text
        assert scene.row(WorkRequest, request["id"]).status == "EXPIRED"
        with Session(scene.db_engine) as db:
            assert len(db.scalars(select(Notification).where(
                Notification.event_type == "WORK_REQUEST_NO_RESPONSE", Notification.target_id == request["id"],
            )).all()) == 1
        scene.clock.at = NOW


@mysql_only
def test_expiry_sweep_skips_a_posting_another_transaction_holds(scene):
    """SKIP LOCKED: the sweep never waits for a transition on the posting; it retries next run."""
    import time

    from app.db.models import JobPosting

    job = scene.job()
    request = scene.request(job, scene.apply(scene.worker(), job))
    deadline = scene.row(WorkRequest, request["id"]).expires_at
    with Session(scene.db_engine) as holder:
        holder.scalar(select(JobPosting).where(JobPosting.id == job).with_for_update())
        started = time.monotonic()
        assert expire_due_requests(deadline) == 0
        assert time.monotonic() - started < 2  # far below the lock wait timeout
        holder.rollback()
    assert notes(scene.db_engine, "WORK_REQUEST_NO_RESPONSE") == []
    assert scene.row(WorkRequest, request["id"]).status == "PENDING"
    assert expire_due_requests(deadline) == 1
    assert one(scene.db_engine, "WORK_REQUEST_NO_RESPONSE").target_id == request["id"]

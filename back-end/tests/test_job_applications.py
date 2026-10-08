"""Worker applications (#112): apply, list, detail, withdraw and re-apply, SQLite and MySQL."""
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ApplicationCareer,
    JobApplication,
    JobPosting,
    Store,
    User,
    WorkerCareer,
    WorkerProfile,
    WorkRequest,
)
from tests.api_contract import ORIGIN
from tests.factories import make_application, make_job, make_request, make_shift
from tests.jobs_support import (
    NOW,
    as_user,
    client_factory,
    key,
    pin_clock,
    race,
    run_concurrently,
    seed_shop,
    seed_user,
)

TODAY = date(2026, 10, 5)


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


def seed_job(db_engine, store_id, **overrides) -> str:
    with Session(db_engine) as db:
        job = make_job(db, db.get(Store, store_id), **overrides)
        db.commit()
        return job.id


def seed_application(db_engine, job_id, worker_id, **overrides) -> str:
    with Session(db_engine) as db:
        application = make_application(db, db.get(JobPosting, job_id), db.get(User, worker_id), **overrides)
        db.commit()
        return application.id


def apply(api, me, job_id, idem=None, introduction="성실하게 일하겠습니다."):
    return api.post(f"/api/job-postings/{job_id}/applications", json={"introduction": introduction},
                    headers=me.headers(idem or key()))


def withdraw(api, me, application_id, revision, idem=None):
    return api.post(f"/api/users/me/applications/{application_id}/withdrawal",
                    json={"expectedRevision": revision}, headers=me.headers(idem or key()))


@pytest.fixture
def world(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    worker = seed_user(db_engine)
    me = as_user(api, worker)
    return shop, job_id, worker, me


# --- apply ---------------------------------------------------------------------------------


def test_apply_snapshots_name_age_and_careers(api, db_engine, world):
    _shop, job_id, worker, me = world
    with Session(db_engine) as db:
        db.get(User, worker).name = "김지단"
        profile = db.get(WorkerProfile, worker)
        profile.experience_level = "EXPERIENCED"
        db.add_all([
            WorkerCareer(worker_id=worker, sort_order=1, industry="CAFE", duties="바리스타",
                         store_name=None, start_month="2024-01", end_month=None, is_current=True),
            WorkerCareer(worker_id=worker, sort_order=0, industry="RESTAURANT", duties="서빙",
                         store_name="월계 식당", start_month="2022-03", end_month="2023-02", is_current=False),
        ])
        db.commit()
    response = apply(api, me, job_id, introduction="  음료 제조 경험이 있어요.  ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "APPLIED" and body["revision"] == 1 and body["withdrawnAt"] is None
    assert body["introduction"] == "음료 제조 경험이 있어요."
    assert body["submittedAt"] == "2026-10-05T03:00:00+00:00"
    assert body["job"]["id"] == job_id and body["job"]["applicantCount"] == 1
    assert body["job"]["revision"] == 2  # every change to the posting's applicants bumps it
    with Session(db_engine) as db:
        row = db.get(JobApplication, body["id"])
        assert (row.applicant_name, row.age_at_submission, row.experience_level) == ("김지단", 26, "EXPERIENCED")
        careers = list(db.scalars(select(ApplicationCareer).where(ApplicationCareer.application_id == row.id)
                                  .order_by(ApplicationCareer.sort_order)))
        assert [(c.sort_order, c.duties, c.store_name, c.end_month) for c in careers] == [
            (0, "서빙", "월계 식당", "2023-02"), (1, "바리스타", None, None),
        ]
        # A later profile change never rewrites the submitted snapshot.
        db.get(User, worker).name = "새 이름"
        db.get(WorkerProfile, worker).birth_date = date(1990, 1, 1)
        db.commit()
    assert api.get(f"/api/users/me/applications/{body['id']}").json() == body | {"job": body["job"]}
    with Session(db_engine) as db:
        row = db.get(JobApplication, body["id"])
        assert (row.applicant_name, row.age_at_submission) == ("김지단", 26)


@pytest.mark.parametrize(("birth", "at", "age"), [
    (date(2000, 10, 5), NOW, 26),
    (date(2000, 10, 6), NOW, 25),
    (date(2000, 10, 6), datetime(2026, 10, 5, 15, 0, tzinfo=UTC), 26),  # already the 6th in Seoul
    (date(2004, 2, 29), datetime(2026, 2, 28, 3, 0, tzinfo=UTC), 21),
    (date(2004, 2, 29), datetime(2026, 3, 1, 3, 0, tzinfo=UTC), 22),
])
def test_age_is_full_years_on_the_seoul_date(api, db_engine, world, clock, birth, at, age):
    _shop, job_id, worker, me = world
    with Session(db_engine) as db:
        db.get(WorkerProfile, worker).birth_date = birth
        db.commit()
    clock.at = at
    response = apply(api, me, job_id)
    assert response.status_code == 201, response.text
    with Session(db_engine) as db:
        assert db.get(JobApplication, response.json()["id"]).age_at_submission == age


@pytest.mark.parametrize(("introduction", "ok"), [
    ("가", True), ("가" * 500, True), ("  " + "가" * 500 + "\n", True),
    ("", False), ("   ", False), ("\n\t", False), ("가" * 501, False),
])
def test_introduction_length_and_blank(api, db_engine, world, introduction, ok):
    _shop, job_id, _worker, me = world
    response = apply(api, me, job_id, introduction=introduction)
    assert response.status_code == (201 if ok else 422), response.text


@pytest.mark.parametrize("body", [{}, {"introduction": None}, {"introduction": 5},
                                  {"introduction": "안녕", "name": "다른 이름"}])
def test_apply_rejects_bad_body(api, db_engine, world, body):
    _shop, job_id, _worker, me = world
    response = api.post(f"/api/job-postings/{job_id}/applications", json=body, headers=me.headers(key()))
    assert response.status_code == 422


def test_minimum_experience_does_not_block(api, db_engine, world):
    shop, _job_id, _worker, me = world
    strict = seed_job(db_engine, shop.store_id, min_experience_months=12)
    assert apply(api, me, strict).status_code == 201


def test_apply_conflicts(api, db_engine, world, clock):
    shop, job_id, worker, me = world
    other = seed_user(db_engine)
    closed = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW)
    started = seed_job(db_engine, shop.store_id, work_date=TODAY, start_time=time(12), end_time=time(13))
    filled = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW)
    mine_confirmed = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW)
    with Session(db_engine) as db:
        for job, who in ((filled, other), (mine_confirmed, worker)):
            application = make_application(db, db.get(JobPosting, job), db.get(User, who), status="CONFIRMED")
            request = make_request(db, application, shop.owner_id, status="ACCEPTED", responded_at=NOW, ended_at=NOW)
            make_shift(db, db.get(JobPosting, job), request, who)
        db.commit()

    assert apply(api, me, job_id).status_code == 201
    assert apply(api, me, job_id).json()["code"] == "APPLICATION_ALREADY_ACTIVE"
    assert apply(api, me, mine_confirmed).json()["code"] == "APPLICATION_ALREADY_ACTIVE"
    assert apply(api, me, filled).json()["code"] == "JOB_FILLED"
    assert apply(api, me, closed).json()["code"] == "JOB_NOT_RECRUITING"
    response = apply(api, me, started)
    assert response.status_code == 409 and response.json()["code"] == "JOB_STARTED"
    clock.at = NOW - timedelta(microseconds=1)
    assert apply(api, me, started).status_code == 201


def test_apply_hides_unapproved_and_unknown_postings(api, db_engine, world):
    _shop, _job, _worker, me = world
    pending = seed_shop(db_engine, approved=False)
    hidden = seed_job(db_engine, pending.store_id)
    assert apply(api, me, hidden).status_code == 404
    assert apply(api, me, key()).json()["code"] == "RESOURCE_NOT_FOUND"
    assert apply(api, me, "x").status_code == 422


def test_apply_auth(api, db_engine, world):
    shop, job_id, _worker, _me = world
    url = f"/api/job-postings/{job_id}/applications"
    no_token = api.post(url, json={"introduction": "a"}, headers={"Origin": ORIGIN, "Idempotency-Key": key()})
    assert no_token.json()["code"] == "CSRF_INVALID"
    api.cookies.clear()
    assert api.post(url, json={"introduction": "a"}, headers={"Origin": ORIGIN}).status_code == 401
    owner = as_user(api, shop.owner_id)
    assert apply(api, owner, job_id).json()["code"] == "FORBIDDEN"
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(JobApplication)) == 0


def test_withdraw_then_reapply_creates_a_new_application(api, db_engine, world):
    shop, job_id, worker, me = world
    first = apply(api, me, job_id).json()
    with Session(db_engine) as db:
        request_id = make_request(db, db.get(JobApplication, first["id"]), shop.owner_id,
                                  requested_at=NOW, expires_at=NOW + timedelta(hours=1)).id
        db.get(JobApplication, first["id"]).status = "REQUESTED"
        db.commit()
    withdrawn = withdraw(api, me, first["id"], 1)
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "WITHDRAWN" and withdrawn.json()["revision"] == 2

    with Session(db_engine) as db:
        db.get(User, worker).name = "개명한 이름"
        db.commit()
    again = apply(api, me, job_id, introduction="다시 지원합니다.")
    assert again.status_code == 201
    assert again.json()["id"] != first["id"] and again.json()["revision"] == 1
    with Session(db_engine) as db:
        old = db.get(JobApplication, first["id"])
        assert (old.status, old.withdrawn_at, old.applicant_name) == ("WITHDRAWN", NOW, "테스터")
        assert db.get(WorkRequest, request_id).status == "CANCELLED"
        assert db.get(JobApplication, again.json()["id"]).applicant_name == "개명한 이름"
        assert db.get(JobPosting, job_id).revision == 4  # apply, withdraw, apply


def test_apply_idempotency(api, db_engine, world):
    _shop, job_id, _worker, me = world
    idem = key()
    first = apply(api, me, job_id, idem)
    withdraw(api, me, first.json()["id"], 1)
    replay = apply(api, me, job_id, idem)  # an old key replays; it is not a re-application
    assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == first.json()
    assert apply(api, me, job_id, idem, introduction="다른 내용").json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(JobApplication)) == 1


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_applications_create_one(api, db_engine, world):
    _shop, job_id, _worker, me = world
    url = f"/api/job-postings/{job_id}/applications"
    calls = [(lambda c: c.post(url, json={"introduction": "동시"}, headers=me.headers(key()))) for _ in range(8)]
    responses = run_concurrently(client_factory(me.token), calls)
    assert sorted(r.status_code for r in responses) == [201] + [409] * 7, [r.text for r in responses]
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"APPLICATION_ALREADY_ACTIVE"}
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(JobApplication)) == 1
        assert db.get(JobPosting, job_id).revision == 2


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_apply_and_closure_race(api, db_engine, world):
    shop, _job, _worker, me = world
    owner = as_user(api, shop.owner_id)
    outcomes = set()
    for _ in range(6):
        job_id = seed_job(db_engine, shop.store_id)
        applied, closed = race([
            (client_factory(me.token), lambda c, j=job_id: c.post(
                f"/api/job-postings/{j}/applications", json={"introduction": "경쟁"}, headers=me.headers(key()))),
            (client_factory(owner.token), lambda c, j=job_id: c.post(
                f"/api/stores/{shop.store_id}/job-postings/{j}/closure", json={"expectedRevision": 1},
                headers=owner.headers(key()))),
        ])
        with Session(db_engine) as db:
            job = db.get(JobPosting, job_id)
            statuses = list(db.scalars(select(JobApplication.status).where(JobApplication.job_id == job_id)))
        if applied.status_code == 201:
            # The application won the posting lock: the closure's revision is now stale.
            assert closed.json()["code"] == "JOB_REVISION_CONFLICT"
            assert (job.status, statuses) == ("RECRUITING", ["APPLIED"])
        else:
            assert applied.json()["code"] == "JOB_NOT_RECRUITING" and closed.status_code == 200
            assert (job.status, statuses) == ("CLOSED", [])
        outcomes.add(applied.status_code)
    assert outcomes <= {201, 409}


# --- list and detail -----------------------------------------------------------------------


def test_list_tabs_counts_and_projection(api, db_engine, world, clock):
    shop, job_id, worker, _me = world
    other = seed_user(db_engine)
    a = seed_application(db_engine, job_id, worker, applied_at=NOW - timedelta(minutes=1))
    due_job = seed_job(db_engine, shop.store_id)
    due = seed_application(db_engine, due_job, worker, status="REQUESTED", applied_at=NOW - timedelta(minutes=2))
    live_job = seed_job(db_engine, shop.store_id)
    live = seed_application(db_engine, live_job, worker, status="REQUESTED", applied_at=NOW - timedelta(minutes=3))
    future = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW)
    ongoing = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW, work_date=TODAY,
                       start_time=time(11), end_time=time(12, 0, 1))
    done = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW, work_date=TODAY - timedelta(days=1),
                    start_time=time(22), end_time=time(12), ends_next_day=True)  # ends exactly now
    confirmed = {j: seed_application(db_engine, j, worker, status="CONFIRMED", applied_at=NOW - timedelta(hours=i))
                 for i, j in enumerate((future, ongoing, done), start=1)}
    withdrawn = seed_application(db_engine, seed_job(db_engine, shop.store_id), worker, status="WITHDRAWN",
                                 withdrawn_at=NOW, applied_at=NOW - timedelta(days=1))
    not_selected = seed_application(db_engine, seed_job(db_engine, shop.store_id), worker, status="NOT_SELECTED",
                                    applied_at=NOW - timedelta(days=2))
    seed_application(db_engine, job_id, other)  # someone else's
    with Session(db_engine) as db:
        make_request(db, db.get(JobApplication, due), shop.owner_id, requested_at=NOW - timedelta(hours=1),
                     expires_at=NOW)
        make_request(db, db.get(JobApplication, live), shop.owner_id, requested_at=NOW,
                     expires_at=NOW + timedelta(hours=1))
        db.commit()

    def tab(name, **params):
        response = api.get("/api/users/me/applications", params={"tab": name, **params})
        assert response.status_code == 200, response.text
        return response.json()

    pending = tab("PENDING")
    assert [(i["id"], i["status"]) for i in pending["items"]] == [(a, "APPLIED"), (due, "APPLIED"), (live, "REQUESTED")]
    assert pending["counts"] == {"pending": 3, "confirmed": 2, "ended": 3} and pending["totalItems"] == 3
    assert [i["id"] for i in tab("CONFIRMED")["items"]] == [confirmed[future], confirmed[ongoing]]
    ended = tab("ENDED")
    assert [(i["id"], i["status"]) for i in ended["items"]] == [
        (confirmed[done], "COMPLETED"), (withdrawn, "WITHDRAWN"), (not_selected, "NOT_SELECTED"),
    ]
    assert ended["items"][1]["withdrawnAt"] == "2026-10-05T03:00:00+00:00"
    assert tab("ENDED", page=1, size=2)["items"][0]["id"] == not_selected
    assert api.get(f"/api/users/me/applications/{confirmed[done]}").json()["status"] == "COMPLETED"
    assert api.get(f"/api/users/me/applications/{due}").json()["status"] == "APPLIED"
    clock.at = NOW - timedelta(microseconds=1)
    assert api.get(f"/api/users/me/applications/{due}").json()["status"] == "REQUESTED"
    assert api.get(f"/api/users/me/applications/{confirmed[done]}").json()["status"] == "CONFIRMED"


def test_confirmed_tab_holds_until_the_exact_shift_end(api, db_engine, world, clock):
    shop, _job, worker, _me = world
    job_id = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW, work_date=TODAY,
                      start_time=time(10), end_time=time(18))
    application = seed_application(db_engine, job_id, worker, status="CONFIRMED")
    end = NOW.replace(hour=9)  # 18:00 KST
    # The tab SQL and the item status must agree to the microsecond on MySQL as well.
    for at, tab, status in ((end - timedelta(microseconds=400_000), "CONFIRMED", "CONFIRMED"),
                            (end - timedelta(microseconds=1), "CONFIRMED", "CONFIRMED"),
                            (end, "ENDED", "COMPLETED")):
        clock.at = at
        body = api.get("/api/users/me/applications", params={"tab": tab}).json()
        assert [(i["id"], i["status"]) for i in body["items"]] == [(application, status)], at


def test_list_order_ties_and_empty(api, db_engine, world):
    shop, _job, worker, _me = world
    assert api.get("/api/users/me/applications").json()["items"] == []
    tied = [seed_application(db_engine, seed_job(db_engine, shop.store_id), worker, applied_at=NOW)
            for _ in range(4)]
    pages = [api.get("/api/users/me/applications", params={"page": p, "size": 3}).json() for p in (0, 1)]
    assert [i["id"] for page in pages for i in page["items"]] == sorted(tied, reverse=True)


@pytest.mark.parametrize("query", ["tab=ALL", "tab=pending", "tab=PENDING&tab=ENDED", "size=0"])
def test_list_rejects_bad_query(api, db_engine, world, query):
    assert api.get(f"/api/users/me/applications?{query}").status_code == 422


def test_detail_and_list_are_private(api, db_engine, world):
    shop, job_id, _worker, me = world
    mine = apply(api, me, job_id).json()["id"]
    stranger = as_user(api, seed_user(db_engine))
    assert api.get(f"/api/users/me/applications/{mine}").status_code == 404
    assert api.get("/api/users/me/applications").json()["counts"] == {"pending": 0, "confirmed": 0, "ended": 0}
    assert withdraw(api, stranger, mine, 1).status_code == 404
    assert api.get(f"/api/users/me/applications/{key()}").json()["code"] == "RESOURCE_NOT_FOUND"
    as_user(api, shop.owner_id)
    assert api.get(f"/api/users/me/applications/{mine}").status_code == 403
    api.cookies.clear()
    assert api.get("/api/users/me/applications").status_code == 401


# --- withdraw ------------------------------------------------------------------------------


def test_withdraw_applied(api, db_engine, world):
    _shop, job_id, _worker, me = world
    application = apply(api, me, job_id).json()
    response = withdraw(api, me, application["id"], 1)
    body = response.json()
    assert (body["status"], body["revision"], body["withdrawnAt"]) == ("WITHDRAWN", 2, "2026-10-05T03:00:00+00:00")
    assert body["job"]["applicantCount"] == 0 and body["job"]["revision"] == 3


def test_withdraw_requested_cancels_live_request(api, db_engine, world):
    shop, job_id, worker, me = world
    application = seed_application(db_engine, job_id, worker, status="REQUESTED")
    with Session(db_engine) as db:
        request_id = make_request(db, db.get(JobApplication, application), shop.owner_id,
                                  requested_at=NOW, expires_at=NOW + timedelta(hours=1)).id
        db.commit()
    assert withdraw(api, me, application, 1).status_code == 200
    with Session(db_engine) as db:
        request = db.get(WorkRequest, request_id)
        assert (request.status, request.ended_at, request.responded_at, request.revision) == ("CANCELLED", NOW, None, 2)


def test_withdraw_requested_after_deadline_keeps_expiry(api, db_engine, world):
    shop, job_id, worker, me = world
    application = seed_application(db_engine, job_id, worker, status="REQUESTED")
    deadline = NOW - timedelta(minutes=5)
    with Session(db_engine) as db:
        request_id = make_request(db, db.get(JobApplication, application), shop.owner_id,
                                  requested_at=deadline - timedelta(hours=1), expires_at=deadline).id
        db.commit()
    response = withdraw(api, me, application, 1)  # the revision the worker read stays valid
    assert response.status_code == 200 and response.json()["revision"] == 2
    with Session(db_engine) as db:
        request = db.get(WorkRequest, request_id)
        assert (request.status, request.ended_at) == ("EXPIRED", deadline)


def test_withdraw_states(api, db_engine, world):
    shop, job_id, worker, me = world
    withdrawn = seed_application(db_engine, job_id, worker, status="WITHDRAWN", withdrawn_at=NOW - timedelta(hours=1),
                                 revision=2)
    before = NOW - timedelta(hours=1)
    again = withdraw(api, me, withdrawn, 2)
    assert again.status_code == 200 and again.json()["withdrawnAt"] == "2026-10-05T02:00:00+00:00"
    assert "Idempotent-Replayed" not in again.headers and again.json()["revision"] == 2
    assert withdraw(api, me, withdrawn, 1).json()["code"] == "APPLICATION_REVISION_CONFLICT"
    for status in ("CONFIRMED", "NOT_SELECTED", "COMPLETED"):
        job = seed_job(db_engine, shop.store_id)
        application = seed_application(db_engine, job, worker, status=status)
        response = withdraw(api, me, application, 1)
        assert response.status_code == 409 and response.json()["code"] == "APPLICATION_NOT_WITHDRAWABLE", status
    applied = seed_application(db_engine, seed_job(db_engine, shop.store_id), worker)
    assert withdraw(api, me, applied, 2).json()["code"] == "APPLICATION_REVISION_CONFLICT"
    with Session(db_engine) as db:
        assert db.get(JobApplication, withdrawn).withdrawn_at == before
        assert db.get(JobApplication, applied).status == "APPLIED"


def test_withdraw_idempotency_and_body(api, db_engine, world):
    _shop, job_id, _worker, me = world
    application = apply(api, me, job_id).json()["id"]
    idem = key()
    first = withdraw(api, me, application, 1, idem)
    replay = withdraw(api, me, application, 1, idem)
    assert replay.headers["Idempotent-Replayed"] == "true" and replay.json() == first.json()
    assert withdraw(api, me, application, 2, idem).json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    for body in ({}, {"expectedRevision": 0}, {"expectedRevision": "2"}, {"expectedRevision": 2, "x": 1}):
        url = f"/api/users/me/applications/{application}/withdrawal"
        assert api.post(url, json=body, headers=me.headers(key())).status_code == 422


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_withdrawals_apply_once(api, db_engine, world):
    _shop, job_id, _worker, me = world
    application = apply(api, me, job_id).json()["id"]
    url = f"/api/users/me/applications/{application}/withdrawal"
    calls = [(lambda c: c.post(url, json={"expectedRevision": 1}, headers=me.headers(key()))) for _ in range(6)]
    responses = run_concurrently(client_factory(me.token), calls)
    assert sorted(r.status_code for r in responses) == [200] + [409] * 5
    with Session(db_engine) as db:
        assert db.get(JobApplication, application).revision == 2
        assert db.get(JobPosting, job_id).revision == 3


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_snapshot_is_one_committed_profile_state(api, db_engine, world, monkeypatch):
    """A profile save (#106) locks the worker row and changes level and careers together. The
    snapshot must not read the level before that commit and the careers after it."""
    import threading
    import uuid

    from sqlalchemy import text

    from app.jobs import applications

    _shop, job_id, worker, me = world
    profile_read, saved = threading.Event(), threading.Event()
    original_age_on = applications.age_on

    def age_on(birth, today):  # runs between the profile read and the careers read
        profile_read.set()
        saved.wait(timeout=10)
        return original_age_on(birth, today)

    monkeypatch.setattr(applications, "age_on", age_on)
    results = {}
    with db_engine.connect() as profile_save:
        profile_save.begin()
        profile_save.execute(text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": worker})
        profile_save.execute(text("UPDATE worker_profiles SET experience_level = 'EXPERIENCED' WHERE user_id = :id"),
                             {"id": worker})
        profile_save.execute(text(
            "INSERT INTO worker_careers (id, worker_id, sort_order, industry, duties, start_month, is_current)"
            " VALUES (:cid, :id, 0, 'CAFE', '바리스타', '2024-01', 1)"), {"cid": str(uuid.uuid4()), "id": worker})

        def run():
            client = client_factory(me.token)()
            try:
                results["apply"] = apply(client, me, job_id)
            finally:
                client.close()

        thread = threading.Thread(target=run)
        thread.start()
        profile_read.wait(timeout=2)
        profile_save.commit()
        saved.set()
        thread.join(timeout=60)
    assert results["apply"].status_code == 201, results["apply"].text
    with Session(db_engine) as db:
        row = db.get(JobApplication, results["apply"].json()["id"])
        careers = db.scalar(select(func.count()).select_from(ApplicationCareer)
                            .where(ApplicationCareer.application_id == row.id))
        assert (row.experience_level, careers) == ("EXPERIENCED", 1)


def test_open_applications_read_not_selected_once_the_shift_starts(api, db_engine, world, clock):
    """A posting nobody was confirmed for (never chosen, or reopened by a confirmation
    withdrawal) cannot take a request or an acceptance after its start: its open applications
    end like a manual closure's, as a read-time projection."""
    from app.jobs.queries import pending_application_count

    shop, job_id, worker, me = world
    applied = apply(api, me, job_id).json()["id"]
    requested = seed_application(db_engine, job_id, seed_user(db_engine), status="REQUESTED")
    start = datetime(2026, 10, 10, 9, tzinfo=UTC)  # 18:00 KST
    with Session(db_engine) as db:
        make_request(db, db.get(JobApplication, requested), shop.owner_id, requested_at=start - timedelta(minutes=30),
                     expires_at=start)
        db.commit()

    def read(at):
        clock.at = at
        as_user(api, worker)
        tabs = {tab: api.get("/api/users/me/applications", params={"tab": tab}).json() for tab in ("PENDING", "ENDED")}
        detail = api.get(f"/api/users/me/applications/{applied}").json()
        posting = api.get(f"/api/job-postings/{job_id}").json()
        with Session(db_engine) as db:
            pending = pending_application_count(db, worker, at)
        as_user(api, shop.owner_id)
        owner = {view: api.get(f"/api/stores/{shop.store_id}/job-postings/{job_id}/applications",
                               params={"filter": view}).json() for view in ("ACTIVE", "ALL")}
        return tabs, detail, posting, pending, owner

    tabs, detail, posting, pending, owner = read(start - timedelta(microseconds=1))
    assert [i["id"] for i in tabs["PENDING"]["items"]] == [applied] and tabs["PENDING"]["counts"]["pending"] == 1
    assert detail["status"] == "APPLIED" and posting["myApplicationId"] == applied and pending == 1
    assert posting["applicantCount"] == 2 and owner["ACTIVE"]["totalItems"] == 2

    tabs, detail, posting, pending, owner = read(start)
    assert tabs["PENDING"]["items"] == [] and tabs["PENDING"]["counts"] == {"pending": 0, "confirmed": 0, "ended": 1}
    assert [(i["id"], i["status"]) for i in tabs["ENDED"]["items"]] == [(applied, "NOT_SELECTED")]
    assert detail["status"] == "NOT_SELECTED" and detail["job"]["applicantCount"] == 0 and pending == 0
    assert (posting["myApplicationId"], posting["canApply"], posting["cannotApplyReason"]) == (None, False, "JOB_STARTED")
    assert owner["ACTIVE"]["items"] == []
    assert {(i["id"], i["status"]) for i in owner["ALL"]["items"]} == {(applied, "NOT_SELECTED"), (requested, "NOT_SELECTED")}
    # Writes agree with what is read: no withdrawal of a NOT_SELECTED one, no "already applied".
    me_again = as_user(api, worker)
    response = withdraw(api, me_again, applied, detail["revision"])
    assert (response.status_code, response.json()["code"]) == (409, "APPLICATION_NOT_WITHDRAWABLE")
    response = apply(api, me_again, job_id)
    assert (response.status_code, response.json()["code"]) == (409, "JOB_STARTED")
    with Session(db_engine) as db:  # nothing was written
        assert {a.status for a in db.scalars(select(JobApplication).where(JobApplication.job_id == job_id))} == {
            "APPLIED", "REQUESTED"}

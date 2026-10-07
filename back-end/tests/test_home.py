"""Owner and worker home summaries (#117), on SQLite and MySQL."""
import itertools
from datetime import date, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.models import (
    AvailabilityDay,
    AvailabilityRule,
    FavoriteStore,
    JobPosting,
    Store,
    User,
)
from tests.factories import (
    make_application,
    make_job,
    make_notification,
    make_regular_grant,
    make_request,
    make_shift,
    make_store_with_request,
    make_user,
    make_worker,
)
from tests.jobs_support import NOW, as_user, key, pin_clock

TODAY = date(2026, 10, 5)  # Monday; NOW = 12:00 KST


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


def seed(db_engine, build):
    with Session(db_engine) as db:
        result = build(db)
        db.commit()
        return result


# --- owner home ----------------------------------------------------------------------------


def test_owner_without_stores(api, db_engine, clock):
    owner = seed(db_engine, lambda db: make_user(db, "OWNER", name="김점주").id)
    as_user(api, owner)
    assert api.get("/api/owners/me/home").json() == {
        "name": "김점주", "stores": [], "selectedStoreId": None, "recruitingCount": 0,
        "recruitingJobs": [], "unreadNotificationCount": 0, "asOf": "2026-10-05T03:00:00+00:00",
    }


def test_owner_home_selects_first_store_and_summarizes_recruiting(api, db_engine, clock):
    def build(db):
        owner = make_user(db, "OWNER")
        first, _ = make_store_with_request(db, owner, approved_at=NOW, created_at=NOW - timedelta(days=2))
        pending, _ = make_store_with_request(db, owner, created_at=NOW - timedelta(days=1))
        jobs = [make_job(db, first, created_at=NOW - timedelta(minutes=i)).id for i in range(4)]
        make_job(db, first, status="CLOSED", closed_at=NOW)
        make_notification(db, owner)
        make_notification(db, owner).read_at = NOW
        make_notification(db, owner, created_at=NOW + timedelta(seconds=1))  # after asOf
        make_notification(db, make_user(db, "OWNER"))
        return owner.id, first.id, pending.id, jobs

    owner, first, pending, jobs = seed(db_engine, build)
    as_user(api, owner)
    body = api.get("/api/owners/me/home").json()
    assert [s["id"] for s in body["stores"]] == [first, pending]
    assert body["stores"][1]["approvalStatus"] == "PENDING"
    assert body["selectedStoreId"] == first and body["recruitingCount"] == 4
    assert [j["id"] for j in body["recruitingJobs"]] == jobs[:3]
    assert body["unreadNotificationCount"] == 1

    chosen = api.get("/api/owners/me/home", params={"storeId": pending.upper()}).json()
    assert (chosen["selectedStoreId"], chosen["recruitingCount"], chosen["recruitingJobs"]) == (pending, 0, [])
    assert len(chosen["stores"]) == 2


def test_owner_home_store_filter_errors(api, db_engine, clock):
    owner = seed(db_engine, lambda db: make_user(db, "OWNER").id)
    other = seed(db_engine, lambda db: make_store_with_request(db, approved_at=NOW)[0].id)
    as_user(api, owner)
    assert api.get("/api/owners/me/home", params={"storeId": other}).json()["code"] == "RESOURCE_NOT_FOUND"
    assert api.get("/api/owners/me/home", params={"storeId": key()}).status_code == 404
    assert api.get("/api/owners/me/home", params={"storeId": "x"}).status_code == 422
    assert api.get(f"/api/owners/me/home?storeId={other}&storeId={other}").status_code == 422


def test_owner_home_store_limit(api, db_engine, clock):
    def build(db):
        owner = make_user(db, "OWNER")
        for _ in range(100):
            make_store_with_request(db, owner)
        return owner.id

    owner = seed(db_engine, build)
    as_user(api, owner)
    assert len(api.get("/api/owners/me/home").json()["stores"]) == 100
    seed(db_engine, lambda db: make_store_with_request(db, db.get(User, owner)))
    response = api.get("/api/owners/me/home")
    assert response.status_code == 422 and response.json()["code"] == "HOME_STORE_LIMIT"


def test_home_roles(api, db_engine, clock):
    owner = seed(db_engine, lambda db: make_user(db, "OWNER").id)
    worker = seed(db_engine, lambda db: make_worker(db).id)
    assert api.get("/api/owners/me/home").status_code == 401
    assert api.get("/api/users/me/home").status_code == 401
    as_user(api, worker)
    assert api.get("/api/owners/me/home").json()["code"] == "FORBIDDEN"
    as_user(api, owner)
    assert api.get("/api/users/me/home").json()["code"] == "FORBIDDEN"


# --- worker home ---------------------------------------------------------------------------


def test_worker_home_counts(api, db_engine, clock):
    def build(db):
        worker = make_worker(db)
        worker.name = "김근무"
        store, _ = make_store_with_request(db, approved_at=NOW)
        other, _ = make_store_with_request(db, approved_at=NOW)
        third, _ = make_store_with_request(db, approved_at=NOW)
        pending, _ = make_store_with_request(db)
        for status in ("APPLIED", "REQUESTED", "NOT_SELECTED"):
            make_application(db, make_job(db, store), worker, status=status)
        make_application(db, make_job(db, store), worker, status="WITHDRAWN", withdrawn_at=NOW)
        db.add_all([FavoriteStore(worker_id=worker.id, store_id=s.id, saved_at=NOW) for s in (store, other, pending)])
        db.add(FavoriteStore(worker_id=worker.id, store_id=third.id, saved_at=NOW + timedelta(seconds=1)))
        # Regular access: two grants at one store count once; ended, future and temporary do not.
        make_regular_grant(db, store, worker)
        make_regular_grant(db, store, worker)
        make_regular_grant(db, other, worker, granted_at=NOW - timedelta(days=1), valid_until=NOW)  # ends now
        make_regular_grant(db, third, worker, revoked_at=NOW)
        make_regular_grant(db, pending, worker, granted_at=NOW + timedelta(minutes=1))
        job = make_job(db, other, status="CLOSED", closed_at=NOW)
        application = make_application(db, job, worker, status="CONFIRMED")
        request = make_request(db, application, other.owner_id, status="ACCEPTED", responded_at=NOW, ended_at=NOW)
        shift = make_shift(db, job, request, worker.id)
        from app.db.models import StoreAccessGrant

        db.add(StoreAccessGrant(store_id=other.id, worker_id=worker.id, assignment_id=shift.id, granted_at=NOW,
                                valid_until=NOW + timedelta(days=5)))
        make_notification(db, worker)
        return worker.id

    worker = seed(db_engine, build)
    as_user(api, worker)
    body = api.get("/api/users/me/home").json()
    assert {k: body[k] for k in ("name", "pendingApplicationCount", "favoriteStoreCount", "regularStoreCount",
                                 "unreadNotificationCount")} == {
        "name": "김근무", "pendingApplicationCount": 2, "favoriteStoreCount": 2, "regularStoreCount": 1,
        "unreadNotificationCount": 1,
    }
    assert body["asOf"] == "2026-10-05T03:00:00+00:00"


_rule_order = itertools.count()


def availability(db, worker, days, start, end, next_day=False):
    rule = AvailabilityRule(worker_id=worker.id, sort_order=next(_rule_order), start_time=start, end_time=end,
                            ends_next_day=next_day)
    db.add(rule)
    db.flush()
    db.add_all(AvailabilityDay(rule_id=rule.id, weekday=day) for day in days)


def recommended(api) -> list[tuple[str, bool]]:
    body = api.get("/api/users/me/home").json()
    return [(job["id"], job["matchesAvailability"]) for job in body["recommendedJobs"]]


def test_recommendations_without_availability_are_nearest_first(api, db_engine, clock):
    def build(db):
        worker = make_worker(db)
        store, _ = make_store_with_request(db, approved_at=NOW)
        later = make_job(db, store, work_date=TODAY + timedelta(days=3)).id
        soonest = make_job(db, store, work_date=TODAY, start_time=time(12, 1), end_time=time(13)).id
        tie = sorted(make_job(db, store, work_date=TODAY + timedelta(days=1)).id for _ in range(2))
        make_job(db, store, work_date=TODAY + timedelta(days=9))  # fourth: cut off
        return worker.id, [soonest, *tie, later]

    worker, ordered = seed(db_engine, build)
    as_user(api, worker)
    assert recommended(api) == [(job, False) for job in ordered[:3]]


def test_recommendation_candidates(api, db_engine, clock):
    def build(db):
        worker = make_worker(db)
        store, _ = make_store_with_request(db, approved_at=NOW)
        pending, _ = make_store_with_request(db)
        applied = make_job(db, store, work_date=TODAY + timedelta(days=1))
        make_application(db, applied, worker)
        withdrawn = make_job(db, store, work_date=TODAY + timedelta(days=2))
        make_application(db, withdrawn, worker, status="WITHDRAWN", withdrawn_at=NOW)
        make_job(db, store, status="CLOSED", closed_at=NOW)
        make_job(db, store, work_date=TODAY, start_time=time(12), end_time=time(13))  # starts now
        make_job(db, pending)
        return worker.id, withdrawn.id

    worker, withdrawn = seed(db_engine, build)
    as_user(api, worker)
    body = api.get("/api/users/me/home").json()
    assert [(j["id"], j["canApply"], j["myApplicationId"]) for j in body["recommendedJobs"]] == [(withdrawn, True, None)]


def test_matching_availability_comes_first(api, db_engine, clock):
    def build(db):
        worker = make_worker(db)
        store, _ = make_store_with_request(db, approved_at=NOW)
        # Weekly availability: TUE 09-12 and 12-18 (adjacent), SUN 22:00 -> MON 06:00.
        availability(db, worker, ["TUE"], time(9), time(12))
        availability(db, worker, ["TUE"], time(12), time(18))
        availability(db, worker, ["SUN"], time(22), time(6), next_day=True)
        tue = TODAY + timedelta(days=1)
        jobs = {
            "partial": make_job(db, store, work_date=tue, start_time=time(17), end_time=time(19)),  # nearer, no match
            "adjacent": make_job(db, store, work_date=tue + timedelta(days=7), start_time=time(10), end_time=time(17)),
            "overnight": make_job(db, store, work_date=date(2026, 10, 12), start_time=time(1), end_time=time(5)),
        }
        return worker.id, {name: job.id for name, job in jobs.items()}

    worker, jobs = seed(db_engine, build)
    as_user(api, worker)
    # MON 10-12 01:00-05:00 is covered by SUN 22:00 -> MON 06:00 (the day before applies).
    assert recommended(api) == [(jobs["overnight"], True), (jobs["adjacent"], True), (jobs["partial"], False)]


@pytest.mark.parametrize(("rules", "shift", "match"), [
    ([(["WED"], time(18), time(22))], (time(18), time(22), False), True),           # exact
    ([(["WED"], time(18), time(22))], (time(22), time(23), False), False),          # touches the end only
    ([(["WED"], time(18), time(22))], (time(17, 59), time(22), False), False),      # one minute short
    ([(["WED"], time(20), time(2), True)], (time(22), time(1), True), True),        # overnight in overnight
    ([(["WED"], time(20), time(0), True), (["THU"], time(0), time(3))], (time(22), time(2), True), True),
    ([(["WED"], time(20), time(0), True)], (time(22), time(2), True), False),       # nothing on THU
    ([(["THU"], time(0), time(3))], (time(22), time(2), True), False),
    ([(["WED"], time(10), time(20)), (["WED"], time(12), time(14))], (time(11), time(19), False), True),  # nested
    ([(["TUE"], time(18), time(22))], (time(18), time(22), False), False),          # other weekday
    ([(["WED"], time(9), time(9), True)], (time(10), time(8), True), True),         # 24-hour window
])
def test_availability_coverage(api, db_engine, clock, rules, shift, match):
    wednesday = date(2026, 10, 7)

    def build(db):
        worker = make_worker(db)
        store, _ = make_store_with_request(db, approved_at=NOW)
        for rule in rules:
            availability(db, worker, *rule)
        job = make_job(db, store, work_date=wednesday, start_time=shift[0], end_time=shift[1], ends_next_day=shift[2])
        return worker.id, job.id

    worker, job = seed(db_engine, build)
    as_user(api, worker)
    assert recommended(api) == [(job, match)]


def test_empty_recommendations(api, db_engine, clock):
    worker = seed(db_engine, lambda db: make_worker(db).id)
    as_user(api, worker)
    body = api.get("/api/users/me/home").json()
    assert body["recommendedJobs"] == [] and body["pendingApplicationCount"] == 0


def test_recommendation_is_open_job_shape(api, db_engine, clock):
    def build(db):
        worker = make_worker(db)
        store, _ = make_store_with_request(db, approved_at=NOW)
        job = make_job(db, store)
        make_application(db, job, make_worker(db))  # someone else's application
        return worker.id, job.id

    worker, job = seed(db_engine, build)
    as_user(api, worker)
    item = api.get("/api/users/me/home").json()["recommendedJobs"][0]
    assert (item["id"], item["applicantCount"], item["status"], item["canApply"]) == (job, 1, "RECRUITING", True)
    with Session(db_engine) as db:
        assert db.get(JobPosting, job).store_id == item["store"]["id"]
        assert db.get(Store, item["store"]["id"]).approval_status == "APPROVED"


def test_regular_store_count_skips_a_suspended_owners_store(api, db_engine, clock):
    # The worker store list and material access refuse a store whose owner is suspended.
    def build(db):
        worker = make_worker(db)
        store, _ = make_store_with_request(db, approved_at=NOW)
        suspended, _ = make_store_with_request(db, approved_at=NOW)
        make_regular_grant(db, store, worker)
        make_regular_grant(db, suspended, worker)
        db.get(User, suspended.owner_id).status = "SUSPENDED"
        return worker.id

    worker = seed(db_engine, build)
    as_user(api, worker)
    assert api.get("/api/users/me/home").json()["regularStoreCount"] == 1
    listed = api.get("/api/users/me/stores").json()["items"]
    assert len(listed) == 1

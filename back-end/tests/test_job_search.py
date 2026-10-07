"""Worker job search and detail (#111), on SQLite and MySQL."""
from datetime import date, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.models import JobPosting, Store, User
from tests.factories import make_application, make_job, make_request, make_shift
from tests.jobs_support import NOW, as_user, key, pin_clock, seed_shop, seed_user

TODAY = date(2026, 10, 5)  # NOW is Monday 12:00 in Seoul


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


def seed_job(db_engine, store_id, **overrides) -> str:
    with Session(db_engine) as db:
        overrides = {"work_date": TODAY + timedelta(days=1), **overrides}
        job = make_job(db, db.get(Store, store_id), **overrides)
        db.commit()
        return job.id


def search(api, **params):
    response = api.get("/api/job-postings", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(body) -> list[str]:
    return [item["id"] for item in body["items"]]


@pytest.fixture
def world(api, db_engine, clock):
    shop = seed_shop(db_engine)
    worker = seed_user(db_engine)
    as_user(api, worker)
    return shop, worker


# --- visibility ----------------------------------------------------------------------------


def test_search_shows_only_open_future_postings_of_approved_stores(api, db_engine, world):
    shop, _worker = world
    pending = seed_shop(db_engine, approved=False)
    visible = seed_job(db_engine, shop.store_id)
    seed_job(db_engine, pending.store_id)
    seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW)
    seed_job(db_engine, shop.store_id, work_date=TODAY, start_time=time(12), end_time=time(13))  # starts now
    soon = seed_job(db_engine, shop.store_id, work_date=TODAY, start_time=time(12, 1), end_time=time(13))
    seed_job(db_engine, shop.store_id, work_date=TODAY - timedelta(days=1), start_time=time(22),
             end_time=time(14), ends_next_day=True)  # started yesterday, still running
    body = search(api)
    assert sorted(ids(body)) == sorted([visible, soon]) and body["totalItems"] == 2
    item = body["items"][0]
    assert item["canApply"] is True and item["cannotApplyReason"] is None and item["myApplicationId"] is None
    assert body["asOf"] == "2026-10-05T03:00:00+00:00"


def test_reopened_posting_is_searchable_again(api, db_engine, world):
    shop, _ = world
    job_id = seed_job(db_engine, shop.store_id, revision=4)
    with Session(db_engine) as db:
        job = db.get(JobPosting, job_id)
        application = make_application(db, job)
        request = make_request(db, application, shop.owner_id, status="CONFIRMATION_WITHDRAWN",
                               responded_at=NOW, ended_at=NOW)
        make_shift(db, job, request, application.worker_id, withdrawn_at=NOW)
        db.commit()
    body = search(api)
    assert ids(body) == [job_id] and body["items"][0]["canApply"] is True


def test_detail_reasons(api, db_engine, world):
    shop, worker = world
    other_worker = seed_user(db_engine)
    open_job = seed_job(db_engine, shop.store_id)
    closed = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW)
    started = seed_job(db_engine, shop.store_id, work_date=TODAY, start_time=time(12), end_time=time(13))
    filled = seed_job(db_engine, shop.store_id, status="CLOSED", closed_at=NOW)
    with Session(db_engine) as db:
        job = db.get(JobPosting, filled)
        application = make_application(db, job, db.get(User, other_worker), status="CONFIRMED")
        request = make_request(db, application, shop.owner_id, status="ACCEPTED",
                               responded_at=NOW, ended_at=NOW)
        make_shift(db, job, request, other_worker)
        db.commit()

    def detail(job_id):
        response = api.get(f"/api/job-postings/{job_id}")
        assert response.status_code == 200, response.text
        return response.json()

    assert detail(open_job)["canApply"] is True
    assert detail(closed)["cannotApplyReason"] == "JOB_CLOSED"
    assert detail(started)["cannotApplyReason"] == "JOB_STARTED"
    body = detail(filled)
    assert (body["canApply"], body["cannotApplyReason"], body["myApplicationId"]) == (False, "JOB_FILLED", None)
    assert body["applicantCount"] == 1  # count only; no other applicant's data
    assert worker != other_worker


def test_own_application_state_wins(api, db_engine, world):
    shop, worker = world
    applied, requested, withdrawn_only, confirmed = (seed_job(db_engine, shop.store_id) for _ in range(4))
    with Session(db_engine) as db:
        me = db.get(User, worker)
        a = make_application(db, db.get(JobPosting, applied), me).id
        r_app = make_application(db, db.get(JobPosting, requested), me, status="REQUESTED")
        make_request(db, r_app, shop.owner_id)
        make_application(db, db.get(JobPosting, withdrawn_only), me, status="WITHDRAWN", withdrawn_at=NOW)
        job = db.get(JobPosting, confirmed)
        job.status, job.closed_at = "CLOSED", NOW
        c_app = make_application(db, job, me, status="CONFIRMED")
        request = make_request(db, c_app, shop.owner_id, status="ACCEPTED", responded_at=NOW, ended_at=NOW)
        make_shift(db, job, request, worker)
        db.commit()
        r_id, c_id = r_app.id, c_app.id

    by_id = {item["id"]: item for item in search(api)["items"]}
    assert (by_id[applied]["cannotApplyReason"], by_id[applied]["myApplicationId"]) == ("ALREADY_APPLIED", a)
    assert (by_id[requested]["cannotApplyReason"], by_id[requested]["myApplicationId"]) == ("ALREADY_APPLIED", r_id)
    assert by_id[withdrawn_only]["canApply"] is True and by_id[withdrawn_only]["myApplicationId"] is None
    assert confirmed not in by_id
    body = api.get(f"/api/job-postings/{confirmed}").json()
    assert (body["cannotApplyReason"], body["myApplicationId"]) == ("ALREADY_CONFIRMED", c_id)

    # Another worker sees none of my applications.
    as_user(api, seed_user(db_engine))
    assert all(item["myApplicationId"] is None for item in search(api)["items"])


def test_detail_hides_pending_store_and_unknown_ids(api, db_engine, world):
    pending = seed_shop(db_engine, approved=False)
    hidden = seed_job(db_engine, pending.store_id)
    assert api.get(f"/api/job-postings/{hidden}").status_code == 404
    assert api.get(f"/api/job-postings/{key()}").json()["code"] == "RESOURCE_NOT_FOUND"
    assert api.get("/api/job-postings/not-a-uuid").status_code == 422


def test_access_rules(api, db_engine, clock):
    shop = seed_shop(db_engine)
    job_id = seed_job(db_engine, shop.store_id)
    assert api.get("/api/job-postings").status_code == 401
    assert api.get(f"/api/job-postings/{job_id}").status_code == 401
    as_user(api, shop.owner_id)
    assert api.get("/api/job-postings").json()["code"] == "FORBIDDEN"
    assert api.get(f"/api/job-postings/{job_id}").status_code == 403
    suspended = seed_user(db_engine)
    as_user(api, suspended)
    with Session(db_engine) as db:
        db.get(User, suspended).status = "SUSPENDED"
        db.commit()
    assert api.get("/api/job-postings").json()["code"] == "ACCOUNT_SUSPENDED"


# --- filters -------------------------------------------------------------------------------


def test_query_matches_store_name_title_and_description(api, db_engine, world):
    shop, _ = world
    named = seed_shop(db_engine, name="컴포즈커피 광운대점")
    by_store = seed_job(db_engine, named.store_id)
    by_title = seed_job(db_engine, shop.store_id, title="커피 마감 대타")
    by_duty = seed_job(db_engine, shop.store_id, duty_description="커피 머신 세척")
    seed_job(db_engine, shop.store_id, title="홀 서빙")
    assert sorted(ids(search(api, q="  커피 "))) == sorted([by_store, by_title, by_duty])
    assert len(search(api, q="   ")["items"]) == 4  # blank after trim = everything
    assert search(api, q="없는말")["items"] == []


def test_query_wildcards_are_literal(api, db_engine, world):
    shop, _ = world
    percent = seed_job(db_engine, shop.store_id, title="시급 10% 인상")
    underscore = seed_job(db_engine, shop.store_id, title="a_b 대타")
    slash = seed_job(db_engine, shop.store_id, title="오픈/마감")
    seed_job(db_engine, shop.store_id, title="aXb 대타")
    assert ids(search(api, q="%")) == [percent]
    assert ids(search(api, q="a_b")) == [underscore]
    assert ids(search(api, q="/")) == [slash]


def test_industry_filter(api, db_engine, world):
    shop, _ = world
    store = seed_shop(db_engine, industry="RESTAURANT")
    cafe = seed_job(db_engine, shop.store_id)
    restaurant = seed_job(db_engine, store.store_id)
    assert ids(search(api, industry="RESTAURANT")) == [restaurant]
    assert ids(search(api, industry="CAFE")) == [cafe]
    assert search(api, industry="CONVENIENCE_STORE")["items"] == []


def test_work_day_ranges_are_half_open_from_seoul_today(api, db_engine, world):
    shop, _ = world
    jobs = {offset: seed_job(db_engine, shop.store_id, work_date=TODAY + timedelta(days=offset))
            for offset in (0, 1, 2, 6, 7, 29, 30)}
    jobs[0] = seed_job(db_engine, shop.store_id, work_date=TODAY, start_time=time(18), end_time=time(22))

    def days(work_day):
        found = set(ids(search(api, workDay=work_day, size=100)))
        return sorted(offset for offset, job in jobs.items() if job in found)

    assert days("TODAY") == [0]
    assert days("TOMORROW") == [1]
    assert days("WEEK") == [0, 1, 2, 6]
    assert days("MONTH") == [0, 1, 2, 6, 7, 29]
    assert days("ALL") == [0, 1, 2, 6, 7, 29, 30]


def test_work_day_uses_seoul_date_near_midnight(api, db_engine, world, clock):
    shop, _ = world
    # 00:30 KST on the 6th is still the 5th in UTC; TODAY must follow Seoul.
    clock.at = NOW.replace(hour=15, minute=30)  # 00:30 KST, 2026-10-06
    on_sixth = seed_job(db_engine, shop.store_id, work_date=date(2026, 10, 6), start_time=time(9), end_time=time(10))
    seed_job(db_engine, shop.store_id, work_date=date(2026, 10, 7), start_time=time(9), end_time=time(10))
    assert ids(search(api, workDay="TODAY")) == [on_sixth]


def test_posting_stays_searchable_until_its_exact_start(api, db_engine, world, clock):
    shop, _ = world
    job_id = seed_job(db_engine, shop.store_id, work_date=TODAY, start_time=time(18), end_time=time(22))
    start = NOW.replace(hour=9)  # 18:00 KST
    # MySQL rounds a fractional clock to the TIME(0) column; 17:59:59.6 must not read as 18:00.
    for at, found in ((start - timedelta(microseconds=400_000), [job_id]),
                      (start - timedelta(microseconds=1), [job_id]), (start, [])):
        clock.at = at
        assert ids(search(api)) == found, at


@pytest.mark.parametrize(("start", "end", "next_day", "bands"), [
    ("06:00", "12:00", False, {"MORNING"}),
    ("05:00", "06:00", False, {"NIGHT"}),
    ("05:30", "06:30", False, {"NIGHT", "MORNING"}),
    ("12:00", "18:00", False, {"AFTERNOON"}),
    ("11:59", "12:01", False, {"MORNING", "AFTERNOON"}),
    ("17:00", "19:00", False, {"AFTERNOON", "NIGHT"}),
    ("18:00", "23:59", False, {"NIGHT"}),
    ("22:00", "02:00", True, {"NIGHT"}),
    ("22:00", "06:00", True, {"NIGHT"}),
    ("22:00", "06:01", True, {"NIGHT", "MORNING"}),
    ("23:00", "00:00", True, {"NIGHT"}),
    ("13:00", "00:00", True, {"AFTERNOON", "NIGHT"}),
    ("10:00", "08:00", True, {"MORNING", "AFTERNOON", "NIGHT"}),
    ("19:00", "12:30", True, {"NIGHT", "MORNING", "AFTERNOON"}),
])
def test_time_band_overlap(api, db_engine, world, start, end, next_day, bands):
    shop, _ = world
    job_id = seed_job(db_engine, shop.store_id, start_time=time.fromisoformat(start),
                      end_time=time.fromisoformat(end), ends_next_day=next_day)
    found = {band for band in ("MORNING", "AFTERNOON", "NIGHT") if ids(search(api, timeBand=band)) == [job_id]}
    assert found == bands
    assert ids(search(api, timeBand="ALL")) == [job_id]


def test_filters_combine_with_and(api, db_engine, world):
    shop, _ = world
    restaurant = seed_shop(db_engine, industry="RESTAURANT", name="월계 식당")
    hit = seed_job(db_engine, restaurant.store_id, title="점심 대타", work_date=TODAY + timedelta(days=1),
                   start_time=time(11), end_time=time(14))
    seed_job(db_engine, restaurant.store_id, title="점심 대타", work_date=TODAY + timedelta(days=8),
             start_time=time(11), end_time=time(14))  # outside WEEK
    seed_job(db_engine, restaurant.store_id, title="점심 대타", work_date=TODAY + timedelta(days=1),
             start_time=time(18), end_time=time(22))  # not MORNING
    seed_job(db_engine, shop.store_id, title="점심 대타", work_date=TODAY + timedelta(days=1),
             start_time=time(11), end_time=time(14))  # a cafe
    seed_job(db_engine, restaurant.store_id, title="저녁 대타", work_date=TODAY + timedelta(days=1),
             start_time=time(11), end_time=time(14))  # q misses
    body = search(api, q="점심", industry="RESTAURANT", workDay="WEEK", timeBand="MORNING")
    assert ids(body) == [hit] and body["totalItems"] == 1


# --- order and pages -----------------------------------------------------------------------


def test_latest_first_with_id_tie_breaker_and_stable_pages(api, db_engine, world):
    shop, _ = world
    tied = [seed_job(db_engine, shop.store_id, created_at=NOW - timedelta(hours=1)) for _ in range(5)]
    newer = seed_job(db_engine, shop.store_id, created_at=NOW - timedelta(minutes=1))
    older = seed_job(db_engine, shop.store_id, created_at=NOW - timedelta(days=2))
    expected = [newer, *sorted(tied, reverse=True), older]
    pages = [search(api, page=page, size=3) for page in range(4)]
    assert [job for body in pages for job in ids(body)] == expected
    assert all(body["totalItems"] == 7 for body in pages) and pages[3]["items"] == []


@pytest.mark.parametrize("query", [
    "industry=cafe", "industry=BAR", "workDay=YESTERDAY", "workDay=today", "timeBand=EVENING",
    "q=" + "가" * 101, "q=a&q=b", "workDay=TODAY&workDay=WEEK", "size=101", "page=-1",
])
def test_invalid_filters(api, db_engine, world, query):
    response = api.get(f"/api/job-postings?{query}")
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


def test_query_of_100_characters_is_allowed(api, db_engine, world):
    assert search(api, q="가" * 100)["items"] == []

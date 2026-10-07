"""Jobs read helpers for home and calendar (#117), on SQLite and MySQL."""
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from app.db.models import JobPosting, Store, User
from app.jobs import queries
from tests.factories import make_application, make_job, make_request, make_shift
from tests.jobs_support import NOW, seed_shop, seed_user

OCTOBER = (datetime(2026, 9, 30, 15, tzinfo=UTC), datetime(2026, 10, 31, 15, tzinfo=UTC))  # Seoul month


def confirm(db, store, worker, *, withdrawn=False, **job):
    job = make_job(db, store, status="CLOSED", closed_at=NOW, **job)
    application = make_application(db, job, worker, status="APPLIED" if withdrawn else "CONFIRMED",
                                   applicant_name="확정자")
    request = make_request(db, application, store.owner_id, status="CONFIRMATION_WITHDRAWN" if withdrawn else "ACCEPTED",
                           responded_at=NOW, ended_at=NOW)
    return make_shift(db, job, request, worker.id, withdrawn_at=NOW if withdrawn else None)


@pytest.fixture
def setup(db_engine):
    shop = seed_shop(db_engine)
    worker = seed_user(db_engine)
    other = seed_user(db_engine)
    with Session(db_engine) as db:
        store, me, them = db.get(Store, shop.store_id), db.get(User, worker), db.get(User, other)
        ids = {
            "inside": confirm(db, store, me, work_date=date(2026, 10, 10)).id,
            # 09-30 22:00 -> 10-01 02:00 KST overlaps October; 10-31 23:00 -> 11-01 01:00 too.
            "prev_night": confirm(db, store, me, work_date=date(2026, 9, 30), start_time=time(22),
                                  end_time=time(2), ends_next_day=True).id,
            "last_night": confirm(db, store, me, work_date=date(2026, 10, 31), start_time=time(23),
                                  end_time=time(1), ends_next_day=True).id,
            # ends exactly at October 00:00 KST: touching only
            "touching": confirm(db, store, me, work_date=date(2026, 9, 30), start_time=time(20),
                                end_time=time(0), ends_next_day=True).id,
            "withdrawn": confirm(db, store, me, withdrawn=True, work_date=date(2026, 10, 12)).id,
            "theirs": confirm(db, store, them, work_date=date(2026, 10, 11)).id,
        }
        db.commit()
    return shop, worker, ids


def test_confirmed_shifts_in_a_seoul_month(db_engine, setup):
    shop, worker, ids = setup
    with Session(db_engine) as db:
        mine = queries.confirmed_shifts(db, *OCTOBER, worker_id=worker)
        assert [s.shift.id for s in mine] == [ids["prev_night"], ids["inside"], ids["last_night"]]
        assert mine[0].worker_name == "확정자" and mine[0].start_at == datetime(2026, 9, 30, 13, tzinfo=UTC)
        store_view = queries.confirmed_shifts(db, *OCTOBER, store_ids=[shop.store_id])
        assert [s.shift.id for s in store_view] == [ids["prev_night"], ids["inside"], ids["theirs"], ids["last_night"]]
        assert queries.confirmed_shifts(db, *OCTOBER, store_ids=[]) == []


def test_home_counts_and_candidates(db_engine, setup):
    shop, worker, _ids = setup
    with Session(db_engine) as db:
        store, me = db.get(Store, shop.store_id), db.get(User, worker)
        open_job = make_job(db, store, created_at=NOW)
        applied_job = make_job(db, store, created_at=NOW - timedelta(minutes=1))
        withdrawn_job = make_job(db, store, created_at=NOW - timedelta(minutes=2))
        make_application(db, applied_job, me)
        make_application(db, withdrawn_job, me, status="WITHDRAWN", withdrawn_at=NOW)
        db.commit()
        # applied_job plus the application restored to APPLIED by the withdrawn confirmation
        assert queries.pending_application_count(db, worker, NOW) == 2
        total, newest = queries.recruiting_jobs(db, shop.store_id, NOW, limit=2)
        assert total == 3 and [j["id"] for j in newest] == [open_job.id, applied_job.id]
        candidates = {job.id for job, _store in queries.recommendation_candidates(db, worker, NOW)}
        assert candidates == {open_job.id, withdrawn_job.id}
        assert db.get(JobPosting, open_job.id).status == "RECRUITING"

"""B06: a response reads the clock once and every count in it uses that instant.

An APPLIED application counts toward `applicantCount` only before the shift starts. The owner
home, the store's posting list and the posting detail must agree at 1 µs before, exactly at and
1 µs after the start, and the home's counts must use the instant reported as `asOf`.
"""

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.jobs import common
from tests.api_contract import login
from tests.factories import NOW, make_application, make_job, make_store_with_request


@pytest.fixture
def shop(api, db_engine):
    with Session(db_engine) as db:
        store, _ = make_store_with_request(db, approved_at=NOW, created_at=NOW)
        job = make_job(db, store, created_at=NOW)
        make_application(db, job)
        db.commit()
        start = common.job_times(job).start_at
        ids = (store.owner_id, store.id, job.id)
    return api, login(api, ids[0]), ids, start


def clock(monkeypatch, *instants):
    """common.now() returns the given instants in turn and records how often it was read."""
    reads = []

    def now():
        reads.append(1)
        return instants[min(len(reads), len(instants)) - 1]

    monkeypatch.setattr(common, "now", now)
    return reads


def test_owner_home_counts_at_its_own_asof(shop, monkeypatch):
    api, _auth, _ids, start = shop
    reads = clock(monkeypatch, start - timedelta(microseconds=1), start)
    body = api.get("/api/owners/me/home").json()
    assert body["asOf"] == common.iso(start - timedelta(microseconds=1))
    assert body["recruitingJobs"][0]["applicantCount"] == 1
    assert len(reads) == 1  # one instant per response


@pytest.mark.parametrize("offset,expected", [
    (timedelta(microseconds=-1), 1), (timedelta(0), 0), (timedelta(microseconds=1), 0),
])
def test_home_list_and_detail_agree_around_the_start(shop, monkeypatch, offset, expected):
    api, _auth, (_owner, store_id, job_id), start = shop
    clock(monkeypatch, start + offset)
    home = api.get("/api/owners/me/home").json()["recruitingJobs"][0]
    listing = api.get(f"/api/stores/{store_id}/job-postings").json()["items"][0]
    detail = api.get(f"/api/stores/{store_id}/job-postings/{job_id}").json()
    assert (home["applicantCount"], listing["applicantCount"], detail["applicantCount"]) == (expected,) * 3


def test_posting_detail_reads_the_clock_once(shop, monkeypatch):
    api, _auth, (_owner, store_id, job_id), start = shop
    reads = clock(monkeypatch, start - timedelta(microseconds=1), start)
    assert api.get(f"/api/stores/{store_id}/job-postings/{job_id}").json()["applicantCount"] == 1
    assert len(reads) == 1

"""The early-exit recommendation equals a full rank of every candidate (SQLite and MySQL).

`recommended_jobs` reads candidates in (startAt, id) order, a first batch and then the rest,
and stops at the third match. The reference below is the previous algorithm: load every
candidate, sort by (not match, startAt, id) and take 3. Tiny first batches put matches before,
on and after the batch boundary; the cases cover 0, 1, 2, exactly 3 and more than 3 matches,
tied start times, no availability at all and postings excluded by a live application.
"""
import random
from datetime import time, timedelta

import pytest
from sqlalchemy.orm import Session

from app import home
from app.db.models import AvailabilityDay, AvailabilityRule, JobPosting
from app.jobs import common, queries
from tests.factories import make_application, make_store, make_user, make_worker

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
EVENING = (WEEKDAYS, time(18), time(23), False)
INSIDE = (time(19), 120)  # 19:00-21:00, covered by EVENING
OUTSIDE = (time(10), 120)  # 10:00-12:00, never covered by EVENING


def reference(db, worker_id, at):
    windows = home.availability_windows(db, worker_id)
    ranked = []
    for job, _store in queries.recommendation_candidates(db, worker_id, at):
        times = common.job_times(job)
        match = bool(windows) and home.covers(windows, times.start_at, times.end_at)
        ranked.append((not match, times.start_at, job.id, match))
    ranked.sort(key=lambda item: item[:3])
    return [(job_id, match) for _, _, job_id, match in ranked[:home.RECOMMENDATION_LIMIT]]


def optimized(db, worker_id, at):
    return [(job["id"], job["matchesAvailability"]) for job in home.recommended_jobs(db, worker_id, at)]


def compare(db_engine, monkeypatch, rng, *, batch, shifts, windows, live_applications=0):
    """Seed `shifts` [(start, minutes)] on random near dates and compare both algorithms."""
    monkeypatch.setattr(queries, "FIRST_CANDIDATE_BATCH", batch)
    today = common.seoul_today(common.now())
    with Session(db_engine) as db:
        worker = make_worker(db)
        for order, (days, start, end, next_day) in enumerate(windows):
            rule = AvailabilityRule(worker_id=worker.id, sort_order=order, start_time=start, end_time=end,
                                    ends_next_day=next_day)
            db.add(rule)
            db.flush()
            db.add_all(AvailabilityDay(rule_id=rule.id, weekday=day) for day in days)
        stores = [make_store(db, make_user(db, "OWNER"), approval_status="APPROVED", approved_at=common.now())
                  for _ in range(3)]
        jobs = []
        for index, (start, minutes) in enumerate(shifts):
            begin = start.hour * 60 + start.minute
            end = (begin + minutes) % 1440
            store = stores[index % len(stores)]
            jobs.append(JobPosting(
                store_id=store.id, created_by_owner_id=store.owner_id, title=f"공고 {index}",
                duty_description="홀", work_part="WEEKDAY_CLOSE",
                work_date=today + timedelta(days=rng.randrange(1, 4)),  # few dates: many ties
                start_time=start, end_time=time(end // 60, end % 60), ends_next_day=begin + minutes >= 1440,
                hourly_wage_krw=11000, payment_timing="WORK_DAY"))
        db.add_all(jobs)
        db.flush()
        for job in rng.sample(jobs, min(live_applications, len(jobs))):
            make_application(db, job, worker, status="APPLIED")
        db.commit()
        worker_id = worker.id
    with Session(db_engine) as db:
        at = common.now()
        expected = reference(db, worker_id, at)
        assert optimized(db, worker_id, at) == expected
        return sum(match for _, match in expected)


@pytest.mark.parametrize("batch", [1, 2, 4])
@pytest.mark.parametrize("matching", [0, 1, 2, 3, 5])
def test_match_counts_and_batch_boundaries(db_engine, monkeypatch, matching, batch):
    # Matches shuffled among non-matches: with first batches of 1, 2 and 4 they land before, on
    # and after the batch edge, and equal start times force the id tiebreak.
    rng = random.Random(matching * 10 + batch)
    shifts = [INSIDE] * matching + [OUTSIDE] * (12 - matching)
    rng.shuffle(shifts)
    found = compare(db_engine, monkeypatch, rng, batch=batch, shifts=shifts, windows=[EVENING])
    assert found == min(matching, home.RECOMMENDATION_LIMIT)


@pytest.mark.parametrize("batch", [1, 3])
def test_without_availability_takes_the_nearest_three(db_engine, monkeypatch, batch):
    rng = random.Random(batch)
    assert compare(db_engine, monkeypatch, rng, batch=batch, shifts=[INSIDE, OUTSIDE] * 4, windows=[]) == 0


@pytest.mark.parametrize("batch", [1, 2, 3])
@pytest.mark.parametrize("seed", range(5))
def test_random_worlds(db_engine, monkeypatch, batch, seed):
    rng = random.Random(seed * 10 + batch)
    windows = [
        (rng.sample(WEEKDAYS, rng.randint(1, 5)), time(rng.choice([9, 17, 18])), time(23), False),
        (rng.sample(WEEKDAYS, rng.randint(1, 4)), time(22), time(3), True),
    ][:rng.randint(1, 2)]
    starts = [INSIDE, OUTSIDE, (time(22), 240), (time(23), 120), (time(17), 360), (time(9), 600)]
    shifts = [rng.choice(starts) for _ in range(rng.randint(0, 20))]
    compare(db_engine, monkeypatch, rng, batch=batch, shifts=shifts, windows=windows,
            live_applications=rng.randint(0, 3))

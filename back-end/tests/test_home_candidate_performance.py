"""Real fetch/load budgets and full-ranking equivalence for home recommendations.

The budget tests call the original public recommended_jobs function unchanged: on 94982be
its wide tail loads hundreds of entities and fetches an unbounded result, so they fail.
The DBAPI cursor wrapper counts rows actually fetched; it never consumes extra rows.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, time, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app import home
from app.db.models import AvailabilityDay, AvailabilityRule, JobPosting, Store, User
from app.jobs import queries
from tests.factories import make_application, make_store, make_worker
from tests.jobs_support import NOW
from tests.test_home_recommendation_equivalence import reference


@dataclass
class ReadCost:
    selects: int = 0
    rows: int = 0
    cells: int = 0
    job_loads: int = 0
    store_loads: int = 0
    recommendation_reads: list[dict] = field(default_factory=list)


class CountingCursor:
    def __init__(self, cursor, cost, record):
        self.cursor, self.cost, self.record = cursor, cost, record
        self.width = len(cursor.description or ())

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def counted(self, rows):
        count = len(rows)
        self.cost.rows += count
        self.cost.cells += count * self.width
        if self.record is not None:
            self.record["rows"] += count
        return rows

    def fetchall(self):
        return self.counted(self.cursor.fetchall())

    def fetchmany(self, *args):
        return self.counted(self.cursor.fetchmany(*args))

    def fetchone(self):
        row = self.cursor.fetchone()
        self.counted([] if row is None else [row])
        return row


@contextmanager
def measure_reads(db):
    cost = ReadCost()

    def after_execute(_connection, _cursor, statement, _parameters, context, _many):
        sql = " ".join(statement.lower().split())
        if not sql.startswith("select"):
            return
        cost.selects += 1
        record = None
        if "from job_postings" in sql and "join stores" in sql and "exists" in sql:
            record = {"rows": 0, "columns": len(context.cursor.description), "sql": sql}
            cost.recommendation_reads.append(record)
        context.cursor = CountingCursor(context.cursor, cost, record)

    def loaded(_session, entity):
        cost.job_loads += isinstance(entity, JobPosting)
        cost.store_loads += isinstance(entity, Store)

    engine = db.get_bind()
    event.listen(engine, "after_cursor_execute", after_execute)
    event.listen(db, "loaded_as_persistent", loaded)
    try:
        yield cost
    finally:
        event.remove(engine, "after_cursor_execute", after_execute)
        event.remove(db, "loaded_as_persistent", loaded)


def seed_candidates(engine, count, *, matches=(), availability=True, tied=False, overnight=False):
    """Large descriptions make accidental entity hydration visible in memory benchmarks."""
    matching = set(matches)
    with Session(engine) as db:
        worker = make_worker(db)
        store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        if availability:
            rule = AvailabilityRule(worker_id=worker.id, sort_order=0, start_time=time(19),
                                    end_time=time(2) if overnight else time(23),
                                    ends_next_day=overnight)
            db.add(rule)
            db.flush()
            db.add_all(AvailabilityDay(rule_id=rule.id, weekday=day) for day in home.WEEKDAYS)
        jobs = []
        for index in range(count):
            match = index in matching
            jobs.append(JobPosting(
                id=f"00000000-0000-4000-8000-{index + 1:012x}", store_id=store.id,
                created_by_owner_id=store.owner_id, title=f"후보 {index}",
                duty_description="업무 설명 " * 150, work_part="WEEKDAY_CLOSE",
                work_date=date(2026, 10, 6) + timedelta(days=0 if tied else index),
                start_time=time(23) if match and overnight else time(19) if match else time(10),
                end_time=time(1) if match and overnight else time(21) if match else time(12),
                ends_next_day=match and overnight, hourly_wage_krw=11000,
                payment_timing="WORK_DAY",
            ))
        db.add_all(jobs)
        db.commit()
        return worker.id


def selected(db, worker):
    return [(item["id"], item["matchesAvailability"])
            for item in home.recommended_jobs(db, worker, NOW)]


@pytest.mark.parametrize("count,matches", [
    (199, (196, 197, 198)), (200, (197, 198, 199)),
    (201, (198, 199, 200)), (603, (400, 401, 602)), (603, ()),
    (2200, ()), (4003, ()), (4200, (4197, 4198, 4199)),
])
def test_original_home_has_bounded_fetches_and_only_three_job_loads(db_engine, count, matches):
    worker = seed_candidates(db_engine, count, matches=matches)
    with Session(db_engine) as db:
        expected = reference(db, worker, NOW)
    with Session(db_engine) as db:
        with measure_reads(db) as cost:
            assert selected(db, worker) == expected
        assert cost.job_loads <= 3, cost
        assert cost.store_loads <= 3, cost
        assert cost.recommendation_reads
        assert max(read["rows"] for read in cost.recommendation_reads) <= queries.FOLLOWING_CANDIDATE_BATCH, cost
        # Every scan is narrow; the single final detail fetch has at most three rows.
        assert all(read["columns"] == 5 or read["rows"] <= 3
                   for read in cost.recommendation_reads), cost
        candidate_queries = (1 if count < queries.FIRST_CANDIDATE_BATCH else 2
                             + (count - queries.FIRST_CANDIDATE_BATCH) // queries.FOLLOWING_CANDIDATE_BATCH)
        assert cost.selects <= 2 + candidate_queries + 1 + 3, cost
        assert cost.rows <= count + 3 + 1 + 7, cost


@pytest.mark.parametrize("availability,tied,overnight,matches", [
    (False, False, False, ()), (True, True, False, (200, 201, 202)),
    (True, False, True, (199, 200, 201)), (True, False, False, (400,)),
])
def test_exact_rank_with_no_availability_ties_overnight_and_partial_matches(
    db_engine, availability, tied, overnight, matches,
):
    worker = seed_candidates(db_engine, 403, matches=matches, availability=availability,
                             tied=tied, overnight=overnight)
    with Session(db_engine) as db:
        expected = reference(db, worker, NOW)
    with Session(db_engine) as db:
        with measure_reads(db) as cost:
            assert selected(db, worker) == expected
        assert cost.job_loads <= 3, cost
        if not availability:
            assert sum(read["rows"] for read in cost.recommendation_reads) <= 203, cost


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_paged_scan_and_details_keep_one_snapshot_when_postings_close_or_receive_applications(
    db_engine, monkeypatch,
):
    worker = seed_candidates(db_engine, 403, matches=(200, 201, 202))
    target_ids = [f"00000000-0000-4000-8000-{index + 1:012x}" for index in (200, 201, 202)]
    original = queries.recommendation_details

    def commit_between_scan_and_details(db, worker_id, at, job_ids):
        assert job_ids == target_ids
        with Session(db_engine) as writer:
            closed = writer.get(JobPosting, job_ids[0])
            closed.status, closed.closed_at = "CLOSED", NOW
            make_application(writer, writer.get(JobPosting, job_ids[1]), writer.get(User, worker))
            writer.commit()
        return original(db, worker_id, at, job_ids)

    monkeypatch.setattr(queries, "recommendation_details", commit_between_scan_and_details)
    with Session(db_engine) as reader:
        assert reader.scalar(text("SELECT @@transaction_isolation")) == "REPEATABLE-READ"
        assert selected(reader, worker) == [(job_id, True) for job_id in target_ids]
    monkeypatch.setattr(queries, "recommendation_details", original)
    with Session(db_engine) as fresh_reader:
        actual = selected(fresh_reader, worker)
        assert actual[0] == (target_ids[2], True)
        assert all(job_id not in target_ids[:2] for job_id, _ in actual)

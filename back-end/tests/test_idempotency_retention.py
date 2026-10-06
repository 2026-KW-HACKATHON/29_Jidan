"""B05: expired idempotency records are deleted by a registered periodic job.

Replay window 24 h (`expires_at`); the job then deletes in bounded batches, keeps a PROCESSING
record whose lease is still live, and a failed run is retried at the next interval.
"""

import asyncio
import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import idempotency, lifespan, periodic
from app.db import utcnow
from app.db.models import IdempotencyRecord


def record(db, *, expires_in, state="COMPLETED", lease_in=None):
    now = utcnow()
    row = IdempotencyRecord(
        subject_id="s" * 64, idempotency_key=str(uuid.uuid4()), endpoint="POST /api/x",
        request_hash="h" * 64, state=state, created_at=now - timedelta(hours=24) + expires_in,
        expires_at=now + expires_in,
        response_status=201 if state == "COMPLETED" else None, response_body={"ok": True} if state == "COMPLETED" else None,
        lock_token=str(uuid.uuid4()) if state == "PROCESSING" else None,
        locked_until=now + lease_in if lease_in is not None else None,
    )
    db.add(row)
    return row


def keys(engine) -> set[str]:
    with Session(engine) as db:
        return set(db.scalars(select(IdempotencyRecord.idempotency_key)))


def count(engine) -> int:
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(IdempotencyRecord))


def test_retention_job_is_registered():
    jobs = {job.name: job for job in lifespan.PERIODIC_JOBS}
    job = jobs.get("idempotency-retention")
    assert job is not None and job.run is idempotency.run_idempotency_retention
    assert job.interval_seconds == idempotency.RETENTION_INTERVAL_SECONDS == 300


def test_expired_records_go_and_live_ones_and_live_leases_stay(db_engine):
    with Session(db_engine) as db:
        expired = record(db, expires_in=-timedelta(seconds=1))
        boundary = record(db, expires_in=timedelta(0))  # expires_at == now: past its window
        live = record(db, expires_in=timedelta(hours=1))
        stuck = record(db, expires_in=-timedelta(hours=1), state="PROCESSING", lease_in=-timedelta(seconds=1))
        running = record(db, expires_in=-timedelta(hours=1), state="PROCESSING", lease_in=timedelta(seconds=30))
        fresh = record(db, expires_in=timedelta(hours=23), state="PROCESSING", lease_in=timedelta(seconds=30))
        db.commit()
        names = {n: r.idempotency_key for n, r in
                 {"expired": expired, "boundary": boundary, "live": live, "stuck": stuck, "running": running,
                  "fresh": fresh}.items()}
    later = utcnow() + timedelta(milliseconds=10)
    assert idempotency.purge_expired(now=later) == 3
    assert keys(db_engine) == {names["live"], names["running"], names["fresh"]}
    assert idempotency.purge_expired(now=later) == 0


def test_runs_are_bounded_and_a_backlog_drains_over_runs(db_engine, monkeypatch):
    monkeypatch.setattr(idempotency, "RETENTION_BATCH", 4)
    monkeypatch.setattr(idempotency, "RETENTION_MAX_BATCHES", 2)
    with Session(db_engine) as db:
        for _ in range(11):
            record(db, expires_in=-timedelta(minutes=5))
        record(db, expires_in=timedelta(hours=2))
        db.commit()
    assert idempotency.run_idempotency_retention() == 8  # 2 batches of 4
    assert count(db_engine) == 4
    assert idempotency.run_idempotency_retention() == 3
    assert idempotency.run_idempotency_retention() == 0
    assert count(db_engine) == 1


def test_a_failed_run_is_retried_at_the_next_interval(db_engine, monkeypatch, caplog):
    with Session(db_engine) as db:
        record(db, expires_in=-timedelta(minutes=5))
        db.commit()
    calls, delays = [], []
    real = idempotency.run_idempotency_retention

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("password=secret")
        return real()

    async def sleep(seconds):
        delays.append(seconds)
        if len(delays) == 2:
            raise asyncio.CancelledError

    job = periodic.PeriodicJob("idempotency-retention", idempotency.RETENTION_INTERVAL_SECONDS, flaky)

    async def run():
        try:
            await periodic.run_periodically(job, sleep=sleep)
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert len(calls) == 2 and delays == [300, 300]
    assert count(db_engine) == 0
    assert "secret" not in caplog.text and "idempotency-retention" in caplog.text


def test_concurrent_purges_delete_each_record_once(db_engine, monkeypatch):
    if db_engine.dialect.name != "mysql":
        pytest.skip("MySQL SKIP LOCKED")
    monkeypatch.setattr(idempotency, "RETENTION_BATCH", 25)
    with Session(db_engine) as db:
        for _ in range(200):
            record(db, expires_in=-timedelta(minutes=5))
        db.commit()
    results, errors = [], []
    barrier = threading.Barrier(4)

    def worker():
        try:
            barrier.wait()
            results.append(idempotency.run_idempotency_retention())
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sum(results) == 200 and count(db_engine) == 0


def test_lock_contention_retries_the_batch_then_waits_for_the_next_run(db_engine, monkeypatch):
    import pymysql
    from sqlalchemy.exc import OperationalError

    deadlock = OperationalError("DELETE", {}, pymysql.err.OperationalError(1213, "Deadlock"))
    with Session(db_engine) as db:
        record(db, expires_in=-timedelta(minutes=5))
        db.commit()
    real, calls = idempotency.purge_expired, []

    def flaky(**kwargs):
        calls.append(1)
        if len(calls) <= 2:
            raise deadlock
        return real(**kwargs)

    monkeypatch.setattr(idempotency, "purge_expired", flaky)
    monkeypatch.setattr(idempotency.time, "sleep", lambda _s: None)
    assert idempotency.run_idempotency_retention() == 1 and len(calls) == 3
    monkeypatch.setattr(idempotency, "purge_expired", lambda **_k: (_ for _ in ()).throw(deadlock))
    assert idempotency.run_idempotency_retention() == 0  # gives up quietly; next run retries


def test_purges_during_an_in_flight_request_keep_its_live_lease(db_engine, monkeypatch):
    """A request holds its key as PROCESSING (handler running) while three purges sweep 150
    expired records. Its record is made already past `expires_at` (worst case), so only the live
    lease protects it: it is not deleted, nothing deadlocks or waits out a lock timeout, and the
    request still completes and stores its response."""
    if db_engine.dialect.name != "mysql":
        pytest.skip("MySQL row locks")
    from types import SimpleNamespace

    from app.idempotency import IdempotentResult, run_idempotent

    monkeypatch.setattr(idempotency, "RETENTION_BATCH", 25)
    with Session(db_engine) as db:
        for _ in range(150):
            record(db, expires_in=-timedelta(minutes=5))
        db.commit()
    monkeypatch.setattr(idempotency, "RECORD_TTL", timedelta(seconds=-1))
    key, entered, release = str(uuid.uuid4()), threading.Event(), threading.Event()
    outcome, errors = {}, []

    def handler():
        entered.set()
        assert release.wait(30)
        return IdempotentResult(201, {"ok": True})

    def request():
        try:
            with Session(db_engine) as db:
                response = run_idempotent(db=db, principal=SimpleNamespace(google_sub="in-flight"), key=key,
                                          method="POST", path="/api/t/things", body={"a": 1}, handler=handler)
                outcome["status"] = response.status_code
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    def purge():
        try:
            started = time.monotonic()
            outcome.setdefault("purged", []).append(idempotency.run_idempotency_retention())
            outcome.setdefault("seconds", []).append(time.monotonic() - started)
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    caller = threading.Thread(target=request)
    caller.start()
    assert entered.wait(30)
    purgers = [threading.Thread(target=purge) for _ in range(3)]
    for thread in purgers:
        thread.start()
    for thread in purgers:
        thread.join(60)
    assert errors == [] and sum(outcome["purged"]) == 150
    assert max(outcome["seconds"]) < 10  # never waited on a lock (innodb_lock_wait_timeout 50 s)
    with Session(db_engine) as db:
        [row] = db.scalars(select(IdempotencyRecord)).all()
        assert (row.idempotency_key, row.state) == (key, "PROCESSING")
    release.set()
    caller.join(30)
    assert errors == [] and outcome["status"] == 201
    with Session(db_engine) as db:
        assert db.scalar(select(IdempotencyRecord.state)) == "COMPLETED"

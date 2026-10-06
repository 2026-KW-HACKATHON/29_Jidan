"""Background task runner: atomic enqueue, single application, retries, leases, recovery."""

import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.errors import AiError, AiErrorCode
from app.db import utcnow
from app.db.models import BackgroundTask
from app.tasks import TaskHandler, cancel_tasks, claim, drain, enqueue, recover_expired, run_claimed
from app.tasks import runner as runner_module

KIND = "QA_ANSWER"


class Domain:
    """A stand-in domain table: subject -> the task it waits for and what was applied."""

    def __init__(self):
        self.waiting: dict[str, str] = {}
        self.applied: list[tuple[str, object]] = []
        self.failed: list[tuple[str, str]] = []
        self.script: list[object] = []  # results or exceptions for execute, in order
        self.executions = 0
        self.apply_error: Exception | None = None

    def execute(self, ctx):
        self.executions += 1
        outcome = self.script.pop(0) if self.script else f"result-{ctx.payload['n']}"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def apply(self, db, ctx, result):
        ctx.ensure(self.waiting.get(ctx.subject_id) == ctx.task_id)
        if self.apply_error is not None:
            db.add(BackgroundTask(  # a write that must be rolled back with the failing apply
                kind=KIND, subject_id="x" * 36, attempt=1, status="QUEUED", tries=0,
                max_tries=1, payload={},
            ))
            db.flush()
            raise self.apply_error
        self.applied.append((ctx.subject_id, result))
        del self.waiting[ctx.subject_id]

    def fail(self, db, ctx, error):
        ctx.ensure(self.waiting.get(ctx.subject_id) == ctx.task_id)
        self.failed.append((ctx.subject_id, error.code.value if isinstance(error, AiError) else "internal"))
        del self.waiting[ctx.subject_id]


@pytest.fixture
def domain(db_engine):
    domain = Domain()
    handler = TaskHandler(kind=KIND, execute=domain.execute, apply=domain.apply, fail=domain.fail,
                          max_tries=3, lease_seconds=60, backoff_seconds=(5.0, 20.0))
    saved = runner_module.registered_handlers()
    with runner_module._handlers_lock:
        runner_module._handlers.clear()
        runner_module._handlers[KIND] = handler
    domain.engine = db_engine
    yield domain
    with runner_module._handlers_lock:
        runner_module._handlers.clear()
        runner_module._handlers.update(saved)


def submit(domain, subject="s-1", n=1, **kwargs) -> str:
    with Session(domain.engine) as db:
        task_id = enqueue(db, KIND, subject, {"n": n}, **kwargs)
        db.commit()
    domain.waiting[subject] = task_id
    return task_id


def task(domain, task_id) -> BackgroundTask:
    with Session(domain.engine) as db:
        return db.get(BackgroundTask, task_id)


def test_rolled_back_enqueue_leaves_no_task(domain):
    with Session(domain.engine) as db:
        enqueue(db, KIND, "s-1", {"n": 1})
        db.rollback()
        assert db.scalars(select(BackgroundTask)).all() == []
    assert drain() == []


def test_committed_task_runs_once_and_applies_once(domain):
    task_id = submit(domain)
    runs = drain()
    assert [(r.task_id, r.outcome) for r in runs] == [(task_id, "succeeded")]
    assert domain.applied == [("s-1", "result-1")]
    stored = task(domain, task_id)
    assert (stored.status, stored.tries, stored.lease_token) == ("SUCCEEDED", 1, None)
    assert stored.finished_at is not None and drain() == []


def test_retryable_failure_is_requeued_with_backoff_then_succeeds(domain):
    task_id = submit(domain)
    domain.script = [AiError(AiErrorCode.TIMEOUT)]
    now = utcnow()
    assert [r.outcome for r in drain(now=now)] == ["requeued"]
    assert task(domain, task_id).last_error_code == "TIMEOUT" and domain.applied == []
    assert drain(now=now + timedelta(seconds=4)) == []  # backoff not over
    assert [r.outcome for r in drain(now=now + timedelta(seconds=5))] == ["succeeded"]
    assert task(domain, task_id).tries == 2 and domain.applied == [("s-1", "result-1")]


def test_retries_are_bounded_then_the_domain_records_the_failure(domain):
    task_id = submit(domain)
    domain.script = [AiError(AiErrorCode.UNAVAILABLE)] * 3
    now = utcnow()
    outcomes = [r.outcome for minutes in (0, 1, 2) for r in drain(now=now + timedelta(minutes=minutes))]
    assert outcomes == ["requeued", "requeued", "failed"]
    stored = task(domain, task_id)
    assert (stored.status, stored.tries, stored.last_error_code) == ("FAILED", 3, "UNAVAILABLE")
    assert domain.failed == [("s-1", "unavailable")] and domain.applied == []


@pytest.mark.parametrize("code", [AiErrorCode.EMPTY_TRANSCRIPT, AiErrorCode.INPUT_REJECTED,
                                  AiErrorCode.NOT_CONFIGURED, AiErrorCode.REFUSED])
def test_non_retryable_failure_fails_at_once(domain, code):
    submit(domain)
    domain.script = [AiError(code)]
    assert [r.outcome for r in drain()] == ["failed"]
    assert domain.failed == [("s-1", code.value)] and domain.executions == 1


def test_unexpected_execute_exception_is_retried_as_internal(domain):
    task_id = submit(domain)
    domain.script = [RuntimeError("boom with secret")]
    assert drain()[0].error_code == "INTERNAL"
    assert task(domain, task_id).status == "QUEUED"


def test_superseded_task_result_is_not_applied(domain):
    old = submit(domain)
    domain.waiting["s-1"] = "another-task"  # e.g. a retry or a newer input replaced it
    runs = drain()
    assert [(r.task_id, r.outcome) for r in runs] == [(old, "cancelled")]
    assert domain.applied == [] and task(domain, old).status == "CANCELLED"


def test_failing_apply_rolls_back_its_writes_and_retries(domain):
    task_id = submit(domain)
    domain.apply_error = RuntimeError("apply crashed")
    run = drain()[0]
    assert (run.outcome, run.error_code) == ("requeued", "INTERNAL")
    with Session(domain.engine) as db:
        assert db.scalars(select(BackgroundTask.id)).all() == [task_id]  # the apply's insert is gone


def test_expired_lease_is_recovered_and_the_late_result_is_discarded(domain):
    task_id = submit(domain)
    now = utcnow()
    stale_claim = claim(now=now)[0]
    assert recover_expired(now=now + timedelta(seconds=59)) == 0
    assert recover_expired(now=now + timedelta(seconds=60)) == 1
    stored = task(domain, task_id)
    assert (stored.status, stored.last_error_code, stored.lease_token) == ("QUEUED", "LEASE_EXPIRED", None)
    assert run_claimed(stale_claim).outcome == "discarded"  # the stuck worker finally returns
    assert domain.applied == []
    assert [r.outcome for r in drain(now=now + timedelta(seconds=61))] == ["succeeded"]
    assert domain.applied == [("s-1", "result-1")] and task(domain, task_id).tries == 2


def test_expired_lease_without_tries_left_fails_the_domain(domain):
    task_id = submit(domain, max_tries=1)
    now = utcnow()
    claim(now=now)
    assert recover_expired(now=now + timedelta(minutes=5)) == 1
    assert task(domain, task_id).status == "FAILED" and domain.failed == [("s-1", "timeout")]


def test_cancelled_task_is_neither_run_nor_applied(domain):
    task_id = submit(domain)
    with Session(domain.engine) as db:
        assert cancel_tasks(db, KIND, "s-1") == 1
        db.commit()
    assert drain() == [] and task(domain, task_id).status == "CANCELLED"


def test_tasks_without_a_handler_in_this_process_are_left_alone(domain):
    with Session(domain.engine) as db:
        other = enqueue(db, "TRANSCRIPTION", "m-1", {})
        db.commit()
    assert drain() == [] and task(domain, other).status == "QUEUED"


def test_enqueue_validates_kind_and_payload(domain):
    with Session(domain.engine) as db:
        with pytest.raises(ValueError):
            enqueue(db, "UNKNOWN", "s", {})
        with pytest.raises(ValueError):
            enqueue(db, KIND, "s", {"blob": "x" * 1_000_001})
        with pytest.raises(TypeError):
            enqueue(db, KIND, "s", {"bytes": b"raw"})


def test_commit_wakes_the_runner_and_rollback_does_not(domain):
    runner_module._wake.clear()
    with Session(domain.engine) as db:
        enqueue(db, KIND, "s-1", {"n": 1})
        db.rollback()
    assert not runner_module._wake.is_set()
    with Session(domain.engine) as db:
        enqueue(db, KIND, "s-1", {"n": 1})
        db.commit()
    assert runner_module._wake.is_set()


def test_concurrent_claims_lease_each_task_exactly_once(domain):
    for n in range(6):
        submit(domain, subject=f"s-{n}", n=n)
    claimed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def worker():
        barrier.wait()
        for _ in range(6):
            for item in claim(limit=1):
                with lock:
                    claimed.append(item.context.task_id)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(claimed) == len(set(claimed)) == 6


def test_background_runner_executes_committed_tasks(domain):
    background = runner_module.BackgroundRunner(workers=2, poll_seconds=0.05)
    background.start()
    try:
        task_id = submit(domain)
        deadline = time.monotonic() + 10
        while task(domain, task_id).status != "SUCCEEDED" and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        background.stop()
    assert task(domain, task_id).status == "SUCCEEDED" and domain.applied == [("s-1", "result-1")]


def test_handler_limits_are_validated():
    with pytest.raises(ValueError):
        TaskHandler(kind="NOPE", execute=print, apply=print, fail=print)
    with pytest.raises(ValueError):
        TaskHandler(kind=KIND, execute=print, apply=print, fail=print, max_tries=0)


@pytest.mark.parametrize(("background", "mode", "starts"), [
    ("off", "background", False), ("off", "manual", False), ("on", "manual", False), ("on", "background", True),
])
def test_runner_threads_follow_background_jobs_and_runner_mode(monkeypatch, background, mode, starts):
    import asyncio

    from app import lifespan as app_lifespan

    events = []

    class FakeRunner:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(runner_module, "BackgroundRunner", FakeRunner)
    monkeypatch.setenv("BACKGROUND_JOBS", background)
    monkeypatch.setenv("TASK_RUNNER_MODE", mode)

    async def serve():
        async with app_lifespan.ai_task_runner_lifespan(None):
            events.append("serving")

    asyncio.run(serve())
    assert events == (["start", "serving", "stop"] if starts else ["serving"])


def test_runner_and_media_retention_are_registered_once(monkeypatch):
    from app import lifespan as app_lifespan
    from app.media import retention

    assert app_lifespan.ai_task_runner_lifespan in app_lifespan.LIFESPANS
    assert [job.run for job in app_lifespan.PERIODIC_JOBS].count(retention.run_retention) == 1
    monkeypatch.setenv("TASK_RUNNER_MODE", "sometimes")
    with pytest.raises(ValueError):
        app_lifespan.validate_background_settings()

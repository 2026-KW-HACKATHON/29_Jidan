"""Task leases around provider calls longer than the lease (heartbeat, crash recovery, limits).

The scenarios scale the production numbers (lease 300 s, recovery every 30 s, provider timeout up
to 600 s) down to a lease of about a second so they run in real time: a provider call that takes
several leases, a second worker recovering expired leases in a tight loop, and a worker process
killed in the middle of a call.
"""

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.ai.contracts import TranscriptionRequest
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.ai.provider import FallbackAiProvider
from app.db import utcnow
from app.db.models import BackgroundTask
from app.tasks import TaskHandler, claim, drain, enqueue, recover_expired, run_claimed
from app.tasks import runner as runner_module

KIND = "TRANSCRIPTION"
BACK_END = Path(__file__).resolve().parents[1]


class SlowProvider:
    """A handler around a FakeAiProvider whose call outlives the lease; counts calls per task."""

    def __init__(self, call_seconds: float):
        self.provider = FakeAiProvider(timeout_seconds=600)  # provider timeout far above the lease
        self.call_seconds = call_seconds
        self.calls: Counter[str] = Counter()
        self.applied: list[tuple[str, str]] = []
        self.lock = threading.Lock()

    def execute(self, ctx):
        with self.lock:
            self.calls[ctx.task_id] += 1
        self.provider.script("transcribe", FakeOutcome.delay(self.call_seconds))
        return self.provider.transcribe(TranscriptionRequest(audio=b"\0", mime_type="audio/wav")).text

    def apply(self, db, ctx, result):
        with self.lock:
            self.applied.append((ctx.task_id, result))

    def fail(self, db, ctx, error):  # pragma: no cover - not reached in these scenarios
        raise AssertionError(f"unexpected failure {error!r}")


@pytest.fixture
def handlers():
    saved = runner_module.registered_handlers()

    def install(handler: TaskHandler):
        with runner_module._handlers_lock:
            runner_module._handlers.clear()
            runner_module._handlers[handler.kind] = handler

    yield install
    with runner_module._handlers_lock:
        runner_module._handlers.clear()
        runner_module._handlers.update(saved)


def submit(engine) -> str:
    with Session(engine) as db:
        task_id = enqueue(db, KIND, "subject-1", {"n": 1})
        db.commit()
    return task_id


def stored(engine, task_id) -> BackgroundTask:
    with Session(engine) as db:
        return db.get(BackgroundTask, task_id)


def test_long_call_keeps_its_lease_while_a_second_worker_recovers(db_engine, handlers):
    """Lease 0.9 s, call 3 s (> 3 leases), worker B recovering and claiming every 50 ms."""
    slow = SlowProvider(call_seconds=3.0)
    handlers(TaskHandler(kind=KIND, execute=slow.execute, apply=slow.apply, fail=slow.fail,
                         max_tries=3, lease_seconds=0.9, backoff_seconds=(0.1,)))
    task_id = submit(db_engine)
    owners: list[tuple[str, str]] = []  # (worker, lease token) in claim order
    outcomes: dict[str, list[str]] = {"A": [], "B": []}
    done = threading.Event()

    first = claim(limit=1)
    owners.append(("A", first[0].lease_token))

    def worker_a():
        outcomes["A"].append(run_claimed(first[0]).outcome)
        done.set()

    def worker_b():
        recovered = 0
        while not done.is_set():
            recovered += recover_expired()
            for item in claim(limit=1):
                owners.append(("B", item.lease_token))
                outcomes["B"].append(run_claimed(item).outcome)
            time.sleep(0.05)
        outcomes["B"].append(f"recovered={recovered}")

    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert dict(slow.calls) == {task_id: 1}, (owners, outcomes)
    assert [worker for worker, _ in owners] == ["A"]
    assert outcomes == {"A": ["succeeded"], "B": ["recovered=0"]}
    assert slow.applied == [(task_id, "테스트 전사 결과예요.")]
    row = stored(db_engine, task_id)
    assert (row.status, row.tries, row.lease_token) == ("SUCCEEDED", 1, None)


def test_renewal_moves_the_lease_and_fails_once_ownership_is_lost(db_engine, handlers):
    """Deterministic clock: claim at t0 (lease 300 s), heartbeat at t0+200 s, recovery checks."""
    slow = SlowProvider(call_seconds=0)
    handlers(TaskHandler(kind=KIND, execute=slow.execute, apply=slow.apply, fail=slow.fail,
                         max_tries=3, lease_seconds=300))
    task_id = submit(db_engine)
    t0 = utcnow()
    held = claim(now=t0)[0]
    assert runner_module.renew_lease(held, now=t0 + timedelta(seconds=200)) is True
    assert recover_expired(now=t0 + timedelta(seconds=301)) == 0  # would have expired without it
    assert recover_expired(now=t0 + timedelta(seconds=500)) == 1  # heartbeats stopped (crash)
    assert runner_module.renew_lease(held, now=t0 + timedelta(seconds=501)) is False
    other = claim(now=t0 + timedelta(seconds=501))[0]
    assert other.lease_token != held.lease_token
    assert runner_module.renew_lease(held, now=t0 + timedelta(seconds=502)) is False
    assert run_claimed(held).outcome == "discarded"
    assert run_claimed(other).outcome == "succeeded"
    assert stored(db_engine, task_id).tries == 2


def test_lost_lease_is_seen_by_the_heartbeat_and_the_late_result_is_discarded(db_engine, handlers):
    """The heartbeat cannot stop a call already sent, but it reports the loss and nothing applies."""
    lost = threading.Event()
    slow = SlowProvider(call_seconds=0)
    task_ids: list[str] = []

    def execute(ctx):
        # Another worker takes the task over while this call is still running.
        with Session(db_engine) as db:
            row = db.get(BackgroundTask, ctx.task_id)
            row.lease_token = "x" * 36
            db.commit()
        deadline = time.monotonic() + 5
        while not ctx.lease_lost() and time.monotonic() < deadline:
            time.sleep(0.02)
        if ctx.lease_lost():
            lost.set()
        return slow.execute(ctx)

    handlers(TaskHandler(kind=KIND, execute=execute, apply=slow.apply, fail=slow.fail,
                         max_tries=3, lease_seconds=0.3))
    task_ids.append(submit(db_engine))
    run = drain()
    assert lost.is_set()
    assert [r.outcome for r in run] == ["discarded"] and slow.applied == []


def test_handler_lease_must_cover_its_provider_calls(handlers):
    def handler(lease, calls=1):
        return TaskHandler(kind=KIND, execute=print, apply=print, fail=print,
                           lease_seconds=lease, provider_calls=calls)

    fast = FakeAiProvider(timeout_seconds=60)
    handlers(handler(300))
    runner_module.validate_task_leases(fast)  # 60 s + margin < 300 s
    handlers(handler(300, calls=5))
    with pytest.raises(ValueError, match="TRANSCRIPTION"):
        runner_module.validate_task_leases(fast)  # 5 x 60 s + 30 s margin > 300 s
    handlers(handler(300))
    with pytest.raises(ValueError):
        runner_module.validate_task_leases(FakeAiProvider(timeout_seconds=600))
    with pytest.raises(ValueError):  # a fallback model doubles the worst case: 2 x 150 s
        runner_module.validate_task_leases(FallbackAiProvider(
            FakeAiProvider(timeout_seconds=150), FakeAiProvider(timeout_seconds=150)))
    handlers(handler(1300))
    runner_module.validate_task_leases(FallbackAiProvider(
        FakeAiProvider(timeout_seconds=600), FakeAiProvider(timeout_seconds=600)))
    with pytest.raises(ValueError):
        TaskHandler(kind=KIND, execute=print, apply=print, fail=print, provider_calls=0)


def test_default_settings_pass_the_lease_check(monkeypatch):
    import app.tasks.handlers  # noqa: F401 - the production handlers
    from app.ai import build_provider_from_env

    for name in ("OPENAI_TIMEOUT_SECONDS", "OPENAI_TRANSCRIBE_TIMEOUT_SECONDS", "OPENAI_FALLBACK_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    runner_module.validate_task_leases(build_provider_from_env())
    monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "gpt-other")
    runner_module.validate_task_leases(build_provider_from_env())  # 2 x 120 s + 30 s < 300 s
    monkeypatch.setenv("OPENAI_TRANSCRIBE_TIMEOUT_SECONDS", "600")
    with pytest.raises(ValueError):
        runner_module.validate_task_leases(build_provider_from_env())


CRASHING_WORKER = textwrap.dedent("""
    import sys, time
    from app.tasks import TaskHandler, claim, register_handler, run_claimed

    calls = sys.argv[1]

    def execute(ctx):
        with open(calls, "a") as out:
            out.write(f"killed-worker {ctx.task_id}\\n")
        time.sleep(60)  # a provider call that never returns before the process is killed

    register_handler(TaskHandler(kind="TRANSCRIPTION", execute=execute, apply=print, fail=print,
                                 lease_seconds=1.5))
    run_claimed(claim(limit=1)[0])
""")


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)  # a second process: MySQL only
def test_killed_worker_is_recovered_after_its_last_heartbeat(db_engine, handlers, tmp_path):
    calls = tmp_path / "calls.txt"
    calls.touch()
    script = tmp_path / "worker.py"
    script.write_text(CRASHING_WORKER)
    slow = SlowProvider(call_seconds=0)
    handlers(TaskHandler(kind=KIND, execute=slow.execute, apply=slow.apply, fail=slow.fail,
                         lease_seconds=1.5))
    task_id = submit(db_engine)
    process = subprocess.Popen([sys.executable, str(script), str(calls)], cwd=BACK_END,
                               env={**os.environ, "PYTHONPATH": str(BACK_END)})
    try:
        deadline = time.monotonic() + 30
        while not calls.read_text() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls.read_text(), "the worker process never started the call"
        time.sleep(2.5)  # longer than the 1.5 s lease: the live worker's heartbeat keeps it
        assert recover_expired() == 0
        assert stored(db_engine, task_id).status == "RUNNING"
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(10)

    deadline = time.monotonic() + 10
    recovered = 0
    while not recovered and time.monotonic() < deadline:
        recovered = recover_expired()
        time.sleep(0.1)
    assert recovered == 1  # the dead worker's lease ran out after its last heartbeat
    assert [r.outcome for r in drain()] == ["succeeded"]
    # A crash after the provider accepted the call is re-run: at-least-once, never twice at once.
    assert calls.read_text().splitlines() == [f"killed-worker {task_id}"]
    assert dict(slow.calls) == {task_id: 1}
    row = stored(db_engine, task_id)
    assert (row.status, row.tries, row.last_error_code) == ("SUCCEEDED", 2, "LEASE_EXPIRED")

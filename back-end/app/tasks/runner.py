"""DB-backed task runner for AI/STT work that must not run inside a request transaction.

Lifecycle of a task (`background_tasks` row):

    enqueue (request tx) --commit--> QUEUED --claim--> RUNNING (lease) --execute (no tx)-->
        finalize tx: lock task, verify lease -> handler.apply (domain guard) -> SUCCEEDED
                                             -> retryable error: QUEUED again after backoff
                                             -> terminal error: handler.fail -> FAILED
        domain row moved on (StaleTask) -> CANCELLED; lease lost -> result discarded

* Enqueue happens in the caller's transaction, so the domain state change and the task commit
  together (or not at all). A committed enqueue wakes the background runner.
* `execute` runs with no DB transaction open and may block on the network (provider timeouts
  bound it). `apply`/`fail` run in one short transaction inside a savepoint; they must lock the
  domain row and call `ctx.ensure(...)` on "this row still waits for this task" (task ID,
  RUNNING state, input revision). That is the single place where results become visible, so a
  late, duplicated or superseded result can never overwrite newer state.
* While `execute` runs, a heartbeat thread renews the lease every `lease_seconds / 3` with an
  UPDATE conditioned on the lease token (`renew_lease`). A long provider call therefore keeps its
  task; only a dead process (no more heartbeats) lets the lease run out. A failed renewal means
  another worker owns the task: `ctx.lease_lost()` turns true and the result is discarded at
  finalize. A call already sent cannot be taken back, so ownership is checked before applying,
  never assumed from having started.
* A RUNNING task whose lease expired (process crash or a stuck call) is re-queued while tries
  remain, otherwise failed through `handler.fail`. A late finisher then fails the lease check.
  Recovery after a crash re-runs the call (at-least-once); two live workers never run it at once.
* `validate_task_leases(provider)` (runner startup) also refuses a lease shorter than the
  handler's worst-case provider time (`provider_calls` x the provider's per-call bound + a
  margin), so even with no heartbeat at all (database unreachable meanwhile) a live call ends
  before its lease does.
* `TASK_RUNNER_MODE=manual` (tests) starts no threads; call `drain()` to run due tasks
  synchronously in the calling thread.
"""

import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.ai.errors import AiError, AiErrorCode
from app.db import new_uuid, session_scope, utcnow
from app.db.availability import is_unavailable
from app.db.keyed import update_by_key
from app.db.models import TASK_KINDS, BackgroundTask
from app.errors import ServiceUnavailable

logger = logging.getLogger("jidan.tasks")

DEFAULT_MAX_TRIES = 3
DEFAULT_LEASE_SECONDS = 300.0
DEFAULT_BACKOFF = (2.0, 10.0, 30.0)
MAX_PAYLOAD_BYTES = 1_000_000
RECOVERY_INTERVAL_SECONDS = 30.0
HEARTBEATS_PER_LEASE = 3
# Time a task needs besides its provider calls (DB reads, storage, finalize) in the lease check.
LEASE_MARGIN_SECONDS = 30.0
INTERNAL_ERROR = "INTERNAL"
LEASE_EXPIRED = "LEASE_EXPIRED"
STALE = "STALE"
DEFERRED = "DEFERRED"
MAX_DEFERRAL = timedelta(minutes=30)  # a task waits for its inputs at most this long


class StaleTask(Exception):
    """Raised by `ctx.ensure` when the domain row no longer waits for this task."""


class TaskDeferred(Exception):
    """Raised by `execute` before any external call when an input the task must read is not
    ready yet (e.g. another summary it depends on is still being generated). The task goes back
    to QUEUED after `seconds` without using one of its tries. The dependency must be one that
    always ends (its own task succeeds, fails or is recovered). As a safety net, a task still
    deferring `MAX_DEFERRAL` after it was enqueued is handled as a TIMEOUT failure instead.
    Deferral happens before any provider call; the lease heartbeat stops with `execute` and the
    finalize step clears the lease as for any re-queue."""

    def __init__(self, seconds: float = 5.0):
        super().__init__("deferred")
        self.seconds = seconds


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    kind: str
    subject_id: str
    attempt: int
    input_revision: int | None
    payload: Any
    tries: int
    _lost: threading.Event = field(default_factory=threading.Event, compare=False, repr=False)

    def ensure(self, condition: bool) -> None:
        """Abort apply/fail without touching anything when the domain row moved on."""
        if not condition:
            raise StaleTask(self.task_id)

    def lease_lost(self) -> bool:
        """True once a heartbeat found another owner; an execute with several provider calls
        should stop before the next one (its result would be discarded anyway)."""
        return self._lost.is_set()


@dataclass(frozen=True)
class TaskHandler:
    """How one task kind runs.

    execute(ctx) -> result            outside any transaction; raise AiError (classified) on failure
    apply(db, ctx, result)            lock the domain row, ctx.ensure(...), write the result
    fail(db, ctx, error)              lock the domain row, ctx.ensure(...), record a public ERROR
    cancel(db, ctx)                   optional cleanup after stale savepoint rollback or explicit cancel;
                                     must preserve any successor's resources

    provider_calls: the most AI/STT calls one execute makes in sequence (for the lease check).
    """

    kind: str
    execute: Callable[[TaskContext], Any]
    apply: Callable[[Session, TaskContext, Any], None]
    fail: Callable[[Session, TaskContext, Exception], None]
    max_tries: int = DEFAULT_MAX_TRIES
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF
    provider_calls: int = 1
    cancel: Callable[[Session, TaskContext], None] | None = None

    def __post_init__(self) -> None:
        if self.kind not in TASK_KINDS:
            raise ValueError(f"unknown task kind {self.kind!r}")
        if (self.max_tries < 1 or self.lease_seconds <= 0 or not self.backoff_seconds
                or self.provider_calls < 1):
            raise ValueError("invalid task handler limits")


_handlers: dict[str, TaskHandler] = {}
_handlers_lock = threading.Lock()
_wake = threading.Event()


def register_handler(handler: TaskHandler, *, replace: bool = False) -> None:
    with _handlers_lock:
        if handler.kind in _handlers and not replace and _handlers[handler.kind] is not handler:
            raise ValueError(f"a handler for {handler.kind} is already registered")
        _handlers[handler.kind] = handler


def unregister_handler(kind: str) -> None:
    with _handlers_lock:
        _handlers.pop(kind, None)


def registered_handlers() -> dict[str, TaskHandler]:
    with _handlers_lock:
        return dict(_handlers)


def wake_runner() -> None:
    _wake.set()


@event.listens_for(Session, "after_commit")
def _wake_after_commit(session: Session) -> None:
    if session.info.pop("jidan_tasks_enqueued", False):
        _wake.set()


@event.listens_for(Session, "after_rollback")
def _forget_after_rollback(session: Session) -> None:
    session.info.pop("jidan_tasks_enqueued", None)


class TaskQueueUnavailable(ServiceUnavailable):
    """The task INSERT itself failed for availability (connection lost, server gone, table or
    disk full): 503 JOB_QUEUE_UNAVAILABLE, and the caller's transaction rolls back with it."""

    boundary = "task-queue"


def enqueue(
    db: Session,
    kind: str,
    subject_id: str,
    payload: Any,
    *,
    input_revision: int | None = None,
    attempt: int = 1,
    delay_seconds: float = 0.0,
    max_tries: int | None = None,
) -> str:
    """Add a task in the caller's transaction and return its task ID (store it on the domain
    row). Nothing runs until the caller commits; a rollback discards the task with the rest.

    Raises TaskQueueUnavailable only for an OperationalError of the task's own INSERT whose
    driver code means the database is unavailable (`app.db.availability`). The caller's pending
    rows are flushed first and outside that boundary, so their errors (and lock waits or
    deadlocks anywhere, which callers retry or answer as 409) keep their meaning."""
    if kind not in TASK_KINDS:
        raise ValueError(f"unknown task kind {kind!r}")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("task payload is too large; store large inputs in their own rows")
    handler = registered_handlers().get(kind)
    now = utcnow()
    task = BackgroundTask(
        id=new_uuid(), kind=kind, subject_id=subject_id, input_revision=input_revision,
        attempt=attempt, status="QUEUED", tries=0,
        max_tries=max_tries or (handler.max_tries if handler else DEFAULT_MAX_TRIES),
        payload=json.loads(encoded), available_at=now + timedelta(seconds=delay_seconds),
        created_at=now,
    )
    db.flush()  # the caller's rows: not the queue's to report
    db.add(task)
    try:
        db.flush()
    except OperationalError as error:
        # By driver error code: lock waits, CHECK violations (3819) and the rest stay as they are.
        if is_unavailable(error):
            raise TaskQueueUnavailable() from error
        raise
    db.info["jidan_tasks_enqueued"] = True
    return task.id


def cancel_tasks(db: Session, kind: str, subject_id: str) -> int:
    """Cancel queued/running tasks of a subject in the caller's transaction (superseded work).
    A running execution finishes but its result is discarded by the lease check."""
    # Plain read, then primary-key updates: a range UPDATE on the (kind, subject) index would
    # gap-lock other subjects' task INSERTs. Callers hold the subject's own lock while they
    # enqueue, so no task of this subject appears between the read and the updates.
    now = utcnow()
    live = ("QUEUED", "RUNNING")
    ids = list(db.scalars(select(BackgroundTask.id).where(
        BackgroundTask.kind == kind, BackgroundTask.subject_id == subject_id, BackgroundTask.status.in_(live))))
    handler = registered_handlers().get(kind)
    changed = 0
    for task_id in sorted(ids):
        count = update_by_key(db, BackgroundTask, [task_id], {
            "status": "CANCELLED", "finished_at": now, "lease_token": None, "lease_expires_at": None,
            "last_error_code": STALE,
        }, BackgroundTask.status.in_(live))
        changed += count
        if count and handler is not None and handler.cancel is not None:
            task = db.get(BackgroundTask, task_id, populate_existing=True)
            handler.cancel(db, _context(task))
    return changed


@dataclass(frozen=True)
class ClaimedTask:
    context: TaskContext
    lease_token: str


@dataclass(frozen=True)
class TaskRun:
    task_id: str
    kind: str
    outcome: str  # succeeded | requeued | deferred | failed | cancelled | discarded
    error_code: str | None = None


def _context(task: BackgroundTask) -> TaskContext:
    return TaskContext(
        task_id=task.id, kind=task.kind, subject_id=task.subject_id, attempt=task.attempt,
        input_revision=task.input_revision, payload=task.payload, tries=task.tries,
    )


def claim(*, now: datetime | None = None, limit: int = 1, kinds: tuple[str, ...] | None = None) -> list[ClaimedTask]:
    """Lease up to `limit` due QUEUED tasks whose kind has a handler in this process."""
    handlers = registered_handlers()
    wanted = tuple(kind for kind in (kinds or tuple(handlers)) if kind in handlers)
    if not wanted or limit < 1:
        return []
    now = now or utcnow()
    claimed: list[ClaimedTask] = []
    with session_scope() as db:
        candidates = db.scalars(
            select(BackgroundTask)
            .where(BackgroundTask.status == "QUEUED", BackgroundTask.available_at <= now,
                   BackgroundTask.kind.in_(wanted))
            .order_by(BackgroundTask.available_at, BackgroundTask.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for task in candidates:
            token = new_uuid()
            lease = now + timedelta(seconds=handlers[task.kind].lease_seconds)
            taken = db.execute(
                update(BackgroundTask)
                .where(BackgroundTask.id == task.id, BackgroundTask.status == "QUEUED")
                .values(status="RUNNING", lease_token=token, lease_expires_at=lease,
                        tries=BackgroundTask.tries + 1, started_at=task.started_at or now)
                .execution_options(synchronize_session=False)
            ).rowcount
            if taken == 1:
                db.refresh(task)
                claimed.append(ClaimedTask(_context(task), token))
    return claimed


def renew_lease(claimed: ClaimedTask, *, now: datetime | None = None) -> bool:
    """Extend a held lease to `now + lease_seconds` (never shorten it). False when the task is no
    longer RUNNING under this lease token: another worker or a cancel took it over."""
    handler = registered_handlers().get(claimed.context.kind)
    if handler is None:
        return False
    until = (now or utcnow()) + timedelta(seconds=handler.lease_seconds)
    with session_scope() as db:
        return db.execute(
            update(BackgroundTask)
            .where(BackgroundTask.id == claimed.context.task_id, BackgroundTask.status == "RUNNING",
                   BackgroundTask.lease_token == claimed.lease_token)
            .values(lease_expires_at=case((BackgroundTask.lease_expires_at < until, until),
                                          else_=BackgroundTask.lease_expires_at))
            .execution_options(synchronize_session=False)
        ).rowcount == 1


class _Heartbeat:
    """Renews a lease in its own thread while `execute` runs (see module docstring)."""

    def __init__(self, claimed: ClaimedTask, interval: float):
        self.claimed = claimed
        self.interval = interval
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"jidan-task-heartbeat-{claimed.context.task_id[:8]}")

    def __enter__(self) -> None:
        self.thread.start()

    def __exit__(self, *_exc) -> None:
        self.stop.set()
        self.thread.join(self.interval + 5)

    def _run(self) -> None:
        ctx = self.claimed.context
        while not self.stop.wait(self.interval):
            try:
                held = renew_lease(self.claimed)
            except Exception as error:  # noqa: BLE001 - try again next beat; finalize re-checks
                logger.warning("task heartbeat failed kind=%s task=%s error=%s", ctx.kind,
                               ctx.task_id, type(error).__name__)
                continue
            if not held:
                ctx._lost.set()
                logger.warning("task lease lost during execution kind=%s task=%s", ctx.kind, ctx.task_id)
                return


def validate_task_leases(provider=None) -> None:
    """Refuse a handler lease that a live call could outlive even without heartbeats.

    The bound is `provider_calls x provider.max_call_seconds + LEASE_MARGIN_SECONDS` per claim:
    the SDK makes no retries of its own (runner retries are new claims with new leases), and a
    fallback model counts as a second call. Raises ValueError naming the kind and the numbers.
    """
    if provider is None:
        from app.ai import get_ai_provider

        provider = get_ai_provider()
    per_call = provider.max_call_seconds
    for kind, handler in sorted(registered_handlers().items()):
        needed = handler.provider_calls * per_call + LEASE_MARGIN_SECONDS
        if per_call and handler.lease_seconds < needed:
            raise ValueError(
                f"{kind} lease {handler.lease_seconds:g}s is shorter than its worst-case provider "
                f"time {needed:g}s ({handler.provider_calls} call(s) x {per_call:g}s + "
                f"{LEASE_MARGIN_SECONDS:g}s); lower the OPENAI_*TIMEOUT_SECONDS settings"
            )


def _locked_running_task(db: Session, claimed: ClaimedTask) -> BackgroundTask | None:
    task = db.execute(
        select(BackgroundTask).where(BackgroundTask.id == claimed.context.task_id).with_for_update()
    ).scalar_one_or_none()
    if task is None or task.status != "RUNNING" or task.lease_token != claimed.lease_token:
        return None
    return task


def _close(task: BackgroundTask, status: str, now: datetime, error_code: str | None = None) -> None:
    task.status = status
    task.finished_at = now
    task.lease_token = None
    task.lease_expires_at = None
    if error_code is not None:
        task.last_error_code = error_code


def task_error_code(error: Exception) -> str:
    """The stored failure code: the upper-cased AiErrorCode, or INTERNAL for anything else
    (one of app.db.models.TASK_ERROR_CODES; domain tables may store it as is)."""
    return error.code.value.upper() if isinstance(error, AiError) else INTERNAL_ERROR


def _is_retryable(error: Exception) -> bool:
    # Unclassified exceptions (a transient DB read in execute, a bug) get the bounded retries
    # too; a deterministic bug just exhausts them and ends as a public ERROR.
    return error.retryable if isinstance(error, AiError) else True


def _finish_failure(db: Session, task: BackgroundTask, handler: TaskHandler, ctx: TaskContext,
                    error: Exception, now: datetime) -> TaskRun:
    code = task_error_code(error)
    if _is_retryable(error) and task.tries < task.max_tries:
        backoff = handler.backoff_seconds[min(task.tries - 1, len(handler.backoff_seconds) - 1)]
        task.status = "QUEUED"
        task.lease_token = None
        task.lease_expires_at = None
        task.available_at = now + timedelta(seconds=backoff)
        task.last_error_code = code
        return TaskRun(task.id, task.kind, "requeued", code)
    try:
        with db.begin_nested():
            handler.fail(db, ctx, error)
    except StaleTask:
        if handler.cancel is not None:
            handler.cancel(db, ctx)  # outside the rolled-back domain savepoint
        _close(task, "CANCELLED", now, STALE)
        return TaskRun(task.id, task.kind, "cancelled", STALE)
    _close(task, "FAILED", now, code)
    return TaskRun(task.id, task.kind, "failed", code)


def run_claimed(claimed: ClaimedTask, *, now: datetime | None = None) -> TaskRun:
    """Execute a claimed task and finalize it. Never raises for task failures."""
    ctx = claimed.context
    handler = registered_handlers().get(ctx.kind)
    if handler is None:  # cannot happen for tasks claimed by this process
        raise RuntimeError(f"no handler for {ctx.kind}")
    started = time.monotonic()
    result: Any = None
    error: Exception | None = None
    try:
        with _Heartbeat(claimed, handler.lease_seconds / HEARTBEATS_PER_LEASE):
            result = handler.execute(ctx)
    except Exception as caught:  # noqa: BLE001 - every failure is classified and recorded
        error = caught
    try:
        run = _finalize(claimed, handler, result, error, now)
    except Exception as caught:  # noqa: BLE001 - apply/fail crashed: rolled back, retry as failure
        logger.error("task finalize failed kind=%s task=%s error=%s", ctx.kind, ctx.task_id,
                     type(caught).__name__)
        run = _finalize(claimed, handler, None, caught if error is None else error, now, skip_apply=True)
    logger.info("task kind=%s task=%s attempt=%d try=%d outcome=%s error=%s ms=%d", ctx.kind,
                ctx.task_id, ctx.attempt, ctx.tries, run.outcome, run.error_code or "-",
                int((time.monotonic() - started) * 1000))
    return run


def _finalize(claimed: ClaimedTask, handler: TaskHandler, result: Any, error: Exception | None,
              now: datetime | None, *, skip_apply: bool = False) -> TaskRun:
    ctx = claimed.context
    with session_scope() as db:
        now = now or utcnow()
        task = _locked_running_task(db, claimed)
        if task is None:
            return TaskRun(ctx.task_id, ctx.kind, "discarded")
        if isinstance(error, TaskDeferred) and not skip_apply and now - task.created_at > MAX_DEFERRAL:
            # Safety net: the input never became ready. End like a timeout (normal retries/fail).
            return _finish_failure(db, task, handler, ctx, AiError(AiErrorCode.TIMEOUT, detail=DEFERRED), now)
        if isinstance(error, TaskDeferred) and not skip_apply:
            task.status, task.lease_token, task.lease_expires_at = "QUEUED", None, None
            task.tries -= 1  # waiting for an input is not an attempt
            task.available_at = now + timedelta(seconds=error.seconds)
            task.last_error_code = DEFERRED
            return TaskRun(task.id, task.kind, "deferred", DEFERRED)
        if error is not None or skip_apply:
            return _finish_failure(db, task, handler, ctx, error or RuntimeError(), now)
        try:
            with db.begin_nested():
                handler.apply(db, ctx, result)
        except StaleTask:
            if handler.cancel is not None:
                handler.cancel(db, ctx)  # outside the rolled-back domain savepoint
            _close(task, "CANCELLED", now, STALE)
            return TaskRun(task.id, task.kind, "cancelled", STALE)
        _close(task, "SUCCEEDED", now)
        return TaskRun(task.id, task.kind, "succeeded")


def recover_expired(*, now: datetime | None = None, limit: int = 100) -> int:
    """Re-queue (or fail, when no tries remain) RUNNING tasks whose lease expired."""
    handlers = registered_handlers()
    if not handlers:
        return 0
    now = now or utcnow()
    recovered = 0
    with session_scope() as db:
        expired = db.scalars(
            select(BackgroundTask)
            .where(BackgroundTask.status == "RUNNING", BackgroundTask.lease_expires_at <= now,
                   BackgroundTask.kind.in_(tuple(handlers)))
            .order_by(BackgroundTask.lease_expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for task in expired:
            _finish_failure(db, task, handlers[task.kind], _context(task),
                            AiError(AiErrorCode.TIMEOUT, detail=LEASE_EXPIRED), now)
            if task.status == "QUEUED":
                task.available_at = now
                task.last_error_code = LEASE_EXPIRED
            recovered += 1
    if recovered:
        logger.warning("recovered %d expired task lease(s)", recovered)
    return recovered


def drain(*, now: datetime | None = None, max_tasks: int = 100,
          kinds: tuple[str, ...] | None = None) -> list[TaskRun]:
    """Run due tasks one by one in this thread until none is due (tests, scripts).

    Tasks re-queued with a backoff are not due again until `now` passes their time, so pass a
    later `now` to run the retry."""
    runs: list[TaskRun] = []
    while len(runs) < max_tasks:
        claimed = claim(now=now, limit=1, kinds=kinds)
        if not claimed:
            break
        runs.append(run_claimed(claimed[0], now=now))
    return runs


# --- background mode ---------------------------------------------------------------------------


@dataclass
class BackgroundRunner:
    workers: int = 2
    poll_seconds: float = 2.0
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _executor: ThreadPoolExecutor | None = None
    _inflight: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if type(self.workers) is not int or self.workers <= 0:
            raise ValueError("TASK_RUNNER_WORKERS must be a positive integer")
        if not math.isfinite(self.poll_seconds) or self.poll_seconds <= 0:
            raise ValueError("TASK_RUNNER_POLL_SECONDS must be finite and positive")

    def start(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=self.workers + 1, thread_name_prefix="jidan-task")
        self._thread = threading.Thread(target=self._loop, name="jidan-task-dispatcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        _wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        if self._executor is not None:
            # Running calls finish on their own; their leases expire if the process exits first.
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _done(self, _future) -> None:
        with self._lock:
            self._inflight -= 1
        _wake.set()

    def _loop(self) -> None:
        next_recovery = 0.0
        while not self._stop.is_set():
            wait = self.poll_seconds
            try:
                clock = time.monotonic()
                if clock >= next_recovery:
                    recover_expired()
                    next_recovery = clock + RECOVERY_INTERVAL_SECONDS
                with self._lock:
                    free = self.workers - self._inflight
                for claimed in claim(limit=free) if free > 0 else []:
                    with self._lock:
                        self._inflight += 1
                    self._executor.submit(run_claimed, claimed).add_done_callback(self._done)
            except Exception as error:  # noqa: BLE001 - keep polling; never log payloads or SQL
                logger.error("task dispatcher poll failed error=%s", type(error).__name__)
                wait = max(self.poll_seconds, 30.0)
            _wake.wait(wait)
            _wake.clear()


def runner_mode() -> str:
    mode = os.getenv("TASK_RUNNER_MODE", "background").strip().lower() or "background"
    if mode not in ("background", "manual"):
        raise ValueError("TASK_RUNNER_MODE must be background or manual")
    return mode


def runner_settings() -> BackgroundRunner:
    try:
        workers = int(os.getenv("TASK_RUNNER_WORKERS", "2"))
    except ValueError:
        raise ValueError("TASK_RUNNER_WORKERS must be a positive integer") from None
    try:
        poll_seconds = float(os.getenv("TASK_RUNNER_POLL_SECONDS", "2"))
    except ValueError:
        raise ValueError("TASK_RUNNER_POLL_SECONDS must be finite and positive") from None
    return BackgroundRunner(workers=workers, poll_seconds=poll_seconds)


@asynccontextmanager
async def task_runner_lifespan(_app):
    """Start the background runner with the app (no-op in manual mode)."""
    if runner_mode() == "manual":
        yield
        return
    import app.tasks.handlers  # noqa: F401 - registers every task handler

    validate_task_leases()
    runner = runner_settings()
    runner.start()
    try:
        yield
    finally:
        runner.stop()

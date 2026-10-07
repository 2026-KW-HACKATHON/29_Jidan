"""TaskDeferred: waiting for an input re-queues without using a try, at most MAX_DEFERRAL long."""

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import BackgroundTask
from app.tasks import TaskDeferred, TaskHandler, drain, enqueue
from app.tasks import runner as runner_module
from tests.test_tasks import KIND, Domain


@pytest.fixture
def waiting(db_engine):
    domain = Domain()
    handler = TaskHandler(kind=KIND, execute=domain.execute, apply=domain.apply, fail=domain.fail,
                          max_tries=2, lease_seconds=60, backoff_seconds=(5.0,))
    saved = runner_module.registered_handlers()
    with runner_module._handlers_lock:
        runner_module._handlers.clear()
        runner_module._handlers[KIND] = handler
    domain.engine = db_engine
    with Session(db_engine) as db:
        domain.waiting["s-1"] = enqueue(db, KIND, "s-1", {"n": 1})
        db.commit()
    yield domain
    with runner_module._handlers_lock:
        runner_module._handlers.clear()
        runner_module._handlers.update(saved)


def row(domain) -> BackgroundTask:
    with Session(domain.engine) as db:
        return db.get(BackgroundTask, domain.waiting["s-1"])


def test_deferral_requeues_without_using_a_try(waiting):
    waiting.script = [TaskDeferred(7.0), TaskDeferred(7.0)]
    start = utcnow()
    assert [r.outcome for r in drain(now=start)] == ["deferred"]
    task = row(waiting)
    assert (task.status, task.tries, task.last_error_code, task.lease_token) == ("QUEUED", 0, "DEFERRED", None)
    assert task.available_at == start + timedelta(seconds=7)
    assert [r.outcome for r in drain(now=start + timedelta(seconds=8))] == ["deferred"]
    assert [r.outcome for r in drain(now=start + timedelta(seconds=16))] == ["succeeded"]
    assert waiting.applied == [("s-1", "result-1")] and waiting.executions == 3


def test_deferring_past_the_limit_ends_as_a_timeout(waiting):
    task_id = waiting.waiting["s-1"]
    waiting.script = [TaskDeferred(1.0)] * 5
    late = utcnow() + runner_module.MAX_DEFERRAL + timedelta(seconds=1)
    runs = drain(now=late) + drain(now=late + timedelta(minutes=1))
    assert [r.outcome for r in runs] == ["requeued", "failed"]  # counted tries, then fail()
    assert waiting.failed == [("s-1", "timeout")]
    with Session(waiting.engine) as db:
        task = db.get(BackgroundTask, task_id)
        assert (task.status, task.last_error_code) == ("FAILED", "TIMEOUT")

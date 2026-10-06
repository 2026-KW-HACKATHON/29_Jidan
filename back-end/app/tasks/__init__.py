"""Background execution of external AI/STT calls (see app.tasks.runner for the lifecycle).

Typical use from a domain module:

    register_handler(TaskHandler(kind="TRANSCRIPTION", execute=..., apply=..., fail=...))
    task_id = enqueue(db, "TRANSCRIPTION", row.id, {"mediaId": ...}, attempt=row.attempt)
    row.task_id = task_id   # same transaction; the handler's apply checks it
"""

from app.tasks.runner import (
    StaleTask,
    TaskContext,
    TaskDeferred,
    TaskHandler,
    TaskQueueUnavailable,
    TaskRun,
    cancel_tasks,
    claim,
    drain,
    enqueue,
    recover_expired,
    register_handler,
    renew_lease,
    run_claimed,
    task_error_code,
    task_runner_lifespan,
    validate_task_leases,
    wake_runner,
)

__all__ = [
    "StaleTask",
    "TaskContext",
    "TaskDeferred",
    "TaskHandler",
    "TaskQueueUnavailable",
    "TaskRun",
    "cancel_tasks",
    "claim",
    "drain",
    "enqueue",
    "recover_expired",
    "register_handler",
    "renew_lease",
    "run_claimed",
    "task_error_code",
    "task_runner_lifespan",
    "validate_task_leases",
    "wake_runner",
]

"""The application lifespan: the one place background work is registered.

* `PERIODIC_JOBS`: synchronous jobs run at a fixed interval (app.periodic). Add a `PeriodicJob`.
* `LIFESPANS`: whole lifespan context managers for work that needs its own setup/teardown
  (e.g. the OAuth cleanup, the AI/STT task runner). Add `your_lifespan` (an
  `asynccontextmanager` taking the app).
Both start in list order and stop in reverse order.

`BACKGROUND_JOBS=off` keeps `PERIODIC_JOBS` and the AI/STT task runner threads from starting (tests run on fixed dates, so a sweep on
the real clock would add rows to them); unset, empty or `on` starts them, anything else stops
the app at startup.
"""
import os
from contextlib import AsyncExitStack, asynccontextmanager

from app import idempotency, invitation_mail
from app.db import validate_database_settings
from app.media import retention as media_retention
from app.notification_sweeps import INTERVAL_SECONDS, run_notification_sweeps
from app.oauth_cleanup import oauth_cleanup_lifespan
from app.periodic import PeriodicJob, periodic_jobs
from app.tasks import task_runner_lifespan
from app.tasks.runner import runner_mode, runner_settings
from app.work_reminders import reminder_hour

PERIODIC_JOBS: list[PeriodicJob] = [
    PeriodicJob("notification-sweeps", INTERVAL_SECONDS, run_notification_sweeps),
    PeriodicJob(
        "invitation-mail", invitation_mail.INTERVAL_SECONDS, invitation_mail.run_invitation_mail_delivery,
    ),
    PeriodicJob("media-retention", media_retention.INTERVAL_SECONDS, media_retention.run_retention),
    PeriodicJob(
        "idempotency-retention", idempotency.RETENTION_INTERVAL_SECONDS, idempotency.run_idempotency_retention,
    ),
]


BACKGROUND_JOBS_ENV = "BACKGROUND_JOBS"


class BackgroundJobsConfigurationError(ValueError):
    pass


def background_jobs_enabled() -> bool:
    raw = os.getenv(BACKGROUND_JOBS_ENV, "").strip()
    if raw in ("", "on"):
        return True
    if raw == "off":
        return False
    raise BackgroundJobsConfigurationError(f"{BACKGROUND_JOBS_ENV} must be on or off")


@asynccontextmanager
async def periodic_jobs_lifespan(_app):
    if not background_jobs_enabled():
        yield
        return
    async with periodic_jobs(PERIODIC_JOBS):
        yield


@asynccontextmanager
async def ai_task_runner_lifespan(app):
    """The AI/STT task runner threads (app.tasks); off with BACKGROUND_JOBS=off as well as with
    TASK_RUNNER_MODE=manual, in which case due tasks run only through `app.tasks.drain`."""
    if not background_jobs_enabled():
        yield
        return
    async with task_runner_lifespan(app):
        yield


LIFESPANS = [oauth_cleanup_lifespan, periodic_jobs_lifespan, ai_task_runner_lifespan]


def validate_background_settings() -> None:
    """Fail at startup on a malformed setting rather than on every interval."""
    validate_database_settings()
    background_jobs_enabled()
    runner_mode()
    runner_settings()
    reminder_hour()
    invitation_mail.validate_mail_settings()


@asynccontextmanager
async def lifespan(app):
    validate_background_settings()
    async with AsyncExitStack() as stack:
        for factory in LIFESPANS:
            await stack.enter_async_context(factory(app))
        yield

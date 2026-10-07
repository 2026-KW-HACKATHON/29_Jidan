"""In-process periodic jobs started by the FastAPI lifespan (see app/lifespan.py).

Same contract as app/oauth_cleanup.py: each job is a synchronous function run in a worker
thread at a fixed interval, starting once at startup. A failure is logged by job name only (no
exception text, which may hold SQL or credentials) and retried at the next interval. Jobs must
be safe to run in several processes at once (bounded batches, `SKIP LOCKED`, idempotent writes)
and must not keep a database transaction open across external calls.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("jidan.errors")


@dataclass(frozen=True)
class PeriodicJob:
    name: str  # appears in logs; keep it free of data
    interval_seconds: float
    run: Callable[[], Any]


async def run_periodically(
    job: PeriodicJob, *, sleep: Callable[[float], Awaitable[Any]] | None = None,
) -> None:
    """Run `job` now and then every interval until cancelled."""
    sleep = sleep or asyncio.sleep
    while True:
        try:
            await asyncio.to_thread(job.run)
        except Exception:  # noqa: BLE001 - retry next interval; never log the exception text
            logger.error("Periodic job %s failed; retry at next interval", job.name)
        await sleep(job.interval_seconds)


@asynccontextmanager
async def periodic_jobs(jobs: Sequence[PeriodicJob]):
    """Start every job for the lifetime of the block and cancel them all on exit."""
    tasks = [asyncio.create_task(run_periodically(job), name=f"periodic:{job.name}") for job in jobs]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

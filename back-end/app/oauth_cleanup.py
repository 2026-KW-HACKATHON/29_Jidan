"""Bounded cleanup of expired OAuth attempts, independent of authentication requests."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import delete, select

from app.db import session_scope, utcnow
from app.db.models import OAuthTransaction

INTERVAL_SECONDS = 300
RETENTION_GRACE = timedelta(minutes=10)
BATCH_SIZE = 500
MAX_BATCHES = 10
logger = logging.getLogger("jidan.errors")


def purge_expired_oauth() -> int:
    # Preserve the browser-binding interval and an extra grace period for late responses.
    # Session IDs are logical links: removing expired OAuth metadata never deletes sessions.
    cutoff = utcnow() - RETENTION_GRACE
    removed = 0
    for _ in range(MAX_BATCHES):
        with session_scope() as db:
            ids = list(db.scalars(select(OAuthTransaction.id).where(
                OAuthTransaction.expires_at <= cutoff,
            ).order_by(OAuthTransaction.expires_at, OAuthTransaction.id).limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)))
            if not ids:
                break
            removed += db.execute(delete(OAuthTransaction).where(
                OAuthTransaction.id.in_(ids), OAuthTransaction.expires_at <= cutoff,
            )).rowcount
        if len(ids) < BATCH_SIZE:
            break
    return removed


async def cleanup_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(purge_expired_oauth)
        except Exception:  # noqa: BLE001 - retry next interval; never log SQL/credentials
            logger.error("OAuth cleanup failed; retry at next interval")
        await asyncio.sleep(INTERVAL_SECONDS)


@asynccontextmanager
async def oauth_cleanup_lifespan(_app):
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, oauth_cleanup
from app.db import utcnow
from app.db.models import AuthSession, OAuthTransaction
from tests.factories import make_user


def test_cleanup_retains_live_and_grace_rows_and_sessions(db_engine, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(oauth_cleanup, "utcnow", lambda: now)
    with Session(db_engine) as db:
        session = auth.create_session(make_user(db, "WORKER").id, db=db)
        member = db.scalar(select(AuthSession))
        for name, expires in (("expired", now - timedelta(minutes=10)),
                              ("grace", now - timedelta(minutes=9)),
                              ("live", now + timedelta(minutes=5))):
            db.add(OAuthTransaction(token_hash=auth.hash_token(name), state_hash=auth.hash_token(name),
                   nonce_hash=auth.hash_token(name), expires_at=expires, issued_session_id=member.id))
        db.commit()
    assert oauth_cleanup.purge_expired_oauth() == 1
    assert oauth_cleanup.purge_expired_oauth() == 0
    with Session(db_engine) as db:
        assert len(db.scalars(select(OAuthTransaction)).all()) == 2
        assert db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(session.token))).revoked_at is None


def test_cleanup_is_bounded_and_resumes_next_run(db_engine, monkeypatch):
    monkeypatch.setattr(oauth_cleanup, "BATCH_SIZE", 2)
    monkeypatch.setattr(oauth_cleanup, "MAX_BATCHES", 1)
    with Session(db_engine) as db:
        for index in range(3):
            token = auth.hash_token(str(index))
            db.add(OAuthTransaction(token_hash=token, state_hash=token, nonce_hash=token,
                                   expires_at=utcnow() - timedelta(hours=1)))
        db.commit()
    assert oauth_cleanup.purge_expired_oauth() == 2
    assert oauth_cleanup.purge_expired_oauth() == 1


def test_worker_retries_failure_at_five_minute_interval(monkeypatch):
    import asyncio
    calls = []
    delays = []
    def purge():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("secret must not be logged")
        return 0
    async def sleep(seconds):
        delays.append(seconds)
        if len(delays) == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(oauth_cleanup, "purge_expired_oauth", purge)
    monkeypatch.setattr(oauth_cleanup.asyncio, "sleep", sleep)
    async def run():
        try:
            await oauth_cleanup.cleanup_loop()
        except asyncio.CancelledError:
            pass
    asyncio.run(run())
    assert len(calls) == 2
    assert delays == [300, 300]

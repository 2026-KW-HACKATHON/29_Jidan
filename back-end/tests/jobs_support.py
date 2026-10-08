"""Shared setup for the jobs domain API tests: a pinned clock and seeded owners/stores."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from tests.api_contract import login
from tests.factories import make_store, make_user

# Monday 2026-10-05 12:00 in Seoul.
NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at


def pin_clock(monkeypatch) -> Clock:
    """Pins `app.jobs.common.now`, the only clock of the jobs domain, at NOW."""
    pinned = Clock(NOW)
    monkeypatch.setattr("app.jobs.common.now", lambda: pinned.at)
    return pinned


def key() -> str:
    return str(uuid.uuid4())


@dataclass
class Shop:
    owner_id: str
    store_id: str


def seed_shop(db_engine, *, approved: bool = True, **store) -> Shop:
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        if approved:
            store = {"approval_status": "APPROVED", "approved_at": NOW, **store}
        row = make_store(db, owner, **store)
        db.commit()
        return Shop(owner.id, row.id)


def add_store(db_engine, owner_id: str, *, approved: bool = True) -> str:
    with Session(db_engine) as db:
        from app.db.models import User

        overrides = {"approval_status": "APPROVED", "approved_at": NOW} if approved else {}
        row = make_store(db, db.get(User, owner_id), **overrides)
        db.commit()
        return row.id


def seed_user(db_engine, role: str = "WORKER") -> str:
    with Session(db_engine) as db:
        if role == "WORKER":
            from tests.factories import make_worker

            user = make_worker(db)
        else:
            user = make_user(db, role)
        db.commit()
        return user.id


def as_user(api, user_id: str):
    """Log `api` in as `user_id`; returns the `LoggedIn` used for write headers."""
    api.cookies.clear()
    return login(api, user_id)


def race(pairs):
    """Run each `(client_factory, call)` as `call(client)` on its own thread, released together.

    Results keep the order of `pairs`; an exception in any call is re-raised here.
    """
    barrier = threading.Barrier(len(pairs))

    def runner(factory, call):
        client = factory()
        try:
            barrier.wait(timeout=10)
            return call(client)
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
        futures = [pool.submit(runner, factory, call) for factory, call in pairs]
        return [future.result(timeout=60) for future in futures]


def run_concurrently(api_factory, calls):
    """`race` with one shared client factory."""
    return race([(api_factory, call) for call in calls])


def client_factory(token: str):
    """A new contract-checked client sharing a member session cookie."""
    from app.auth import SESSION_COOKIE_NAME
    from app.main import app
    from tests.api_contract import ApiContractClient

    def make():
        client = ApiContractClient(app, raise_server_exceptions=False)
        client.cookies.set(SESSION_COOKIE_NAME, token)
        return client

    return make


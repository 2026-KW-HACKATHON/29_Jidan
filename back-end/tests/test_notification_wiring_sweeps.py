"""Expiry sweeps registered for NO_RESPONSE and INVITATION_EXPIRED (#116 wiring).
"""
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import invitation_expiry, notification_sweeps
from app.db import utcnow
from app.db.models import Notification, StoreInvitation
from tests.invitation_helpers import (
    configure_mail,
    make_world,
    seed_invitation,
)
from tests.jobs_support import pin_clock, seed_shop
from tests.test_work_requests import Scene


@pytest.fixture
def clock(monkeypatch):
    return pin_clock(monkeypatch)


@pytest.fixture
def scene(api, db_engine, clock) -> Scene:
    shop = seed_shop(db_engine, name="월계 카페")
    return Scene(api, db_engine, clock, shop.store_id, shop.owner_id)


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


def notes(db_engine, kind=None) -> list[Notification]:
    with Session(db_engine) as db:
        statement = select(Notification).order_by(Notification.created_at, Notification.id)
        if kind is not None:
            statement = statement.where(Notification.event_type == kind)
        return list(db.scalars(statement))


def one(db_engine, kind) -> Notification:
    rows = notes(db_engine, kind)
    assert len(rows) == 1, [(r.event_type, r.recipient_user_id) for r in rows]
    return rows[0]


# --- INVITATION_EXPIRED sweep ---------------------------------------------------------------------


def test_expired_invitation_is_announced_once(db_engine):
    world = make_world(db_engine)
    now = utcnow()
    expired, _ = seed_invitation(db_engine, world.store_id, created_at=now - timedelta(days=7, hours=1))
    seed_invitation(db_engine, world.store_id, email="live@example.com")
    seed_invitation(db_engine, world.store_id, email="old@example.com", created_at=now - timedelta(days=9))
    seed_invitation(db_engine, world.store_id, email="cancel@example.com",
                    created_at=now - timedelta(days=7, hours=1), canceled_at=now - timedelta(days=1))
    assert invitation_expiry.sweep_expired_invitations(now) == 1
    assert invitation_expiry.sweep_expired_invitations(now) == 0
    row = one(db_engine, "INVITATION_EXPIRED")
    with Session(db_engine) as db:
        deadline = db.get(StoreInvitation, expired).expires_at
    assert row.recipient_user_id == world.owner_id and row.created_at == deadline
    assert row.target_context == {"type": "STORE", "storeId": world.store_id}


def test_access_end_before_link_expiry_is_the_deadline(db_engine):
    world = make_world(db_engine)
    now = utcnow()
    seed_invitation(db_engine, world.store_id, created_at=now - timedelta(days=2),
                    access_expires_at=now - timedelta(hours=1))
    assert invitation_expiry.sweep_expired_invitations(now - timedelta(hours=2)) == 0
    assert invitation_expiry.sweep_expired_invitations(now) == 1


def test_expiry_sweep_is_bounded_and_resumes(db_engine, monkeypatch):
    monkeypatch.setattr(invitation_expiry, "BATCH_SIZE", 2)
    monkeypatch.setattr(invitation_expiry, "MAX_BATCHES", 1)
    world = make_world(db_engine)
    now = utcnow()
    for index in range(3):
        seed_invitation(db_engine, world.store_id, email=f"w{index}@example.com",
                        created_at=now - timedelta(days=7, minutes=index + 1))
    assert invitation_expiry.sweep_expired_invitations(now) == 2
    assert invitation_expiry.sweep_expired_invitations(now) == 1
    assert len(notes(db_engine, "INVITATION_EXPIRED")) == 3


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_expiry_sweeps_announce_once(db_engine):
    import threading

    world = make_world(db_engine)
    now = utcnow()
    for index in range(5):
        seed_invitation(db_engine, world.store_id, email=f"r{index}@example.com",
                        created_at=now - timedelta(days=7, minutes=index + 1))
    barrier = threading.Barrier(4)
    errors = []

    def run():
        try:
            barrier.wait()
            invitation_expiry.sweep_expired_invitations(now)
        except Exception as error:  # noqa: BLE001 - surfaced below
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    assert len(notes(db_engine, "INVITATION_EXPIRED")) == 5


def test_sweeps_are_registered():
    names = [name for name, _sweep in notification_sweeps.SWEEPS]
    assert names == ["work-reminder", "work-request-expiry", "invitation-expiry"]


# Which modules record each kind of notification. Two paths for one event are safe only when both
# lock the same domain row first (docs/notification-design.md "잠금 순서": a concurrent duplicate
# followed by another row for the same recipient deadlocks on the UNIQUE index's next-key lock).
RECORDING_PATHS = {
    "store_approved": {"app/store_approvals.py", "app/demo_seed.py"},
    "store_invited": {"app/invitations.py", "app/demo_seed.py"},  # create and resend: one key per send
    "invitation_accepted": {"app/invitation_responses.py", "app/demo_seed.py"},
    "invitation_expired": {"app/invitation_expiry.py"},
    "new_application": {"app/jobs/applications.py", "app/demo_seed.py"},
    "work_request_received": {"app/jobs/work_requests.py", "app/demo_seed.py"},
    "work_request_withdrawn": {"app/jobs/work_requests.py"},
    "work_confirmed": {"app/jobs/work_requests.py", "app/demo_seed.py"},
    "work_confirmation_withdrawn": {"app/jobs/work_requests.py"},
    # Only expire_request records it; every caller (a settling posting write, the
    # expire_due_requests sweep with SKIP LOCKED) holds the posting row first.
    "work_request_no_response": {"app/jobs/state.py"},
    "record_notification": {"app/work_reminders.py"},  # WORK_REMINDER, from its sweep only
}


def test_each_event_is_recorded_from_known_paths_only():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    found: dict[str, set[str]] = {}
    for path in sorted((root / "app").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in ("app/notification_events.py", "app/notifications.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                    and func.value.id == "notification_events":
                found.setdefault(func.attr, set()).add(relative)
            elif isinstance(func, ast.Name) and func.id == "record_notification":
                found.setdefault("record_notification", set()).add(relative)
    assert found == RECORDING_PATHS, (
        "A notification recording path changed. If one event can now be recorded from two places, "
        "make both lock the same domain row first (docs/notification-design.md 잠금 순서), then "
        "update RECORDING_PATHS."
    )

"""Shared setup for invitation, worker and inbox tests (#108, #109, #115)."""
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app import invitation_mail
from app.invitations import generate_invitation_token, hash_invitation_token
from tests.api_contract import login
from tests.factories import make_invitation, make_store_with_request, make_user, make_worker

APPROVED_AT = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
WORKER_EMAIL = "jisu@example.com"
FRONTEND = "https://app.jidan.test"
MISSING = "00000000-0000-4000-8000-000000000000"


def configure_mail(monkeypatch):
    """Mail key, frontend origin and the memory sender; use from a module's `mail_env` fixture."""
    monkeypatch.setenv(invitation_mail.KEY_ENV, Fernet.generate_key().decode())
    monkeypatch.setenv(invitation_mail.FRONTEND_ORIGIN_ENV, FRONTEND)
    monkeypatch.delenv(invitation_mail.BACKEND_ENV, raising=False)
    invitation_mail.memory_sender.clear()
    return invitation_mail.memory_sender


@dataclass
class World:
    owner_id: str
    owner_email: str
    store_id: str
    worker_id: str
    worker_email: str


def make_world(db_engine, *, worker_email=WORKER_EMAIL, approved=True) -> World:
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER", google_email="Owner@Example.com")
        store, _ = make_store_with_request(db, owner, approved_at=APPROVED_AT if approved else None)
        worker = make_worker(db)
        worker.google_email = worker_email
        db.commit()
        return World(owner.id, owner.google_email, store.id, worker.id, worker_email)


def seed_invitation(db_engine, store_id, *, email=WORKER_EMAIL, created_at=None, access_expires_at=None, **overrides):
    """An invitation with a known raw token, created `created_at` (default: one hour ago)."""
    from app.db import utcnow
    from app.db.models import Store

    created_at = created_at or utcnow() - timedelta(hours=1)
    token = generate_invitation_token()
    with Session(db_engine) as db:
        store = db.get(Store, store_id)
        invitation = make_invitation(
            db, store, invited_email=email, token_hash=hash_invitation_token(token), created_at=created_at,
            last_sent_at=created_at, expires_at=created_at + timedelta(days=7),
            access_expires_at=access_expires_at, **overrides,
        )
        db.commit()
        return invitation.id, token


def owner_session(api, world):
    return login(api, world.owner_id)


def new_key() -> str:
    return str(uuid.uuid4())


def token_of(message) -> str:
    assert message.link.startswith(f"{FRONTEND}/invitations/accept#token=")
    return message.link.split("#token=", 1)[1]

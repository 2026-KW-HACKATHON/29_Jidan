import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.models import InvitationMailOutbox
from tests.factories import NOW, make_invitation, make_store


def _row(session, **overrides):
    invitation = make_invitation(session, make_store(session))
    values = {"invitation_id": invitation.id, "status": "QUEUED", "payload": "ciphertext", "attempts": 0}
    values.update(overrides)
    row = InvitationMailOutbox(**values)
    session.add(row)
    session.flush()
    return row


@pytest.mark.parametrize("values", [
    {"status": "QUEUED", "payload": "c"},
    {"status": "SENT", "payload": None, "processed_at": NOW},
    {"status": "FAILED", "payload": None, "processed_at": NOW, "attempts": 5},
    {"status": "DISCARDED", "payload": None, "processed_at": NOW},
    {"status": "QUEUED", "payload": "c", "claim_token": "t" * 36, "claimed_until": NOW},
    {"status": "QUEUED", "payload": "c", "attempts": 2, "next_attempt_at": NOW},
])
def test_valid_rows(session, values):
    _row(session, **values)


@pytest.mark.parametrize("values", [
    {"status": "QUEUED", "payload": None},  # a queued row needs its encrypted payload
    {"status": "SENT", "payload": "c", "processed_at": NOW},  # finished rows keep no secrets
    {"status": "SENT", "payload": None},  # finished rows record when
    {"status": "QUEUED", "payload": "c", "processed_at": NOW},
    {"status": "sent", "payload": None, "processed_at": NOW},
    {"status": "QUEUED", "payload": "c", "attempts": -1},
    {"status": "QUEUED", "payload": "c", "claim_token": "t" * 36},  # a claim needs its lease end
    {"status": "QUEUED", "payload": "c", "claimed_until": NOW},
    {"status": "SENT", "payload": None, "processed_at": NOW, "claim_token": "t" * 36, "claimed_until": NOW},
])
def test_invalid_rows(session, values):
    with pytest.raises((IntegrityError, OperationalError)), session.begin_nested():
        _row(session, **values)


def test_0020_round_trip_keeps_queued_rows(engine):
    """Downgrading 0020 drops only the delivery columns; upgrading again keeps the rows due."""
    from alembic import command
    from sqlalchemy import inspect, text
    from sqlalchemy.orm import Session

    from tests.conftest import alembic_config

    with Session(engine) as session:
        _row(session)
        session.commit()
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "0009")
        connection.commit()
        columns = {c["name"] for c in inspect(connection).get_columns("invitation_mail_outbox")}
        assert {"next_attempt_at", "claim_token", "claimed_until"}.isdisjoint(columns)
        command.upgrade(config, "head")
        connection.commit()
        row = connection.execute(text("SELECT status, next_attempt_at, claim_token FROM invitation_mail_outbox")).one()
        assert tuple(row) == ("QUEUED", None, None)

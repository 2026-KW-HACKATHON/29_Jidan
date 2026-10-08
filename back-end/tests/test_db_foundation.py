from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import UtcDateTime, database_url, new_uuid, session_scope, utcnow
from app.db.models import User


def configure(monkeypatch, **overrides):
    monkeypatch.delenv("DB_PORT", raising=False)
    values = {"DB_HOST": "mysql", "DB_NAME": "jidan_dev", "DB_USER": "jidan",
              "DB_PASSWORD": "p@ss/word:1", **overrides}
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_database_url_uses_environment_and_quotes_secrets(monkeypatch):
    configure(monkeypatch, DB_PORT="3307")
    url = database_url()
    assert (url.host, url.port, url.database, url.drivername) == (
        "mysql", 3307, "jidan_dev", "mysql+pymysql")
    assert url.password == "p@ss/word:1"
    assert "p@ss" not in str(url) and "p@ss" not in repr(url)


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"])
def test_database_url_requires_every_setting(monkeypatch, missing):
    configure(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError, match="Incomplete"):
        database_url()


@pytest.mark.parametrize("port", ["zero", "0", "65536", "-1"])
def test_database_url_rejects_invalid_port(monkeypatch, port):
    configure(monkeypatch, DB_PORT=port)
    with pytest.raises(ValueError):
        database_url()


def test_new_uuid_fits_char36():
    assert len(new_uuid()) == 36 and new_uuid() != new_uuid()


def test_utc_datetime_round_trips_as_aware_utc(session):
    kst = timezone(timedelta(hours=9))
    user = User(
        google_sub="s", google_email="a@example.com", email_verified=True, role="WORKER",
        name="n", phone_number="1", created_at=datetime(2026, 10, 5, 12, 0, tzinfo=kst),
    )
    session.add(user)
    session.commit()
    session.expire_all()
    stored = session.get(User, user.id).created_at
    assert stored == datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
    assert stored.utcoffset() == timedelta(0)
    raw = session.execute(text("SELECT created_at FROM users")).scalar()
    assert str(raw).startswith("2026-10-05 03:00:00")  # naive UTC on disk


def test_utc_datetime_rejects_naive_values():
    with pytest.raises(ValueError, match="Naive"):
        UtcDateTime().process_bind_param(datetime(2026, 10, 5, tzinfo=None), None)  # noqa: DTZ001
    assert UtcDateTime().process_bind_param(None, None) is None
    assert UtcDateTime().process_result_value(None, None) is None
    assert utcnow().tzinfo is UTC


@pytest.fixture
def scoped(engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(
        "app.db.session.get_session_factory", lambda: sessionmaker(engine, expire_on_commit=False))


def add_user(session, sub):
    session.add(User(google_sub=sub, google_email="a@example.com", email_verified=True,
                     role="OWNER", name="n", phone_number="1"))


def count(engine):
    with Session(engine) as check:
        return len(check.scalars(select(User)).all())


def test_session_scope_commits_on_success(engine, scoped):
    with session_scope() as session:
        add_user(session, "ok")
    assert count(engine) == 1


def test_session_scope_rolls_back_on_error(engine, scoped):
    with pytest.raises(RuntimeError), session_scope() as session:
        add_user(session, "boom")
        session.flush()
        raise RuntimeError
    assert count(engine) == 0


def test_session_scope_rolls_back_when_commit_fails(engine, scoped):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), session_scope() as session:
        add_user(session, "dup")
        add_user(session, "dup")
    assert count(engine) == 0


def test_get_session_dependency_closes_session(scoped, monkeypatch):
    from app.db import get_session

    generator = get_session()
    session = next(generator)
    assert session.is_active
    with pytest.raises(StopIteration):
        next(generator)


def test_get_session_dependency_propagates_errors(scoped):
    from app.db import get_session

    generator = get_session()
    next(generator)
    with pytest.raises(ValueError):
        generator.throw(ValueError("handler failed"))


def test_engine_is_created_lazily_and_reset(monkeypatch):
    from app import db

    configure(monkeypatch)
    created = MagicMock()
    monkeypatch.setattr("app.db.engine.create_engine", created)
    db.reset_engine()
    assert db.get_engine() is db.get_engine()
    assert created.call_count == 1
    kwargs = created.call_args.kwargs["connect_args"]
    assert kwargs["init_command"] == "SET time_zone = '+00:00', innodb_lock_wait_timeout = 5"
    assert (kwargs["connect_timeout"], kwargs["read_timeout"], kwargs["write_timeout"]) == (3, 15, 15)
    db.reset_engine()
    db.get_engine()
    assert created.call_count == 2
    db.reset_engine()


def test_select_one_via_engine(engine):
    with engine.connect() as connection:
        assert connection.execute(select(1)).scalar() == 1


# --- timeouts --------------------------------------------------------------------------------


def test_db_timeouts_default_and_environment(monkeypatch):
    from app.db.engine import db_timeouts

    monkeypatch.delenv("DB_LOCK_WAIT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DB_READ_TIMEOUT_SECONDS", raising=False)
    assert db_timeouts() == (5, 15)
    monkeypatch.setenv("DB_LOCK_WAIT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DB_READ_TIMEOUT_SECONDS", "55")
    assert db_timeouts() == (1, 55)
    monkeypatch.setenv("DB_LOCK_WAIT_TIMEOUT_SECONDS", "")  # empty means the default
    assert db_timeouts() == (5, 55)


@pytest.mark.parametrize(("lock_wait", "read_timeout"), [
    ("0", "15"), ("-1", "15"), ("abc", "15"), ("1.5", "15"), ("\uff15", "15"), (" 5", "15"),
    ("5", "5"), ("10", "5"), ("5", "56"), ("5", "0"),
])
def test_db_timeouts_reject_invalid_values(monkeypatch, lock_wait, read_timeout):
    """A read timeout at or below the lock wait would turn lock waits into 2013 again."""
    from app.db.engine import db_timeouts

    monkeypatch.setenv("DB_LOCK_WAIT_TIMEOUT_SECONDS", lock_wait)
    monkeypatch.setenv("DB_READ_TIMEOUT_SECONDS", read_timeout)
    with pytest.raises(ValueError) as error:
        db_timeouts()
    message = str(error.value)  # names the settings, never echoes the value
    assert message.startswith("Invalid DB_") and not any(char.isdigit() for char in message)


def test_invalid_timeouts_stop_startup(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("DB_READ_TIMEOUT_SECONDS", "2")
    with pytest.raises(ValueError), TestClient(app):
        pass


def test_health_engine_keeps_short_timeouts(monkeypatch):
    from app import db

    configure(monkeypatch)
    created = MagicMock()
    monkeypatch.setattr("app.db.engine.create_engine", created)
    db.reset_engine()
    assert db.get_health_engine() is db.get_health_engine()
    kwargs = created.call_args.kwargs
    assert (kwargs["connect_args"]["read_timeout"], kwargs["connect_args"]["write_timeout"]) == (3, 3)
    assert (kwargs["pool_size"], kwargs["max_overflow"], kwargs["pool_timeout"]) == (1, 0, 3)
    db.reset_engine()


@pytest.mark.mysql
def test_mysql_session_uses_the_configured_lock_wait(mysql_engine):
    with mysql_engine.connect() as connection:
        assert connection.execute(text("SELECT @@session.innodb_lock_wait_timeout")).scalar() == 5
        assert connection.execute(text("SELECT @@session.time_zone")).scalar() == "+00:00"


@pytest.fixture
def short_timeouts(mysql_engine, monkeypatch):
    """Lock wait 1 s, read timeout 3 s: the defaults' ratio, fast enough for a test."""
    from app.db import get_engine, reset_engine

    monkeypatch.setenv("DB_LOCK_WAIT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DB_READ_TIMEOUT_SECONDS", "3")
    reset_engine()
    yield get_engine()
    reset_engine()


@pytest.mark.mysql
def test_mysql_lock_wait_ends_as_1205_not_a_lost_connection(short_timeouts):
    """Before the session lock wait was set, the 3 s socket timeout ended every wait longer
    than 3 s as 2013 "Lost connection" (not lock contention, so a 500 nothing retried)."""
    import time

    from sqlalchemy.exc import OperationalError

    from app.idempotency import is_lock_contention

    with Session(short_timeouts) as holder:
        user = User(google_sub=f"lock-{new_uuid()}", google_email="lock@example.com", email_verified=True,
                    role="WORKER", name="잠금", phone_number="01012345678")
        holder.add(user)
        holder.commit()
        try:
            holder.execute(select(User).where(User.id == user.id).with_for_update())
            started = time.monotonic()
            with short_timeouts.connect() as waiter, pytest.raises(OperationalError) as error:
                waiter.execute(select(User.id).where(User.id == user.id).with_for_update())
            assert error.value.orig.args[0] == 1205 and is_lock_contention(error.value)
            assert time.monotonic() - started < 3
        finally:
            holder.rollback()
            holder.delete(holder.get(User, user.id))
            holder.commit()

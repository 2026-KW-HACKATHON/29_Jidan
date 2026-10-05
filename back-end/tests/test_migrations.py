import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.db.models import Base
from tests.conftest import alembic_config, migrated_sqlite_engine

EXPECTED_TABLES = {
    "users", "worker_profiles", "worker_careers", "availability_rules", "availability_days",
    "stores", "store_approval_requests", "store_invitations", "store_access_grants",
    "job_postings", "job_applications", "application_careers", "work_requests",
    "shift_assignments", "application_selection_effects",
}


def test_history_is_linear_with_a_single_head():
    """Two heads mean two people added a revision in parallel; merge before release."""
    script = ScriptDirectory.from_config(alembic_config())
    assert len(script.get_heads()) == 1
    revisions = list(script.walk_revisions())
    assert all(len(revision.nextrev) <= 1 for revision in revisions)
    assert all(not isinstance(revision.down_revision, tuple) for revision in revisions)


def test_upgrade_creates_every_baseline_table(engine):
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES | {"alembic_version"}


def test_models_and_migrations_agree(engine):
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_one_step_then_upgrade_again(engine):
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "-1")
        connection.commit()
        assert set(inspect(connection).get_table_names()) <= {"alembic_version"}
        command.upgrade(config, "head")
        connection.commit()
        assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())


def test_upgrade_is_idempotent_at_head(engine):
    with engine.connect() as connection:
        command.upgrade(alembic_config(connection), "head")
        command.upgrade(alembic_config(connection), "head")
        connection.commit()
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_every_revision_downgrades_to_base():
    engine = migrated_sqlite_engine()
    with engine.connect() as connection:
        command.downgrade(alembic_config(connection), "base")
        connection.commit()
        assert inspect(connection).get_table_names() == ["alembic_version"]


def test_offline_sql_generation_needs_no_connection(monkeypatch, capsys):
    for key, value in {"DB_HOST": "h", "DB_NAME": "n", "DB_USER": "u", "DB_PASSWORD": "pw"}.items():
        monkeypatch.setenv(key, value)
    command.upgrade(alembic_config(), "head", sql=True)
    output = capsys.readouterr().out
    assert "CREATE TABLE users" in output and "pw" not in output


@pytest.mark.mysql
def test_mysql_migration_round_trip_and_no_drift(mysql_engine):
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    with mysql_engine.connect() as connection:
        assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []
    command.downgrade(config, "-1")
    command.upgrade(config, "head")
    with mysql_engine.connect() as connection:
        assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())


@pytest.mark.mysql
def test_mysql_stores_timestamps_in_utc(mysql_session):
    from datetime import UTC, datetime

    from sqlalchemy import text

    from tests.factories import make_user

    user = make_user(mysql_session, created_at=datetime(2026, 10, 5, 3, 0, 0, 123456, tzinfo=UTC))
    raw = mysql_session.execute(
        text("SELECT created_at FROM users WHERE id = :id"), {"id": user.id}).scalar()
    assert raw == datetime(2026, 10, 5, 3, 0, 0, 123456)  # noqa: DTZ001 - microseconds survive, no zone shift

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.db.models import Base
from tests.conftest import alembic_config, migrated_sqlite_engine

BASELINE_TABLES = {
    "users", "worker_profiles", "worker_careers", "availability_rules", "availability_days",
    "stores", "store_approval_requests", "store_invitations", "store_access_grants",
    "job_postings", "job_applications", "application_careers", "work_requests",
    "shift_assignments", "application_selection_effects",
}
SESSION_TABLES = {"auth_sessions", "registration_sessions"}
IDEMPOTENCY_TABLES = {"idempotency_records"}
EXPECTED_TABLES = BASELINE_TABLES | SESSION_TABLES | IDEMPOTENCY_TABLES


def test_history_is_linear_with_a_single_head():
    """Two heads mean two people added a revision in parallel; merge before release."""
    script = ScriptDirectory.from_config(alembic_config())
    assert len(script.get_heads()) == 1
    revisions = list(script.walk_revisions())
    assert all(len(revision.nextrev) <= 1 for revision in revisions)
    assert all(not isinstance(revision.down_revision, tuple) for revision in revisions)


def test_revisions_form_a_chain_on_top_of_the_untouched_baseline():
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_revision("0001").down_revision is None
    assert script.get_revision("0002").down_revision == "0001"
    assert script.get_revision("0003").down_revision == "0002"
    assert script.get_revision("0004").down_revision == "0003"


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
        columns = {c["name"] for c in inspect(connection).get_columns("idempotency_records")}
        assert "response_headers" not in columns and "response_body" in columns
        command.upgrade(config, "head")
        connection.commit()
        assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())
        columns = {c["name"] for c in inspect(connection).get_columns("idempotency_records")}
        assert "response_headers" in columns


def test_downgrade_to_0002_drops_the_idempotency_table(engine):
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "0002")
        connection.commit()
        assert set(inspect(connection).get_table_names()) == BASELINE_TABLES | SESSION_TABLES | {"alembic_version"}
        command.upgrade(config, "head")
        connection.commit()


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
    try:
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
    finally:
        command.upgrade(config, "head")  # leave the shared test schema at head for later tests


@pytest.mark.mysql
def test_mysql_stores_timestamps_in_utc(mysql_session):
    from datetime import UTC, datetime

    from sqlalchemy import text

    from tests.factories import make_user

    user = make_user(mysql_session, created_at=datetime(2026, 10, 5, 3, 0, 0, 123456, tzinfo=UTC))
    raw = mysql_session.execute(
        text("SELECT created_at FROM users WHERE id = :id"), {"id": user.id}).scalar()
    assert raw == datetime(2026, 10, 5, 3, 0, 0, 123456)  # noqa: DTZ001 - microseconds survive, no zone shift


def uuid_columns():
    """Every UUID-valued column: primary keys, foreign keys and the generated uniqueness helpers."""
    from sqlalchemy import Computed

    return [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.primary_key and column.type.python_type is str
        or column.foreign_keys
        or isinstance(column.computed, Computed)
    ]


def test_uuid_columns_are_char36_in_models_and_sqlite_schema(engine):
    from sqlalchemy import CHAR

    columns = uuid_columns()
    assert len(columns) >= 40  # guards against the helper silently matching nothing
    for column in columns:
        assert isinstance(column.type, CHAR) and column.type.length == 36, str(column)
    inspector = inspect(engine)
    for column in columns:
        reflected = {c["name"]: c for c in inspector.get_columns(column.table.name)}[column.name]
        assert reflected["type"].__class__.__name__ == "CHAR", str(column)
        assert reflected["type"].length == 36, str(column)


@pytest.mark.mysql
def test_mysql_uuid_columns_are_char36_with_matching_collation(mysql_engine):
    from sqlalchemy import text

    rows = {}
    with mysql_engine.connect() as connection:
        for row in connection.execute(text(
            "SELECT table_name, column_name, column_type, character_set_name, collation_name "
            "FROM information_schema.columns WHERE table_schema = DATABASE()"
        )):
            rows[(row[0].lower(), row[1].lower())] = row[2:]
        columns = uuid_columns()
        assert len(columns) >= 40
        for column in columns:
            column_type, charset, collation = rows[(column.table.name, column.name)]
            assert column_type == "char(36)", f"{column}: {column_type}"
            for foreign_key in column.foreign_keys:
                target = foreign_key.column
                assert rows[(target.table.name, target.name)] == (column_type, charset, collation), (
                    f"{column} differs from referenced {target}"
                )
        # No UUID column may have been created as VARCHAR(36).
        assert not [key for key, value in rows.items() if value[0] == "varchar(36)"]
        # SHOW CREATE TABLE exposes the generated active_* helper columns as CHAR(36) too.
        for table, column in (("job_applications", "active_worker_id"),
                              ("shift_assignments", "active_job_id"),
                              ("work_requests", "pending_application_id")):
            ddl = connection.execute(text(f"SHOW CREATE TABLE {table}")).one()[1]
            assert f"`{column}` char(36)" in ddl and "GENERATED ALWAYS" in ddl

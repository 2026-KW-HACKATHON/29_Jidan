import time

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

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
TASK_TABLES = {"background_tasks"}
MEDIA_TABLES = {"manual_media", "qa_media", "media_transcriptions"}
MANUAL_TABLES = {
    "store_manuals", "manual_versions", "manual_shifts", "manual_sections", "manual_steps",
    "manual_photo_attachments", "manual_media_snapshot_refs", "interview_question_sets",
    "interview_intents", "interview_sessions", "interview_session_intents",
    "interview_intent_reviews", "interview_review_confirmations", "interview_probe_batches",
    "interview_turns", "interview_turn_photos", "interview_evaluations", "manual_review_issues",
    "manual_issue_acknowledgements", "manual_draft_corrections",
}
QA_TABLES = {"manual_qa_conversations", "manual_qa", "manual_qa_citations", "manual_qa_photos"}
OUTBOX_TABLES = {"invitation_mail_outbox"}
EXPECTED_TABLES = (
    BASELINE_TABLES | SESSION_TABLES | IDEMPOTENCY_TABLES | {"oauth_transactions"} | OUTBOX_TABLES
    | {"notifications", "favorite_stores"} | TASK_TABLES | MEDIA_TABLES | MANUAL_TABLES | QA_TABLES
)


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
    assert script.get_revision("0005").down_revision == "0004"
    assert script.get_revision("0006").down_revision == "0005"
    assert script.get_revision("0007").down_revision == "0006"
    assert script.get_revision("0008").down_revision == "0007"
    assert script.get_revision("0009").down_revision == "0008"
    # 0020 and 0030 were applied to databases before the AI schema existed, so they stay
    # right after 0009 and the AI revisions follow; every state that ever existed is a prefix.
    assert script.get_revision("0020").down_revision == "0009"
    assert script.get_revision("0030").down_revision == "0020"
    assert script.get_revision("0032").down_revision == "0030"
    assert script.get_revision("0033").down_revision == "0032"
    assert script.get_revision("0034").down_revision == "0033"
    assert script.get_revision("0035").down_revision == "0034"
    assert script.get_revision("0036").down_revision == "0035"
    for gone in ("0010", "0011", "0012", "0013"):
        with pytest.raises(CommandError):
            script.get_revision(gone)
    assert script.get_revision("0040").down_revision == "0036"
    assert script.get_revision("0041").down_revision == "0040"
    assert script.get_revision("0042").down_revision == "0041"
    assert script.get_revision("0043").down_revision == "0042"


def test_upgrade_creates_every_baseline_table(engine):
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES | {"alembic_version"}


def test_models_and_migrations_agree(engine):
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_0006_then_upgrade_again(engine):
    # Pinned to 0005 rather than "-1" so later revisions do not change what this checks.
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "0005")
        connection.commit()
        columns = {c["name"] for c in inspect(connection).get_columns("idempotency_records")}
        assert "response_headers" in columns and "response_body" in columns
        assert "invitation_mail_outbox" not in inspect(connection).get_table_names()
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


def test_downgrade_0032_drops_only_the_ai_tables(engine):
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "0030")
        connection.commit()
        tables = set(inspect(connection).get_table_names())
        assert not (TASK_TABLES | MEDIA_TABLES | MANUAL_TABLES | QA_TABLES) & tables
        assert {"oauth_transactions", "favorite_stores", "invitation_mail_outbox"} <= tables
        assert "ix_work_requests_status_expires_at" in {
            index["name"] for index in inspect(connection).get_indexes("work_requests")}
        outbox = {c["name"] for c in inspect(connection).get_columns("invitation_mail_outbox")}
        assert {"next_attempt_at", "claim_token", "claimed_until"} <= outbox
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
    """Every UUID-valued column: primary keys, foreign keys and the generated uniqueness helpers
    (string-valued generated columns; an integer helper such as a depth is not a UUID)."""
    from sqlalchemy import Computed

    return [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.primary_key and column.type.python_type is str
        or column.foreign_keys
        or isinstance(column.computed, Computed) and column.type.python_type is str
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


def online_engine_kwargs(monkeypatch) -> dict:
    """Keyword arguments `alembic upgrade` passes to create_engine when deploy runs it."""
    import sqlalchemy

    captured = {}

    class Stop(Exception):
        pass

    def fake_create_engine(url, **kwargs):
        captured.update(kwargs)
        raise Stop

    for key, value in {"HOST": "db", "NAME": "jidan_dev", "USER": "u", "PASSWORD": "p"}.items():
        monkeypatch.setenv(f"DB_{key}", value)
    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
    with pytest.raises(Stop):
        command.upgrade(alembic_config(), "head")
    return captured


def test_online_migration_engine_hides_sql_parameters(monkeypatch):
    """Deploy runs `alembic upgrade head` with output in CI logs; errors must not echo values."""
    assert online_engine_kwargs(monkeypatch)["hide_parameters"] is True


@pytest.mark.mysql
def test_online_migration_session_fails_fast_on_metadata_locks(mysql_engine, monkeypatch):
    """An open reader transaction must fail the DDL in seconds, not queue the table for a year."""
    init_command = online_engine_kwargs(monkeypatch)["connect_args"]["init_command"]
    migrator = create_engine(mysql_engine.url, connect_args={"init_command": init_command})
    try:
        with mysql_engine.connect() as reader, migrator.connect() as connection:
            assert connection.execute(text("SELECT @@session.time_zone")).scalar() == "+00:00"
            assert 0 < connection.execute(text("SELECT @@session.lock_wait_timeout")).scalar() <= 30
            connection.execute(text("SET SESSION lock_wait_timeout = 1"))  # keep the test fast
            reader.execute(text("SELECT COUNT(*) FROM users"))  # holds a shared metadata lock
            started = time.monotonic()
            with pytest.raises(OperationalError) as error:
                connection.execute(text("ALTER TABLE users ADD COLUMN mdl_probe INT NULL"))
            assert error.value.orig.args[0] == 1205 and time.monotonic() - started < 10
    finally:
        migrator.dispose()
        with mysql_engine.connect() as connection:  # never leave the shared schema altered
            if "mdl_probe" in {c["name"] for c in inspect(connection).get_columns("users")}:
                connection.execute(text("ALTER TABLE users DROP COLUMN mdl_probe"))


def test_downgrade_0030_drops_only_the_expiry_index(engine):
    def indexes(connection):
        return {index["name"] for index in inspect(connection).get_indexes("work_requests")}

    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "0020")
        connection.commit()
        assert "ix_work_requests_status_expires_at" not in indexes(connection)
        assert "ix_work_requests_application_id" in indexes(connection)
        assert not TASK_TABLES & set(inspect(connection).get_table_names())  # AI revisions sit above 0030
        command.upgrade(config, "head")
        connection.commit()
        assert "ix_work_requests_status_expires_at" in indexes(connection)


def test_downgrade_to_0009_leaves_the_pre_ai_mail_outbox(engine):
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "0009")
        connection.commit()
        tables = set(inspect(connection).get_table_names())
        assert not (TASK_TABLES | MEDIA_TABLES | MANUAL_TABLES | QA_TABLES) & tables
        outbox = {c["name"] for c in inspect(connection).get_columns("invitation_mail_outbox")}
        assert not {"next_attempt_at", "claim_token", "claimed_until"} & outbox
        command.upgrade(config, "head")
        connection.commit()
        assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())


@pytest.mark.mysql
@pytest.mark.parametrize("released", ["0006", "0009", "0020", "0030", "0035"])
def test_mysql_released_states_upgrade_to_the_fresh_head_schema(mysql_engine, released):
    """Every revision ever applied somewhere (remote dev at 0006, local DBs that ran
    0009 -> 0020 -> 0030 before the AI schema existed, and 0035 before 0036) must reach the same
    head with rows kept."""
    from sqlalchemy.orm import Session

    from tests.factories import make_user
    from tests.schema_snapshot import mysql_schema_snapshot, snapshot_diff

    config = alembic_config()
    try:
        with mysql_engine.connect() as connection:
            fresh = mysql_schema_snapshot(connection)
        command.downgrade(config, released)
        with mysql_engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == released
            if released < "0032":  # the AI revisions come after every pre-AI state
                assert not TASK_TABLES & set(inspect(connection).get_table_names())
        with Session(mysql_engine) as session:  # users keeps its columns since 0001
            user_id = make_user(session).id
            session.commit()
        command.upgrade(config, "head")
        with mysql_engine.connect() as connection:
            head = ScriptDirectory.from_config(config).get_current_head()
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
            assert connection.execute(
                text("SELECT COUNT(*) FROM users WHERE id = :id"), {"id": user_id}).scalar() == 1
            assert snapshot_diff(fresh, mysql_schema_snapshot(connection)) == []
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            assert compare_metadata(context, Base.metadata) == []
    finally:
        command.upgrade(config, "head")
        with mysql_engine.begin() as connection:
            connection.execute(text("DELETE FROM users"))


EMAIL_COLUMNS = (
    ("users", "google_email"), ("registration_sessions", "google_email"),
    ("store_invitations", "invited_email"),
)


def _before_0036() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_revision("0036").down_revision


def test_downgrade_0036_leaves_sqlite_email_columns_alone(engine):
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, _before_0036())
        connection.commit()
        assert "ix_store_invitations_store_id_invited_email" in {
            index["name"] for index in inspect(connection).get_indexes("store_invitations")}
        command.upgrade(config, "head")
        connection.commit()


@pytest.mark.mysql
def test_mysql_0036_switches_email_collation_and_keeps_the_index(mysql_engine):
    def state():
        with mysql_engine.connect() as connection:
            collations = {key: connection.execute(text(
                "SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = :t AND COLUMN_NAME = :c"), {"t": key[0], "c": key[1]}).scalar_one()
                for key in EMAIL_COLUMNS}
            nullable = {key: column["nullable"] for key in EMAIL_COLUMNS
                        for column in inspect(connection).get_columns(key[0]) if column["name"] == key[1]}
            indexes = {index["name"]: index["column_names"]
                       for index in inspect(connection).get_indexes("store_invitations")}
        return collations, nullable, indexes

    config = alembic_config()
    try:
        command.downgrade(config, _before_0036())
        collations, nullable, indexes = state()
        assert set(collations.values()) == {"utf8mb4_0900_ai_ci"}
        assert not any(nullable.values())
        assert indexes["ix_store_invitations_store_id_invited_email"] == ["store_id", "invited_email"]
        command.upgrade(config, "head")
        collations, nullable, indexes = state()
        assert set(collations.values()) == {"utf8mb4_0900_as_ci"}
        assert not any(nullable.values())
        assert indexes["ix_store_invitations_store_id_invited_email"] == ["store_id", "invited_email"]
    finally:
        command.upgrade(config, "head")


def test_0042_keeps_existing_turns_and_allows_answer_snapshots(engine):
    """Deployed 0042 permits immutable guidance on the accepted ANSWER as well as QUESTION."""
    with engine.connect() as connection:
        config = alembic_config(connection)
        command.downgrade(config, "0041")
        connection.commit()
        # Turns only; their parents are not needed. A PRAGMA inside a transaction is ignored, so it
        # goes to the driver connection, outside SQLAlchemy's autobegin.
        connection.connection.driver_connection.execute("PRAGMA foreign_keys = OFF")
        insert = text(
            "INSERT INTO interview_turns (id, session_id, turn_no, speaker, turn_kind, question_kind, intent_id,"
            " depth, probe_batch_id, reply_to_question_turn_id, input_method, content, created_at)"
            " VALUES (:id, 's', :no, :speaker, :kind, :qkind, 'i', 0, NULL, :reply, :method, :content,"
            " '2026-10-01 00:00:00')")
        connection.execute(insert, {"id": "q", "no": 1, "speaker": "AI", "kind": "QUESTION", "qkind": "BASE",
                                    "reply": None, "method": None, "content": "질문?"})
        connection.execute(insert, {"id": "a", "no": 2, "speaker": "OWNER", "kind": "ANSWER", "qkind": None,
                                    "reply": "q", "method": "TEXT", "content": "답변"})
        connection.commit()
        command.upgrade(config, "head")
        connection.commit()
        assert connection.execute(text(
            "SELECT id, guidance, guidance_cards FROM interview_turns ORDER BY turn_no")).all() == [
            ("q", None, None), ("a", None, None)]
        connection.execute(text("UPDATE interview_turns SET guidance = '안내' WHERE id = 'q'"))
        connection.execute(text("UPDATE interview_turns SET guidance = '안내' WHERE id = 'a'"))
        assert connection.execute(text("SELECT guidance FROM interview_turns WHERE id = 'a'")).scalar_one() == "안내"
        connection.commit()
        command.downgrade(config, "0041")
        connection.commit()
        assert "guidance" not in {c["name"] for c in inspect(connection).get_columns("interview_turns")}
        assert connection.execute(text(
            "SELECT id, content, reply_to_question_turn_id FROM interview_turns ORDER BY turn_no")).all() == [
            ("q", "질문?", None), ("a", "답변", "q")]
        command.upgrade(config, "head")
        connection.commit()

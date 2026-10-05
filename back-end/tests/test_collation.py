"""Case-sensitive enum and identifier columns (MySQL's default collation is case-insensitive)."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from app.db.models import (
    ApplicationSelectionEffect,
    AvailabilityDay,
    AvailabilityRule,
    Base,
    StoreApprovalRequest,
)
from tests.factories import (
    NOW,
    make_application,
    make_invitation,
    make_job,
    make_request,
    make_store,
    make_user,
    make_worker,
)
from tests.schema_checks import (
    collation_differences,
    database_collations,
    enum_columns,
    model_collations,
)

ENUM_COLUMNS = enum_columns(Base.metadata)
CASE_SENSITIVE = [*ENUM_COLUMNS, ("users", "google_sub"), ("store_invitations", "token_hash")]
EMAIL_COLUMNS = [("users", "google_email"), ("store_invitations", "invited_email")]


def test_every_enum_check_column_is_discovered():
    assert len(ENUM_COLUMNS) == 14  # a new enum column must be added to this count consciously
    assert ("users", "role") in ENUM_COLUMNS and ("application_selection_effects", "previous_status") in ENUM_COLUMNS


def test_models_ask_for_case_sensitive_collation_exactly_where_intended():
    collations = model_collations(Base.metadata)
    for key in CASE_SENSITIVE:
        assert collations[key] == "utf8mb4_0900_as_cs", key
    for key in EMAIL_COLUMNS:  # emails are deliberately case-insensitive (see docs/erd/README.md)
        assert collations[key] is None, key


def test_collation_comparison_notices_drift():
    model = model_collations(Base.metadata)
    database = {key: (value or "utf8mb4_0900_ai_ci") for key, value in model.items()}
    assert collation_differences(model, database) == []
    database[("users", "role")] = "utf8mb4_0900_ai_ci"
    database[("users", "google_email")] = "utf8mb4_0900_as_cs"
    problems = collation_differences(model, database)
    assert len(problems) == 2
    assert "users.role" in problems[1] or "users.role" in problems[0]
    assert any("users.google_email" in problem for problem in problems)


@pytest.mark.mysql
def test_mysql_columns_have_the_collation_the_models_declare(mysql_engine):
    with mysql_engine.connect() as connection:
        actual = database_collations(connection)
    model = model_collations(Base.metadata)
    assert collation_differences(model, actual) == []
    for key in CASE_SENSITIVE:
        assert actual[key] == "utf8mb4_0900_as_cs", key


@pytest.fixture
def enum_rows(session):
    """One valid row in every table that has an enum-like column."""
    owner = make_user(session, "OWNER")
    worker = make_worker(session)
    store = make_store(session, owner=owner)
    session.add(StoreApprovalRequest(store_id=store.id))
    job = make_job(session, store)
    application = make_application(session, job, worker)
    request = make_request(session, application, owner.id)
    session.add(ApplicationSelectionEffect(
        request_id=request.id, application_id=application.id, previous_status="APPLIED",
        applied_revision=1,
    ))
    rule = AvailabilityRule(
        worker_id=worker.id, sort_order=0, start_time=NOW.time(), end_time=NOW.time(),
        ends_next_day=True,
    )
    session.add(rule)
    session.flush()
    session.add(AvailabilityDay(rule_id=rule.id, weekday="MON"))
    session.flush()
    return session


@pytest.mark.parametrize("table,column", ENUM_COLUMNS)
@pytest.mark.parametrize("variant", ["lower", "title", "padded"])
def test_enum_column_rejects_other_letter_case(enum_rows, table, column, variant):
    session = enum_rows
    stored = session.execute(text(f"SELECT {column} FROM {table} LIMIT 1")).scalar_one()
    assert stored == stored.upper(), "fixture must hold a valid upper-case value"
    wrong = {"lower": stored.lower(), "title": stored.title(), "padded": stored + " "}[variant]
    assert wrong != stored
    # MySQL silently drops spaces beyond the column length (weekday VARCHAR(3): 'MON ' -> 'MON').
    truncates = variant == "padded" and len(wrong) > column_length(table, column)
    try:
        with session.begin_nested():
            session.execute(text(f"UPDATE {table} SET {column} = :value"), {"value": wrong})
    except DBAPIError as error:
        # SQLite: IntegrityError; MySQL: OperationalError 3819 (CHECK violated).
        assert "CHECK" in str(error).upper() or error.orig.args[0] == 3819
    else:
        assert truncates, f"{wrong!r} was accepted by {table}.{column}"
    after = session.execute(text(f"SELECT {column} FROM {table} LIMIT 1")).scalar_one()
    assert after == stored


def column_length(table, column) -> int:
    return Base.metadata.tables[table].c[column].type.length


def test_google_sub_and_token_hash_are_case_sensitive(session):
    make_user(session, google_sub="AbCdEf")
    make_user(session, google_sub="abcdef")  # a distinct subject, not a duplicate
    store = make_store(session)
    make_invitation(session, store, token_hash="A" * 64)
    make_invitation(session, store, token_hash="a" * 64)
    make_user(session, google_sub="abcdef ")  # trailing space is a different value (NO PAD)
    found = session.execute(
        text("SELECT COUNT(*) FROM users WHERE google_sub = 'abcdef'")).scalar_one()
    assert found == 1  # a lookup by the exact value finds exactly that subject


def test_emails_stay_case_insensitive_on_mysql(session):
    """Intentional: invitation emails are compared case-insensitively (normalized in the service)."""
    make_user(session, google_email="Person@Example.com")
    count = session.execute(
        text("SELECT COUNT(*) FROM users WHERE google_email = 'person@example.com'")).scalar_one()
    assert count == (0 if session.get_bind().dialect.name == "sqlite" else 1)


def test_inspector_sees_the_enum_checks(engine):
    assert any(c["name"] == "ck_users_role" for c in inspect(engine).get_check_constraints("users"))

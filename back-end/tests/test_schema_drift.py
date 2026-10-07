"""Drift detection beyond compare_metadata: CHECK constraints, plus proof that UNIQUE,
INDEX and FOREIGN KEY drift is caught by compare_metadata."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    MetaData,
    UniqueConstraint,
    inspect,
)

from app.db.models import Base
from tests.schema_checks import (
    check_differences,
    database_checks,
    model_checks,
    normalize_check_sql,
)


def test_model_and_sqlite_checks_match(engine):
    model = model_checks(Base.metadata)
    assert len(model) >= 40  # guards against the helper matching nothing
    assert check_differences(model, database_checks(inspect(engine)), keep_parentheses=True) == []


@pytest.mark.mysql
def test_model_and_mysql_checks_match(mysql_engine):
    model = model_checks(Base.metadata, mysql_engine.dialect)
    database = database_checks(inspect(mysql_engine))
    assert check_differences(model, database, keep_parentheses=False) == []
    assert len(database) == len(model) >= 40


def test_dialect_specific_checks_render_differently_per_database():
    from sqlalchemy.dialects import mysql

    sqlite = model_checks(Base.metadata)
    on_mysql = model_checks(Base.metadata, mysql.dialect())
    assert sqlite.keys() == on_mysql.keys()
    different = sorted(key for key in sqlite if sqlite[key] != on_mysql[key])
    assert different == [
        ("application_careers", "ck_application_careers_store_name_not_blank"),
        ("interview_intents", "ck_interview_intents_base_question_not_blank"),
        ("interview_intents", "ck_interview_intents_coverage_criteria_not_blank"),
        ("interview_turns", "ck_interview_turns_content_not_blank"),
        ("job_applications", "ck_job_applications_introduction"),
        ("manual_draft_corrections", "ck_manual_draft_corrections_input_text_not_blank"),
        ("manual_issue_acknowledgements", "ck_manual_issue_acknowledgements_owner_note_not_blank"),
        ("manual_photo_attachments", "ck_manual_photo_attachments_title_not_blank"),
        ("manual_qa", "ck_manual_qa_answer_not_blank"),
        ("manual_qa", "ck_manual_qa_question_not_blank"),
        ("manual_qa_citations", "ck_manual_qa_citations_excerpt_not_blank"),
        ("manual_review_issues", "ck_manual_review_issues_description_not_blank"),
        ("manual_review_issues", "ck_manual_review_issues_public_description_not_blank"),
        ("manual_sections", "ck_manual_sections_title_not_blank"),
        ("manual_shifts", "ck_manual_shifts_name_not_blank"),
        ("manual_steps", "ck_manual_steps_instruction_not_blank"),
        ("media_transcriptions", "ck_media_transcriptions_text_not_blank"),
        ("notifications", "ck_notifications_body_not_blank"),
        ("notifications", "ck_notifications_title_not_blank"),
        ("stores", "ck_stores_brn_digits"),
        ("worker_careers", "ck_worker_careers_store_name_not_blank"),
    ]
    assert "GLOB" in sqlite[("stores", "ck_stores_brn_digits")]
    assert "REGEXP_LIKE" in on_mysql[("stores", "ck_stores_brn_digits")]


# --- the comparison itself must notice drift -------------------------------------------------


def checks_after(change) -> list[str]:
    model = model_checks(Base.metadata)
    database = dict(model)
    change(model)
    return check_differences(model, database, keep_parentheses=True)


def test_added_check_in_models_only_is_reported():
    problems = checks_after(lambda m: m.update({("users", "ck_users_extra"): "id <> ''"}))
    assert problems == [
        "CHECK users.ck_users_extra is in the models but missing from the database"]


def test_dropped_check_in_models_is_reported():
    problems = checks_after(lambda m: m.pop(("users", "ck_users_role")))
    assert problems == [
        "CHECK users.ck_users_role is in the database but missing from the models"]


def test_modified_check_is_reported_with_both_texts():
    problems = checks_after(
        lambda m: m.update({("users", "ck_users_role"): "role IN ('WORKER', 'OWNER', 'ADMIN')"}))
    assert len(problems) == 1
    assert problems[0].startswith("CHECK users.ck_users_role differs")
    assert "'ADMIN'" in problems[0]


@pytest.mark.parametrize("edited", [
    "role IN ('WORKER', 'owner')",  # literal case matters
    "role NOT IN ('WORKER', 'OWNER')",
    "status IN ('WORKER', 'OWNER')",
])
def test_literals_operators_and_columns_are_compared(edited):
    original = "role IN ('WORKER', 'OWNER')"
    assert normalize_check_sql(original, True) != normalize_check_sql(edited, True)
    assert normalize_check_sql(original, False) != normalize_check_sql(edited, False)


def test_normalization_absorbs_only_cosmetic_differences():
    sqlite_form = "(is_current = 1 AND end_month IS NULL) OR (is_current = 0 AND end_month IS NOT NULL)"
    mysql_form = ("(((`is_current` = 1) and (`end_month` is null)) or "
                  "((`is_current` = 0) and (`end_month` is not null)))")
    assert normalize_check_sql(sqlite_form, False) == normalize_check_sql(mysql_form, False)
    assert normalize_check_sql(sqlite_form, True) != normalize_check_sql(mysql_form, True)
    assert normalize_check_sql("TRIM(x) != ''", True) == normalize_check_sql("trim(`x`)  <> _utf8mb4''", True)
    assert normalize_check_sql("a  IN ('X',\n 'Y')", True) == normalize_check_sql("a in ('X','Y')", True)


# --- compare_metadata covers UNIQUE / INDEX / FK drift; show it on a mutated copy ------------


def mutated_metadata(mutate) -> MetaData:
    copy = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        table.to_metadata(copy)
    mutate(copy)
    return copy


def differences(engine, mutate) -> list:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, mutated_metadata(mutate))


def test_unmodified_copy_has_no_drift(engine):
    assert differences(engine, lambda md: None) == []


def test_compare_metadata_catches_unique_index_and_foreign_key_drift(engine):
    def add_unique(md):
        md.tables["stores"].append_constraint(UniqueConstraint("name", name="uq_stores_name"))

    def add_index(md):
        Index("ix_users_name", md.tables["users"].c.name)

    def drop_index(md):
        index = next(i for i in md.tables["stores"].indexes if i.name == "ix_stores_owner_id")
        md.tables["stores"].indexes.discard(index)

    def drop_unique(md):
        users = md.tables["users"]
        users.constraints.discard(next(
            c for c in users.constraints if isinstance(c, UniqueConstraint)))

    def drop_foreign_key(md):
        stores = md.tables["stores"]
        stores.constraints.discard(next(
            c for c in stores.constraints if isinstance(c, ForeignKeyConstraint)))

    def change_on_delete(md):
        stores = md.tables["stores"]
        next(c for c in stores.constraints if isinstance(c, ForeignKeyConstraint)).ondelete = "CASCADE"

    for mutate in (add_unique, add_index, drop_index, drop_unique, drop_foreign_key,
                   change_on_delete):
        assert differences(engine, mutate), mutate.__name__


def test_compare_metadata_does_not_catch_check_drift(engine):
    """Why the CHECK comparison above exists."""
    def add_check(md):
        md.tables["users"].append_constraint(CheckConstraint("id <> ''", name="ck_users_extra"))

    assert differences(engine, add_check) == []

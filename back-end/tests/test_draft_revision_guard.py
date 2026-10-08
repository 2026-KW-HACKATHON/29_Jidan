"""The draft revision guard (tests/draft_revision.py) matches content writes to their own version."""

from datetime import timedelta

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

from app.db.keyed import delete_by_key, update_by_key
from app.db.models import ManualSection, ManualStep, ManualVersion
from tests import draft_revision
from tests.factories import make_manual_draft, make_store, make_user


@pytest.fixture
def drafts(db_engine):
    with Session(db_engine) as db:
        a = make_manual_draft(db, make_store(db, owner=make_user(db, "OWNER")))
        b = make_manual_draft(db, make_store(db, owner=make_user(db, "OWNER")))
        section = ManualSection(version_id=a.id, sort_order=0, category="RULE", title="복장")
        db.add(section)
        db.flush()
        step = ManualStep(section_id=section.id, sort_order=0, instruction="앞치마를 입어요.")
        db.add(step)
        db.commit()
        return a.id, b.id, section.id, step.id


def take_found() -> list[str]:
    with draft_revision._lock:
        found, draft_revision._found[:] = list(draft_revision._found), []
    return found


@pytest.mark.parametrize("write", ["delete_step", "update_section"])
def test_content_of_one_version_with_another_versions_revision_is_a_violation(db_engine, drafts, write):
    a, b, section, step = drafts
    with Session(db_engine) as db:
        if write == "delete_step":
            delete_by_key(db, ManualStep, [step])  # app code; the step belongs to version A via its section
        else:
            update_by_key(db, ManualSection, [section], {"title": "복장 규정"})
        db.get(ManualVersion, b).revision += 1  # only B moves
        db.commit()
    [problem] = take_found()
    assert a in problem and b not in problem


@pytest.mark.parametrize("write", ["delete_step", "update_section"])
def test_content_with_its_own_versions_revision_passes(db_engine, drafts, write):
    a, _b, section, step = drafts
    with Session(db_engine) as db:
        if write == "delete_step":
            delete_by_key(db, ManualStep, [step])
        else:
            update_by_key(db, ManualSection, [section], {"title": "복장 규정"})
        version = db.get(ManualVersion, a)
        version.revision += 1
        version.content_revision += 1
        db.commit()
    assert take_found() == []


def test_rolled_back_content_writes_are_not_judged(db_engine, drafts):
    _a, _b, section, _step = drafts
    with Session(db_engine) as db:
        update_by_key(db, ManualSection, [section], {"title": "취소"})
        db.rollback()
    assert take_found() == []


@pytest.mark.parametrize("sql", [
    "UPDATE manual_versions SET revision=%(revision)s WHERE id=%(id_1)s",
    "update `manual_versions` set `revision` = %s where `id` = %s",
    "UPDATE manual_versions SET updated_at=?, generation_status = ? WHERE id=?",
    "UPDATE manual_versions SET updated_at='WHERE revision = 7', `generation_status`=? WHERE id=?",
    "UPDATE manual_versions SET manual_versions.revision = revision + 1 WHERE id=?",
])
def test_version_state_assignment_formats_are_recognized(sql):
    assert draft_revision._updates_version_state(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE manual_versions SET updated_at=%(updated_at)s WHERE id=%(id_1)s AND revision=%(revision_1)s",
    "update `manual_versions` set `updated_at` = ? where `generation_status` = ? and `id` = ?",
    "UPDATE manual_versions SET updated_at='revision = 7' WHERE id=?",
    "UPDATE manual_versions SET updated_at='generation_status = READY, revision = 7' WHERE id=?",
    "UPDATE manual_versions SET updated_at=printf('note, revision = 7', ?) WHERE id=?",
    "UPDATE manual_versions SET updated_at='note with ''quote, revision = 7' WHERE id=?",
    "UPDATE manual_versions SET updated_at=CASE WHEN revision = ? THEN ? ELSE ? END WHERE id=?",
])
def test_predicates_and_unrelated_assignment_values_are_not_version_changes(sql):
    assert not draft_revision._updates_version_state(sql)


@pytest.mark.parametrize("comparison", ["revision", "generation_status"])
def test_content_write_with_only_a_version_predicate_is_a_violation(db_engine, drafts, comparison):
    a, _b, section, _step = drafts
    statements = []

    def capture(_connection, _cursor, statement, _parameters, context, _many):
        if statement.lstrip().lower().startswith("update manual_versions"):
            statements.extend(context.compiled_parameters)

    event.listen(db_engine, "before_cursor_execute", capture)
    try:
        with Session(db_engine) as db:
            update_by_key(db, ManualSection, [section], {"title": "새 복장 규정"})
            version = db.get(ManualVersion, a)
            original_value = getattr(version, comparison)
            db.execute(update(ManualVersion).where(
                ManualVersion.id == a, getattr(ManualVersion, comparison) == original_value,
            ).values(updated_at=version.updated_at + timedelta(seconds=1)).execution_options(synchronize_session=False))
            db.commit()
    finally:
        event.remove(db_engine, "before_cursor_execute", capture)
    [parameters] = statements
    assert parameters["id_1"] == a
    assert parameters[f"{comparison}_1"] == original_value
    assert "revision" not in parameters and "generation_status" not in parameters
    [problem] = take_found()
    assert a in problem

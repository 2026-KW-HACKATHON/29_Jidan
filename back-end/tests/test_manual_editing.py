"""app.manual_editing.write_initial_content: the first content of a generated draft (#120 uses it)."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ManualReviewIssue, Store
from app.manual_content import content_body
from app.manual_editing import ContentIn, prepare_content, write_initial_content
from tests.factories import NOW, make_manual_draft, make_question_set, make_store
from tests.manual_factories import make_photo, make_published, sample_content


def test_initial_content_keeps_the_revision_and_links_gaps_to_intents(db_engine):
    with Session(db_engine) as db:
        store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        photo = make_photo(db, store)
        _set, intents = make_question_set(db)
        version = make_manual_draft(db, store, generation_status="RUNNING")
        content = sample_content(photo=photo.id)
        prepared = prepare_content(db, version, store.id, ContentIn.model_validate(content))
        night_gap = content["missingInformation"][0]["id"]
        write_initial_content(db, version, prepared, issue_intents={night_gap: intents[0].id}, now=NOW)
        db.commit()
        assert (version.revision, version.content_revision, version.updated_at) == (1, 1, NOW)
        assert content_body(db, version.id) == content
        issues = {i.id: i.intent_id for i in db.scalars(
            select(ManualReviewIssue).where(ManualReviewIssue.version_id == version.id))}
        assert issues == {night_gap: intents[0].id, content["missingInformation"][1]["id"]: None}
        with pytest.raises(ValueError):  # only into an empty draft
            write_initial_content(db, version, prepared)


def test_initial_content_is_refused_for_a_published_version(db_engine):
    with Session(db_engine) as db:
        store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        published = make_published(db, store)
        empty = sample_content()
        db.commit()
        with pytest.raises(ValueError):
            write_initial_content(db, published, prepare_content(
                db, make_manual_draft(db, db.get(Store, store.id)), store.id, ContentIn.model_validate(empty)))

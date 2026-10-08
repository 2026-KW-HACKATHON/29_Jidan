"""app.manual_content lookups under MySQL REPEATABLE READ (TEAM_BRIEF §9)."""

import pytest
from sqlalchemy.orm import Session

from app.db.models import Store
from app.manual_content import active_draft, store_manual
from tests.factories import NOW, make_store
from tests.manual_factories import make_published, make_ready_draft


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_active_draft_refresh_sees_a_draft_committed_after_the_first_read(db_engine):
    with Session(db_engine) as db:
        store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        make_published(db, store)  # the manual row exists, no draft yet
        db.commit()
        store_id = store.id
    with Session(db_engine) as a, Session(db_engine) as b:
        a.get(Store, store_id)  # A's snapshot starts here (an auth dependency's read)
        draft = make_ready_draft(b, b.get(Store, store_id))
        b.commit()
        manual = store_manual(a, store_id, lock=True)
        assert active_draft(a, manual.id) is None  # a plain read still sees A's old snapshot
        found = active_draft(a, manual.id, refresh=True)
        assert found is not None and found.id == draft.id
        a.rollback()

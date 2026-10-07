"""Demo seed (#122): guards, repeatable reset and a consistent data set, SQLite and MySQL."""
import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import demo_seed
from app.db.models import (
    Base,
    JobApplication,
    JobPosting,
    ShiftAssignment,
    Store,
    StoreAccessGrant,
    StoreInvitation,
    User,
    WorkRequest,
)
from tests.factories import make_application, make_store, make_user, make_worker
from tests.jobs_support import as_user

# Tables the reset handles; a new table pointing at these must be added to demo_seed.reset.
HANDLED = {
    "users", "stores", "store_approval_requests", "worker_profiles", "worker_careers", "availability_rules",
    "availability_days", "favorite_stores", "job_postings", "job_applications", "application_careers",
    "work_requests", "application_selection_effects", "shift_assignments", "store_access_grants",
    "store_invitations", "invitation_mail_outbox", "notifications", "auth_sessions",
    # app.manual_demo_reset
    "store_manuals", "manual_versions", "manual_shifts", "manual_sections", "manual_steps",
    "manual_photo_attachments", "manual_media", "manual_media_snapshot_refs", "qa_media",
    "media_transcriptions", "interview_sessions", "interview_session_intents", "interview_intent_reviews",
    "interview_review_confirmations", "interview_probe_batches", "interview_turns", "interview_turn_photos",
    "interview_evaluations", "manual_review_issues", "manual_issue_acknowledgements",
    "manual_draft_corrections", "manual_qa_conversations", "manual_qa", "manual_qa_citations",
    "manual_qa_photos",
}


@pytest.fixture
def safe_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", os.getenv("DB_NAME") or "jidan_seed_test")


def test_every_table_referencing_demo_rows_is_reset():
    reaching = set()
    changed = True
    while changed:
        changed = False
        for table in Base.metadata.sorted_tables:
            targets = {fk.column.table.name for fk in table.foreign_keys}
            if table.name not in reaching and (targets & ({"users"} | reaching)):
                reaching.add(table.name)
                changed = True
    assert reaching | {"users"} <= HANDLED, sorted((reaching | {"users"}) - HANDLED)


@pytest.mark.parametrize(("env", "name"), [
    ({"APP_ENV": "production"}, "jidan_local"), ({"APP_ENV": "staging"}, "jidan_local"),
    ({"APP_ENV": "local"}, "jidan"), ({"APP_ENV": "dev"}, "jidan_prod"), ({"APP_ENV": "local"}, ""),
    ({"APP_ENV": "local"}, "jidan_local_backup"),
])
def test_refuses_unsafe_targets(monkeypatch, env, name):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DB_NAME", name)
    with pytest.raises(demo_seed.UnsafeTarget):
        demo_seed.check_target()
    assert demo_seed.main() == 2


@pytest.mark.parametrize("name", ["jidan_local", "jidan_dev", "jidan_demo", "jidan_demo_test"])
def test_allows_demo_databases(monkeypatch, name):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", name)
    demo_seed.check_target()


def snapshot(db_engine) -> dict:
    with Session(db_engine) as db:
        return {
            "users": sorted(db.scalars(select(User.id))),
            "stores": sorted((s.id, s.approval_status) for s in db.scalars(select(Store))),
            "jobs": sorted((j.id, j.status) for j in db.scalars(select(JobPosting))),
            "applications": sorted((a.job_id, a.worker_id, a.status) for a in db.scalars(select(JobApplication))),
            "requests": sorted(db.scalars(select(WorkRequest.status))),
            "shifts": db.scalar(select(func.count()).select_from(ShiftAssignment)),
            "grants": sorted((g.invitation_id is None, g.revoked_at is None) for g in db.scalars(select(StoreAccessGrant))),
            "invitations": sorted((i.id, i.accepted_at is not None) for i in db.scalars(select(StoreInvitation))),
        }


def test_reseeding_is_repeatable_and_scoped(db_engine, safe_env):
    with Session(db_engine) as db:  # someone else's data, which must survive
        outsider = make_worker(db)
        other_store = make_store(db, make_user(db, "OWNER"))
        db.commit()
        outsider_id, other_store_id = outsider.id, other_store.id
    first_ids = demo_seed.run()
    first = snapshot(db_engine)
    with Session(db_engine) as db:  # an outsider applies to a demo posting between runs
        make_application(db, db.get(JobPosting, first_ids["open_job"]), db.get(User, outsider_id))
        db.commit()
    second_ids = demo_seed.run()
    assert second_ids == first_ids
    assert snapshot(db_engine) == first
    with Session(db_engine) as db:
        assert db.get(User, outsider_id) is not None and db.get(Store, other_store_id) is not None
        assert db.scalar(select(func.count()).select_from(JobApplication)
                         .where(JobApplication.worker_id == outsider_id)) == 0


def test_seeded_state(db_engine, safe_env):
    ids = demo_seed.run()
    with Session(db_engine) as db:
        assert db.get(Store, ids["cafe"]).approval_status == "APPROVED"
        assert db.get(Store, ids["snack"]).approval_status == "PENDING"
        assert sorted(j.status for j in db.scalars(select(JobPosting))) == [
            "CLOSED", "CLOSED", "CLOSED", "RECRUITING", "RECRUITING"]
        assert sorted(db.scalars(select(WorkRequest.status))) == ["ACCEPTED", "ACCEPTED", "PENDING"]
        statuses = sorted(db.scalars(select(JobApplication.status)))
        assert statuses == ["APPLIED", "CONFIRMED", "CONFIRMED", "NOT_SELECTED", "NOT_SELECTED", "REQUESTED"]
        assert db.scalar(select(ShiftAssignment.worker_id).where(ShiftAssignment.job_id == ids["confirmed_job"])) \
            == ids["worker:jisu"]
        assert db.scalar(select(func.count()).select_from(StoreInvitation)
                         .where(StoreInvitation.accepted_at.is_(None))) == 1


def test_seeded_screens_pass_the_contract(api, db_engine, safe_env):
    """Every main screen of the demo accounts answers within the OpenAPI contract."""
    ids = demo_seed.run()
    as_user(api, ids["owner"])
    month = demo_seed.common.seoul_today(demo_seed.utcnow()).strftime("%Y-%m")
    owner_screens = [
        "/api/owners/me/home", f"/api/owners/me/calendar/events?month={month}",
        f"/api/stores/{ids['cafe']}/job-postings", f"/api/stores/{ids['cafe']}/job-postings?status=CLOSED",
        f"/api/stores/{ids['cafe']}/job-postings/{ids['open_job']}/applications",
        f"/api/stores/{ids['cafe']}/job-postings/{ids['open_job']}/work-requests",
        f"/api/stores/{ids['cafe']}/job-postings/{ids['confirmed_job']}/onboarding",
        f"/api/stores/{ids['cafe']}/workers", f"/api/stores/{ids['cafe']}/invitations",
        f"/api/stores/{ids['cafe']}/management-summary", "/api/owners/me/stores",
    ]
    for url in owner_screens:
        assert api.get(url).status_code == 200, url
    for key in ("jisu", "minjun", "seoyeon", "doyoon"):
        as_user(api, ids[f"worker:{key}"])
        for url in ("/api/users/me/home", "/api/job-postings", "/api/users/me/applications?tab=ENDED",
                    "/api/users/me/work-requests?filter=ALL", f"/api/users/me/calendar/events?month={month}",
                    "/api/users/me/store-invitations", "/api/users/me/stores", "/api/users/me/profile"):
            assert api.get(url).status_code == 200, (key, url)
    as_user(api, ids["worker:seoyeon"])
    assert len(api.get("/api/users/me/work-requests").json()["items"]) == 1
    as_user(api, ids["worker:doyoon"])
    assert len(api.get("/api/users/me/store-invitations").json()["items"]) == 1


def test_seeded_notifications(api, db_engine, safe_env):
    """The seed records the notifications the API transitions would have; older ones are read."""
    from collections import Counter

    from app.db.models import Notification

    ids = demo_seed.run()
    expected = {
        "owner": ({"STORE_APPROVED": 1, "INVITATION_ACCEPTED": 1, "NEW_APPLICATION": 6, "WORK_CONFIRMED": 2}, 3),
        "worker:jisu": ({"WORK_REQUEST_RECEIVED": 2, "WORK_CONFIRMED": 2, "MANUAL_PUBLISHED": 1}, 2),
        "worker:seoyeon": ({"WORK_REQUEST_RECEIVED": 1}, 1),
        "worker:doyoon": ({"STORE_INVITED": 1}, 1),
        "worker:minjun": ({}, 0),
    }
    with Session(db_engine) as db:
        for name, (types, unread) in expected.items():
            rows = list(db.scalars(select(Notification).where(Notification.recipient_user_id == ids[name])))
            assert Counter(row.event_type for row in rows) == Counter(types), name
            assert sum(row.read_at is None for row in rows) == unread, name
    for name, (types, unread) in expected.items():
        as_user(api, ids[name])
        body = api.get("/api/users/me/notifications").json()
        assert body["unreadCount"] == unread and body["totalItems"] == sum(types.values()), name
    demo_seed.run()  # reseeding replaces them instead of adding duplicates
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 17

"""#118 published manual list and section detail (workers and owners), SQLite and MySQL."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import ManualSection, Store, StoreAccessGrant, User
from app.manual_content import content_body
from tests.api_contract import login
from tests.factories import (
    NOW,
    make_regular_grant,
    make_store,
    make_temporary_grant,
    make_user,
    make_worker,
)
from tests.manual_factories import (
    make_photo,
    make_published,
    make_ready_draft,
    publish_version,
    sample_content,
)


class Ctx:
    pass


@pytest.fixture
def ctx(api, db_engine):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW)
        worker = make_worker(db)
        make_regular_grant(db, store, worker)
        outsider = make_worker(db)
        other_owner = make_user(db, "OWNER")
        photo, board = make_photo(db, store), make_photo(db, store)
        content = sample_content(photo=photo.id, structure_photo=board.id)
        version = make_published(db, store, content)
        db.commit()
        context = Ctx()
        context.__dict__.update(
            api=api, engine=db_engine, owner=owner.id, store=store.id, worker=worker.id,
            outsider=outsider.id, other_owner=other_owner.id, content=content, version=version.id,
            photo=photo.id, board=board.id,
        )
    return context


def get(ctx, user_id, path="", **params):
    login(ctx.api, user_id)
    return ctx.api.get(f"/api/stores/{ctx.store}/manual/published{path}", params=params)


def section(ctx, index):
    return ctx.content["sections"][index]


def test_worker_reads_the_whole_published_manual_in_content_order(ctx):
    response = get(ctx, ctx.worker)
    assert response.status_code == 200
    body = response.json()
    assert body["versionId"] == ctx.version and body["storeId"] == ctx.store
    assert body["versionNumber"] == 1 and body["ownerConfirmed"] is True
    assert body["publishedAt"] == NOW.isoformat()
    assert body["shifts"] == ctx.content["shifts"]
    assert [s["id"] for s in body["sections"]] == [s["id"] for s in ctx.content["sections"]]
    assert [(s["stepCount"], s["photoCount"]) for s in body["sections"]] == [(2, 1), (0, 0), (1, 0)]
    assert body["structurePhotos"] == ctx.content["structurePhotos"]
    assert body["missingInformation"] == ctx.content["missingInformation"]


def test_owner_reads_own_published_manual(ctx):
    assert get(ctx, ctx.owner).json()["versionId"] == ctx.version


def test_filters_keep_common_items_and_the_selected_shift(ctx):
    day, night = (shift["id"] for shift in ctx.content["shifts"])
    common = [section(ctx, 1)["id"], section(ctx, 2)["id"]]
    assert [s["id"] for s in get(ctx, ctx.worker, filter="COMMON").json()["sections"]] == common
    shift_view = get(ctx, ctx.worker, filter="SHIFT", shiftId=day).json()
    assert [s["id"] for s in shift_view["sections"]] == [section(ctx, 0)["id"], *common]
    # A shift without its own tasks shows only the common items; shifts are always listed.
    night_view = get(ctx, ctx.worker, filter="SHIFT", shiftId=night.upper()).json()
    assert [s["id"] for s in night_view["sections"]] == common
    assert len(night_view["shifts"]) == 2


@pytest.mark.parametrize("params", [
    {"filter": "SHIFT"},
    {"filter": "ALL", "shiftId": str(uuid.uuid4())},
    {"filter": "COMMON", "shiftId": str(uuid.uuid4())},
    {"filter": "TODAY"},
    {"filter": "SHIFT", "shiftId": "not-a-uuid"},
    {"expectedVersionId": "x"},
])
def test_invalid_filter_combinations_are_422(ctx, params):
    response = get(ctx, ctx.worker, **params)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_shift_of_another_version_is_not_found(ctx):
    response = get(ctx, ctx.worker, filter="SHIFT", shiftId=str(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json()["code"] == "MANUAL_RESOURCE_NOT_FOUND"


def test_expected_version_detects_a_replaced_manual(ctx):
    assert get(ctx, ctx.worker, expectedVersionId=ctx.version.upper()).status_code == 200
    with Session(ctx.engine) as db:
        newer = make_published(db, db.get(Store, ctx.store), at=NOW + timedelta(days=1))
        db.commit()
        newer_id = newer.id
    stale = get(ctx, ctx.worker, expectedVersionId=ctx.version)
    assert stale.status_code == 409 and stale.json()["code"] == "MANUAL_VERSION_CHANGED"
    stale_section = get(ctx, ctx.worker, f"/sections/{section(ctx, 0)['id']}", expectedVersionId=ctx.version)
    assert stale_section.status_code == 409 and stale_section.json()["code"] == "MANUAL_VERSION_CHANGED"
    current = get(ctx, ctx.worker)
    assert current.json()["versionId"] == newer_id and current.json()["versionNumber"] == 2
    # The old version's sections are not reachable through the current manual.
    old = get(ctx, ctx.worker, f"/sections/{section(ctx, 0)['id']}")
    assert old.status_code == 404 and old.json()["code"] == "MANUAL_RESOURCE_NOT_FOUND"


def test_section_detail_has_steps_photos_and_only_related_gaps(ctx):
    task = section(ctx, 0)
    body = get(ctx, ctx.worker, f"/sections/{task['id'].upper()}", expectedVersionId=ctx.version).json()
    assert body["section"] == task
    assert body["versionId"] == ctx.version and body["ownerConfirmed"] is True
    assert body["missingInformation"] == []  # the day shift is complete
    empty = section(ctx, 1)
    detail = get(ctx, ctx.worker, f"/sections/{empty['id']}").json()
    assert detail["section"]["steps"] == []
    assert detail["missingInformation"] == [ctx.content["missingInformation"][1]]


def test_section_of_a_shift_with_gaps_lists_the_shift_gap(api, db_engine):
    with Session(db_engine) as db:
        store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        content = sample_content()
        night = content["shifts"][1]["id"]
        night_task = {"id": str(uuid.uuid4()), "category": "SHIFT_TASK", "shiftId": night, "title": "마감",
                      "steps": [{"id": str(uuid.uuid4()), "instruction": "불을 끄세요.", "checklistItem": False}],
                      "photos": []}
        content["sections"].append(night_task)
        make_published(db, store, content)
        db.commit()
        owner_id, store_id = store.owner_id, store.id
    login(api, owner_id)
    body = api.get(f"/api/stores/{store_id}/manual/published/sections/{night_task['id']}").json()
    assert body["missingInformation"] == [content["missingInformation"][0]]


def test_draft_is_never_served_to_readers(ctx):
    with Session(ctx.engine) as db:
        draft = make_ready_draft(db, db.get(Store, ctx.store))
        db.commit()
        draft_section = db.scalars(
            select(ManualSection.id).where(ManualSection.version_id == draft.id)).first()
    assert get(ctx, ctx.worker).json()["versionId"] == ctx.version
    response = get(ctx, ctx.worker, f"/sections/{draft_section}")
    assert response.status_code == 404 and response.json()["code"] == "MANUAL_RESOURCE_NOT_FOUND"


def test_unpublished_manual_is_404_after_the_access_check(api, db_engine):
    with Session(db_engine) as db:
        store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        worker, stranger = make_worker(db), make_worker(db)
        make_regular_grant(db, store, worker)
        make_ready_draft(db, store)  # a draft alone is not published
        db.commit()
        ids = (store.id, store.owner_id, worker.id, stranger.id)
    store_id, owner_id, worker_id, stranger_id = ids
    for user, code in ((owner_id, "MANUAL_NOT_PUBLISHED"), (worker_id, "MANUAL_NOT_PUBLISHED"),
                       (stranger_id, "STORE_NOT_FOUND")):
        login(api, user)
        for path in ("", f"/sections/{uuid.uuid4()}"):
            response = api.get(f"/api/stores/{store_id}/manual/published{path}")
            assert (response.status_code, response.json()["code"]) == (404, code)


@pytest.mark.parametrize("who", ["outsider", "other_owner"])
def test_readers_without_rights_see_store_not_found(ctx, who):
    for path in ("", f"/sections/{section(ctx, 0)['id']}"):
        response = get(ctx, getattr(ctx, who), path)
        assert (response.status_code, response.json()["code"]) == (404, "STORE_NOT_FOUND")
    login(ctx.api, ctx.worker)
    missing = ctx.api.get(f"/api/stores/{uuid.uuid4()}/manual/published")
    assert (missing.status_code, missing.json()["code"]) == (404, "STORE_NOT_FOUND")


def _set_grant(ctx, **values):
    with Session(ctx.engine) as db:
        db.execute(update(StoreAccessGrant).where(StoreAccessGrant.worker_id == ctx.worker).values(**values))
        db.commit()


def test_ended_access_blocks_the_worker_at_the_exact_end(ctx, monkeypatch):
    end = utcnow() + timedelta(hours=1)
    _set_grant(ctx, valid_until=end)
    monkeypatch.setattr("app.manual_published.utcnow", lambda: end - timedelta(microseconds=1))
    assert get(ctx, ctx.worker).status_code == 200
    monkeypatch.setattr("app.manual_published.utcnow", lambda: end)
    response = get(ctx, ctx.worker, f"/sections/{section(ctx, 0)['id']}")
    assert (response.status_code, response.json()["code"]) == (404, "STORE_NOT_FOUND")


@pytest.mark.parametrize("change", ["revoked", "not_started"])
def test_revoked_or_future_access_is_not_access(ctx, change):
    if change == "revoked":
        _set_grant(ctx, revoked_at=utcnow() - timedelta(seconds=1))
    else:
        _set_grant(ctx, granted_at=utcnow() + timedelta(hours=1))
    response = get(ctx, ctx.worker)
    assert (response.status_code, response.json()["code"]) == (404, "STORE_NOT_FOUND")


def test_temporary_access_reads_while_valid(ctx):
    with Session(ctx.engine) as db:
        make_temporary_grant(db, db.get(Store, ctx.store), db.get(User, ctx.outsider),
                             valid_until=utcnow() + timedelta(hours=2))
        db.commit()
    assert get(ctx, ctx.outsider).status_code == 200


def test_store_of_a_suspended_owner_is_403_for_workers_with_access_only(ctx):
    with Session(ctx.engine) as db:
        db.get(User, ctx.owner).status = "SUSPENDED"
        db.commit()
    for path in ("", f"/sections/{section(ctx, 0)['id']}"):
        response = get(ctx, ctx.worker, path)
        assert (response.status_code, response.json()["code"]) == (403, "STORE_APPROVAL_REQUIRED")
        outsider = get(ctx, ctx.outsider, path)  # no valid grant: the store stays hidden
        assert (outsider.status_code, outsider.json()["code"]) == (404, "STORE_NOT_FOUND")


def test_store_that_lost_approval_is_403_for_workers_and_owner(ctx):
    with Session(ctx.engine) as db:
        store = db.get(Store, ctx.store)
        store.approval_status, store.approved_at = "PENDING", None
        db.commit()
    for user in (ctx.worker, ctx.owner):
        response = get(ctx, user)
        assert (response.status_code, response.json()["code"]) == (403, "STORE_APPROVAL_REQUIRED")
    outsider = get(ctx, ctx.outsider)  # no valid grant: the store stays hidden
    assert (outsider.status_code, outsider.json()["code"]) == (404, "STORE_NOT_FOUND")


def test_previous_version_rows_stay_immutable_after_republishing(ctx):
    with Session(ctx.engine) as db:
        store = db.get(Store, ctx.store)
        newer = make_ready_draft(db, store)
        publish_version(db, newer, at=NOW + timedelta(hours=1))
        db.commit()
    with Session(ctx.engine) as db:
        assert content_body(db, ctx.version) == ctx.content


def test_published_structure_is_the_ai_view_of_published_versions_only(ctx):
    from app.manual_content import published_structure

    with Session(ctx.engine) as db:
        other_store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        foreign = make_published(db, other_store)
        draft = make_ready_draft(db, db.get(Store, ctx.store))
        db.commit()
        snapshot = published_structure(db, ctx.version.upper(), store_id=ctx.store)
        assert snapshot == published_structure(db, ctx.version)
        assert [s.id for s in snapshot.shifts] == [s["id"] for s in ctx.content["shifts"]]
        # The section with a photo is there with its steps; photos never reach the model.
        task = snapshot.sections[0]
        assert task.id == section(ctx, 0)["id"]
        assert [step.instruction for step in task.steps] == [
            step["instruction"] for step in section(ctx, 0)["steps"]]
        assert "photos" not in task.model_dump()
        assert [m.id for m in snapshot.missing_information] == [
            m["id"] for m in ctx.content["missingInformation"]]
        assert published_structure(db, draft.id) is None  # not published
        assert published_structure(db, str(uuid.uuid4())) is None
        assert published_structure(db, foreign.id, store_id=ctx.store) is None  # another store
        assert published_structure(db, foreign.id) is not None

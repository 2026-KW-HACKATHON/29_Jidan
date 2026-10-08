"""#118 owner manual state, draft, edit, preview, acknowledgements and publication."""

import copy
import threading
import uuid
from collections import Counter
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import (
    ManualDraftCorrection,
    ManualIssueAcknowledgement,
    ManualMedia,
    ManualReviewIssue,
    ManualVersion,
    Notification,
    Store,
    StoreManual,
    User,
)
from tests.api_contract import ORIGIN, login
from tests.factories import (
    NOW,
    make_interview,
    make_manual_draft,
    make_question_set,
    make_regular_grant,
    make_store,
    make_user,
    make_worker,
)
from tests.manual_factories import (
    make_photo,
    make_ready_draft,
    new_id,
    sample_content,
)


class Ctx:
    pass


@pytest.fixture
def ctx(api, db_engine):
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW)
        photo, spare = make_photo(db, store), make_photo(db, store)
        content = sample_content(photo=photo.id)
        draft = make_ready_draft(db, store, content)
        worker = make_worker(db)
        make_regular_grant(db, store, worker)
        db.commit()
        context = Ctx()
        context.__dict__.update(
            api=api, engine=db_engine, owner=owner.id, store=store.id, draft=draft.id, content=content,
            photo=photo.id, spare=spare.id, worker=worker.id,
        )
    context.auth = login(api, context.owner)
    return context


def url(ctx, tail=""):
    return f"/api/stores/{ctx.store}/manual{tail}"


def draft(ctx):
    response = ctx.api.get(url(ctx, "/draft"))
    assert response.status_code == 200, response.text
    return response.json()


def put_content(ctx, content, *, revision=1, version=None, key=None):
    return ctx.api.put(url(ctx, "/draft/content"), headers=ctx.auth.headers(key or str(uuid.uuid4())), json={
        "expectedVersionId": version or ctx.draft, "expectedRevision": revision, "content": content})


def ack(ctx, issue_ids, *, revision=1, version=None, note=None, key=None):
    body = {"expectedVersionId": version or ctx.draft, "expectedRevision": revision, "issueIds": issue_ids,
            "confirmed": True}
    if note is not None:
        body["note"] = note
    return ctx.api.post(url(ctx, "/draft/acknowledgements"), headers=ctx.auth.headers(key or str(uuid.uuid4())),
                        json=body)


def publish(ctx, issue_ids, *, revision=1, version=None, key=None, client=None, auth=None):
    return (client or ctx.api).post(
        url(ctx, "/draft/publication"), headers=(auth or ctx.auth).headers(key or str(uuid.uuid4())),
        json={"expectedVersionId": version or ctx.draft, "expectedRevision": revision, "confirmed": True,
              "acknowledgedIssueIds": issue_ids})


def gap_ids(ctx):
    return [item["id"] for item in ctx.content["missingInformation"]]


def code(response):
    return response.status_code, response.json()["code"]


def scalar(ctx, statement):
    with Session(ctx.engine) as db:
        return db.scalar(statement)


# --- state, draft, preview ----------------------------------------------------------------------


def test_state_of_a_store_without_manual_is_all_null(api, db_engine):
    with Session(db_engine) as db:
        store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        db.commit()
        store_id, owner_id = store.id, store.owner_id
    login(api, owner_id)
    assert api.get(f"/api/stores/{store_id}/manual").json() == {
        "storeId": store_id, "currentPublishedVersionId": None, "draftVersionId": None, "interviewSessionId": None}
    response = api.get(f"/api/stores/{store_id}/manual/draft")
    assert code(response) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    assert code(api.get(f"/api/stores/{store_id}/manual/draft/preview")) == (404, "MANUAL_RESOURCE_NOT_FOUND")


def test_state_shows_published_draft_and_interview(ctx):
    with Session(ctx.engine) as db:
        version = db.get(ManualVersion, ctx.draft)
        question_set, intents = make_question_set(db)
        interview = make_interview(db, version, question_set, intents, status="COMPLETED",
                                   completed_at=NOW, current_intent_id=None)
        db.commit()
        session_id = interview.id
    assert ctx.api.get(url(ctx)).json() == {
        "storeId": ctx.store, "currentPublishedVersionId": None, "draftVersionId": ctx.draft,
        "interviewSessionId": session_id}
    assert draft(ctx)["interviewSessionId"] == session_id
    published = publish(ctx, gap_ids(ctx))
    assert published.status_code == 200
    with Session(ctx.engine) as db:
        newer = make_manual_draft(db, db.get(Store, ctx.store))
        db.commit()
        newer_id = newer.id
    assert ctx.api.get(url(ctx)).json() == {
        "storeId": ctx.store, "currentPublishedVersionId": ctx.draft, "draftVersionId": newer_id,
        "interviewSessionId": None}


def test_ready_draft_body(ctx):
    body = draft(ctx)
    assert body["versionId"] == ctx.draft and body["versionNumber"] == 1 and body["revision"] == 1
    assert body["status"] == "DRAFT" and body["generationStatus"] == "READY"
    assert body["content"] == ctx.content and body["latestCorrection"] is None
    assert [(i["id"], i["status"], i["intentId"]) for i in body["issues"]] == [
        (gap, "OPEN", None) for gap in gap_ids(ctx)]


@pytest.mark.parametrize("status", ["NOT_STARTED", "RUNNING", "ERROR"])
def test_draft_before_generation_has_no_content_and_no_preview(api, db_engine, status):
    with Session(db_engine) as db:
        store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        version = make_manual_draft(db, store, generation_status=status)
        db.commit()
        store_id, owner_id, version_id = store.id, store.owner_id, version.id
    auth = login(api, owner_id)
    body = api.get(f"/api/stores/{store_id}/manual/draft").json()
    assert (body["generationStatus"], body["content"], body["issues"]) == (status, None, [])
    assert code(api.get(f"/api/stores/{store_id}/manual/draft/preview")) == (409, "MANUAL_NOT_READY")
    edit = api.put(f"/api/stores/{store_id}/manual/draft/content", headers=auth.headers(str(uuid.uuid4())),
                   json={"expectedVersionId": version_id, "expectedRevision": 1, "content": sample_content()})
    assert code(edit) == (409, "MANUAL_STATE_CONFLICT")
    published = api.post(f"/api/stores/{store_id}/manual/draft/publication", headers=auth.headers(str(uuid.uuid4())),
                         json={"expectedVersionId": version_id, "expectedRevision": 1, "confirmed": True,
                               "acknowledgedIssueIds": []})
    assert code(published) == (409, "MANUAL_NOT_READY")


def test_preview_is_the_saved_content_without_issues(ctx):
    response = ctx.api.get(url(ctx, "/draft/preview"))
    assert response.json() == {"preview": True, "versionId": ctx.draft, "revision": 1, "content": ctx.content}


@pytest.mark.parametrize("path", ["", "/draft", "/draft/preview"])
def test_reads_hide_other_stores_and_need_approval(ctx, path):
    with Session(ctx.engine) as db:
        stranger = make_user(db, "OWNER")
        pending = make_store(db, owner=db.get(User, ctx.owner))
        db.commit()
        stranger_id, pending_id = stranger.id, pending.id
    assert code(ctx.api.get(f"/api/stores/{pending_id}/manual{path}")) == (403, "STORE_APPROVAL_REQUIRED")
    login(ctx.api, stranger_id)
    assert code(ctx.api.get(url(ctx, path))) == (404, "STORE_NOT_FOUND")


# --- content edit -------------------------------------------------------------------------------


def edited(ctx):
    content = copy.deepcopy(ctx.content)
    night = content["shifts"][1]
    night["endTime"] = "06:00"  # fills the night shift's end time gap
    content["sections"][0]["title"] = "오픈 준비하기"
    new_section = new_id()
    content["sections"].append({"id": new_section, "category": "EQUIPMENT", "shiftId": None,
                                "title": "커피 머신", "steps": [], "photos": []})
    gap = {"id": new_id(), "target": "SECTION", "targetId": new_section, "field": "steps",
           "description": "커피 머신 사용법이 아직 없어요."}
    content["missingInformation"] = [content["missingInformation"][1], gap]
    return content, gap


def test_real_edit_bumps_revision_and_syncs_issues(ctx):
    content, gap = edited(ctx)
    response = put_content(ctx, content)
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 2 and body["content"]["sections"][0]["title"] == "오픈 준비하기"
    assert body["content"]["missingInformation"] == content["missingInformation"]
    assert [i["id"] for i in body["issues"]] == [ctx.content["missingInformation"][1]["id"], gap["id"]]
    assert body["issues"][1]["intentId"] is None
    with Session(ctx.engine) as db:
        filled = db.get(ManualReviewIssue, gap_ids(ctx)[0])
        assert filled.resolved_at is not None  # kept for history, gone from the list
        version = db.get(ManualVersion, ctx.draft)
        assert (version.revision, version.content_revision) == (2, 2)
    assert draft(ctx)["content"] == body["content"]


def test_identical_edit_changes_nothing(ctx):
    before = draft(ctx)
    response = put_content(ctx, copy.deepcopy(ctx.content) | {
        "missingInformation": list(reversed(ctx.content["missingInformation"]))})
    assert response.status_code == 200
    assert response.json()["revision"] == 1 and response.json()["updatedAt"] == before["updatedAt"]


def test_reordering_is_a_change_and_ids_are_kept(ctx):
    content = copy.deepcopy(ctx.content)
    content["sections"].reverse()
    content["sections"][2]["steps"].reverse()
    body = put_content(ctx, content).json()
    assert body["revision"] == 2 and body["content"]["sections"] == content["sections"]


def test_uppercase_ids_are_the_same_items(ctx):
    content = copy.deepcopy(ctx.content)
    for shift in content["shifts"]:
        shift["id"] = shift["id"].upper()
    for section in content["sections"]:
        section["id"] = section["id"].upper()
        section["shiftId"] = section["shiftId"] and section["shiftId"].upper()
    for item in content["missingInformation"]:
        item["id"], item["targetId"] = item["id"].upper(), item["targetId"].upper()
    response = put_content(ctx, content, version=ctx.draft.upper())
    assert response.status_code == 200 and response.json()["revision"] == 1


def test_whole_day_and_overnight_spans(ctx):
    content = copy.deepcopy(ctx.content)
    content["shifts"][0].update(startTime="09:00", endTime="09:00", endsNextDay=True)
    content["shifts"][1].update(endTime="22:00")
    content["missingInformation"] = [content["missingInformation"][1]]
    assert put_content(ctx, content).status_code == 200


def mutate(ctx, change):
    content = copy.deepcopy(ctx.content)
    change(content)
    return content


INVALID_EDITS = {
    "duplicate-id": lambda c: c["sections"][1].update(id=c["shifts"][0]["id"]),
    "duplicate-step-id": lambda c: c["sections"][2]["steps"][0].update(id=c["sections"][0]["steps"][0]["id"]),
    "unknown-shift": lambda c: c["sections"][0].update(shiftId=new_id()),
    "shift-task-without-shift": lambda c: c["sections"][0].update(shiftId=None),
    "common-with-shift": lambda c: c["sections"][1].update(shiftId=c["shifts"][0]["id"]),
    "same-day-backwards": lambda c: c["shifts"][0].update(startTime="15:00", endTime="09:00"),
    "same-day-zero": lambda c: c["shifts"][0].update(endTime="09:00"),
    "overnight-over-24h": lambda c: c["shifts"][0].update(endsNextDay=True),
    "bad-time": lambda c: c["shifts"][0].update(startTime="24:00"),
    "blank-title": lambda c: c["sections"][0].update(title="   "),
    "blank-step": lambda c: c["sections"][0]["steps"][0].update(instruction=" \t"),
    "gap-on-known-value": lambda c: c["missingInformation"].append(
        {"id": new_id(), "target": "SHIFT", "targetId": c["shifts"][0]["id"], "field": "startTime",
         "description": "x"}),
    "unexplained-null": lambda c: c["missingInformation"].pop(0),
    "unexplained-empty-steps": lambda c: c["missingInformation"].pop(1),
    "duplicate-gap": lambda c: c["missingInformation"].append(
        {**c["missingInformation"][0], "id": new_id()}),
    "gap-for-other-target": lambda c: c["missingInformation"][1].update(targetId=new_id()),
    "manual-gap-with-target": lambda c: c["missingInformation"].append(
        {"id": new_id(), "target": "MANUAL", "targetId": c["shifts"][0]["id"], "field": "shifts", "description": "x"}),
    "no-shifts-unexplained": lambda c: (c.update(shifts=[], sections=[s for s in c["sections"] if s["shiftId"] is None]),
                                        c.update(missingInformation=[c["missingInformation"][1]])),
    "duplicate-photo": lambda c: c["sections"][0]["photos"].append(dict(c["sections"][0]["photos"][0])),
    "unknown-photo": lambda c: c["structurePhotos"].append({"mediaId": new_id(), "title": "x", "caption": None}),
    "extra-field": lambda c: c["shifts"][0].update(sortOrder=1),
    "too-many-shifts": lambda c: c.update(shifts=[dict(c["shifts"][0], id=new_id()) for _ in range(21)]),
}


@pytest.mark.parametrize("case", list(INVALID_EDITS))
def test_invalid_content_is_422_and_changes_nothing(ctx, case):
    response = put_content(ctx, mutate(ctx, INVALID_EDITS[case]))
    assert code(response) == (422, "VALIDATION_ERROR"), response.text
    assert response.json()["fieldErrors"]
    assert draft(ctx)["revision"] == 1


def test_ids_of_other_versions_or_kinds_are_rejected(ctx):
    with Session(ctx.engine) as db:
        other_store = make_store(db, approval_status="APPROVED", approved_at=NOW)
        other = sample_content()
        make_ready_draft(db, other_store, other)
        db.commit()
    def swap_gap_ids(c):
        first, second = c["missingInformation"]
        first["id"], second["id"] = second["id"], first["id"]

    def step_id_as_section(c):
        step = c["sections"][2]["steps"][0]
        c["sections"][1]["id"], step["id"] = step["id"], new_id()
        c["missingInformation"][1]["targetId"] = c["sections"][1]["id"]

    for change in (
        lambda c: c["shifts"][0].update(id=other["shifts"][0]["id"]),
        lambda c: c["sections"][2]["steps"][0].update(id=other["sections"][0]["steps"][0]["id"]),
        lambda c: c["missingInformation"][0].update(id=other["missingInformation"][0]["id"]),
        swap_gap_ids,  # a gap ID never moves to another target
        step_id_as_section,  # nor does an item ID change its kind
    ):
        response = put_content(ctx, mutate(ctx, change))
        assert code(response) == (422, "VALIDATION_ERROR"), response.text


def test_photos_must_be_live_images_of_the_store(ctx):
    with Session(ctx.engine) as db:
        store = db.get(Store, ctx.store)
        audio = make_photo(db, store, kind="AUDIO")
        deleted = make_photo(db, store, deleted_at=NOW)
        purged = make_photo(db, store, content_deleted_at=NOW)
        foreign = make_photo(db, make_store(db, approval_status="APPROVED", approved_at=NOW))
        db.commit()
        bad = [audio.id, deleted.id, purged.id, foreign.id]
    for media_id in bad:
        content = mutate(ctx, lambda c, m=media_id: c["sections"][2]["photos"].append(
            {"mediaId": m, "title": "사진 1", "caption": None}))
        assert code(put_content(ctx, content)) == (422, "VALIDATION_ERROR")


def test_photo_changes_link_new_and_release_removed(ctx):
    content = mutate(ctx, lambda c: (c["sections"][0].update(photos=[]), c.update(structurePhotos=[
        {"mediaId": ctx.spare, "title": "근무표", "caption": "설명"}])))
    body = put_content(ctx, content).json()
    assert body["revision"] == 2 and body["content"]["structurePhotos"][0]["mediaId"] == ctx.spare
    with Session(ctx.engine) as db:
        released = db.get(ManualMedia, ctx.photo)
        assert released.expires_at > utcnow() + timedelta(hours=23)  # a fresh grace period
    renamed = mutate(ctx, lambda c: None)
    renamed["sections"][0]["photos"] = []
    renamed["structurePhotos"] = [{"mediaId": ctx.spare, "title": "근무표 2", "caption": None}]
    assert put_content(ctx, renamed, revision=2).json()["revision"] == 3


def test_edit_conflicts(ctx):
    content = edited(ctx)[0]
    assert code(put_content(ctx, content, revision=2)) == (409, "REVISION_CONFLICT")
    assert code(put_content(ctx, content, version=new_id())) == (409, "MANUAL_VERSION_CONFLICT")
    assert put_content(ctx, content).status_code == 200
    assert code(put_content(ctx, content)) == (409, "REVISION_CONFLICT")


def test_published_version_with_the_same_revision_is_not_the_new_draft(ctx):
    assert publish(ctx, gap_ids(ctx)).status_code == 200  # A published at revision 2
    with Session(ctx.engine) as db:
        newer = make_ready_draft(db, db.get(Store, ctx.store), revision=2)
        db.commit()
        newer_id = newer.id
    stale = put_content(ctx, ctx.content, revision=2)
    assert code(stale) == (409, "MANUAL_VERSION_CONFLICT")
    assert code(ack(ctx, [new_id()], revision=2)) == (409, "MANUAL_VERSION_CONFLICT")
    assert code(publish(ctx, [], revision=2)) == (409, "MANUAL_VERSION_CONFLICT")
    with Session(ctx.engine) as db:
        assert db.get(ManualVersion, newer_id).revision == 2


def test_running_correction_blocks_edit_ack_and_publication(ctx):
    with Session(ctx.engine) as db:
        db.add(ManualDraftCorrection(
            version_id=ctx.draft, base_revision=1, target_kind="MANUAL", input_method="TEXT",
            input_text="고쳐 주세요", status="RUNNING", task_id=new_id(), requested_by_owner_id=ctx.owner))
        db.commit()
    assert code(put_content(ctx, edited(ctx)[0])) == (409, "MANUAL_CORRECTION_IN_PROGRESS")
    assert code(ack(ctx, gap_ids(ctx)[:1])) == (409, "MANUAL_CORRECTION_IN_PROGRESS")
    assert code(publish(ctx, gap_ids(ctx))) == (409, "MANUAL_CORRECTION_IN_PROGRESS")
    # Reads keep showing the last saved content while it runs.
    body = draft(ctx)
    assert body["content"] == ctx.content and body["latestCorrection"]["status"] == "RUNNING"
    assert ctx.api.get(url(ctx, "/draft/preview")).status_code == 200


def test_edit_replays_and_rejects_key_reuse(ctx):
    content = edited(ctx)[0]
    key = str(uuid.uuid4())
    first = put_content(ctx, content, key=key)
    again = put_content(ctx, content, key=key)
    assert again.headers.get("Idempotent-Replayed") == "true" and again.json() == first.json()
    assert draft(ctx)["revision"] == 2
    upper = put_content(ctx, content, key=key, version=ctx.draft.upper())  # the same normalized body
    assert upper.headers.get("Idempotent-Replayed") == "true"
    reused = put_content(ctx, content, key=key, revision=2)
    assert code(reused) == (409, "IDEMPOTENCY_KEY_REUSED")


@pytest.mark.parametrize("body", [
    {"expectedRevision": 1, "content": {"shifts": [], "sections": []}},
    {"expectedVersionId": None, "expectedRevision": 1, "content": {"shifts": [], "sections": []}},
    {"expectedVersionId": "abc", "expectedRevision": 1, "content": {"shifts": [], "sections": []}},
    {"expectedVersionId": str(uuid.uuid4()), "expectedRevision": 0, "content": {"shifts": [], "sections": []}},
    {"expectedVersionId": str(uuid.uuid4()), "expectedRevision": 1},
])
def test_edit_request_shape_is_422(ctx, body):
    response = ctx.api.put(url(ctx, "/draft/content"), headers=ctx.auth.headers(str(uuid.uuid4())), json=body)
    assert code(response) == (422, "VALIDATION_ERROR")


def test_writes_hide_other_stores(ctx):
    with Session(ctx.engine) as db:
        stranger = make_user(db, "OWNER")
        db.commit()
        stranger_id = stranger.id
    ctx.auth = login(ctx.api, stranger_id)
    assert code(put_content(ctx, ctx.content)) == (404, "STORE_NOT_FOUND")
    assert code(ack(ctx, gap_ids(ctx))) == (404, "STORE_NOT_FOUND")
    assert code(publish(ctx, gap_ids(ctx))) == (404, "STORE_NOT_FOUND")


# --- acknowledgements ---------------------------------------------------------------------------


def test_acknowledging_keeps_content_and_survives_other_acknowledgements(ctx):
    first, second = gap_ids(ctx)
    body = ack(ctx, [first], note="야간 종료는 매일 달라요").json()
    assert body["revision"] == 2
    issue = next(i for i in body["issues"] if i["id"] == first)
    assert issue["status"] == "ACKNOWLEDGED" and issue["ownerNote"] == "야간 종료는 매일 달라요"
    assert body["content"] == ctx.content
    later = ack(ctx, [second], revision=2).json()
    assert later["revision"] == 3
    assert [i["status"] for i in later["issues"]] == ["ACKNOWLEDGED", "ACKNOWLEDGED"]
    assert later["issues"][0]["acknowledgedAt"] == issue["acknowledgedAt"]


def test_same_acknowledgement_again_changes_nothing_and_note_change_does(ctx):
    first = gap_ids(ctx)[0]
    body = ack(ctx, [first], note="메모").json()
    again = ack(ctx, [first], revision=2, note="메모").json()
    assert again["revision"] == 2 and again["issues"] == body["issues"]
    changed = ack(ctx, [first], revision=2, note="다른 메모").json()
    assert changed["revision"] == 3 and changed["issues"][0]["ownerNote"] == "다른 메모"
    cleared = ack(ctx, [first], revision=3).json()
    assert cleared["revision"] == 4 and cleared["issues"][0]["ownerNote"] is None
    assert scalar(ctx, select(func.count()).select_from(ManualIssueAcknowledgement)) == 3


def test_content_change_reopens_acknowledged_issues_and_keeps_history(ctx):
    second = gap_ids(ctx)[1]
    ack(ctx, [second])
    content = mutate(ctx, lambda c: c["sections"][0].update(title="오픈"))
    body = put_content(ctx, content, revision=2).json()
    assert body["revision"] == 3
    assert [i["status"] for i in body["issues"]] == ["OPEN", "OPEN"]
    assert scalar(ctx, select(func.count()).select_from(ManualIssueAcknowledgement)) == 1


def test_acknowledgement_errors(ctx):
    with Session(ctx.engine) as db:
        other = sample_content()
        make_ready_draft(db, make_store(db, approval_status="APPROVED", approved_at=NOW), other)
        db.commit()
    assert code(ack(ctx, [new_id()])) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    assert code(ack(ctx, [other["missingInformation"][0]["id"]])) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    gap = gap_ids(ctx)[0]
    assert code(ack(ctx, [gap, gap.upper()])) == (422, "VALIDATION_ERROR")
    assert code(ack(ctx, [])) == (422, "VALIDATION_ERROR")
    assert code(ack(ctx, [gap], note=" ")) == (422, "VALIDATION_ERROR")
    false = ctx.api.post(url(ctx, "/draft/acknowledgements"), headers=ctx.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": ctx.draft, "expectedRevision": 1, "issueIds": [gap], "confirmed": False})
    assert code(false) == (422, "VALIDATION_ERROR")
    assert code(ack(ctx, [gap], revision=5)) == (409, "REVISION_CONFLICT")
    assert draft(ctx)["revision"] == 1


def test_resolved_issue_cannot_be_acknowledged(ctx):
    put_content(ctx, edited(ctx)[0])
    assert code(ack(ctx, [gap_ids(ctx)[0]], revision=2)) == (404, "MANUAL_RESOURCE_NOT_FOUND")


# --- publication --------------------------------------------------------------------------------


def test_publication_requires_every_open_issue(ctx):
    first, second = gap_ids(ctx)
    response = publish(ctx, [first])
    assert code(response) == (409, "MANUAL_REVIEW_REQUIRED")
    assert code(publish(ctx, [first, second, new_id()])) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    with Session(ctx.engine) as db:
        assert db.get(ManualVersion, ctx.draft).status == "DRAFT"
        assert db.scalar(select(StoreManual.current_published_version_id)) is None
        assert db.scalar(select(func.count()).select_from(ManualIssueAcknowledgement)) == 0
        assert db.scalar(select(func.count()).select_from(Notification)) == 0


def test_already_acknowledged_issues_need_not_be_resent(ctx):
    first, second = gap_ids(ctx)
    ack(ctx, [first])
    assert code(publish(ctx, [], revision=2)) == (409, "MANUAL_REVIEW_REQUIRED")
    assert publish(ctx, [second], revision=2).status_code == 200


def test_publication_swaps_the_pointer_and_notifies_current_readers(ctx):
    with Session(ctx.engine) as db:
        store = db.get(Store, ctx.store)
        expired, suspended = make_worker(db), make_worker(db)
        make_worker(db)  # no grant at all
        make_regular_grant(db, store, expired, valid_until=utcnow() - timedelta(minutes=1))
        make_regular_grant(db, store, suspended)
        suspended.status = "SUSPENDED"
        db.commit()
    key = str(uuid.uuid4())
    response = publish(ctx, gap_ids(ctx), key=key)
    assert response.status_code == 200
    body = response.json()
    assert body["versionId"] == ctx.draft and body["status"] == "PUBLISHED" and body["ownerConfirmed"] is True
    assert body["content"] == ctx.content and body["versionNumber"] == 1
    with Session(ctx.engine) as db:
        assert db.scalar(select(StoreManual.current_published_version_id)) == ctx.draft
        rows = db.scalars(select(Notification)).all()
        assert [(n.recipient_user_id, n.event_type, n.target_kind, n.target_id) for n in rows] == [
            (ctx.worker, "MANUAL_PUBLISHED", "MANUAL", ctx.store)]
        assert rows[0].dedupe_key == f"MANUAL_PUBLISHED:{ctx.draft}"
        assert db.scalar(select(func.count()).select_from(ManualIssueAcknowledgement)) == 2
    replay = publish(ctx, gap_ids(ctx), key=key)
    assert replay.headers.get("Idempotent-Replayed") == "true" and replay.json() == body
    assert code(publish(ctx, gap_ids(ctx))) == (409, "MANUAL_VERSION_CONFLICT")
    assert scalar(ctx, select(func.count()).select_from(Notification)) == 1
    # The worker now reads it; the owner has no draft any more.
    assert code(ctx.api.get(url(ctx, "/draft"))) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    login(ctx.api, ctx.worker)
    assert ctx.api.get(url(ctx, "/published")).json()["missingInformation"] == ctx.content["missingInformation"]


def test_republication_keeps_the_previous_version_until_the_new_one(ctx):
    publish(ctx, gap_ids(ctx))
    with Session(ctx.engine) as db:
        newer = make_ready_draft(db, db.get(Store, ctx.store), sample_content())
        db.commit()
        newer_id = newer.id
        newer_gaps = [i.id for i in db.scalars(select(ManualReviewIssue).where(ManualReviewIssue.version_id == newer_id))]
    login(ctx.api, ctx.worker)
    assert ctx.api.get(url(ctx, "/published")).json()["versionId"] == ctx.draft
    ctx.auth = login(ctx.api, ctx.owner)
    body = publish(ctx, newer_gaps, version=newer_id).json()
    assert body["versionNumber"] == 2
    with Session(ctx.engine) as db:
        assert db.get(ManualVersion, ctx.draft).status == "PUBLISHED"
        assert db.scalar(select(StoreManual.current_published_version_id)) == newer_id
        assert db.scalar(select(func.count()).select_from(Notification)) == 2  # one per version


def test_publication_rechecks_photos(ctx):
    with Session(ctx.engine) as db:
        db.get(ManualMedia, ctx.photo).deleted_at = NOW
        db.commit()
    assert code(publish(ctx, gap_ids(ctx))) == (422, "VALIDATION_ERROR")
    assert scalar(ctx, select(ManualVersion.status).where(ManualVersion.id == ctx.draft)) == "DRAFT"


def test_unfinished_interview_blocks_publication(ctx):
    with Session(ctx.engine) as db:
        question_set, intents = make_question_set(db)
        make_interview(db, db.get(ManualVersion, ctx.draft), question_set, intents)
        db.commit()
    assert code(publish(ctx, gap_ids(ctx))) == (409, "MANUAL_NOT_READY")
    assert code(put_content(ctx, ctx.content)) == (409, "MANUAL_STATE_CONFLICT")


@pytest.mark.parametrize("body", [
    {"expectedRevision": 1, "confirmed": True, "acknowledgedIssueIds": []},
    {"expectedVersionId": str(uuid.uuid4()), "expectedRevision": 1, "confirmed": False, "acknowledgedIssueIds": []},
    {"expectedVersionId": str(uuid.uuid4()), "expectedRevision": 1, "confirmed": True},
    {"expectedVersionId": str(uuid.uuid4()), "expectedRevision": 1, "confirmed": True, "acknowledgedIssueIds": ["x"]},
])
def test_publication_request_shape_is_422(ctx, body):
    response = ctx.api.post(url(ctx, "/draft/publication"), headers=ctx.auth.headers(str(uuid.uuid4())), json=body)
    assert code(response) == (422, "VALIDATION_ERROR")


# --- MySQL concurrency --------------------------------------------------------------------------


def _parallel(ctx, count, request):
    from app.main import app

    barrier = threading.Barrier(count)
    results = []

    def run(index):
        with TestClient(app, raise_server_exceptions=False) as client:
            auth = login(client, ctx.owner)
            barrier.wait(10)
            results.append(request(client, auth, index))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_publications_publish_once(ctx, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    results = _parallel(ctx, 6, lambda client, auth, _i: code_or_ok(publish(ctx, gap_ids(ctx), client=client, auth=auth)))
    assert Counter(results) == Counter({200: 1, (409, "MANUAL_VERSION_CONFLICT"): 5}), results
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 1
        assert db.scalar(select(func.count()).select_from(ManualIssueAcknowledgement)) == 2


def code_or_ok(response):
    return 200 if response.status_code == 200 else code(response)


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_edits_of_the_same_revision_apply_once(ctx):
    def request(client, auth, index):
        content = mutate(ctx, lambda c: c["sections"][0].update(title=f"오픈 {index}"))
        return code_or_ok(client.put(url(ctx, "/draft/content"), headers=auth.headers(str(uuid.uuid4())), json={
            "expectedVersionId": ctx.draft, "expectedRevision": 1, "content": content}))

    results = _parallel(ctx, 6, request)
    assert Counter(results) == Counter({200: 1, (409, "REVISION_CONFLICT"): 5}), results
    assert draft(ctx)["revision"] == 2


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_edit_acknowledgement_and_publication_race_serialize(ctx):
    def request(client, auth, index):
        if index % 3 == 0:
            response = client.put(url(ctx, "/draft/content"), headers=auth.headers(str(uuid.uuid4())), json={
                "expectedVersionId": ctx.draft, "expectedRevision": 1,
                "content": mutate(ctx, lambda c: c["sections"][0].update(title=f"오픈 {index}"))})
        elif index % 3 == 1:
            response = client.post(url(ctx, "/draft/acknowledgements"), headers=auth.headers(str(uuid.uuid4())),
                                   json={"expectedVersionId": ctx.draft, "expectedRevision": 1,
                                         "issueIds": gap_ids(ctx), "confirmed": True})
        else:
            response = publish(ctx, gap_ids(ctx), client=client, auth=auth)
        return code_or_ok(response)

    results = _parallel(ctx, 9, request)
    assert results.count(200) == 1, results
    assert set(results) - {200} <= {(409, "REVISION_CONFLICT"), (409, "MANUAL_VERSION_CONFLICT")}, results

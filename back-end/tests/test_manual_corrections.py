"""#118 draft corrections: acceptance, DRAFT_CORRECTION task results, status and retries."""

import threading
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.ai.contracts import StructureSnapshot
from app.ai.fake import FakeOutcome, structure_to_raw
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    ManualDraftCorrection,
    ManualIssueAcknowledgement,
    ManualMedia,
    ManualVersion,
    MediaTranscription,
    Store,
)
from app.manual_content import structure_snapshot
from app.tasks import StaleTask, TaskContext, claim, drain, run_claimed
from tests.api_contract import login
from tests.factories import NOW, make_store, make_user
from tests.manual_factories import (
    make_photo,
    make_ready_draft,
    new_id,
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
        photo = make_photo(db, store)
        content = sample_content(photo=photo.id)
        draft = make_ready_draft(db, store, content)
        db.commit()
        context = Ctx()
        context.__dict__.update(api=api, engine=db_engine, owner=owner.id, store=store.id, draft=draft.id,
                                content=content, photo=photo.id)
    context.auth = login(api, context.owner)
    return context


def url(ctx, tail=""):
    return f"/api/stores/{ctx.store}/manual/draft{tail}"


def correct(ctx, *, text="야간조는 6시에 끝나요", target=None, revision=1, version=None, key=None, voice=None,
            client=None, auth=None):
    payload = {"method": "VOICE", "transcriptionId": voice} if voice else {"method": "TEXT", "text": text}
    return (client or ctx.api).post(url(ctx, "/corrections"), headers=(auth or ctx.auth).headers(key or str(uuid.uuid4())),
                                    json={"expectedVersionId": version or ctx.draft, "expectedRevision": revision,
                                          "target": target or {"kind": "MANUAL", "targetId": None}, "input": payload})


def retry(ctx, correction_id, *, revision=1, version=None, key=None):
    return ctx.api.post(url(ctx, f"/corrections/{correction_id}/retries"),
                        headers=ctx.auth.headers(key or str(uuid.uuid4())),
                        json={"expectedVersionId": version or ctx.draft, "expectedRevision": revision})


def status(ctx, correction_id):
    return ctx.api.get(url(ctx, f"/corrections/{correction_id}"))


def draft(ctx):
    return ctx.api.get(url(ctx)).json()


def code(response):
    return response.status_code, response.json()["code"]


def snapshot(ctx) -> StructureSnapshot:
    with Session(ctx.engine) as db:
        return structure_snapshot(db, ctx.draft)


def applied(raw: dict) -> FakeOutcome:
    return FakeOutcome.ok({"outcome": "APPLIED", "summary": None, "structure": raw})


def fill_night_end(ctx) -> dict:
    """The model's answer to "야간조는 6시에 끝나요": the end time is set, its gap goes away, and
    a new step is added to the rule section (new-1 placeholder)."""
    raw = structure_to_raw(snapshot(ctx))
    raw["shifts"][1]["end_time"] = "06:00"
    raw["missing_information"] = [m for m in raw["missing_information"] if m["field"] != "endTime"]
    raw["sections"][2]["steps"].append({"ref": "new-1", "instruction": "명찰을 다세요.", "checklist_item": True})
    return raw


# --- acceptance and success ---------------------------------------------------------------------


def test_accepted_correction_runs_and_applies_a_real_change(ctx, fake_ai):
    fake_ai.script("revise_structure", applied(fill_night_end(ctx)))
    response = correct(ctx)
    assert response.status_code == 202 and response.headers["Retry-After"] == "2"
    body = response.json()
    assert (body["status"], body["attempt"], body["baseRevision"], body["resultRevision"], body["error"]) == (
        "RUNNING", 1, 1, None, None)
    assert body["target"] == {"kind": "MANUAL", "targetId": None} and body["versionId"] == ctx.draft
    # Until the task runs, reads show the saved content and the running correction.
    before = draft(ctx)
    assert before["content"] == ctx.content and before["latestCorrection"] == body
    assert status(ctx, body["id"]).headers["Retry-After"] == "2"

    runs = drain()
    assert [run.outcome for run in runs] == ["succeeded"]
    call = fake_ai.calls_for("revise_structure")[0]
    assert call.data["instruction"] == "야간조는 6시에 끝나요" and call.data["require_manual_level"] is True
    done = status(ctx, body["id"])
    assert "Retry-After" not in done.headers
    assert (done.json()["status"], done.json()["resultRevision"]) == ("SUCCEEDED", 2)
    after = draft(ctx)
    assert after["revision"] == 2 and after["latestCorrection"]["status"] == "SUCCEEDED"
    assert after["content"]["shifts"][1]["endTime"] == "06:00"
    new_step = after["content"]["sections"][2]["steps"][-1]
    assert new_step["instruction"] == "명찰을 다세요." and uuid.UUID(new_step["id"])
    assert after["content"]["sections"][0]["photos"] == ctx.content["sections"][0]["photos"]  # photo kept
    assert [i["id"] for i in after["issues"]] == [ctx.content["missingInformation"][1]["id"]]


def test_success_resets_acknowledgements_but_no_change_keeps_them(ctx, fake_ai):
    gap = ctx.content["missingInformation"][1]["id"]
    acked = ctx.api.post(url(ctx, "/acknowledgements"), headers=ctx.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": ctx.draft, "expectedRevision": 1, "issueIds": [gap], "confirmed": True})
    assert acked.json()["revision"] == 2
    first = correct(ctx, revision=2).json()  # default fake answer: NO_CHANGE
    drain()
    assert status(ctx, first["id"]).json()["resultRevision"] == 2
    unchanged = draft(ctx)
    assert unchanged["revision"] == 2 and unchanged["issues"][1]["status"] == "ACKNOWLEDGED"

    fake_ai.script("revise_structure", applied(fill_night_end(ctx)))
    second = correct(ctx, revision=2).json()
    drain()
    assert status(ctx, second["id"]).json()["resultRevision"] == 3
    changed = draft(ctx)
    assert changed["revision"] == 3 and [i["status"] for i in changed["issues"]] == ["OPEN"]
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualIssueAcknowledgement)) == 1  # history kept


def test_identical_applied_structure_is_no_change(ctx, fake_ai):
    raw = structure_to_raw(snapshot(ctx))
    fake_ai.script("revise_structure", applied(raw))
    body = correct(ctx).json()
    drain()
    assert status(ctx, body["id"]).json()["resultRevision"] == 1
    assert draft(ctx)["revision"] == 1


def test_section_target_changes_only_that_section_and_unlinks_photos_of_removed_sections(ctx, fake_ai):
    task = ctx.content["sections"][0]
    raw = structure_to_raw(snapshot(ctx))
    raw["sections"] = raw["sections"][1:]  # the owner asked to delete the task with the photo
    fake_ai.script("revise_structure", applied(raw))
    body = correct(ctx, text="오픈 준비 업무는 없애 주세요", target={"kind": "SECTION", "targetId": task["id"].upper()})
    assert body.json()["target"] == {"kind": "SECTION", "targetId": task["id"]}
    drain()
    after = draft(ctx)
    assert [s["id"] for s in after["content"]["sections"]] == [s["id"] for s in ctx.content["sections"][1:]]
    with Session(ctx.engine) as db:
        assert db.get(ManualMedia, ctx.photo).expires_at > utcnow() + timedelta(hours=23)


# --- failures -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("outcome", "error"), [
    ("CLARIFICATION_REQUIRED", "CORRECTION_CLARIFICATION_REQUIRED"),
    ("REFERENCE_CONFLICT", "MANUAL_REFERENCE_CONFLICT"),
])
def test_unclear_or_conflicting_corrections_end_without_guessing(ctx, fake_ai, outcome, error):
    fake_ai.script("revise_structure", FakeOutcome.ok({
        "outcome": outcome, "summary": None, "structure": structure_to_raw(snapshot(ctx))}))
    body = correct(ctx).json()
    drain()
    failed = status(ctx, body["id"]).json()
    assert failed["status"] == "ERROR" and failed["error"]["code"] == error
    assert failed["error"]["retryable"] is False and failed["resultRevision"] is None
    assert draft(ctx)["content"] == ctx.content and draft(ctx)["revision"] == 1
    assert code(retry(ctx, body["id"])) == (409, "MANUAL_CORRECTION_NOT_RETRYABLE")


def test_removing_a_referenced_shift_is_a_reference_conflict(ctx, fake_ai):
    raw = structure_to_raw(snapshot(ctx))
    raw["shifts"] = raw["shifts"][1:]  # the day shift still has its task
    fake_ai.script("revise_structure", applied(raw))
    body = correct(ctx, text="오전조는 없애 주세요").json()
    drain()
    assert status(ctx, body["id"]).json()["error"]["code"] == "MANUAL_REFERENCE_CONFLICT"
    assert draft(ctx)["content"] == ctx.content


def test_ai_failure_is_retryable_with_the_saved_input(ctx, fake_ai):
    fake_ai.script("revise_structure", *[FakeOutcome.fail("timeout")] * 3)
    body = correct(ctx).json()
    later = utcnow() + timedelta(minutes=5)
    drain()
    drain(now=later)
    drain(now=later + timedelta(minutes=5))
    failed = status(ctx, body["id"]).json()
    assert failed["error"] == {"code": "AI_PROCESSING_FAILED", "message": failed["error"]["message"],
                               "retryable": True}
    assert draft(ctx)["content"] == ctx.content
    with Session(ctx.engine) as db:
        first_task = db.get(ManualDraftCorrection, body["id"]).task_id

    fake_ai.script("revise_structure", applied(fill_night_end(ctx)))
    retried = retry(ctx, body["id"])
    assert retried.status_code == 202 and retried.headers["Retry-After"] == "2"
    again = retried.json()
    assert (again["id"], again["attempt"], again["status"], again["error"], again["completedAt"]) == (
        body["id"], 2, "RUNNING", None, None)
    assert again["createdAt"] == body["createdAt"] and again["target"] == body["target"]
    with Session(ctx.engine) as db:
        assert db.get(ManualDraftCorrection, body["id"]).task_id != first_task
    drain()
    assert status(ctx, body["id"]).json()["resultRevision"] == 2
    assert fake_ai.calls_for("revise_structure")[-1].data["instruction"] == "야간조는 6시에 끝나요"
    assert code(retry(ctx, body["id"], revision=2)) == (409, "MANUAL_CORRECTION_NOT_RETRYABLE")


def test_broken_model_output_fails_publicly(ctx, fake_ai):
    fake_ai.script("revise_structure", *[FakeOutcome.raw('{"outcome": ')] * 3)
    body = correct(ctx).json()
    for minutes in (0, 5, 10):
        drain(now=utcnow() + timedelta(minutes=minutes))
    assert status(ctx, body["id"]).json()["error"]["code"] == "AI_PROCESSING_FAILED"


def test_late_or_superseded_results_change_nothing(ctx, fake_ai):
    fake_ai.script("revise_structure", applied(fill_night_end(ctx)))
    body = correct(ctx).json()
    [claimed] = claim()
    with Session(ctx.engine) as db:  # a retry superseded this task meanwhile
        db.execute(update(ManualDraftCorrection).values(task_id=new_id()))
        db.commit()
    assert run_claimed(claimed).outcome == "cancelled"
    assert draft(ctx)["content"] == ctx.content
    # An apply of an older attempt is refused by the context check too.
    from app import manual_corrections

    with Session(ctx.engine) as db:
        row = db.get(ManualDraftCorrection, body["id"])
        stale = TaskContext(task_id=row.task_id, kind="DRAFT_CORRECTION", subject_id=row.id, attempt=row.attempt + 1,
                            input_revision=1, payload={}, tries=1)
        with pytest.raises(StaleTask):
            manual_corrections._apply(db, stale, None)


@pytest.mark.parametrize("change", ["revision", "published"])
def test_draft_that_moved_meanwhile_records_a_conflict(ctx, fake_ai, change):
    fake_ai.script("revise_structure", applied(fill_night_end(ctx)))
    body = correct(ctx).json()
    with Session(ctx.engine) as db:
        version = db.get(ManualVersion, ctx.draft)
        if change == "revision":
            version.revision = 5
        else:
            publish_version(db, version)
        db.commit()
    drain()
    error = status(ctx, body["id"]) if change == "revision" else None
    with Session(ctx.engine) as db:
        row = db.get(ManualDraftCorrection, body["id"])
        assert row.status == "ERROR"
        assert row.error_code == ("REVISION_CONFLICT" if change == "revision" else "MANUAL_VERSION_CONFLICT")
    if error is not None:
        assert error.json()["error"]["retryable"] is False
        assert draft(ctx)["content"] == ctx.content


# --- acceptance rules ---------------------------------------------------------------------------


def test_one_running_correction_blocks_every_draft_change(ctx):
    first = correct(ctx)
    assert first.status_code == 202
    assert code(correct(ctx)) == (409, "MANUAL_CORRECTION_IN_PROGRESS")
    edit = ctx.api.put(url(ctx, "/content"), headers=ctx.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": ctx.draft, "expectedRevision": 1, "content": ctx.content})
    assert code(edit) == (409, "MANUAL_CORRECTION_IN_PROGRESS")
    published = ctx.api.post(url(ctx, "/publication"), headers=ctx.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": ctx.draft, "expectedRevision": 1, "confirmed": True,
        "acknowledgedIssueIds": [m["id"] for m in ctx.content["missingInformation"]]})
    assert code(published) == (409, "MANUAL_CORRECTION_IN_PROGRESS")
    assert ctx.api.get(url(ctx, "/preview")).json()["content"] == ctx.content


def test_new_correction_after_a_failure_is_allowed_and_only_the_latest_is_retryable(ctx, fake_ai):
    fake_ai.script("revise_structure", FakeOutcome.fail("refused"), FakeOutcome.fail("refused"))
    first = correct(ctx).json()
    drain()
    second = correct(ctx, text="다시 말할게요").json()
    drain()
    assert draft(ctx)["latestCorrection"]["id"] == second["id"]
    assert code(retry(ctx, first["id"])) == (409, "MANUAL_CORRECTION_NOT_RETRYABLE")
    assert status(ctx, first["id"]).json()["status"] == "ERROR"  # older ones stay readable
    assert retry(ctx, second["id"]).status_code == 202


def test_retry_conflicts(ctx, fake_ai):
    fake_ai.script("revise_structure", FakeOutcome.fail("refused"))
    body = correct(ctx).json()
    drain()
    assert code(retry(ctx, body["id"], revision=2)) == (409, "REVISION_CONFLICT")
    assert code(retry(ctx, body["id"], version=new_id())) == (409, "MANUAL_VERSION_CONFLICT")
    assert code(retry(ctx, new_id())) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    content = dict(ctx.content, sections=[dict(ctx.content["sections"][0], title="오픈")] + ctx.content["sections"][1:])
    edit = ctx.api.put(url(ctx, "/content"), headers=ctx.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": ctx.draft, "expectedRevision": 1, "content": content})
    assert edit.json()["revision"] == 2
    assert code(retry(ctx, body["id"], revision=2)) == (409, "REVISION_CONFLICT")  # base moved


@pytest.mark.parametrize(("target", "expected"), [
    ({"kind": "SHIFT", "targetId": str(uuid.uuid4())}, (404, "MANUAL_RESOURCE_NOT_FOUND")),
    ({"kind": "SECTION", "targetId": str(uuid.uuid4())}, (404, "MANUAL_RESOURCE_NOT_FOUND")),
    ({"kind": "MANUAL", "targetId": str(uuid.uuid4())}, (422, "VALIDATION_ERROR")),
    ({"kind": "SHIFT", "targetId": None}, (422, "VALIDATION_ERROR")),
    ({"kind": "STEP", "targetId": None}, (422, "VALIDATION_ERROR")),
])
def test_target_rules(ctx, target, expected):
    assert code(correct(ctx, target=target)) == expected


def test_shift_of_the_draft_is_a_valid_target(ctx):
    night = ctx.content["shifts"][1]["id"]
    assert correct(ctx, target={"kind": "SHIFT", "targetId": night}).status_code == 202


@pytest.mark.parametrize("payload", [
    {"method": "TEXT", "text": "  "},
    {"method": "TEXT", "text": "가" * 10001},
    {"method": "VOICE"},
    {"method": "VOICE", "transcriptionId": "x"},
    {"method": "TEXT", "text": "a", "transcriptionId": str(uuid.uuid4())},
])
def test_input_shape_is_422(ctx, payload):
    response = ctx.api.post(url(ctx, "/corrections"), headers=ctx.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": ctx.draft, "expectedRevision": 1, "target": {"kind": "MANUAL", "targetId": None},
        "input": payload})
    assert code(response) == (422, "VALIDATION_ERROR")


def _transcription(ctx, store_id, status="READY"):
    with Session(ctx.engine) as db:
        media = make_photo(db, db.get(Store, store_id), kind="AUDIO")
        row = MediaTranscription(
            store_id=store_id, manual_media_id=media.id, status=status, attempt=1,
            text="오전조는 열 시에 시작해요" if status == "READY" else None,
            task_id=new_id() if status == "RUNNING" else None,
            error_code="TRANSCRIPTION_FAILED" if status == "ERROR" else None,
            completed_at=None if status == "RUNNING" else NOW)
        db.add(row)
        db.commit()
        return row.id, media.id


def test_voice_input_copies_the_ready_transcription_and_survives_purge(ctx, fake_ai):
    transcription, media = _transcription(ctx, ctx.store)
    fake_ai.script("revise_structure", FakeOutcome.fail("refused"))
    body = correct(ctx, voice=transcription.upper()).json()
    drain()
    with Session(ctx.engine) as db:
        row = db.get(ManualDraftCorrection, body["id"])
        assert (row.input_method, row.input_text, row.transcription_id) == (
            "VOICE", "오전조는 열 시에 시작해요", transcription)
        recording = db.get(ManualMedia, media)
        recording.deleted_at = recording.content_deleted_at = NOW  # the original is gone
        db.commit()
    assert retry(ctx, body["id"]).status_code == 202
    drain()
    assert fake_ai.calls_for("revise_structure")[-1].data["instruction"] == "오전조는 열 시에 시작해요"
    assert status(ctx, body["id"]).json()["status"] == "SUCCEEDED"


def test_voice_input_rules(ctx):
    for state in ("RUNNING", "ERROR"):
        transcription, _ = _transcription(ctx, ctx.store, state)
        assert code(correct(ctx, voice=transcription)) == (409, "TRANSCRIPTION_NOT_READY")
    with Session(ctx.engine) as db:
        other = make_store(db, approval_status="APPROVED", approved_at=NOW)
        db.commit()
        other_id = other.id
    foreign, _ = _transcription(ctx, other_id)
    assert code(correct(ctx, voice=foreign)) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    assert code(correct(ctx, voice=str(uuid.uuid4()))) == (404, "MANUAL_RESOURCE_NOT_FOUND")


def test_acceptance_needs_the_current_ready_draft(ctx):
    assert code(correct(ctx, revision=2)) == (409, "REVISION_CONFLICT")
    assert code(correct(ctx, version=new_id())) == (409, "MANUAL_VERSION_CONFLICT")
    with Session(ctx.engine) as db:
        db.get(ManualVersion, ctx.draft).generation_status = "ERROR"
        db.commit()
    assert code(correct(ctx)) == (409, "MANUAL_STATE_CONFLICT")


def test_acceptance_replays_and_detects_key_reuse(ctx):
    key = str(uuid.uuid4())
    first = correct(ctx, key=key)
    again = correct(ctx, key=key)
    assert again.status_code == 202 and again.headers["Idempotent-Replayed"] == "true"
    assert again.headers["Retry-After"] == "2" and again.json() == first.json()
    assert code(correct(ctx, key=key, text="다른 말")) == (409, "IDEMPOTENCY_KEY_REUSED")
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualDraftCorrection)) == 1
        assert db.scalar(select(func.count()).select_from(BackgroundTask)) == 1


def test_status_is_scoped_to_the_current_draft_and_store(ctx, fake_ai):
    body = correct(ctx).json()
    drain()
    with Session(ctx.engine) as db:
        stranger = make_user(db, "OWNER")
        publish_version(db, db.get(ManualVersion, ctx.draft))
        db.commit()
        stranger_id = stranger.id
    # Published: the correction no longer belongs to a current draft.
    assert code(status(ctx, body["id"])) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    assert code(status(ctx, str(uuid.uuid4()))) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    login(ctx.api, stranger_id)
    assert code(status(ctx, body["id"])) == (404, "STORE_NOT_FOUND")


def test_draft_replaced_after_publication_hides_old_corrections(ctx):
    body = correct(ctx).json()
    drain()
    with Session(ctx.engine) as db:
        publish_version(db, db.get(ManualVersion, ctx.draft))
        newer = make_ready_draft(db, db.get(Store, ctx.store))
        db.commit()
        newer_id = newer.id
    assert draft(ctx)["latestCorrection"] is None
    assert code(retry(ctx, body["id"], version=newer_id)) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    assert code(retry(ctx, body["id"])) == (409, "MANUAL_VERSION_CONFLICT")


# --- MySQL concurrency --------------------------------------------------------------------------


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_concurrent_corrections_and_edits_admit_one(ctx):
    from app.main import app

    barrier = threading.Barrier(8)
    results = []

    def run(index):
        with TestClient(app, raise_server_exceptions=False) as client:
            auth = login(client, ctx.owner)
            barrier.wait(10)
            if index % 2:
                response = correct(ctx, client=client, auth=auth)
            else:
                response = client.put(url(ctx, "/content"), headers=auth.headers(str(uuid.uuid4())), json={
                    "expectedVersionId": ctx.draft, "expectedRevision": 1,
                    "content": dict(ctx.content, structurePhotos=[])
                    | {"sections": [dict(ctx.content["sections"][0], title=f"오픈 {index}")] + ctx.content["sections"][1:]}})
            results.append(response.status_code if response.status_code < 300 else code(response))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    winners = [r for r in results if r in (200, 202)]
    assert len(winners) == 1, results
    assert set(results) - {200, 202} <= {(409, "MANUAL_CORRECTION_IN_PROGRESS"), (409, "REVISION_CONFLICT")}, results
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualDraftCorrection)) == (1 if 202 in winners else 0)


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_task_apply_and_a_waiting_edit_serialize(ctx, fake_ai):
    """The task applies under the manual lock: an edit racing it either sees RUNNING (409) or,
    after it, the moved revision (409) -- never a lost update."""
    fake_ai.script("revise_structure", applied(fill_night_end(ctx)))
    correct(ctx)
    outcome = {}

    def edit():
        outcome["edit"] = code(ctx.api.put(url(ctx, "/content"), headers=ctx.auth.headers(str(uuid.uuid4())), json={
            "expectedVersionId": ctx.draft, "expectedRevision": 1, "content": ctx.content}))

    thread = threading.Thread(target=edit)
    thread.start()
    runs = drain()
    thread.join(30)
    assert [run.outcome for run in runs] == ["succeeded"]
    assert outcome["edit"] in ((409, "MANUAL_CORRECTION_IN_PROGRESS"), (409, "REVISION_CONFLICT"))
    assert draft(ctx)["revision"] == 2

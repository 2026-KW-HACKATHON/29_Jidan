"""Task-owned media holds survive delays, and terminal/cancelled work releases only its own input."""
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.errors import AiErrorCode
from app.ai.fake import FakeOutcome
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    InterviewIntentReview,
    ManualDraftCorrection,
    ManualMediaSnapshotRef,
)
from app.manual_media_writing import hold_media, lock_media_for_writing
from app.media.retention import purge_media_content
from app.tasks import cancel_tasks, claim, enqueue, recover_expired, run_claimed
from tests.test_manual_media_writing import (
    COMMON,
    common_review,
    delete,
    held,
    media,
    media_correction,
    photo,
    ready_draft,
    review,
    video,
    write,
)
from tests.test_manual_media_writing import (
    fake_video as _fake_video,
)
from tests.test_manual_media_writing import (
    flow as _flow,
)

fake_video, flow = _fake_video, _flow


def submit(drv, kind, media_ids):
    if kind == "REVIEW_MEDIA_WRITING":
        sid, _ = common_review(drv)
        assert write(drv, sid, media_ids).status_code == 202
        return sid
    draft = ready_draft(drv)
    response = media_correction(drv, draft, draft["content"]["sections"][0]["id"], media_ids)
    assert response.status_code == 202, response.text
    return response.json()["id"]


@pytest.mark.parametrize("kind", ["REVIEW_MEDIA_WRITING", "DRAFT_MEDIA_WRITING"])
def test_delayed_video_stays_readable_until_the_task_releases_it(flow, kind):
    files = [photo(flow), video(flow)]
    submit(flow, kind, files)
    purge_media_content(now=utcnow() + timedelta(hours=25), media_ids=files)
    assert all(media(flow, mid).content_deleted_at is None for mid in files)
    assert all(delete(flow, mid).status_code == 409 for mid in files)
    flow.run()
    with Session(flow.engine) as db:
        assert db.scalar(select(BackgroundTask.status).where(BackgroundTask.kind == kind)) == "SUCCEEDED"
    assert held(flow) == set()
    purge_media_content(now=utcnow() + timedelta(hours=50), media_ids=files)
    assert all(media(flow, mid).content_deleted_at is not None for mid in files)


@pytest.mark.parametrize("kind", ["REVIEW_MEDIA_WRITING", "DRAFT_MEDIA_WRITING"])
def test_automatic_retry_keeps_its_video_hold_while_requeued(flow, fake_ai, kind):
    files = [photo(flow), video(flow)]
    submit(flow, kind, files)
    fake_ai.script("write_section_from_media", FakeOutcome.fail(AiErrorCode.UNAVAILABLE))
    [first] = claim(kinds=(kind,), limit=1)
    assert run_claimed(first).outcome == "requeued"
    assert held(flow) == set(files)
    with Session(flow.engine) as db:
        task = db.get(BackgroundTask, first.context.task_id)
        assert task.status == "QUEUED" and task.tries == 1
    purge_media_content(now=utcnow() + timedelta(hours=25), media_ids=files)
    assert all(media(flow, mid).content_deleted_at is None for mid in files)
    [retry] = claim(kinds=(kind,), limit=1, now=utcnow() + timedelta(seconds=20))
    assert retry.context.task_id == first.context.task_id
    assert run_claimed(retry).outcome == "succeeded"
    assert held(flow) == set()


@pytest.mark.parametrize("kind", ["REVIEW_MEDIA_WRITING", "DRAFT_MEDIA_WRITING"])
@pytest.mark.parametrize("path", ["apply", "fail", "expired", "explicit"])
@pytest.mark.parametrize("legacy", [False, True])
def test_stale_or_cancelled_work_releases_its_files(flow, fake_ai, kind, path, legacy):
    files = [photo(flow), video(flow)]
    subject = submit(flow, kind, files)
    [claimed] = claim(kinds=(kind,), limit=1)
    ctx = claimed.context
    holder = "MEDIA_WRITING" if kind == "REVIEW_MEDIA_WRITING" else "DRAFT_CORRECTION"
    with Session(flow.engine) as db:
        if legacy:
            for ref in db.scalars(select(ManualMediaSnapshotRef).where(
                    ManualMediaSnapshotRef.holder_id == ctx.task_id)):
                ref.holder_id = subject
        if kind == "REVIEW_MEDIA_WRITING":
            row = db.get(InterviewIntentReview, (subject, flow.intents[COMMON]))
            row.revision += 1
            content_before = row.ready_content
        else:
            row = db.get(ManualDraftCorrection, subject)
            row.status, row.completed_at = "SUCCEEDED", utcnow()
            row.result_revision = row.base_revision
        if path == "expired":
            task = db.get(BackgroundTask, ctx.task_id)
            task.tries, task.lease_expires_at = task.max_tries, utcnow() - timedelta(seconds=1)
        db.commit()
    if path == "fail":
        fake_ai.script("write_section_from_media", FakeOutcome.fail(AiErrorCode.INPUT_REJECTED))
    if path == "expired":
        assert recover_expired() == 1
    elif path == "explicit":
        with Session(flow.engine) as db:
            assert cancel_tasks(db, kind, subject) == 1
            db.commit()
        assert run_claimed(claimed).outcome == "discarded"
    else:
        assert run_claimed(claimed).outcome == "cancelled"
    with Session(flow.engine) as db:
        assert db.get(BackgroundTask, ctx.task_id).status == "CANCELLED"
    if kind == "REVIEW_MEDIA_WRITING":
        assert review(flow, subject)["content"] == content_before
    assert held(flow, holder) == set()
    assert all(delete(flow, mid).status_code == 204 for mid in files)


@pytest.mark.parametrize("kind", ["REVIEW_MEDIA_WRITING", "DRAFT_MEDIA_WRITING"])
@pytest.mark.parametrize("legacy", [False, True])
def test_stale_task_does_not_release_a_successors_shared_media(flow, kind, legacy):
    files = [photo(flow), video(flow)]
    subject = submit(flow, kind, files)
    [old] = claim(kinds=(kind,), limit=1)
    ctx = old.context
    holder = "MEDIA_WRITING" if kind == "REVIEW_MEDIA_WRITING" else "DRAFT_CORRECTION"
    newer_files = [*files, photo(flow)]
    with Session(flow.engine) as db:
        if legacy:
            for ref in db.scalars(select(ManualMediaSnapshotRef).where(
                    ManualMediaSnapshotRef.holder_id == ctx.task_id)):
                ref.holder_id = subject
        newer = enqueue(db, kind, subject, {**ctx.payload, "mediaIds": newer_files}, attempt=2,
                        input_revision=ctx.input_revision + (kind == "REVIEW_MEDIA_WRITING"))
        hold_media(db, holder, newer, lock_media_for_writing(db, flow.store, newer_files),
                   intent_id=ctx.payload.get("intentId"))
        if kind == "REVIEW_MEDIA_WRITING":
            row = db.get(InterviewIntentReview, (subject, flow.intents[COMMON]))
            row.processing_task_id, row.processing_attempt = newer, 2
            row.revision += 1
        else:
            row = db.get(ManualDraftCorrection, subject)
            row.task_id, row.attempt = newer, 2
        db.commit()
    assert run_claimed(old).outcome == "cancelled"
    with Session(flow.engine) as db:
        assert set(db.scalars(select(ManualMediaSnapshotRef.holder_id).where(
            ManualMediaSnapshotRef.holder_kind == holder))) == ({subject, newer} if legacy else {newer})
    assert all(delete(flow, mid).status_code == 409 for mid in newer_files)
    flow.run()
    assert held(flow, holder) == set()
    assert all(delete(flow, mid).status_code == 204 for mid in newer_files)

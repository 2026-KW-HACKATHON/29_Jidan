"""0.12.0 sections written from photos and videos: MANUAL_VIDEO upload, REVIEW_MEDIA_WRITING
(review media-writing) and DRAFT_MEDIA_WRITING (draft correction input MEDIA).

User decision (2026-10-08): the files are AI input only; the manual keeps text. So besides the
writing itself these tests check that nothing gets attached or shown, and that the files are
held exactly while a task waits or runs.

Every API test runs on SQLite and MySQL (`db_engine`) through the real app and the
OpenAPI-checking client, with separate DB sessions to check what was committed. The model is the
scripted FakeAiProvider (its default media writing appends one step citing the first media item).
`fake_video` replaces media-A's decoder with deterministic results; one end-to-end test uses the
real PyAV decoder on a generated clip.
"""

import io
import uuid
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.errors import AiErrorCode
from app.ai.fake import FakeOutcome
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    InterviewIntentReview,
    ManualDraftCorrection,
    ManualMedia,
    ManualMediaSnapshotRef,
    ManualPhotoAttachment,
    ManualVersion,
    Store,
)
from app.media import video as video_layer
from app.media.errors import MediaInvalid
from app.media.inspection import InspectedMedia
from app.media.storage import LocalMediaStorage, set_media_storage
from tests import media_samples as samples
from tests import video_samples
from tests.api_contract import login
from tests.factories import make_regular_grant, make_worker
from tests.test_interview_api import build_ctx, code, rows
from tests.test_interview_reviews import summaries

WORK, COMMON = 0, 1
FRAME_A, FRAME_B = samples.jpeg(32, 24, gps=False, color=(10, 200, 10)), samples.jpeg(32, 24, gps=False)
AUDIO = samples.wav_seconds(1.0)
VIDEO_BYTES = b"\x00\x00\x00\x18ftypisom-not-decoded-in-tests"
DEFAULT_STEP = "첨부한 사진에 보이는 대로 해요."  # the fake's step when the media carry no label


# --- fixtures and helpers -------------------------------------------------------------------------


@pytest.fixture
def fake_video(monkeypatch):
    """Stand-in for media-A's decoder: any bytes are a 5 s MP4 with two frames (out of order,
    to prove sorting) and audio; bytes starting with b"BAD" are refused."""
    state = {"digests": 0, "audio": AUDIO}

    def inspect(data: bytes) -> InspectedMedia:
        if data.startswith(b"BAD"):
            raise MediaInvalid("video:test")
        return InspectedMedia("VIDEO", "video/mp4", data, 5000)

    def digest(data: bytes) -> video_layer.VideoDigest:
        state["digests"] += 1
        frames = (video_layer.VideoFrame(2500, FRAME_B), video_layer.VideoFrame(0, FRAME_A))
        return video_layer.VideoDigest(duration_ms=5000, frames=frames, poster=frames[1],
                                       audio=state["audio"], audio_mime="audio/wav" if state["audio"] else None)

    monkeypatch.setattr(video_layer, "inspect_video", inspect)
    monkeypatch.setattr(video_layer, "digest_video", digest)
    return state


def _driver(api, db_engine, fake_ai, tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    fake_ai.on("summarize_intent", summaries)
    driver = build_ctx(api, db_engine)
    driver.storage = storage
    return driver


@pytest.fixture
def flow(api, db_engine, fake_ai, fake_video, tmp_path):
    yield _driver(api, db_engine, fake_ai, tmp_path)
    set_media_storage(None)


@pytest.fixture
def real_flow(api, db_engine, fake_ai, tmp_path):
    """Same driver with media-A's real decoder (PyAV)."""
    yield _driver(api, db_engine, fake_ai, tmp_path)
    set_media_storage(None)


def upload(drv, data, purpose, *, store=None, key=None):
    return drv.api.post(f"/api/stores/{store or drv.store}/manual/media",
                        headers=drv.auth.headers(key or str(uuid.uuid4())), data={"purpose": purpose},
                        files={"file": ("file.bin", data, "application/octet-stream")})


def video(drv, store=None) -> str:
    response = upload(drv, VIDEO_BYTES, "MANUAL_VIDEO", store=store)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def photo(drv, store=None, purpose="MANUAL_PHOTO", data=None) -> str:
    response = upload(drv, data or samples.jpeg(gps=False), purpose, store=store)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def review(drv, sid, index=COMMON) -> dict:
    response = drv.review(sid, drv.intents[index])
    assert response.status_code == 200, response.text
    return response.json()


def common_review(drv) -> tuple[str, dict]:
    """A session whose COMMON_TASKS review is READY with one section (손님 응대, two steps)."""
    sid = drv.started()
    drv.answer_and_run(sid)
    drv.answer_and_run(sid)
    return sid, review(drv, sid)


def section_of(body: dict) -> dict:
    return body["content"]["sections"][0]


def write(drv, sid, media_ids, *, section=None, revision=None, key=None, index=COMMON):
    body = review(drv, sid, index)
    payload = {"expectedRevision": revision or body["revision"],
               "sectionId": section or section_of(body)["id"], "mediaIds": media_ids}
    return drv.post(drv.review_url(sid, drv.intents[index], "media-writing"), payload, key)


def held(drv, holder_kind=None) -> set[str]:
    with Session(drv.engine) as db:
        query = select(ManualMediaSnapshotRef.media_id)
        if holder_kind:
            query = query.where(ManualMediaSnapshotRef.holder_kind == holder_kind)
        return set(db.scalars(query))


def media(drv, media_id) -> ManualMedia:
    with Session(drv.engine) as db:
        return db.get(ManualMedia, media_id)


def delete(drv, media_id):
    return drv.api.delete(f"/api/stores/{drv.store}/manual/media/{media_id}", headers=drv.auth.headers())


def no_attachments(drv) -> bool:
    return not rows(drv, ManualPhotoAttachment)


# --- upload -------------------------------------------------------------------------------------


def test_video_upload_is_a_plain_ai_input_file(flow):
    response = upload(flow, VIDEO_BYTES, "MANUAL_VIDEO")
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "storeId", "purpose", "mimeType", "sizeBytes", "createdAt"}
    assert (body["purpose"], body["mimeType"], body["sizeBytes"]) == ("MANUAL_VIDEO", "video/mp4", len(VIDEO_BYTES))
    row = media(flow, body["id"])
    assert (row.kind, row.duration_ms) == ("VIDEO", 5000)
    assert flow.storage.read(row.object_key) == VIDEO_BYTES
    assert len(rows(flow, ManualMedia)) == 1  # no derived poster photo
    assert flow.api.get(f"/api/stores/{flow.store}/manual/media/{body['id']}/content").status_code == 404
    # A video is never attached: not as an answer photo, not as a review photo.
    sid, before = common_review(flow)
    assert code(flow.answer(sid, photo_ids=[body["id"]])) == "VALIDATION_ERROR"
    put = flow.api.put(flow.review_url(sid, flow.intents[COMMON], "photos"), headers=flow.auth.headers(str(uuid.uuid4())),
                       json={"expectedRevision": before["revision"], "target": "SECTION",
                             "sectionId": section_of(before)["id"],
                             "photos": [{"mediaId": body["id"], "title": "사진 1", "caption": None}]})
    assert (put.status_code, code(put)) == (422, "VALIDATION_ERROR")


def test_rejected_uploads_keep_nothing_and_stop_at_the_purpose_limit(flow):
    response = upload(flow, b"BAD video", "MANUAL_VIDEO")
    assert (response.status_code, code(response)) == (422, "MEDIA_INVALID")
    assert upload(flow, VIDEO_BYTES, "MANUAL_PHOTO").status_code == 415
    # The photo limit cuts the stream even though videos may be ten times larger.
    big = upload(flow, b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024 + 10), "MANUAL_PHOTO")
    assert (big.status_code, code(big)) == (413, "MEDIA_TOO_LARGE")
    assert not rows(flow, ManualMedia)


def test_video_upload_replays_by_key(flow):
    key = str(uuid.uuid4())
    first, again = upload(flow, VIDEO_BYTES, "MANUAL_VIDEO", key=key), upload(flow, VIDEO_BYTES, "MANUAL_VIDEO", key=key)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    assert len(rows(flow, ManualMedia)) == 1


# --- REVIEW_MEDIA_WRITING -----------------------------------------------------------------------


def test_review_section_is_written_from_photos_and_nothing_is_attached(flow, fake_ai):
    sid, before = common_review(flow)
    first, second = photo(flow), photo(flow, data=samples.png(40, 30))
    confirmed = flow.post(flow.review_url(sid, flow.intents[COMMON], "confirmations"),
                          {"expectedRevision": before["revision"], "confirmed": True})
    assert confirmed.status_code == 200
    accepted = write(flow, sid, [second, first])
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert (body["status"], body["processing"]["kind"], body["processing"]["attempt"]) == ("PROCESSING", "MEDIA_WRITING", 1)
    assert body["confirmedAt"] is None and body["content"] == before["content"]
    [task] = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert task.payload["mediaIds"] == [second, first] and task.payload["sectionId"] == section_of(before)["id"]
    assert task.input_revision == body["revision"]
    # Held while the task waits: neither file can be deleted or purged.
    assert held(flow, "MEDIA_WRITING") == {first, second}
    assert code(delete(flow, first)) == "MEDIA_IN_USE"
    flow.run()
    done = review(flow, sid)
    assert done["status"] == "READY" and done["revision"] == body["revision"] + 1 and done["confirmedAt"] is None
    steps = section_of(done)["steps"]
    assert steps[:2] == section_of(before)["steps"] and steps[2]["instruction"] == DEFAULT_STEP
    assert done["content"]["summary"] == before["content"]["summary"]
    assert section_of(done)["photos"] == [] and done["content"]["structurePhotos"] == []
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [(m["id"], m["kind"], m["title"], m["caption"]) for m in call.data["media"]] == [
        (f"media:{second}", "PHOTO", None, None), (f"media:{first}", "PHOTO", None, None)]
    assert call.data["target"] == {"kind": "SECTION", "target_id": section_of(before)["id"]}
    assert call.data["intent"]["key"] == "COMMON_TASKS"
    # Released: they are unattached uploads again (24 h grace), so they can be deleted, and the
    # written steps stay (Figma 777-3464).
    assert held(flow) == set() and no_attachments(flow)
    assert media(flow, first).expires_at >= utcnow() + timedelta(hours=23)
    assert delete(flow, first).status_code == 204
    assert section_of(review(flow, sid))["steps"] == steps


def test_video_is_read_as_frames_in_time_order_then_its_transcript(flow, fake_ai, fake_video):
    sid, _ = common_review(flow)
    still, clip = photo(flow), video(flow)
    hold_before = media(flow, clip).expires_at
    assert write(flow, sid, [still, clip]).status_code == 202
    assert media(flow, clip).expires_at >= hold_before  # the video bytes are held (media-A rule)
    assert held(flow, "MEDIA_WRITING") == {still, clip}
    assert code(delete(flow, clip)) == "MEDIA_IN_USE"
    flow.run()
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [m["id"] for m in call.data["media"]] == [
        f"media:{still}", f"media:{clip}@0", f"media:{clip}@2500", f"media:{clip}#transcript"]
    assert call.data["media"][-1]["text"] == "테스트 전사 결과예요." and call.data["media"][-1]["image_index"] is None
    assert len(fake_ai.calls_for("transcribe")) == 1 and fake_video["digests"] == 1
    assert review(flow, sid)["status"] == "READY" and held(flow) == set()


def test_failed_transcription_still_writes_from_the_frames(flow, fake_ai):
    sid, _ = common_review(flow)
    clip = video(flow)
    fake_ai.script("transcribe", FakeOutcome.fail(AiErrorCode.EMPTY_TRANSCRIPT))
    assert write(flow, sid, [clip]).status_code == 202
    flow.run()
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [m["kind"] for m in call.data["media"]] == ["VIDEO_FRAME", "VIDEO_FRAME"]
    assert review(flow, sid)["status"] == "READY"


def test_silent_video_is_not_transcribed(flow, fake_ai, fake_video):
    sid, _ = common_review(flow)
    clip = video(flow)
    fake_video["audio"] = None
    write(flow, sid, [clip])
    flow.run()
    assert not fake_ai.calls_for("transcribe")
    assert review(flow, sid)["status"] == "READY"


def test_failure_is_a_review_error_and_a_retry_holds_and_reuses_the_input(flow, fake_ai):
    sid, before = common_review(flow)
    still = photo(flow)
    fake_ai.script("write_section_from_media", *[FakeOutcome.fail(AiErrorCode.TIMEOUT)] * 3)
    assert write(flow, sid, [still]).status_code == 202
    flow.run()
    failed = review(flow, sid)
    assert failed["status"] == "ERROR" and failed["processing"]["kind"] == "MEDIA_WRITING"
    assert failed["error"]["code"] == "AI_PROCESSING_FAILED" and failed["error"]["retryable"] is True
    assert "사진·영상" in failed["error"]["message"]
    assert failed["content"] == before["content"]
    assert held(flow) == set()  # released after the final failure
    assert code(write(flow, sid, [still])) == "REVIEW_NOT_READY"  # only retries in ERROR
    retried = flow.post(flow.review_url(sid, flow.intents[COMMON], "retries"), {"expectedRevision": failed["revision"]})
    assert retried.status_code == 202
    assert (retried.json()["processing"]["kind"], retried.json()["processing"]["attempt"]) == ("MEDIA_WRITING", 2)
    assert held(flow, "MEDIA_WRITING") == {still}
    tasks = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert len(tasks) == 2 and tasks[0].payload == tasks[1].payload
    flow.run()
    assert review(flow, sid)["status"] == "READY" and len(section_of(review(flow, sid))["steps"]) == 3
    assert held(flow) == set()


def test_retry_after_the_files_were_purged_ends_as_an_error(flow, fake_ai):
    sid, _ = common_review(flow)
    still = photo(flow)
    fake_ai.script("write_section_from_media", *[FakeOutcome.fail(AiErrorCode.TIMEOUT)] * 3)
    write(flow, sid, [still])
    flow.run()
    with Session(flow.engine) as db:
        db.execute(update(ManualMedia).where(ManualMedia.id == still).values(content_deleted_at=utcnow()))
        db.commit()
    failed = review(flow, sid)
    assert flow.post(flow.review_url(sid, flow.intents[COMMON], "retries"),
                     {"expectedRevision": failed["revision"]}).status_code == 202
    assert held(flow) == set()  # nothing left to hold
    flow.run()
    assert review(flow, sid)["status"] == "ERROR"
    # The retry (attempt 2) found no file: rows come back unordered on MySQL, so pick by attempt.
    tasks = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert max(tasks, key=lambda task: task.attempt).last_error_code == "INPUT_REJECTED"


def test_request_checks_change_nothing(flow):
    sid, body = common_review(flow)
    still, recording = photo(flow), photo(flow, purpose="INTERVIEW_AUDIO", data=samples.wav_seconds(1.0))
    foreign = photo(flow, store=flow.second)
    videos = [video(flow) for _ in range(3)]
    gone = photo(flow)
    with Session(flow.engine) as db:
        db.execute(update(ManualMedia).where(ManualMedia.id == gone).values(deleted_at=utcnow()))
        db.commit()
    cases = [
        (write(flow, sid, [still], section=str(uuid.uuid4())), 422),
        (write(flow, sid, [still], revision=body["revision"] + 1), 409),
        (write(flow, sid, [foreign]), 404),
        (write(flow, sid, [gone]), 404),
        (write(flow, sid, [str(uuid.uuid4())]), 404),
        (write(flow, sid, [recording]), 422),
        (write(flow, sid, videos), 422),
        (write(flow, sid, [still, still.upper()]), 422),
        (write(flow, sid, []), 422),
        (write(flow, sid, [str(uuid.uuid4()) for _ in range(11)]), 422),
    ]
    assert [response.status_code for response, _ in cases] == [status for _, status in cases]
    assert code(cases[1][0]) == "REVISION_CONFLICT"
    assert not rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert held(flow) == set() and review(flow, sid) == body
    # Another store's session is not found; a pending intent is not ready.
    other = flow.post(flow.url(sid, "intents", flow.intents[COMMON], "review", "media-writing", store=flow.second),
                      {"expectedRevision": 1, "sectionId": section_of(body)["id"], "mediaIds": [still]})
    assert other.status_code == 404
    pending = flow.post(flow.review_url(sid, flow.intents[5], "media-writing"),
                        {"expectedRevision": 1, "sectionId": section_of(body)["id"], "mediaIds": [still]})
    assert code(pending) == "REVIEW_NOT_READY"


def test_upper_bounds_are_accepted(flow):
    """10 files with 2 videos is the largest request (openapi mediaIds, MAX_VIDEOS)."""
    sid, _ = common_review(flow)
    files = [video(flow), video(flow), *[photo(flow) for _ in range(8)]]
    assert write(flow, sid, files).status_code == 202
    assert held(flow, "MEDIA_WRITING") == set(files)
    flow.run()
    assert review(flow, sid)["status"] == "READY" and held(flow) == set()


def test_retention_keeps_held_files_and_purges_them_after_release(flow):
    from app.media.retention import purge_media_content

    sid, _ = common_review(flow)
    still, clip = photo(flow), video(flow)
    write(flow, sid, [still, clip])
    later = utcnow() + timedelta(hours=23)
    purge_media_content(now=later)  # the upload's own 24 h have not passed for either yet
    purge_media_content(now=utcnow() + timedelta(hours=25))  # the photo is held by reference
    assert media(flow, still).content_deleted_at is None
    assert media(flow, clip).content_deleted_at is None
    flow.run()
    assert held(flow) == set()
    purge_media_content(now=utcnow() + timedelta(hours=49))  # released: the fresh grace is over
    assert media(flow, still).content_deleted_at is not None and media(flow, clip).content_deleted_at is not None
    assert len(section_of(review(flow, sid))["steps"]) == 3  # the written text stays


def test_processing_review_refuses_other_changes_and_replays_by_key(flow):
    sid, _ = common_review(flow)
    still = photo(flow)
    key = str(uuid.uuid4())
    first = write(flow, sid, [still], key=key)
    revision = first.json()["revision"] - 1
    again = write(flow, sid, [still], key=key, revision=revision)
    assert again.status_code == 202 and again.json() == first.json()
    assert len(rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")) == 1
    reused = write(flow, sid, [photo(flow)], key=key, revision=revision)
    assert code(reused) == "IDEMPOTENCY_KEY_REUSED"
    assert code(write(flow, sid, [still])) == "REVIEW_PROCESSING"
    assert held(flow, "MEDIA_WRITING") == {still}


def test_no_change_restores_the_previous_confirmation(flow, fake_ai):
    sid, before = common_review(flow)
    confirmed = flow.post(flow.review_url(sid, flow.intents[COMMON], "confirmations"),
                          {"expectedRevision": before["revision"], "confirmed": True}).json()
    fake_ai.on("write_section_from_media", lambda data: {
        "outcome": "NO_CHANGE", "structure": {"shifts": [], "sections": [], "missing_information": []}})
    assert write(flow, sid, [photo(flow)]).status_code == 202
    flow.run()
    done = review(flow, sid)
    assert done["status"] == "READY" and done["confirmedAt"] == confirmed["confirmedAt"]
    assert done["content"] == confirmed["content"] and held(flow) == set()


def test_review_changed_meanwhile_cancels_the_task(flow):
    """The section cannot change while PROCESSING through the API; a review that moved on anyway
    (written directly here) makes the queued result stale, so nothing is applied."""
    sid, _ = common_review(flow)
    assert write(flow, sid, [photo(flow)]).status_code == 202
    with Session(flow.engine) as db:
        row = db.get(InterviewIntentReview, (sid, flow.intents[COMMON]))
        row.ready_content, row.revision = {**row.ready_content, "sections": []}, row.revision + 1
        db.commit()
    flow.run()
    [task] = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert task.status == "CANCELLED"
    assert review(flow, sid)["content"]["sections"] == []


def test_section_gone_at_execution_ends_as_a_review_error(flow):
    sid, _ = common_review(flow)
    assert write(flow, sid, [photo(flow)]).status_code == 202
    with Session(flow.engine) as db:  # same revision: only the section is gone
        row = db.get(InterviewIntentReview, (sid, flow.intents[COMMON]))
        row.ready_content = {**row.ready_content, "sections": []}
        db.commit()
    flow.run()
    assert review(flow, sid)["status"] == "ERROR" and held(flow) == set()
    [task] = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert (task.status, task.last_error_code, task.tries) == ("FAILED", "INPUT_REJECTED", 1)


# --- DRAFT_MEDIA_WRITING (draft correction input MEDIA) -------------------------------------------


def draft_url(drv, tail=""):
    return f"/api/stores/{drv.store}/manual/draft{tail}"


def ready_draft(drv) -> dict:
    sid, _ = common_review(drv)
    drv.finish_all(sid, intents=4)
    response = drv.complete(sid)
    assert response.status_code == 202, response.text
    drv.run()
    draft = drv.api.get(draft_url(drv)).json()
    assert draft["generationStatus"] == "READY", draft
    return draft


def media_correction(drv, draft, section_id, media_ids, *, kind="SECTION", key=None, revision=None):
    return drv.post(draft_url(drv, "/corrections"), {
        "expectedVersionId": draft["versionId"], "expectedRevision": revision or draft["revision"],
        "target": {"kind": kind, "targetId": None if kind == "MANUAL" else section_id},
        "input": {"method": "MEDIA", "mediaIds": media_ids}}, key)


def test_draft_section_is_written_through_a_media_correction_and_stays_text_only(flow, fake_ai, db_engine):
    draft = ready_draft(flow)
    section = draft["content"]["sections"][0]
    still, clip = photo(flow), video(flow)
    accepted = media_correction(flow, draft, section["id"], [clip, still])
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert (body["status"], body["target"]) == ("RUNNING", {"kind": "SECTION", "targetId": section["id"]})
    [row] = rows(flow, ManualDraftCorrection)
    assert (row.input_method, row.input_text, row.transcription_id) == ("MEDIA", None, None)
    [task] = rows(flow, BackgroundTask, BackgroundTask.subject_id == row.id)
    assert (task.kind, task.payload["mediaIds"]) == ("DRAFT_MEDIA_WRITING", [clip, still])
    assert held(flow, "DRAFT_CORRECTION") == {clip, still}
    edit = flow.api.put(draft_url(flow, "/content"), headers=flow.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"], "content": draft["content"]})
    assert code(edit) == "MANUAL_CORRECTION_IN_PROGRESS"
    flow.run()
    done = flow.api.get(draft_url(flow, f"/corrections/{body['id']}")).json()
    assert (done["status"], done["resultRevision"]) == ("SUCCEEDED", draft["revision"] + 1)
    after = flow.api.get(draft_url(flow)).json()
    written = after["content"]["sections"][0]
    assert written["steps"][:len(section["steps"])] == section["steps"]
    assert len(written["steps"]) == len(section["steps"]) + 1 and written["photos"] == section["photos"] == []
    assert held(flow) == set() and no_attachments(flow)
    [call] = fake_ai.calls_for("write_section_from_media")
    assert call.data["intent"] is None and call.data["require_manual_level"] is True
    assert [m["kind"] for m in call.data["media"]] == ["VIDEO_FRAME", "VIDEO_FRAME", "VIDEO_TRANSCRIPT", "PHOTO"]
    # Published, a worker reads the written text and no photo of these files.
    published = flow.post(draft_url(flow, "/publication"), {
        "expectedVersionId": after["versionId"], "expectedRevision": after["revision"], "confirmed": True,
        "acknowledgedIssueIds": [i["id"] for i in after["issues"] if i["status"] == "OPEN"]})
    assert published.status_code == 200, published.text
    with Session(db_engine) as db:
        worker = make_worker(db)
        make_regular_grant(db, db.get(Store, flow.store), worker)
        db.commit()
        worker_id = worker.id
    login(flow.api, worker_id)
    detail = flow.api.get(f"/api/stores/{flow.store}/manual/published/sections/{section['id']}").json()
    assert detail["section"]["steps"][-1]["instruction"] == DEFAULT_STEP and detail["section"]["photos"] == []
    for media_id in (still, clip):
        assert flow.api.get(f"/api/stores/{flow.store}/manual/media/{media_id}/content").status_code == 404


def test_draft_media_correction_rules(flow):
    draft = ready_draft(flow)
    section = draft["content"]["sections"][0]
    still = photo(flow)
    assert code(media_correction(flow, draft, None, [still], kind="MANUAL")) == "VALIDATION_ERROR"
    assert code(media_correction(flow, draft, section["id"], [still], kind="SHIFT")) == "VALIDATION_ERROR"
    assert code(media_correction(flow, draft, section["id"], [])) == "VALIDATION_ERROR"
    assert code(media_correction(flow, draft, section["id"], [still, still.upper()])) == "VALIDATION_ERROR"
    assert media_correction(flow, draft, str(uuid.uuid4()), [still]).status_code == 404
    assert media_correction(flow, draft, section["id"], [photo(flow, store=flow.second)]).status_code == 404
    recording = photo(flow, purpose="INTERVIEW_AUDIO", data=samples.wav_seconds(1.0))
    assert code(media_correction(flow, draft, section["id"], [recording])) == "VALIDATION_ERROR"
    assert code(media_correction(flow, draft, section["id"], [still], revision=draft["revision"] + 1)) == "REVISION_CONFLICT"
    assert code(media_correction(flow, draft, section["id"], [video(flow) for _ in range(3)])) == "VALIDATION_ERROR"
    assert code(media_correction(flow, draft, section["id"], [str(uuid.uuid4()) for _ in range(11)])) == "VALIDATION_ERROR"
    gone = photo(flow)
    with Session(flow.engine) as db:
        db.execute(update(ManualMedia).where(ManualMedia.id == gone).values(content_deleted_at=utcnow()))
        db.commit()
    assert media_correction(flow, draft, section["id"], [gone]).status_code == 404
    assert not rows(flow, ManualDraftCorrection) and held(flow) == set()
    key = str(uuid.uuid4())
    first = media_correction(flow, draft, section["id"], [still], key=key)
    assert media_correction(flow, draft, section["id"], [still], key=key).json() == first.json()
    assert code(media_correction(flow, draft, section["id"], [photo(flow)], key=key)) == "IDEMPOTENCY_KEY_REUSED"
    assert len(rows(flow, ManualDraftCorrection)) == 1


def test_draft_media_correction_retry_holds_the_same_files_again(flow, fake_ai):
    draft = ready_draft(flow)
    section = draft["content"]["sections"][0]
    still = photo(flow)
    fake_ai.script("write_section_from_media", *[FakeOutcome.fail(AiErrorCode.UNAVAILABLE)] * 3)
    first = media_correction(flow, draft, section["id"], [still]).json()
    flow.run()
    failed = flow.api.get(draft_url(flow, f"/corrections/{first['id']}")).json()
    assert failed["status"] == "ERROR" and failed["error"]["retryable"] is True and held(flow) == set()
    retried = flow.post(draft_url(flow, f"/corrections/{first['id']}/retries"),
                        {"expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"]})
    assert retried.status_code == 202 and retried.json()["attempt"] == 2
    assert held(flow, "DRAFT_CORRECTION") == {still}
    tasks = rows(flow, BackgroundTask, BackgroundTask.subject_id == first["id"])
    assert [t.kind for t in tasks] == ["DRAFT_MEDIA_WRITING"] * 2
    assert tasks[0].payload == tasks[1].payload
    flow.run()
    assert flow.api.get(draft_url(flow, f"/corrections/{first['id']}")).json()["status"] == "SUCCEEDED"
    assert held(flow) == set()


def test_draft_that_moved_before_execution_records_a_revision_conflict(flow):
    draft = ready_draft(flow)
    section = draft["content"]["sections"][0]
    accepted = media_correction(flow, draft, section["id"], [photo(flow)]).json()
    with Session(flow.engine) as db:  # an edit committed past the base revision
        db.execute(update(ManualVersion).where(ManualVersion.id == draft["versionId"])
                   .values(revision=ManualVersion.revision + 1))
        db.commit()
    flow.run()
    done = flow.api.get(draft_url(flow, f"/corrections/{accepted['id']}")).json()
    assert (done["status"], done["error"]["code"]) == ("ERROR", "REVISION_CONFLICT")
    assert held(flow) == set()


# --- the real decoder, budgets and leases ---------------------------------------------------------


def test_real_video_is_uploaded_and_read_as_frames_and_transcript(real_flow, fake_ai):
    """End to end with a PyAV-encoded clip (tests/video_samples.py, media-A's decoder)."""
    drv = real_flow
    response = upload(drv, video_samples.video(seconds=3.0), "MANUAL_VIDEO")
    assert response.status_code == 201, response.text
    clip = response.json()["id"]
    assert len(rows(drv, ManualMedia)) == 1
    sid, _ = common_review(drv)
    assert write(drv, sid, [clip]).status_code == 202
    drv.run()
    assert review(drv, sid)["status"] == "READY"
    [call] = fake_ai.calls_for("write_section_from_media")
    kinds = [m["kind"] for m in call.data["media"]]
    assert kinds[-1] == "VIDEO_TRANSCRIPT" and set(kinds[:-1]) == {"VIDEO_FRAME"} and len(kinds) >= 2
    times = [int(m["id"].rsplit("@", 1)[1]) for m in call.data["media"][:-1]]
    assert times == sorted(times) and len(fake_ai.calls_for("transcribe")) == 1
    too_long = upload(drv, video_samples.video(seconds=62.0, fps=1, width=16, height=16, audio=None), "MANUAL_VIDEO")
    assert (too_long.status_code, code(too_long)) == (413, "MEDIA_TOO_LARGE")


def test_image_budget_drops_what_does_not_fit_in_request_order(flow, fake_ai, monkeypatch):
    import app.manual_media_writing as writing

    monkeypatch.setattr(writing, "MAX_MEDIA_IMAGES", 3)
    sid, _ = common_review(flow)
    photos = [photo(flow) for _ in range(2)]
    clip = video(flow)
    write(flow, sid, [*photos, clip])
    flow.run()
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [m["id"] for m in call.data["media"]] == [
        f"media:{photos[0]}", f"media:{photos[1]}", f"media:{clip}@0", f"media:{clip}#transcript"]


def test_photos_are_shrunk_to_the_model_budget():
    from app.manual_media_writing import PHOTO_MAX_SIDE, shrink_photo

    big = shrink_photo(samples.jpeg(3000, 1200, gps=False))
    with Image.open(io.BytesIO(big)) as image:
        assert (image.format, max(image.size)) == ("JPEG", PHOTO_MAX_SIDE)
    small = shrink_photo(samples.png(32, 20))
    with Image.open(io.BytesIO(small)) as image:
        assert (image.format, image.size) == ("JPEG", (32, 20))
    assert shrink_photo(b"not an image") is None


def test_media_writing_leases_cover_their_calls(monkeypatch):
    import app.tasks.handlers  # noqa: F401 - the production handlers
    from app.ai import build_provider_from_env
    from app.manual_media_writing import PROVIDER_CALLS
    from app.tasks.runner import registered_handlers, validate_task_leases

    handlers = registered_handlers()
    for kind in ("REVIEW_MEDIA_WRITING", "DRAFT_MEDIA_WRITING"):
        assert (handlers[kind].provider_calls, handlers[kind].lease_seconds) == (PROVIDER_CALLS, 900) == (3, 900)
    for name in ("OPENAI_TIMEOUT_SECONDS", "OPENAI_WRITING_TIMEOUT_SECONDS", "OPENAI_TRANSCRIBE_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "gpt-other")
    validate_task_leases(build_provider_from_env())  # 3 x (120 + 120) s + 30 s <= 900 s

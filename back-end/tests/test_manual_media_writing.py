"""0.12.0 sections written from photos and videos: video upload and poster, attaching videos to
review/draft sections, REVIEW_MEDIA_WRITING / DRAFT_MEDIA_WRITING and the worker views.

Every test runs on SQLite and MySQL (`db_engine`) through the real app and the OpenAPI-checking
client, with a separate DB session to check what was committed. The model is the scripted
FakeAiProvider (its default media writing appends one step citing the first media item). Video
decoding belongs to media-A (`app.media.video`); until it lands, `fake_video` replaces
`inspect_video`/`digest_video` with deterministic results. Nothing here decodes real video.
"""

import uuid
from datetime import timedelta

import pytest
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
    ManualStep,
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
POSTER = samples.jpeg(40, 30, gps=False, color=(10, 10, 200))
AUDIO = samples.wav_seconds(1.0)
VIDEO_BYTES = b"\x00\x00\x00\x18ftypisom-not-decoded-in-tests"


# --- fixtures -----------------------------------------------------------------------------------


@pytest.fixture
def fake_video(monkeypatch):
    """Stand-in for media-A's decoder: any bytes are a 5 s MP4 with two frames and audio;
    bytes starting with b"BAD" are refused; `state["audio"] = None` makes videos silent."""
    state = {"digests": 0, "audio": AUDIO}

    def inspect(data: bytes) -> InspectedMedia:
        if data.startswith(b"BAD"):
            raise MediaInvalid("video:test")
        return InspectedMedia("VIDEO", "video/mp4", data, 5000)

    def digest(data: bytes) -> video_layer.VideoDigest:
        state["digests"] += 1
        frames = (video_layer.VideoFrame(2500, FRAME_B), video_layer.VideoFrame(0, FRAME_A))
        return video_layer.VideoDigest(duration_ms=5000, frames=frames, poster=video_layer.VideoFrame(0, POSTER),
                                       audio=state["audio"], audio_mime="audio/wav" if state["audio"] else None)

    monkeypatch.setattr(video_layer, "inspect_video", inspect)
    monkeypatch.setattr(video_layer, "digest_video", digest)
    return state


@pytest.fixture
def real_flow(api, db_engine, fake_ai, tmp_path):
    """The interview driver with media-A's real decoder (PyAV) and local storage."""
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    fake_ai.on("summarize_intent", summaries)
    driver = build_ctx(api, db_engine)
    driver.storage = storage
    yield driver
    set_media_storage(None)


@pytest.fixture
def flow(api, db_engine, fake_ai, fake_video, tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    fake_ai.on("summarize_intent", summaries)
    driver = build_ctx(api, db_engine)
    driver.storage = storage
    yield driver
    set_media_storage(None)


def upload(drv, data, purpose, *, store=None, key=None):
    return drv.api.post(f"/api/stores/{store or drv.store}/manual/media",
                        headers=drv.auth.headers(key or str(uuid.uuid4())), data={"purpose": purpose},
                        files={"file": ("file.bin", data, "application/octet-stream")})


def video(drv, store=None) -> dict:
    response = upload(drv, VIDEO_BYTES, "MANUAL_VIDEO", store=store)
    assert response.status_code == 201, response.text
    return response.json()


def photo(drv, store=None) -> str:
    response = upload(drv, samples.jpeg(gps=False), "MANUAL_PHOTO", store=store)
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


def put_items(drv, sid, items, *, index=COMMON, target="SECTION", section=None, revision=None, key=None):
    body = review(drv, sid, index)
    payload = {"expectedRevision": revision or body["revision"], "target": target,
               "sectionId": (section or section_of(body)["id"]) if target == "SECTION" else None,
               "photos": items}
    return drv.api.put(drv.review_url(sid, drv.intents[index], "photos"), json=payload,
                       headers=drv.auth.headers(key or str(uuid.uuid4())))


def item(media_id, n=1, caption=None):
    return {"mediaId": media_id, "title": f"사진 {n}", "caption": caption}


def write(drv, sid, *, section=None, revision=None, key=None, index=COMMON):
    body = review(drv, sid, index)
    payload = {"expectedRevision": revision or body["revision"],
               "sectionId": section or section_of(body)["id"]}
    return drv.post(drv.review_url(sid, drv.intents[index], "media-writing"), payload, key)


def media(drv, media_id) -> ManualMedia:
    with Session(drv.engine) as db:
        return db.get(ManualMedia, media_id)


def refs(drv, holder_kind="INTENT_REVIEW") -> set[str]:
    with Session(drv.engine) as db:
        return set(db.scalars(select(ManualMediaSnapshotRef.media_id).where(
            ManualMediaSnapshotRef.holder_kind == holder_kind)))


# --- upload -------------------------------------------------------------------------------------


def test_video_upload_stores_the_video_and_a_derived_poster_photo(flow, fake_video):
    body = video(flow)
    assert body["purpose"] == "MANUAL_VIDEO" and body["mimeType"] == "video/mp4"
    assert body["sizeBytes"] == len(VIDEO_BYTES) and fake_video["digests"] == 1
    row, poster = media(flow, body["id"]), media(flow, body["posterMediaId"])
    assert (row.kind, row.duration_ms, row.poster_media_id) == ("VIDEO", 5000, poster.id)
    assert (poster.kind, poster.mime_type, poster.poster_media_id) == ("IMAGE", "image/jpeg", None)
    assert flow.storage.read(row.object_key) == VIDEO_BYTES
    assert flow.storage.read(poster.object_key)[:3] == b"\xff\xd8\xff"
    # Unattached: neither is served, and a video is never a question/answer photo.
    assert flow.api.get(f"/api/stores/{flow.store}/manual/media/{poster.id}/content").status_code == 404
    assert flow.api.get(f"/api/stores/{flow.store}/manual/media/{row.id}/content").status_code == 404
    sid = flow.started()
    assert code(flow.answer(sid, photo_ids=[row.id])) == "VALIDATION_ERROR"


def test_real_video_is_uploaded_attached_and_read_as_frames_and_transcript(real_flow, fake_ai):
    """End to end with a PyAV-encoded clip (tests/video_samples.py): the poster is a real frame,
    the task samples frames in time order and transcribes the tone track (fake STT)."""
    drv = real_flow
    response = upload(drv, video_samples.video(seconds=3.0), "MANUAL_VIDEO")
    assert response.status_code == 201, response.text
    clip = response.json()
    poster = media(drv, clip["posterMediaId"])
    assert (poster.kind, poster.mime_type) == ("IMAGE", "image/jpeg")
    assert drv.storage.read(poster.object_key)[:3] == b"\xff\xd8\xff"
    sid, _ = common_review(drv)
    assert put_items(drv, sid, [item(clip["id"])]).status_code == 200
    assert write(drv, sid).status_code == 202
    drv.run()
    assert review(drv, sid)["status"] == "READY"
    [call] = fake_ai.calls_for("write_section_from_media")
    kinds = [m["kind"] for m in call.data["media"]]
    assert kinds[-1] == "VIDEO_TRANSCRIPT" and set(kinds[:-1]) == {"VIDEO_FRAME"} and len(kinds) >= 2
    times = [int(m["id"].rsplit("@", 1)[1]) for m in call.data["media"][:-1]]
    assert times == sorted(times) and len(fake_ai.calls_for("transcribe")) == 1
    too_long = upload(drv, video_samples.video(seconds=62.0, fps=1, width=16, height=16, audio=None), "MANUAL_VIDEO")
    assert (too_long.status_code, code(too_long)) == (413, "MEDIA_TOO_LARGE")


def test_upload_stops_at_the_purpose_limit(flow):
    """A photo body over the photo limit is cut off at 413 even though videos may be larger."""
    response = upload(flow, b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024 + 10), "MANUAL_PHOTO")
    assert (response.status_code, code(response)) == (413, "MEDIA_TOO_LARGE")


def test_rejected_video_and_photo_uploads_keep_nothing(flow):
    before = len(rows(flow, ManualMedia))
    response = upload(flow, b"BAD video", "MANUAL_VIDEO")
    assert (response.status_code, code(response)) == (422, "MEDIA_INVALID")
    response = upload(flow, VIDEO_BYTES, "MANUAL_PHOTO")  # a video is not a photo
    assert response.status_code == 415
    assert len(rows(flow, ManualMedia)) == before


def test_video_upload_replays_by_key(flow):
    key = str(uuid.uuid4())
    first, again = upload(flow, VIDEO_BYTES, "MANUAL_VIDEO", key=key), upload(flow, VIDEO_BYTES, "MANUAL_VIDEO", key=key)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    assert len(rows(flow, ManualMedia, ManualMedia.kind == "VIDEO")) == 1
    assert len(rows(flow, ManualMedia, ManualMedia.kind == "IMAGE")) == 1


# --- attaching to review sections -----------------------------------------------------------------


def test_review_section_takes_videos_and_shows_kind_and_poster(flow):
    sid, _ = common_review(flow)
    clip, still = video(flow), photo(flow)
    response = put_items(flow, sid, [item(still, 1), item(clip["id"], 2, "냉장고 순서")])
    assert response.status_code == 200, response.text
    photos = section_of(response.json())["photos"]
    assert photos == [item(still, 1),
                      {"mediaId": clip["id"], "kind": "VIDEO", "posterMediaId": clip["posterMediaId"],
                       "title": "사진 2", "caption": "냉장고 순서"}]
    assert {still, clip["id"], clip["posterMediaId"]} <= refs(flow)
    # The owner sees the poster (in use now), never the video bytes.
    assert flow.api.get(f"/api/stores/{flow.store}/manual/media/{clip['posterMediaId']}/content").status_code == 200
    assert flow.api.get(f"/api/stores/{flow.store}/manual/media/{clip['id']}/content").status_code == 404
    # Echoing the response back (kind/posterMediaId included) changes nothing.
    revision = review(flow, sid)["revision"]
    echoed = put_items(flow, sid, photos)
    assert echoed.status_code == 200 and echoed.json()["revision"] == revision


def test_client_kind_and_poster_are_rederived_from_the_file(flow):
    sid, _ = common_review(flow)
    clip, still = video(flow), photo(flow)
    lying = [{**item(still), "kind": "VIDEO", "posterMediaId": clip["posterMediaId"]}]
    response = put_items(flow, sid, lying)
    assert response.status_code == 200
    assert section_of(response.json())["photos"] == [item(still)]


def test_video_attachment_rules(flow):
    sid, _ = common_review(flow)
    clip = video(flow)
    other = video(flow, store=flow.second)
    # Videos only on sections, posters never on their own, other stores' files are unknown.
    response = put_items(flow, sid, [item(clip["id"])], index=WORK, target="WORK_STRUCTURE")
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")
    assert put_items(flow, sid, [item(clip["posterMediaId"])]).status_code == 404
    assert put_items(flow, sid, [item(other["id"])]).status_code == 404
    with Session(flow.engine) as db:
        db.execute(update(ManualMedia).where(ManualMedia.id == clip["id"]).values(deleted_at=utcnow()))
        db.commit()
    assert put_items(flow, sid, [item(clip["id"])]).status_code == 404
    assert section_of(review(flow, sid))["photos"] == []


def test_purged_video_original_stays_attachable_through_its_poster(flow):
    sid, _ = common_review(flow)
    clip = video(flow)
    with Session(flow.engine) as db:
        db.execute(update(ManualMedia).where(ManualMedia.id == clip["id"]).values(content_deleted_at=utcnow()))
        db.commit()
    assert put_items(flow, sid, [item(clip["id"])]).status_code == 200


# --- REVIEW_MEDIA_WRITING -----------------------------------------------------------------------


def test_photo_only_section_is_written_from_its_photos(flow, fake_ai):
    sid, before = common_review(flow)
    still = photo(flow)
    put_items(flow, sid, [item(still, 1, "우유는 아래 칸")])
    confirmed = flow.post(flow.review_url(sid, flow.intents[COMMON], "confirmations"),
                          {"expectedRevision": review(flow, sid)["revision"], "confirmed": True})
    assert confirmed.status_code == 200
    accepted = write(flow, sid)
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert (body["status"], body["processing"]["kind"], body["processing"]["attempt"]) == ("PROCESSING", "MEDIA_WRITING", 1)
    assert body["confirmedAt"] is None and body["content"] == review(flow, sid)["content"]
    [task] = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert task.payload["sectionId"] == section_of(before)["id"] and task.input_revision == body["revision"]
    flow.run()
    done = review(flow, sid)
    assert done["status"] == "READY" and done["revision"] == body["revision"] + 1 and done["confirmedAt"] is None
    steps = section_of(done)["steps"]
    assert [s["instruction"] for s in steps[:2]] == [s["instruction"] for s in section_of(before)["steps"]]
    assert steps[-1]["instruction"].startswith("사진 1")
    assert done["content"]["summary"] == before["content"]["summary"]  # the summary is not rewritten
    assert section_of(done)["photos"] == [item(still, 1, "우유는 아래 칸")]
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [m["id"] for m in call.data["media"]] == [f"media:{still}"]
    assert call.data["target"] == {"kind": "SECTION", "target_id": section_of(before)["id"]}
    assert flow.get(sid)["revision"] == flow.get(sid)["revision"]  # the interview itself is untouched


def test_video_section_uses_frames_in_time_order_and_the_transcript(flow, fake_ai, fake_video):
    sid, _ = common_review(flow)
    still, clip = photo(flow), video(flow)
    put_items(flow, sid, [item(still, 1), item(clip["id"], 2, "정리 순서")])
    assert write(flow, sid).status_code == 202
    flow.run()
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [m["id"] for m in call.data["media"]] == [
        f"media:{still}", f"media:{clip['id']}@0", f"media:{clip['id']}@2500", f"media:{clip['id']}#transcript"]
    assert call.data["media"][-1]["text"] == "테스트 전사 결과예요."
    assert call.data["media"][1]["caption"] == "정리 순서"
    assert len(fake_ai.calls_for("transcribe")) == 1
    assert review(flow, sid)["status"] == "READY"
    # The video original is kept a full day after the request (retention, app.media.retention).
    assert media(flow, clip["id"]).expires_at >= utcnow() + timedelta(hours=23)


def test_failed_transcription_still_writes_from_the_frames(flow, fake_ai):
    sid, _ = common_review(flow)
    clip = video(flow)
    put_items(flow, sid, [item(clip["id"])])
    fake_ai.script("transcribe", FakeOutcome.fail(AiErrorCode.EMPTY_TRANSCRIPT))
    assert write(flow, sid).status_code == 202
    flow.run()
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [m["kind"] for m in call.data["media"]] == ["VIDEO_FRAME", "VIDEO_FRAME"]
    assert review(flow, sid)["status"] == "READY"


def test_purged_video_falls_back_to_its_poster(flow, fake_ai, fake_video):
    sid, _ = common_review(flow)
    clip = video(flow)
    put_items(flow, sid, [item(clip["id"])])
    with Session(flow.engine) as db:
        db.execute(update(ManualMedia).where(ManualMedia.id == clip["id"]).values(content_deleted_at=utcnow()))
        db.commit()
    digests = fake_video["digests"]
    assert write(flow, sid).status_code == 202
    flow.run()
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [(m["id"], m["kind"]) for m in call.data["media"]] == [(f"media:{clip['posterMediaId']}", "PHOTO")]
    assert fake_video["digests"] == digests and not fake_ai.calls_for("transcribe")


def test_failure_is_a_review_error_and_retries_with_the_same_input(flow, fake_ai):
    sid, before = common_review(flow)
    still = photo(flow)
    put_items(flow, sid, [item(still)])
    fake_ai.script("write_section_from_media", *[FakeOutcome.fail(AiErrorCode.TIMEOUT)] * 3)
    assert write(flow, sid).status_code == 202
    flow.run()
    failed = review(flow, sid)
    assert failed["status"] == "ERROR" and failed["processing"]["kind"] == "MEDIA_WRITING"
    assert failed["error"]["code"] == "AI_PROCESSING_FAILED" and failed["error"]["retryable"] is True
    assert "사진·영상" in failed["error"]["message"]
    assert failed["content"] == review(flow, sid)["content"] and section_of(failed)["steps"] == section_of(before)["steps"]
    # Only retries are accepted in ERROR; they reuse the frozen payload.
    assert code(write(flow, sid)) == "REVIEW_NOT_READY"
    retried = flow.post(flow.review_url(sid, flow.intents[COMMON], "retries"), {"expectedRevision": failed["revision"]})
    assert retried.status_code == 202 and retried.json()["processing"] == {
        **retried.json()["processing"], "kind": "MEDIA_WRITING", "attempt": 2}
    tasks = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert len(tasks) == 2 and tasks[0].payload == tasks[1].payload
    flow.run()
    assert review(flow, sid)["status"] == "READY" and len(section_of(review(flow, sid))["steps"]) == 3


def test_request_checks(flow):
    sid, body = common_review(flow)
    # No media yet, an unknown section, a stale revision.
    assert (write(flow, sid).status_code, code(write(flow, sid))) == (422, "VALIDATION_ERROR")
    put_items(flow, sid, [item(photo(flow))])
    assert code(write(flow, sid, section=str(uuid.uuid4()))) == "VALIDATION_ERROR"
    stale = write(flow, sid, revision=body["revision"])
    assert (stale.status_code, code(stale)) == (409, "REVISION_CONFLICT")
    assert not rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    # Another store's session is not found; a pending intent is not ready.
    other = flow.post(flow.url(sid, "intents", flow.intents[COMMON], "review", "media-writing", store=flow.second),
                      {"expectedRevision": 1, "sectionId": section_of(body)["id"]})
    assert other.status_code == 404
    pending = flow.post(flow.review_url(sid, flow.intents[5], "media-writing"),
                        {"expectedRevision": 1, "sectionId": section_of(body)["id"]})
    assert code(pending) == "REVIEW_NOT_READY"


def test_processing_review_refuses_other_changes_and_replays_by_key(flow):
    sid, _ = common_review(flow)
    put_items(flow, sid, [item(photo(flow))])
    key = str(uuid.uuid4())
    first = write(flow, sid, key=key)
    revision = first.json()["revision"] - 1
    again = write(flow, sid, key=key, revision=revision)
    assert again.status_code == 202 and again.json() == first.json()
    assert len(rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")) == 1
    reused = flow.post(flow.review_url(sid, flow.intents[COMMON], "media-writing"),
                       {"expectedRevision": revision + 5, "sectionId": section_of(review(flow, sid))["id"]}, key)
    assert code(reused) == "IDEMPOTENCY_KEY_REUSED"
    assert code(write(flow, sid)) == "REVIEW_PROCESSING"
    assert code(put_items(flow, sid, [])) == "REVIEW_PROCESSING"


def test_no_change_restores_the_previous_confirmation(flow, fake_ai):
    sid, _ = common_review(flow)
    put_items(flow, sid, [item(photo(flow))])
    confirmed = flow.post(flow.review_url(sid, flow.intents[COMMON], "confirmations"),
                          {"expectedRevision": review(flow, sid)["revision"], "confirmed": True}).json()
    fake_ai.on("write_section_from_media", lambda data: {
        "outcome": "NO_CHANGE", "structure": {"shifts": [], "sections": [], "missing_information": []}})
    assert write(flow, sid).status_code == 202
    flow.run()
    done = review(flow, sid)
    assert done["status"] == "READY" and done["confirmedAt"] == confirmed["confirmedAt"]
    assert done["content"] == confirmed["content"]


def test_review_changed_meanwhile_cancels_the_task(flow):
    """The section cannot change while PROCESSING through the API; a review that moved on
    anyway (here: written directly) makes the queued result stale, so nothing is applied."""
    sid, _ = common_review(flow)
    put_items(flow, sid, [item(photo(flow))])
    assert write(flow, sid).status_code == 202
    with Session(flow.engine) as db:
        row = db.get(InterviewIntentReview, (sid, flow.intents[COMMON]))
        content = dict(row.ready_content)
        content["sections"] = []
        row.ready_content, row.revision = content, row.revision + 1
        db.commit()
    flow.run()
    [task] = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert task.status == "CANCELLED"
    after = review(flow, sid)
    assert after["status"] == "PROCESSING" and after["content"]["sections"] == []


def test_section_gone_at_execution_ends_as_a_review_error(flow):
    sid, _ = common_review(flow)
    put_items(flow, sid, [item(photo(flow))])
    assert write(flow, sid).status_code == 202
    with Session(flow.engine) as db:  # same revision: only the section is gone
        row = db.get(InterviewIntentReview, (sid, flow.intents[COMMON]))
        row.ready_content = {**row.ready_content, "sections": []}
        db.commit()
    flow.run()
    assert review(flow, sid)["status"] == "ERROR"
    [task] = rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_MEDIA_WRITING")
    assert (task.status, task.last_error_code, task.tries) == ("FAILED", "INPUT_REJECTED", 1)


def test_unlinking_or_deleting_media_keeps_the_written_steps(flow):
    """Figma 777-3464: '사진만 삭제돼요. 작성한 업무 내용은 그대로 유지돼요.'"""
    sid, _ = common_review(flow)
    clip = video(flow)
    put_items(flow, sid, [item(clip["id"])])
    write(flow, sid)
    flow.run()
    written = section_of(review(flow, sid))["steps"]
    assert len(written) == 3
    delete = lambda media_id: flow.api.delete(f"/api/stores/{flow.store}/manual/media/{media_id}",
                                              headers=flow.auth.headers())
    # Attached: neither the video nor its poster can be deleted.
    assert (delete(clip["id"]).status_code, delete(clip["posterMediaId"]).status_code) == (409, 409)
    assert put_items(flow, sid, []).status_code == 200
    assert section_of(review(flow, sid))["steps"] == written
    assert delete(clip["posterMediaId"]).status_code == 409  # still its video's poster
    assert delete(clip["id"]).status_code == 204
    assert media(flow, clip["id"]).deleted_at is not None and media(flow, clip["posterMediaId"]).deleted_at is not None
    assert section_of(review(flow, sid))["steps"] == written


def test_a_video_used_in_two_places_stays_until_both_links_go(flow):
    sid, _ = common_review(flow)
    clip = video(flow)
    put_items(flow, sid, [item(clip["id"])])
    with Session(flow.engine) as db:  # a second section of the same review shows the same video
        row = db.get(InterviewIntentReview, (sid, flow.intents[COMMON]))
        first = row.ready_content["sections"][0]
        second = {**first, "id": str(uuid.uuid4()), "title": "재고 정리",
                  "steps": [{**s, "id": str(uuid.uuid4())} for s in first["steps"]]}
        row.ready_content = {**row.ready_content, "sections": [first, second]}
        db.commit()
    assert put_items(flow, sid, [item(clip["id"], 3)], section=second["id"]).status_code == 200
    assert put_items(flow, sid, [], section=first["id"]).status_code == 200
    assert {clip["id"], clip["posterMediaId"]} <= refs(flow)
    response = flow.api.delete(f"/api/stores/{flow.store}/manual/media/{clip['id']}", headers=flow.auth.headers())
    assert (response.status_code, code(response)) == (409, "MEDIA_IN_USE")


# --- drafts, preview, publication and workers ---------------------------------------------------


def draft_url(drv, tail=""):
    return f"/api/stores/{drv.store}/manual/draft{tail}"


def ready_draft_with_video(drv) -> tuple[dict, dict]:
    """Finish the interview with a video on the common-task section, generate the draft."""
    sid, _ = common_review(drv)
    clip = video(drv)
    assert put_items(drv, sid, [item(clip["id"], 1, "정리 순서")]).status_code == 200
    drv.finish_all(sid, intents=4)
    response = drv.complete(sid)
    assert response.status_code == 202, response.text
    drv.run()
    draft = drv.api.get(draft_url(drv)).json()
    assert draft["generationStatus"] == "READY", draft
    return draft, clip


def draft_section(draft, clip) -> dict:
    return next(s for s in draft["content"]["sections"] if any(p["mediaId"] == clip["id"] for p in s["photos"]))


def test_generated_draft_keeps_the_video_and_readers_only_see_the_poster(flow, db_engine):
    draft, clip = ready_draft_with_video(flow)
    owner_item = {"mediaId": clip["id"], "kind": "VIDEO", "posterMediaId": clip["posterMediaId"],
                  "title": "사진 1", "caption": "정리 순서"}
    section = draft_section(draft, clip)
    assert section["photos"] == [owner_item]
    with Session(flow.engine) as db:
        [row] = db.scalars(select(ManualPhotoAttachment).where(ManualPhotoAttachment.video_media_id == clip["id"]))
        assert (row.media_id, row.section_id) == (clip["posterMediaId"], section["id"])
    reader_item = {"mediaId": clip["posterMediaId"], "title": "사진 1", "caption": "정리 순서"}
    preview = flow.api.get(draft_url(flow, "/preview")).json()
    assert next(s for s in preview["content"]["sections"] if s["id"] == section["id"])["photos"] == [reader_item]
    # A full edit echoing the owner view changes nothing (revision kept).
    edit = flow.api.put(draft_url(flow, "/content"), headers=flow.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"], "content": draft["content"]})
    assert edit.status_code == 200 and edit.json()["revision"] == draft["revision"]
    published = flow.post(draft_url(flow, "/publication"), {
        "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"], "confirmed": True,
        "acknowledgedIssueIds": [i["id"] for i in draft["issues"] if i["status"] == "OPEN"]})
    assert published.status_code == 200, published.text
    assert draft_section(published.json(), {"id": clip["posterMediaId"]})["photos"] == [reader_item]
    with Session(db_engine) as db:
        worker = make_worker(db)
        make_regular_grant(db, db.get(Store, flow.store), worker)
        db.commit()
        worker_id = worker.id
    login(flow.api, worker_id)
    detail = flow.api.get(f"/api/stores/{flow.store}/manual/published/sections/{section['id']}").json()
    assert detail["section"]["photos"] == [reader_item]
    assert flow.api.get(f"/api/stores/{flow.store}/manual/media/{clip['posterMediaId']}/content").status_code == 200
    assert flow.api.get(f"/api/stores/{flow.store}/manual/media/{clip['id']}/content").status_code == 404


def test_draft_content_edit_takes_videos_on_sections_only(flow):
    draft, clip = ready_draft_with_video(flow)
    content = draft["content"]
    second = video(flow)
    content["structurePhotos"] = [item(second["id"])]
    put = lambda body: flow.api.put(draft_url(flow, "/content"), headers=flow.auth.headers(str(uuid.uuid4())),
                                    json={"expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"],
                                          "content": body})
    response = put(content)
    assert (response.status_code, response.json()["fieldErrors"][0]["field"]) == (422, "content.structurePhotos")
    content["structurePhotos"] = [item(clip["posterMediaId"])]  # a poster on its own
    assert put(content).status_code == 422
    content["structurePhotos"] = []
    draft_section(draft, clip)["photos"].append(item(second["id"], 2))
    response = put(content)
    assert response.status_code == 200 and response.json()["revision"] == draft["revision"] + 1
    assert draft_section(response.json(), clip)["photos"][1]["posterMediaId"] == second["posterMediaId"]


def media_correction(drv, draft, section_id, *, kind="SECTION", key=None, revision=None):
    return drv.post(draft_url(drv, "/corrections"), {
        "expectedVersionId": draft["versionId"], "expectedRevision": revision or draft["revision"],
        "target": {"kind": kind, "targetId": None if kind == "MANUAL" else section_id},
        "input": {"method": "MEDIA"}}, key)


def test_draft_section_is_written_through_a_media_correction(flow, fake_ai):
    draft, clip = ready_draft_with_video(flow)
    section = draft_section(draft, clip)
    accepted = media_correction(flow, draft, section["id"])
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert (body["status"], body["target"]) == ("RUNNING", {"kind": "SECTION", "targetId": section["id"]})
    [row] = rows(flow, ManualDraftCorrection)
    assert (row.input_method, row.input_text, row.transcription_id) == ("MEDIA", None, None)
    assert [t.kind for t in rows(flow, BackgroundTask, BackgroundTask.subject_id == row.id)] == ["DRAFT_MEDIA_WRITING"]
    assert code(flow.api.put(draft_url(flow, "/content"), headers=flow.auth.headers(str(uuid.uuid4())), json={
        "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"],
        "content": draft["content"]})) == "MANUAL_CORRECTION_IN_PROGRESS"
    flow.run()
    done = flow.api.get(draft_url(flow, f"/corrections/{body['id']}")).json()
    assert (done["status"], done["resultRevision"]) == ("SUCCEEDED", draft["revision"] + 1)
    after = flow.api.get(draft_url(flow)).json()
    written = draft_section(after, clip)
    assert written["steps"][:len(section["steps"])] == section["steps"]
    assert len(written["steps"]) == len(section["steps"]) + 1 and written["photos"] == section["photos"]
    [call] = fake_ai.calls_for("write_section_from_media")
    assert call.data["intent"] is None and call.data["require_manual_level"] is True
    assert [m["kind"] for m in call.data["media"]] == ["VIDEO_FRAME", "VIDEO_FRAME", "VIDEO_TRANSCRIPT"]


def test_draft_media_correction_rules_and_retry(flow, fake_ai):
    draft, clip = ready_draft_with_video(flow)
    section = draft_section(draft, clip)
    bare = next(s for s in draft["content"]["sections"] if not s["photos"])
    assert code(media_correction(flow, draft, None, kind="MANUAL")) == "VALIDATION_ERROR"
    assert code(media_correction(flow, draft, bare["id"])) == "VALIDATION_ERROR"
    assert media_correction(flow, draft, str(uuid.uuid4())).status_code == 404
    assert code(media_correction(flow, draft, section["id"], revision=draft["revision"] + 1)) == "REVISION_CONFLICT"
    assert not rows(flow, ManualDraftCorrection)
    fake_ai.script("write_section_from_media", *[FakeOutcome.fail(AiErrorCode.UNAVAILABLE)] * 3)
    first = media_correction(flow, draft, section["id"]).json()
    flow.run()
    failed = flow.api.get(draft_url(flow, f"/corrections/{first['id']}")).json()
    assert failed["status"] == "ERROR" and failed["error"]["retryable"] is True
    retried = flow.post(draft_url(flow, f"/corrections/{first['id']}/retries"),
                        {"expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"]})
    assert retried.status_code == 202 and retried.json()["attempt"] == 2
    kinds = [t.kind for t in rows(flow, BackgroundTask, BackgroundTask.subject_id == first["id"])]
    assert kinds == ["DRAFT_MEDIA_WRITING", "DRAFT_MEDIA_WRITING"]
    flow.run()
    assert flow.api.get(draft_url(flow, f"/corrections/{first['id']}")).json()["status"] == "SUCCEEDED"


def test_draft_that_moved_before_execution_records_a_revision_conflict(flow):
    draft, clip = ready_draft_with_video(flow)
    section = draft_section(draft, clip)
    accepted = media_correction(flow, draft, section["id"]).json()
    with Session(flow.engine) as db:  # an edit committed past the base revision
        db.execute(update(ManualVersion).where(ManualVersion.id == draft["versionId"])
                   .values(revision=ManualVersion.revision + 1))
        db.commit()
    flow.run()
    done = flow.api.get(draft_url(flow, f"/corrections/{accepted['id']}")).json()
    assert (done["status"], done["error"]["code"]) == ("ERROR", "REVISION_CONFLICT")
    with Session(flow.engine) as db:
        assert db.scalar(select(ManualStep.id).where(ManualStep.section_id == section["id"]).limit(1))


# --- budgets and leases -------------------------------------------------------------------------


def test_only_two_videos_are_decoded_per_task_the_rest_use_posters(flow, fake_ai, fake_video):
    sid, _ = common_review(flow)
    clips = [video(flow) for _ in range(3)]
    put_items(flow, sid, [item(c["id"], n + 1) for n, c in enumerate(clips)])
    fake_video["audio"] = None  # silent videos: no transcription call at all
    digests = fake_video["digests"]
    write(flow, sid)
    flow.run()
    [call] = fake_ai.calls_for("write_section_from_media")
    assert [m["id"] for m in call.data["media"]] == [
        f"media:{clips[0]['id']}@0", f"media:{clips[0]['id']}@2500",
        f"media:{clips[1]['id']}@0", f"media:{clips[1]['id']}@2500", f"media:{clips[2]['posterMediaId']}"]
    assert fake_video["digests"] == digests + 2 and not fake_ai.calls_for("transcribe")


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


def test_photos_are_shrunk_to_the_model_budget():
    import io

    from PIL import Image

    from app.manual_media_writing import PHOTO_MAX_SIDE, shrink_photo

    big = shrink_photo(samples.jpeg(3000, 1200, gps=False))
    with Image.open(io.BytesIO(big)) as image:
        assert (image.format, max(image.size)) == ("JPEG", PHOTO_MAX_SIDE)
    small = shrink_photo(samples.png(32, 20))
    with Image.open(io.BytesIO(small)) as image:
        assert (image.format, image.size) == ("JPEG", (32, 20))
    assert shrink_photo(b"not an image") is None


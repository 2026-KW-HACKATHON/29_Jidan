"""Photo continuity through real HTTP and an independent isolated MySQL connection.

Only AI/STT output is deterministic (build_lifecycle_provider, installed before the real
server starts). Identity/store/grants are fixtures; all interview, media, correction,
completion, preview and publication writes use public HTTP. No application handler or DB
transaction is replaced. These tests do not establish live AI or browser upload quality.
"""
import io
import math
import struct
import time
import uuid
import wave

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.contracts import StructureSnapshot
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, structure_to_raw
from app.db.models import (
    InterviewIntentReview,
    InterviewSession,
    ManualMedia,
    ManualMediaSnapshotRef,
    ManualPhotoAttachment,
    ManualVersion,
)
from e2e.ai_scenario import summarize
from e2e.conftest import RegistrationCase
from e2e.interview_helpers import interview_case, scenario_server
from tests.media_samples import png

REVIEW_RENAME = "사진 업무 이름 변경"  # exact synthetic STT marker, never sent to a live model
DRAFT_EDIT = "보관 절차 변경"


def build_lifecycle_provider():
    provider = FakeAiProvider()
    def summaries(data):
        result = summarize(data)
        if result["structure"]["sections"]:
            result["structure"]["sections"] = list(result["structure"]["sections"]) + [{
                "ref": "new-90", "category": "COMMON_TASK", "shift_ref": None, "title": "보조 확인",
                "steps": [{"ref": "new-91", "instruction": "위치를 확인해요.", "checklist_item": False}]}]
        return result

    provider.on("summarize_intent", summaries)

    draft_failures = 0

    def revise(data):
        nonlocal draft_failures
        raw = structure_to_raw(StructureSnapshot.model_validate(data["current"]))
        instruction = data["instruction"]
        if instruction == "fixture-draft-failure":
            draft_failures += 1
            if draft_failures <= 3:
                raise AiError(AiErrorCode.TIMEOUT)
        if instruction == "fixture-timeout":
            raise AiError(AiErrorCode.TIMEOUT)
        if instruction == "fixture-no-change":
            return {"outcome": "NO_CHANGE", "summary": None, "structure": raw}
        target = data["target"].get("target_id")
        chosen = next((s for s in raw["sections"] if s["ref"] == target), raw["sections"][0] if raw["sections"] else None)
        if instruction.endswith("를 삭제해 주세요"):
            title = instruction.removesuffix("를 삭제해 주세요")
            raw["sections"] = [s for s in raw["sections"] if s["title"] != title]
        elif chosen is not None:
            if instruction == REVIEW_RENAME:
                chosen["title"] = "사진을 보며 하는 포스 마감"
            else:
                chosen["steps"][-1]["instruction"] = ("재시도한 보관 순서예요." if instruction == "fixture-draft-failure"
                                                       else "영수증을 지정된 서랍에 보관해요.")
        return {"outcome": "APPLIED", "summary": "사진과 함께 확인했어요." if data.get("summary") else None,
                "structure": raw}

    provider.on("revise_structure", revise)
    transcripts = iter((REVIEW_RENAME, DRAFT_EDIT))
    provider.on("transcribe", lambda _request: next(transcripts))
    return provider


@pytest.fixture
def lifecycle(real_db, tmp_path):
    with (
        scenario_server(tmp_path, "e2e.test_interview_photo_lifecycle_http:build_lifecycle_provider") as origin,
        interview_case(real_db, origin) as case,
    ):
        yield case


def manual(case, tail=""):
    return f"/api/stores/{case.store_id}/manual{tail}"


def headers(case, key=None):
    return RegistrationCase(case.client, "", "").headers(key=key)


def get(case, url):
    response = case.client.get(url)
    assert response.status_code == 200, response.text
    return response.json()


def poll(case, url, done, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        body = get(case, url)
        if done(body):
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.1)


def review_url(case, sid, iid):
    return case.url(sid, f"/intents/{iid}/review")


def finish_answers(case):
    sid = case.start()["id"]
    for _ in range(40):
        state = case.wait(sid, lambda value: value["phase"] in {"COLLECTING", "READY_TO_GENERATE"})
        if state["phase"] == "READY_TO_GENERATE":
            break
        response = case.answer(state)
        assert response.status_code == 202, response.text
    else:
        pytest.fail("interview did not finish")
    listing = poll(case, case.url(sid, "/reviews"),
                   lambda value: len(value["items"]) == len(state["intents"])
                   and all(item["status"] == "READY" for item in value["items"]))
    return sid, listing


def upload(case, *, key=None, data=None, purpose="MANUAL_PHOTO"):
    image = purpose == "MANUAL_PHOTO"
    response = case.client.post(manual(case, "/media"), headers=headers(case, key),
                                data={"purpose": purpose}, files={"file": (
                                    "photo.png" if image else "voice.wav", data or png(),
                                    "image/png" if image else "audio/wav")})
    assert response.status_code == 201, response.text
    return response.json()


def photo(media, title="포스 위치", caption="마감 화면"):
    return {"mediaId": media["id"], "title": title, "caption": caption}


def link(case, sid, review, section_id, photos, *, key=None, expected=None):
    body = {"expectedRevision": review["revision"] if expected is None else expected,
            "target": "SECTION", "sectionId": section_id, "photos": photos}
    return case.client.put(review_url(case, sid, review["intentId"]) + "/photos",
                           json=body, headers=headers(case, key))


def section(review, section_id):
    return next(item for item in review["content"]["sections"] if item["id"] == section_id)


def review_with_section(listing):
    return next(item for item in listing["items"] if item["content"]["sections"])


def assert_review_commit(case, sid, review, section_id, expected):
    reread = get(case, review_url(case, sid, review["intentId"]))
    assert section(reread, section_id)["photos"] == expected
    with Session(case.engine) as db:
        stored = db.get(InterviewIntentReview, (sid, review["intentId"]))
        assert stored.revision == reread["revision"]
        assert section({"content": stored.ready_content}, section_id)["photos"] == expected
        refs = list(db.scalars(select(ManualMediaSnapshotRef.media_id).where(
            ManualMediaSnapshotRef.holder_kind == "INTENT_REVIEW",
            ManualMediaSnapshotRef.holder_id == sid,
            ManualMediaSnapshotRef.holder_intent_id == review["intentId"])))
        assert set(refs) == {item["mediaId"] for item in expected}
    return reread


def assert_draft_photos_commit(case, version_id, section_id, expected):
    with Session(case.engine) as db:
        attachments = list(db.scalars(select(ManualPhotoAttachment).where(
            ManualPhotoAttachment.version_id == version_id,
            ManualPhotoAttachment.section_id == section_id).order_by(ManualPhotoAttachment.sort_order)))
        assert [(a.media_id, a.title, a.caption) for a in attachments] == [
            (item["mediaId"], item["title"], item["caption"]) for item in expected]


def voiced_input(case):
    # Non-silent synthetic PCM exercises upload/inspection/transcription; no microphone claim.
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(i * math.tau * 440 / 16000)))
                                   for i in range(16000)))
    recording = upload(case, data=buffer.getvalue(), purpose="INTERVIEW_AUDIO")
    response = case.post(manual(case, "/transcriptions"), {"mediaId": recording["id"]})
    assert response.status_code == 202, response.text
    done = poll(case, manual(case, f"/transcriptions/{response.json()['id']}"),
                lambda value: value["status"] in {"READY", "ERROR"})
    assert done["status"] == "READY", done
    return {"method": "VOICE", "transcriptionId": done["id"]}




def test_upload_and_link_replays_preserve_photos_and_conflicts_have_no_partial_save(lifecycle):
    case = lifecycle
    sid, listing = finish_answers(case)
    review = review_with_section(listing)
    section_id = review["content"]["sections"][0]["id"]
    key = str(uuid.uuid4())
    first = upload(case, key=key)
    assert upload(case, key=key)["id"] == first["id"]
    with Session(case.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualMedia).where(ManualMedia.store_id == case.store_id)) == 1
    expected = [photo(first)]
    key = str(uuid.uuid4())
    linked = link(case, sid, review, section_id, expected, key=key)
    assert linked.status_code == 200, linked.text
    assert link(case, sid, review, section_id, expected, key=key).json() == linked.json()
    conflicting_replay = link(case, sid, review, section_id, [], key=key)
    assert conflicting_replay.status_code == 409, conflicting_replay.text
    assert_review_commit(case, sid, linked.json(), section_id, expected)
    current = linked.json()
    with interview_case(case.engine, str(case.client.base_url)) as foreign_case:
        foreign = upload(foreign_case)
        refused = link(case, sid, current, section_id, expected + [photo(foreign)])
        assert refused.status_code == 404, refused.text
        assert_review_commit(case, sid, current, section_id, expected)
    removed = upload(case)
    deleted = case.client.delete(manual(case, f"/media/{removed['id']}"), headers=headers(case))
    assert deleted.status_code == 204, deleted.text
    refused = link(case, sid, current, section_id, expected + [photo(removed)])
    assert refused.status_code == 404, refused.text
    assert_review_commit(case, sid, current, section_id, expected)
    second = upload(case)
    stale = link(case, sid, review, section_id, expected + [photo(second)])
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    current = assert_review_commit(case, sid, linked.json(), section_id, expected)
    duplicated = link(case, sid, current, section_id, expected + expected)
    assert duplicated.status_code == 422, duplicated.text
    assert_review_commit(case, sid, current, section_id, expected)
    retried = link(case, sid, current, section_id, expected + [photo(second, "추가 위치")])
    assert retried.status_code == 200, retried.text
    assert_review_commit(case, sid, retried.json(), section_id, expected + [photo(second, "추가 위치")])
    # A real MySQL failure while deleting a snapshot reference must roll back the review
    # content and revision as well. The trigger is scoped to this fixture media only.
    trigger = "e2e_photo_ref_" + uuid.uuid4().hex
    with case.engine.begin() as connection:
        connection.exec_driver_sql(f"""CREATE TRIGGER `{trigger}` BEFORE DELETE ON manual_media_snapshot_refs
            FOR EACH ROW BEGIN IF OLD.media_id = '{first['id']}' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'fixture snapshot cleanup failure';
            END IF; END""")
    try:
        refused = link(case, sid, retried.json(), section_id, [])
        assert refused.status_code == 500, refused.text
        unchanged = assert_review_commit(case, sid, retried.json(), section_id,
                                         expected + [photo(second, "추가 위치")])
        assert unchanged["revision"] == retried.json()["revision"]
    finally:
        with case.engine.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS `{trigger}`")
    deletion = case.client.delete(manual(case, f"/media/{first['id']}"), headers=headers(case))
    assert deletion.status_code == 409, deletion.text
    with Session(case.engine) as db:
        assert db.get(ManualMedia, first["id"]).deleted_at is None


def test_review_no_change_failure_and_explicit_section_deletion_preserve_other_photo_references(lifecycle):
    case = lifecycle
    sid, listing = finish_answers(case)
    targets = [item for item in listing["items"] if item["content"]["sections"]]
    first, other = targets[:2]
    section_id = first["content"]["sections"][0]["id"]
    other_id = other["content"]["sections"][0]["id"]
    image = upload(case)
    expected = [photo(image)]
    first_result = link(case, sid, first, section_id, expected)
    other_result = link(case, sid, other, other_id, expected)
    assert first_result.status_code == other_result.status_code == 200
    first, other = first_result.json(), other_result.json()
    retained_id = other["content"]["sections"][1]["id"]
    retained = link(case, sid, other, retained_id, expected)
    assert retained.status_code == 200, retained.text
    other = retained.json()
    before = first["content"]
    request = case.post(review_url(case, sid, first["intentId"]) + "/corrections", {
        "expectedRevision": first["revision"], "input": {"method": "TEXT", "text": "fixture-no-change"}})
    assert request.status_code == 202, request.text
    first = poll(case, review_url(case, sid, first["intentId"]), lambda value: value["status"] in {"READY", "ERROR"})
    assert first["status"] == "READY" and first["content"] == before
    assert_review_commit(case, sid, first, section_id, expected)
    request = case.post(review_url(case, sid, first["intentId"]) + "/corrections", {
        "expectedRevision": first["revision"], "input": {"method": "TEXT", "text": "fixture-timeout"}})
    assert request.status_code == 202, request.text
    failed = poll(case, review_url(case, sid, first["intentId"]), lambda value: value["status"] == "ERROR", timeout=60)
    assert failed["content"] == before
    assert_review_commit(case, sid, failed, section_id, expected)
    # A failed correction must retain the photos and reject completion, with no draft snapshot.
    listing = get(case, case.url(sid, "/reviews"))
    completion = case.post(case.url(sid, "/completion"), {
        "expectedRevision": listing["sessionRevision"], "reviewRevisions": [
            {"intentId": item["intentId"], "revision": item["revision"]} for item in listing["items"]]})
    assert completion.status_code == 409, completion.text
    with Session(case.engine) as db:
        version = db.get(ManualVersion, db.get(InterviewSession, sid).manual_version_id)
        assert version.generation_input_snapshot is None and version.generation_status == "NOT_STARTED"
    # The other independently valid review permits an explicit deletion, without deleting bytes
    # still referenced by the failed first review.
    deleted_title = section(other, other_id)["title"]
    request = case.post(review_url(case, sid, other["intentId"]) + "/corrections", {
        "expectedRevision": other["revision"], "input": {"method": "TEXT", "text": f"{deleted_title}를 삭제해 주세요"}})
    assert request.status_code == 202, request.text
    other = poll(case, review_url(case, sid, other["intentId"]), lambda value: value["status"] in {"READY", "ERROR"})
    assert other["status"] == "READY", other
    assert [item["id"] for item in other["content"]["sections"]] == [retained_id]
    assert section(other, retained_id)["photos"] == expected
    assert_review_commit(case, sid, other, retained_id, expected)
    with Session(case.engine) as db:
        assert db.get(ManualMedia, image["id"]).deleted_at is None
        refs = list(db.scalars(select(ManualMediaSnapshotRef).where(
            ManualMediaSnapshotRef.media_id == image["id"], ManualMediaSnapshotRef.holder_kind == "INTENT_REVIEW")))
        assert {(ref.holder_id, ref.holder_intent_id) for ref in refs} == {(sid, first["intentId"]), (sid, other["intentId"])}
    assert case.client.get(manual(case, f"/media/{image['id']}/content")).status_code == 200








def test_section_deletion_and_photo_link_race_does_not_move_or_delete_media(lifecycle):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    case = lifecycle
    sid, listing = finish_answers(case)
    review = review_with_section(listing)
    chosen, retained = review["content"]["sections"][:2]
    original = photo(upload(case))
    attached = link(case, sid, review, retained["id"], [original])
    assert attached.status_code == 200
    review = attached.json()
    candidate = photo(upload(case), "삭제 경합 사진")
    barrier = Barrier(2)
    correction_headers, link_headers = headers(case), headers(case)
    correction_body = {"expectedRevision": review["revision"], "input": {
        "method": "TEXT", "text": f"{chosen['title']}를 삭제해 주세요"}}
    photo_body = {"expectedRevision": review["revision"], "target": "SECTION", "sectionId": chosen["id"],
                  "photos": [candidate]}
    url = review_url(case, sid, review["intentId"])

    def remove():
        barrier.wait(timeout=10)
        return case.client.post(url + "/corrections", json=correction_body, headers=correction_headers)

    def attach():
        barrier.wait(timeout=10)
        return case.client.put(url + "/photos", json=photo_body, headers=link_headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(remove), pool.submit(attach)
        deleted, linked = first.result(timeout=30), second.result(timeout=30)
    assert (deleted.status_code, linked.status_code) in {(202, 409), (409, 200)}, (deleted.text, linked.text)
    if deleted.status_code == 409:
        latest = get(case, url)
        assert section(latest, chosen["id"])["photos"] == [candidate]
        deleted = case.post(url + "/corrections", {**correction_body, "expectedRevision": latest["revision"]})
        assert deleted.status_code == 202, deleted.text
    ready = poll(case, url, lambda value: value["status"] in {"READY", "ERROR"})
    assert ready["status"] == "READY", ready
    assert [item["id"] for item in ready["content"]["sections"]] == [retained["id"]]
    assert_review_commit(case, sid, ready, retained["id"], [original])
    with Session(case.engine) as db:
        assert db.get(ManualMedia, candidate["mediaId"]).deleted_at is None
        assert db.get(ManualMedia, original["mediaId"]).deleted_at is None

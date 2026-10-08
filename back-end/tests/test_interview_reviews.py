"""#120 intent reviews (summary, confirmation, correction, photos) and completion/first draft.

Reviews have their own revision: nothing here changes the session revision or the current
question. Completion freezes every READY review; review changes after it are refused.
"""

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import StructureSnapshot
from app.ai.fake import FakeOutcome, structure_to_raw
from app.db.models import (
    InterviewReviewConfirmation,
    InterviewSession,
    InterviewTurn,
    ManualMediaSnapshotRef,
    ManualPhotoAttachment,
    ManualReviewIssue,
    ManualSection,
    ManualShift,
    ManualStep,
    ManualVersion,
)
from app.media.storage import LocalMediaStorage, set_media_storage
from tests.interview_factories import raw_section, raw_shift, raw_summary
from tests.test_interview_api import INSUFFICIENT, build_ctx, code, count, media, rows

WORK, COMMON, SHIFT = 0, 1, 2


def summaries(data):
    """Fake summaries per intent: shifts, a common task, a shift task on the first shift."""
    key = data["intent"]["key"]
    if key == "WORK_STRUCTURE":
        return raw_summary("오픈조와 마감조가 있어요.", shifts=[
            raw_shift("new-1", "오픈조", "09:00", "15:00"), raw_shift("new-2", "마감조", "15:00", "23:00")])
    if key == "COMMON_TASKS":
        return raw_summary("손님 응대를 해요.", sections=[
            raw_section("new-1", "손님 응대", steps=[("new-2", "인사해요."), ("new-3", "주문을 받아요.")])])
    if key == "SHIFT_TASKS" and data["available_shifts"]:
        return raw_summary("오픈조는 문을 열어요.", sections=[raw_section(
            "new-1", "오픈 준비", category="SHIFT_TASK", shift_ref=data["available_shifts"][0]["id"],
            steps=[("new-2", "불을 켜요.")])])
    return raw_summary(f"{key} 요약이에요.", sections=[raw_section("new-1", key, steps=[("new-2", "확인해요.")])])


@pytest.fixture
def flow(api, db_engine, fake_ai, tmp_path):
    set_media_storage(LocalMediaStorage(tmp_path / "media"))
    fake_ai.on("summarize_intent", summaries)
    yield build_ctx(api, db_engine)
    set_media_storage(None)


def answered(drv, count_=1):
    sid = drv.started()
    for _ in range(count_):
        drv.answer_and_run(sid)
    return sid


def review(drv, sid, index):
    response = drv.review(sid, drv.intents[index])
    assert response.status_code == 200, response.text
    return response.json()


def confirm(drv, sid, index, revision=None, key=None, confirmed=True):
    revision = revision or review(drv, sid, index)["revision"]
    return drv.post(drv.review_url(sid, drv.intents[index], "confirmations"),
                    {"expectedRevision": revision, "confirmed": confirmed}, key)


def correct(drv, sid, index, text="오픈조는 8시에 시작해요.", revision=None, key=None):
    revision = revision or review(drv, sid, index)["revision"]
    return drv.post(drv.review_url(sid, drv.intents[index], "corrections"),
                    {"expectedRevision": revision, "input": {"method": "TEXT", "text": text}}, key)


def put_photos(drv, sid, index, photos, *, target="WORK_STRUCTURE", section=None, revision=None, key=None):
    revision = revision or review(drv, sid, index)["revision"]
    body = {"expectedRevision": revision, "target": target, "sectionId": section,
            "photos": [{"mediaId": m, "title": f"사진 {i + 1}", "caption": None} for i, m in enumerate(photos)]}
    return drv.api.put(drv.review_url(sid, drv.intents[index], "photos"), json=body,
                       headers=drv.auth.headers(key or str(uuid.uuid4())))


def retry_review(drv, sid, index, revision=None):
    revision = revision or review(drv, sid, index)["revision"]
    return drv.post(drv.review_url(sid, drv.intents[index], "retries"), {"expectedRevision": revision})


def revise(outcome="APPLIED", rename=None, drop_sections=False):
    def handler(data):
        current = StructureSnapshot.model_validate(data["current"])
        raw = structure_to_raw(current)
        if rename:
            raw["shifts"][0]["name"] = rename
        if drop_sections:
            raw["sections"] = []
        return {"outcome": outcome, "summary": "정정한 요약이에요." if outcome == "APPLIED" else None,
                "structure": raw}
    return handler


# --- summary ------------------------------------------------------------------------------------


def test_finished_intent_gets_a_review_that_is_summarized_independently(flow):
    sid = flow.started()
    flow.answer(sid)
    flow.run()  # evaluation, then the next question and the summary
    listing = flow.reviews(sid)
    [item] = listing["items"]
    assert item["status"] == "READY" and item["revision"] == 2
    content = item["content"]
    assert content["intentId"] == flow.intents[WORK] and content["summary"] == "오픈조와 마감조가 있어요."
    assert [s["name"] for s in content["shifts"]] == ["오픈조", "마감조"] and content["needsDetail"] is False
    assert content["structurePhotos"] == [] and content["missingInformation"] == []
    assert listing["sessionRevision"] == flow.get(sid)["revision"]


def test_initial_summary_is_processing_with_no_content(flow, fake_ai):
    fake_ai.script("summarize_intent", FakeOutcome.delay(5.0))
    sid = flow.started()
    flow.answer(sid)
    from app.tasks import drain

    drain(kinds=("EVALUATION",))
    body = review(flow, sid, WORK)
    assert (body["status"], body["revision"], body["content"], body["confirmedAt"]) == ("PROCESSING", 1, None, None)
    assert body["processing"]["kind"] == "UNDERSTANDING" and body["error"] is None


def test_reviews_of_pending_or_foreign_intents(flow):
    sid = answered(flow)
    pending = flow.review(sid, flow.intents[COMMON])
    assert (pending.status_code, code(pending)) == (409, "REVIEW_NOT_READY")
    unknown = flow.review(sid, str(uuid.uuid4()))
    assert (unknown.status_code, code(unknown)) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    for response in (confirm(flow, sid, COMMON, revision=1), correct(flow, sid, COMMON, revision=1)):
        assert (response.status_code, code(response)) == (409, "REVIEW_NOT_READY")


def test_review_and_session_mutations_hide_unknown_and_foreign_resources(flow):
    """confirm/correct/review-retry/session-retry 404: an unknown session, a session of another
    store of the same owner, and an unknown intent are the same MANUAL_RESOURCE_NOT_FOUND."""
    sid = answered(flow)
    revision = review(flow, sid, 0)["revision"]
    started = flow.start(store=flow.second)
    assert started.status_code == 201, started.text
    foreign, unknown = started.json()["id"], str(uuid.uuid4())
    targets = [(unknown, flow.intents[0]), (foreign, flow.intents[0]), (sid, str(uuid.uuid4()))]
    bodies = {
        "confirmations": {"expectedRevision": revision, "confirmed": True},
        "corrections": {"expectedRevision": revision, "input": {"method": "TEXT", "text": "8시에 열어요."}},
        "retries": {"expectedRevision": revision},
    }
    for session_id, intent_id in targets:
        for action, body in bodies.items():
            response = flow.post(flow.review_url(session_id, intent_id, action), body)
            assert (response.status_code, code(response)) == (404, "MANUAL_RESOURCE_NOT_FOUND"), (action, response.text)
    for session_id in (unknown, foreign):
        response = flow.post(flow.url(session_id, "retries"), {"expectedRevision": 1})
        assert (response.status_code, code(response)) == (404, "MANUAL_RESOURCE_NOT_FOUND"), response.text


def test_summary_failure_marks_only_the_review_and_questions_go_on(flow, fake_ai):
    fake_ai.script("summarize_intent", *[FakeOutcome.fail("timeout")] * 3)
    sid = answered(flow)
    state = flow.get(sid)
    assert state["status"] == "IN_PROGRESS" and state["phase"] == "COLLECTING"
    assert state["currentIntentId"] == flow.intents[COMMON]
    failed = review(flow, sid, WORK)
    assert (failed["status"], failed["content"], failed["error"]["code"]) == ("ERROR", None, "AI_PROCESSING_FAILED")
    assert failed["processing"]["kind"] == "UNDERSTANDING"
    # The session retry is for questions/Jev/draft only; the review has its own.
    session_retry = flow.post(flow.url(sid, "retries"), {"expectedRevision": state["revision"]})
    assert (session_retry.status_code, code(session_retry)) == (409, "INTERVIEW_STATE_CONFLICT")
    for response in (confirm(flow, sid, WORK), correct(flow, sid, WORK)):
        assert (response.status_code, code(response)) == (409, "REVIEW_NOT_READY")
    stale = retry_review(flow, sid, WORK, revision=failed["revision"] - 1)
    assert (stale.status_code, code(stale)) == (409, "REVISION_CONFLICT")
    retried = retry_review(flow, sid, WORK)
    assert retried.status_code == 202, retried.text
    assert retried.json()["processing"] == {**retried.json()["processing"], "kind": "UNDERSTANDING", "attempt": 2}
    flow.run()
    assert review(flow, sid, WORK)["status"] == "READY"
    again = retry_review(flow, sid, WORK)
    assert (again.status_code, code(again)) == (409, "INTERVIEW_STATE_CONFLICT")
    assert flow.get(sid)["revision"] == state["revision"]  # review work never moved the session


# --- confirmation ---------------------------------------------------------------------------------


def test_confirmation_is_recorded_once_and_does_not_move_the_session(flow):
    sid = answered(flow)
    before = flow.get(sid)
    current = review(flow, sid, WORK)
    first = confirm(flow, sid, WORK)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["revision"] == current["revision"] + 1 and body["confirmedAt"]
    repeat = confirm(flow, sid, WORK, revision=body["revision"])
    assert repeat.status_code == 200 and repeat.json() == body
    stale = confirm(flow, sid, WORK, revision=current["revision"])
    assert (stale.status_code, code(stale)) == (409, "REVISION_CONFLICT")
    [history] = rows(flow, InterviewReviewConfirmation)
    assert (history.reviewed_revision, history.confirmed_revision) == (current["revision"], body["revision"])
    assert history.confirmed_content == current["content"]
    assert flow.get(sid) == before
    invalid = confirm(flow, sid, WORK, confirmed=False)
    assert (invalid.status_code, code(invalid)) == (422, "VALIDATION_ERROR")


# --- corrections ----------------------------------------------------------------------------------


def test_correction_is_a_new_turn_and_replaces_the_summary(flow, fake_ai):
    sid = answered(flow)
    confirm(flow, sid, WORK)
    session_before = flow.get(sid)
    before = review(flow, sid, WORK)
    fake_ai.on("revise_structure", revise(rename="아침조"))
    accepted = correct(flow, sid, WORK)
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert (body["status"], body["revision"], body["confirmedAt"]) == ("PROCESSING", before["revision"] + 1, None)
    assert body["content"] == before["content"] and body["processing"]["kind"] == "CORRECTION"
    busy = correct(flow, sid, WORK, revision=body["revision"])
    assert (busy.status_code, code(busy)) == (409, "REVIEW_PROCESSING")
    for response in (confirm(flow, sid, WORK, revision=body["revision"]),
                     put_photos(flow, sid, WORK, [], revision=body["revision"])):
        assert (response.status_code, code(response)) == (409, "REVIEW_PROCESSING")
    flow.run()
    after = review(flow, sid, WORK)
    assert after["status"] == "READY" and after["revision"] == body["revision"] + 1
    assert after["content"]["summary"] == "정정한 요약이에요." and after["confirmedAt"] is None
    assert [s["name"] for s in after["content"]["shifts"]] == ["아침조", "마감조"]
    assert [s["id"] for s in after["content"]["shifts"]] == [s["id"] for s in before["content"]["shifts"]]
    [turn] = rows(flow, InterviewTurn, InterviewTurn.turn_kind == "CORRECTION")
    assert (turn.intent_id, turn.content, turn.input_method) == (flow.intents[WORK], "오픈조는 8시에 시작해요.", "TEXT")
    call = fake_ai.calls_for("revise_structure")[0].data
    assert call["instruction"] == "오픈조는 8시에 시작해요." and call["summary"] == before["content"]["summary"]
    session_after = flow.get(sid)
    assert (session_after["revision"], session_after["currentIntentId"], session_after["questions"]) == (
        session_before["revision"], session_before["currentIntentId"], session_before["questions"])


@pytest.mark.parametrize("outcome", ["NO_CHANGE", "CLARIFICATION_REQUIRED", "REFERENCE_CONFLICT"])
def test_unapplied_correction_keeps_content_and_confirmation(flow, fake_ai, outcome):
    sid = answered(flow)
    confirmed = confirm(flow, sid, WORK).json()
    fake_ai.on("revise_structure", revise(outcome=outcome))
    correct(flow, sid, WORK)
    flow.run()
    after = review(flow, sid, WORK)
    assert after["status"] == "READY" and after["content"] == confirmed["content"]
    assert after["confirmedAt"] == confirmed["confirmedAt"]


def test_failed_correction_keeps_the_last_ready_content_and_retries_the_same_input(flow, fake_ai):
    sid = answered(flow)
    before = review(flow, sid, WORK)
    fake_ai.script("revise_structure", *[FakeOutcome.raw("not json")] * 3)
    correct(flow, sid, WORK, text="정정 지시")
    flow.run()
    failed = review(flow, sid, WORK)
    assert (failed["status"], failed["content"], failed["error"]["code"]) == ("ERROR", before["content"],
                                                                             "AI_PROCESSING_FAILED")
    assert failed["processing"]["kind"] == "CORRECTION"
    fake_ai.on("revise_structure", revise(rename="새 이름"))
    assert retry_review(flow, sid, WORK).status_code == 202
    flow.run()
    assert review(flow, sid, WORK)["content"]["shifts"][0]["name"] == "새 이름"
    assert fake_ai.calls_for("revise_structure")[-1].data["instruction"] == "정정 지시"
    assert count(flow, InterviewTurn, InterviewTurn.turn_kind == "CORRECTION") == 1  # no new turn


def test_voice_correction_needs_a_ready_transcript(flow):
    from tests.test_interview_api import _transcription

    sid = answered(flow)
    body = {"expectedRevision": review(flow, sid, WORK)["revision"],
            "input": {"method": "VOICE", "transcriptionId": _transcription(flow, "RUNNING")}}
    response = flow.post(flow.review_url(sid, flow.intents[WORK], "corrections"), body)
    assert (response.status_code, code(response)) == (409, "TRANSCRIPTION_NOT_READY")
    body["input"]["transcriptionId"] = _transcription(flow, "READY", text="마감조는 없어요.")
    assert flow.post(flow.review_url(sid, flow.intents[WORK], "corrections"), body).status_code == 202
    [turn] = rows(flow, InterviewTurn, InterviewTurn.turn_kind == "CORRECTION")
    assert (turn.input_method, turn.content) == ("VOICE", "마감조는 없어요.")


# --- photos ------------------------------------------------------------------------------------------


def test_photo_lists_are_replaced_and_kept_through_corrections(flow, fake_ai):
    sid = answered(flow, 2)
    confirm(flow, sid, WORK)
    photo, other = media(flow), media(flow)
    before = review(flow, sid, WORK)
    response = put_photos(flow, sid, WORK, [photo, other])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == before["revision"] + 1 and body["confirmedAt"] is None
    assert body["content"]["structurePhotos"] == [
        {"mediaId": photo, "title": "사진 1", "caption": None}, {"mediaId": other, "title": "사진 2", "caption": None}]
    same = put_photos(flow, sid, WORK, [photo, other])
    assert same.status_code == 200 and same.json()["revision"] == body["revision"]  # no real change
    refs = rows(flow, ManualMediaSnapshotRef, ManualMediaSnapshotRef.holder_kind == "INTENT_REVIEW")
    assert {r.media_id for r in refs} == {photo, other}
    # Section photos: only a section of this review.
    section_id = review(flow, sid, COMMON)["content"]["sections"][0]["id"]
    wrong = put_photos(flow, sid, COMMON, [photo], target="SECTION", section=str(uuid.uuid4()))
    assert (wrong.status_code, code(wrong)) == (422, "VALIDATION_ERROR")
    structure_on_common = put_photos(flow, sid, COMMON, [photo])
    assert (structure_on_common.status_code, code(structure_on_common)) == (422, "VALIDATION_ERROR")
    assert put_photos(flow, sid, COMMON, [photo], target="SECTION", section=section_id).status_code == 200
    # A correction that keeps the section keeps its photo; deleting the section unlinks it.
    fake_ai.on("revise_structure", revise(drop_sections=True))
    correct(flow, sid, COMMON, text="손님 응대는 빼 주세요.")
    flow.run()
    assert review(flow, sid, COMMON)["content"]["sections"] == []
    refs = rows(flow, ManualMediaSnapshotRef, ManualMediaSnapshotRef.holder_intent_id == flow.intents[COMMON])
    assert refs == []
    # Unlinking with an empty list.
    assert put_photos(flow, sid, WORK, []).json()["content"]["structurePhotos"] == []
    assert rows(flow, ManualMediaSnapshotRef, ManualMediaSnapshotRef.holder_kind == "INTENT_REVIEW") == []


def test_photo_input_rules(flow):
    sid = answered(flow)
    foreign = put_photos(flow, sid, WORK, [media(flow, store=flow.other_store)])
    assert (foreign.status_code, code(foreign)) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    audio = put_photos(flow, sid, WORK, [media(flow, "AUDIO")])
    assert (audio.status_code, code(audio)) == (422, "VALIDATION_ERROR")
    photo = media(flow)
    base = {"expectedRevision": review(flow, sid, WORK)["revision"], "target": "WORK_STRUCTURE", "sectionId": None}
    for body in ({**base, "photos": [{"mediaId": photo, "title": "a", "caption": None}] * 2},
                 {**base, "photos": [{"mediaId": photo, "title": " ", "caption": None}]},
                 {**base, "photos": [{"mediaId": photo, "title": "a"}]},
                 {**base, "photos": [{"mediaId": photo, "title": "a", "caption": "가" * 301}]},
                 {**base, "sectionId": str(uuid.uuid4()), "photos": []},
                 {**base, "target": "SECTION", "photos": []}):
        response = flow.api.put(flow.review_url(sid, flow.intents[WORK], "photos"), json=body,
                                headers=flow.auth.headers(str(uuid.uuid4())))
        assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR"), body
    stale = put_photos(flow, sid, WORK, [photo], revision=base["expectedRevision"] - 1)
    assert (stale.status_code, code(stale)) == (409, "REVISION_CONFLICT")


# --- completion and the first draft -------------------------------------------------------------------


def test_completion_composes_the_draft_with_photos_and_issues(flow, fake_ai):
    sid = flow.started()
    fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(INSUFFICIENT)] * 6)
    for _ in range(6):  # WORK_STRUCTURE ends NEEDS_DETAIL at depth 5
        flow.answer_and_run(sid)
    state = flow.finish_all(sid, 5)
    assert state["phase"] == "READY_TO_GENERATE"
    photo, section_photo = media(flow), media(flow)
    put_photos(flow, sid, WORK, [photo])
    common = review(flow, sid, COMMON)["content"]["sections"][0]
    put_photos(flow, sid, COMMON, [section_photo], target="SECTION", section=common["id"])
    response = flow.complete(sid)
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["phase"], body["processing"]["kind"], body["revision"]) == (
        "GENERATING", "DRAFT_GENERATION", state["revision"] + 1)
    version = rows(flow, ManualVersion)[0]
    assert version.generation_status == "RUNNING" and len(version.generation_input_snapshot["reviews"]) == 6
    # Reviews are frozen while the draft is generated.
    for response in (confirm(flow, sid, WORK), correct(flow, sid, COMMON), put_photos(flow, sid, WORK, []),
                     flow.complete(sid)):
        assert (response.status_code, code(response)) == (409, "INTERVIEW_STATE_CONFLICT")
    flow.run()
    done = flow.get(sid)
    assert (done["status"], done["phase"], done["processing"], done["currentIntentId"]) == (
        "COMPLETED", "COMPLETED", None, None)
    assert done["completedAt"] and done["revision"] == body["revision"] + 1
    version = rows(flow, ManualVersion)[0]
    assert (version.status, version.generation_status, version.revision) == ("DRAFT", "READY", 1)
    shifts = rows(flow, ManualShift)
    work = review(flow, sid, WORK)["content"]
    assert sorted(s.id for s in shifts) == sorted(s["id"] for s in work["shifts"])  # same IDs
    sections = {s.id: s for s in rows(flow, ManualSection)}
    assert common["id"] in sections and len(rows(flow, ManualStep)) >= 2
    shift_task = [s for s in sections.values() if s.category == "SHIFT_TASK"]
    assert shift_task and shift_task[0].shift_id in {s.id for s in shifts}
    attachments = {(a.section_id, a.media_id, a.title) for a in rows(flow, ManualPhotoAttachment)}
    assert attachments == {(None, photo, "사진 1"), (common["id"], section_photo, "사진 1")}
    issues = rows(flow, ManualReviewIssue)
    detail = [i for i in issues if i.target_kind is None]
    assert len(detail) == 1 and detail[0].intent_id == flow.intents[WORK]
    assert "근무 구조" in detail[0].description and "마감 순서" in detail[0].description
    assert rows(flow, ManualMediaSnapshotRef, ManualMediaSnapshotRef.holder_kind == "DRAFT_GENERATION")
    after = flow.post(flow.url(sid, "retries"), {"expectedRevision": done["revision"]})
    assert (after.status_code, code(after)) == (409, "INTERVIEW_STATE_CONFLICT")


def test_completion_preconditions(flow):
    sid = answered(flow, 1)
    early = flow.complete(sid)
    assert (early.status_code, code(early)) == (409, "INTERVIEW_INCOMPLETE")
    flow.finish_all(sid, 5)
    listing = flow.reviews(sid)
    items = [{"intentId": i["intentId"], "revision": i["revision"]} for i in listing["items"]]
    cases = [
        ({"items": items[:-1]}, 422, "VALIDATION_ERROR"),
        ({"items": [*items, items[0]]}, 422, "VALIDATION_ERROR"),
        ({"items": [*items[:-1], {"intentId": str(uuid.uuid4()), "revision": 1}]}, 404,
         "MANUAL_RESOURCE_NOT_FOUND"),
        ({"items": [{**items[0], "revision": items[0]["revision"] + 1}, *items[1:]]}, 409, "REVISION_CONFLICT"),
        ({"revision": listing["sessionRevision"] - 1}, 409, "REVISION_CONFLICT"),
        ({"items": []}, 422, "VALIDATION_ERROR"),
    ]
    for kwargs, status, error in cases:
        response = flow.complete(sid, **kwargs)
        assert (response.status_code, code(response)) == (status, error), kwargs
    correct(flow, sid, WORK)  # a correction accepted first makes the listing stale and not READY
    response = flow.complete(sid, items=[*items[:WORK], {**items[WORK], "revision": items[WORK]["revision"] + 1},
                                         *items[WORK + 1:]])
    assert (response.status_code, code(response)) == (409, "REVIEW_NOT_READY")
    assert rows(flow, InterviewSession)[0].processing_kind is None


def test_shift_removed_by_a_correction_blocks_completion_until_fixed(flow, fake_ai):
    sid = answered(flow, 6)
    shift_task = review(flow, sid, SHIFT)["content"]["sections"][0]
    assert shift_task["shiftId"] == review(flow, sid, WORK)["content"]["shifts"][0]["id"]
    fake_ai.script("revise_structure", FakeOutcome.ok({"outcome": "APPLIED", "summary": "마감조만 있어요.",
                                                        "structure": {"shifts": [raw_shift("new-1", "마감조")],
                                                                      "sections": [], "missing_information": []}}))
    correct(flow, sid, WORK, text="오픈조는 없어요.")
    flow.run()
    conflict = flow.complete(sid)
    assert (conflict.status_code, code(conflict)) == (409, "MANUAL_REFERENCE_CONFLICT")
    # Re-link the shift task through its own review, then generation is accepted.
    fake_ai.on("revise_structure", revise(drop_sections=True))
    correct(flow, sid, SHIFT, text="오픈 준비는 빼 주세요.")
    flow.run()
    assert flow.complete(sid).status_code == 202


def test_draft_failure_keeps_the_snapshot_and_retries_it(flow, fake_ai):
    sid = answered(flow, 6)
    fake_ai.script("compose_draft", *[FakeOutcome.fail("unavailable")] * 3)
    accepted = flow.complete(sid).json()
    flow.run()
    failed = flow.get(sid)
    assert (failed["status"], failed["phase"], failed["processing"]["kind"]) == ("ERROR", "ERROR", "DRAFT_GENERATION")
    assert rows(flow, ManualVersion)[0].generation_status == "ERROR"
    blocked = correct(flow, sid, WORK)  # the frozen snapshot is what the retry composes
    assert (blocked.status_code, code(blocked)) == (409, "INTERVIEW_STATE_CONFLICT")
    retried = flow.post(flow.url(sid, "retries"), {"expectedRevision": failed["revision"]})
    assert retried.status_code == 202, retried.text
    assert (retried.json()["phase"], retried.json()["processing"]["attempt"]) == ("GENERATING", 2)
    assert rows(flow, ManualVersion)[0].generation_status == "RUNNING"
    flow.run()
    assert flow.get(sid)["status"] == "COMPLETED" and rows(flow, ManualVersion)[0].generation_status == "READY"
    calls = fake_ai.calls_for("compose_draft")
    assert calls[0].data == calls[-1].data and accepted["draftVersionId"] == rows(flow, ManualVersion)[0].id


def test_draft_generation_result_applies_once(flow):
    sid = answered(flow, 6)
    flow.complete(sid)
    flow.run()
    flow.run()
    assert len(rows(flow, ManualShift)) == 2 and flow.get(sid)["status"] == "COMPLETED"


# --- races (MySQL row locks) ------------------------------------------------------------------------------


def _race(*calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(index, call):
        barrier.wait()
        results[index] = call()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


@pytest.fixture
def mysql_only(db_engine):
    if db_engine.dialect.name != "mysql":
        pytest.skip("row-lock race: SQLite serializes writers with database locks instead")


def test_concurrent_answers_create_one_turn_and_one_evaluation(flow, mysql_only):
    from app.db.models import BackgroundTask

    sid = flow.started()
    state = flow.get(sid)
    question, revision = state["questions"][0]["id"], state["revision"]
    results = _race(*[lambda i=i: flow.answer(sid, f"답변 {i}", question=question, revision=revision)
                      for i in range(4)])
    statuses = sorted(r.status_code for r in results)
    assert statuses == [202, 409, 409, 409], [r.text for r in results]
    assert {code(r) for r in results if r.status_code == 409} == {"QUESTION_ALREADY_ANSWERED"}
    assert count(flow, InterviewTurn, InterviewTurn.turn_kind == "ANSWER") == 1
    assert count(flow, BackgroundTask, BackgroundTask.kind == "EVALUATION") == 1


def test_correction_and_completion_race_has_one_winner(flow, mysql_only):
    sid = answered(flow, 6)
    results = _race(lambda: correct(flow, sid, WORK), lambda: flow.complete(sid))
    assert sorted(r.status_code for r in results) == [202, 409], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 409)
    assert code(loser) in ("REVIEW_NOT_READY", "REVISION_CONFLICT", "INTERVIEW_STATE_CONFLICT")
    corrected = review(flow, sid, WORK)["status"] == "PROCESSING"
    generating = flow.get(sid)["phase"] == "GENERATING"
    assert corrected != generating


def test_photos_and_completion_race_has_one_winner(flow, mysql_only):
    sid = answered(flow, 6)
    photo = media(flow)
    results = _race(lambda: put_photos(flow, sid, WORK, [photo]), lambda: flow.complete(sid))
    assert sorted(r.status_code for r in results) in ([200, 409], [202, 409]), [r.text for r in results]
    with Session(flow.engine) as db:
        version = db.scalars(select(ManualVersion)).one()
        frozen = version.generation_input_snapshot
    if frozen:  # generation won: the frozen snapshot has no photo and the review did not change
        assert frozen["reviews"][0]["content"]["structurePhotos"] == []
        assert review(flow, sid, WORK)["content"]["structurePhotos"] == []


def test_concurrent_confirmations_record_one_history_row(flow, mysql_only):
    sid = answered(flow)
    revision = review(flow, sid, WORK)["revision"]
    results = _race(*[lambda: confirm(flow, sid, WORK, revision=revision) for _ in range(3)])
    assert sorted(r.status_code for r in results) == [200, 409, 409]
    assert count(flow, InterviewReviewConfirmation) == 1


def test_generated_draft_is_read_by_the_draft_api(flow, fake_ai):
    """#118 serializes what DRAFT_GENERATION wrote: same IDs, photos, issues, revision 1."""
    sid = flow.started()
    fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(INSUFFICIENT)] * 6)
    for _ in range(6):
        flow.answer_and_run(sid)
    flow.finish_all(sid, 5)
    photo = media(flow)
    put_photos(flow, sid, WORK, [photo])
    flow.complete(sid)
    flow.run()
    response = flow.api.get(f"/api/stores/{flow.store}/manual/draft")
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["generationStatus"] == "READY" and draft["revision"] == 1
    assert draft["versionId"] == flow.get(sid)["draftVersionId"]
    content = draft["content"]
    assert [s["id"] for s in content["shifts"]] == [s["id"] for s in review(flow, sid, WORK)["content"]["shifts"]]
    assert content["structurePhotos"] == [{"mediaId": photo, "title": "사진 1", "caption": None}]
    assert any(i["intentId"] == flow.intents[WORK] for i in draft["issues"])


def test_concurrent_starts_create_one_draft(flow, mysql_only):
    results = _race(*[flow.start for _ in range(3)])
    assert sorted(r.status_code for r in results) == [201, 409, 409], [r.text for r in results]
    assert {code(r) for r in results if r.status_code == 409} == {"INTERVIEW_ALREADY_EXISTS"}
    assert len(rows(flow, ManualVersion)) == 1 and len(rows(flow, InterviewSession)) == 1


def test_shift_task_summary_waits_for_the_work_structure_shifts(flow, fake_ai):
    """The SHIFT_TASKS summary may be queued while the WORK_STRUCTURE summary is still being
    retried; it must still see the shifts, or its shift tasks could reference none."""
    from app.tasks import drain

    sid = flow.started()
    fake_ai.script("summarize_intent", FakeOutcome.fail("timeout"))  # WORK summary backs off
    from datetime import timedelta

    from sqlalchemy import update

    from app.db import utcnow
    from app.db.models import BackgroundTask

    for index in range(3):  # finish WORK, COMMON, SHIFT at the current time only
        flow.answer(sid)
        drain()
        drain()
        if index == 0:  # keep the WORK retry due only later, however slow this run is
            with Session(flow.engine) as db:
                db.execute(update(BackgroundTask).where(
                    BackgroundTask.kind == "REVIEW_UNDERSTANDING", BackgroundTask.status == "QUEUED",
                ).values(available_at=utcnow() + timedelta(minutes=3)))
                db.commit()
    assert flow.get(sid)["intents"][SHIFT]["coverage"] == "COVERED"
    assert review(flow, sid, WORK)["status"] == "PROCESSING"
    waiting = [t for t in rows(flow, BackgroundTask, BackgroundTask.kind == "REVIEW_UNDERSTANDING")
               if t.payload["intentId"] == flow.intents[SHIFT]]
    assert [(t.status, t.tries, t.last_error_code) for t in waiting] == [("QUEUED", 0, "DEFERRED")]
    assert flow.get(sid)["phase"] == "COLLECTING"  # questions never wait for summaries
    flow.run()  # later: the WORK retry succeeds, then the waiting SHIFT summary runs
    work = review(flow, sid, WORK)["content"]
    shift = review(flow, sid, SHIFT)["content"]
    assert [s["category"] for s in shift["sections"]] == ["SHIFT_TASK"]
    assert shift["sections"][0]["shiftId"] == work["shifts"][0]["id"]
    assert flow.complete(sid).status_code == 409  # not every intent finished yet; references fine


def test_failed_work_structure_summary_does_not_block_later_summaries(flow, fake_ai):
    from app.tasks import drain

    sid = flow.started()
    fake_ai.script("summarize_intent", *[FakeOutcome.fail("refused")])  # WORK summary: ERROR at once
    for _ in range(3):
        flow.answer(sid)
        drain()
        drain()
    flow.run()
    assert review(flow, sid, WORK)["status"] == "ERROR"
    shift = review(flow, sid, SHIFT)
    assert shift["status"] == "READY" and shift["content"]["sections"][0]["category"] == "COMMON_TASK"


def test_large_interview_stays_under_the_task_payload_limit(flow, fake_ai):
    """Reviews near the content limits make a generation snapshot of several MB; tasks carry only
    IDs and rebuild their request from the rows, so completion and corrections still work."""
    import json

    from app.db.models import BackgroundTask
    from app.tasks.runner import MAX_PAYLOAD_BYTES

    def big_summary(data):
        key = data["intent"]["key"]
        return raw_summary(f"{key} 요약이에요.", sections=[raw_section(
            "new-1", key, steps=[(f"new-{i + 2}", f"{key} {i}번 단계 " + "가" * 2900) for i in range(100)])])

    fake_ai.on("summarize_intent", big_summary)
    sid = answered(flow, 6)
    fake_ai.on("revise_structure", revise(rename=None))
    assert correct(flow, sid, COMMON).status_code == 202
    flow.run()
    response = flow.complete(sid)
    assert response.status_code == 202, response.text
    version = rows(flow, ManualVersion)[0]
    assert len(json.dumps(version.generation_input_snapshot, ensure_ascii=False).encode()) > 3 * MAX_PAYLOAD_BYTES
    assert all(len(json.dumps(t.payload, ensure_ascii=False).encode()) < 100_000
               for t in rows(flow, BackgroundTask, BackgroundTask.kind.in_(("DRAFT_GENERATION", "REVIEW_CORRECTION"))))
    flow.run()
    assert flow.get(sid)["status"] == "COMPLETED" and len(rows(flow, ManualStep)) == 600

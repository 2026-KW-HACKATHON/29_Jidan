"""Optional suggestions through real HTTP and isolated MySQL; only AI output is scripted."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.fake import FakeAiProvider
from app.db.models import BackgroundTask, InterviewIntentReview, InterviewTurn
from e2e.ai_scenario import summarize
from e2e.interview_helpers import interview_case, scenario_server
from e2e.test_interview_photo_lifecycle_http import (
    assert_review_commit,
    get,
    link,
    photo,
    poll,
    review_url,
    upload,
)


def photo_provider():
    provider = FakeAiProvider()
    provider.on("summarize_intent", summarize)

    def suggestions(data):
        return {"suggestions": [{
            "sectionId": section["id"], "title": "업무 위치 사진",
            "items": [{"label": section["title"], "description": None}], "footer": "사진 없이도 계속할 수 있어요.",
        } for section in data["structure"]["sections"][:5]]}

    provider.on("suggest_review_photos", suggestions)
    return provider


def photo_failure_provider():
    from app.ai.errors import AiError, AiErrorCode

    provider = photo_provider()

    def fail(_data):
        raise AiError(AiErrorCode.TIMEOUT)

    provider.on("suggest_review_photos", fail)
    return provider


def photo_cards(state):
    return [card for question in state["questions"] for card in question["guidanceCards"]
            if card["type"] == "PHOTO_SUGGESTIONS"]


def reach_section_review(case):
    sid = case.start()["id"]
    for _ in range(2):
        state = case.wait(sid, lambda body: body["phase"] == "COLLECTING")
        accepted = case.answer(state)
        assert accepted.status_code == 202, accepted.text
    return sid


def test_suggestion_target_is_committed_and_existing_upload_link_retry_preserves_photo(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_interview_photo_suggestions_http:photo_provider") as origin,
          interview_case(real_db, origin) as case):
        sid = reach_section_review(case)
        state = case.wait(sid, lambda body: bool(photo_cards(body)))
        cards = photo_cards(state)
        assert len(state["questions"][0]["guidanceCards"]) <= 5
        assert case.get(sid) == state
        card = cards[0]
        target = card["attachmentTarget"]
        assert target["target"] == "SECTION"
        current = get(case, review_url(case, sid, target["intentId"]))
        assert current["status"] == "READY"
        assert target["sectionId"] in {section["id"] for section in current["content"]["sections"]}
        with Session(case.engine) as db:
            saved = db.get(InterviewTurn, state["questions"][0]["id"])
            assert saved.guidance_cards == state["questions"][0]["guidanceCards"]
            assert db.get(InterviewIntentReview, (sid, target["intentId"])).ready_content == current["content"]
        uploaded = upload(case)
        selected = [photo(uploaded)]
        failed = link(case, sid, current, target["sectionId"], selected, expected=current["revision"] - 1)
        assert failed.status_code == 409
        assert_review_commit(case, sid, current, target["sectionId"], [])
        linked = link(case, sid, current, target["sectionId"], selected)
        assert linked.status_code == 200, linked.text
        assert_review_commit(case, sid, linked.json(), target["sectionId"], selected)
        before = case.get(sid)
        accepted = case.answer(before)
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["lastAnsweredQuestion"] == {**before["questions"][0], "answered": True}
        # Continuing without another upload is not a deletion command.
        assert_review_commit(case, sid, linked.json(), target["sectionId"], selected)
        with Session(case.engine) as db:
            assert db.get(InterviewTurn, before["questions"][0]["id"]).guidance_cards == before["questions"][0]["guidanceCards"]


def test_optional_photo_failure_cannot_block_ready_reviews_or_final_completion(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_interview_photo_suggestions_http:photo_failure_provider") as origin,
          interview_case(real_db, origin) as case):
        sid = case.start()["id"]
        for _ in range(6):
            state = case.wait(sid, lambda body: body["phase"] == "COLLECTING")
            assert not photo_cards(state)
            accepted = case.answer(state)
            assert accepted.status_code == 202, accepted.text
        state = case.wait(sid, lambda body: body["phase"] == "READY_TO_GENERATE")
        assert state["questions"] == []
        listing = poll(case, case.url(sid, "/reviews"), lambda body: len(body["items"]) == 6
                       and all(item["status"] == "READY" for item in body["items"]))
        response = case.post(case.url(sid, "/completion"), {
            "expectedRevision": state["revision"], "reviewRevisions": [
                {"intentId": item["intentId"], "revision": item["revision"]} for item in listing["items"]],
        })
        assert response.status_code == 202, response.text
        case.wait(sid, lambda body: body["status"] == "COMPLETED")


def gated_photo_provider():
    """File handshake controls only the external AI boundary, never a server handler."""
    import os
    import time
    from pathlib import Path

    provider = photo_provider()

    def gated(data):
        gate = Path(os.environ["JIDAN_PHOTO_TEST_GATE"])
        gate.with_suffix(".started").write_text("photo call started")
        deadline = time.monotonic() + 15
        while not gate.exists():
            if time.monotonic() > deadline:
                raise TimeoutError("test photo gate not released")
            time.sleep(0.02)
        return {"suggestions": [{
            "sectionId": data["structure"]["sections"][0]["id"], "title": "업무 위치 사진",
            "items": [{"label": "위치", "description": None}], "footer": None,
        }]}

    provider.on("suggest_review_photos", gated)
    return provider


def test_review_correction_during_optional_call_rejects_stale_cards_over_http(real_db, tmp_path, monkeypatch):
    import time

    gate = tmp_path / "photo-release"
    monkeypatch.setenv("JIDAN_PHOTO_TEST_GATE", str(gate))
    with (scenario_server(tmp_path, "e2e.test_interview_photo_suggestions_http:gated_photo_provider") as origin,
          interview_case(real_db, origin) as case):
        sid = reach_section_review(case)
        deadline = time.monotonic() + 10
        while not gate.with_suffix(".started").exists():
            assert time.monotonic() < deadline, "photo provider was not invoked"
            time.sleep(0.02)
        listing = get(case, case.url(sid, "/reviews"))
        source = next(item for item in listing["items"] if item["content"]["sections"])
        response = case.post(review_url(case, sid, source["intentId"]) + "/corrections", {
            "expectedRevision": source["revision"], "input": {"method": "TEXT", "text": "순서를 수정해 주세요."},
        })
        assert response.status_code == 202, response.text
        gate.write_text("release optional provider")
        corrected = poll(case, review_url(case, sid, source["intentId"]), lambda body: body["status"] == "READY")
        assert corrected["revision"] > source["revision"]
        state = case.wait(sid, lambda body: body["phase"] == "COLLECTING")
        assert not photo_cards(state)
        assert case.get(sid) == state
        with Session(case.engine) as db:
            saved = db.get(InterviewTurn, state["questions"][0]["id"])
            assert saved.guidance_cards == []
            assert db.get(InterviewIntentReview, (sid, source["intentId"])).revision == corrected["revision"]


def summary_failure_provider():
    from app.ai.errors import AiError, AiErrorCode

    provider = photo_provider()

    def fail(_data):
        raise AiError(AiErrorCode.TIMEOUT)

    provider.on("summarize_intent", fail)
    return provider


def test_exhausted_summary_retry_unblocks_next_question_in_real_worker(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_interview_photo_suggestions_http:summary_failure_provider") as origin,
          interview_case(real_db, origin) as case):
        sid = case.start()["id"]
        before = case.wait(sid, lambda body: body["phase"] == "COLLECTING")
        intent_id = before["currentIntentId"]
        response = case.answer(before)
        assert response.status_code == 202, response.text
        independent = case.wait(sid, lambda body: body["phase"] == "COLLECTING", timeout=5)
        assert independent["currentIntentId"] != intent_id
        assert get(case, review_url(case, sid, intent_id))["status"] == "PROCESSING"
        failed = poll(case, case.url(sid, "/reviews"), lambda body: bool(body["items"])
                      and body["items"][0]["status"] == "ERROR")["items"][0]
        assert failed["intentId"] == intent_id
        assert failed["error"]["code"] == "AI_PROCESSING_FAILED"
        # No manual drain or simulated time; the already available question stays unchanged.
        after = case.wait(sid, lambda body: body["phase"] == "COLLECTING")
        assert after["currentIntentId"] != intent_id and not photo_cards(after)
        assert case.get(sid) == after
        with Session(case.engine) as db:
            review = db.get(InterviewIntentReview, (sid, intent_id))
            assert review.status == "ERROR" and review.ready_content is None
            task = db.get(BackgroundTask, review.processing_task_id)
            assert task.status == "FAILED" and task.tries == 3
            question = db.get(InterviewTurn, after["questions"][0]["id"])
            assert question.content == after["questions"][0]["text"]
            initial_tasks = list(db.scalars(select(BackgroundTask).where(
                BackgroundTask.subject_id == sid, BackgroundTask.kind == "INITIAL_QUESTION")))
            assert len(initial_tasks) == 2 and all(task.status == "SUCCEEDED" for task in initial_tasks)


def test_answer_accepted_while_photo_hook_runs_keeps_snapshot_over_http(real_db, tmp_path, monkeypatch):
    import time

    gate = tmp_path / "answer-photo-release"
    monkeypatch.setenv("JIDAN_PHOTO_TEST_GATE", str(gate))
    with (scenario_server(tmp_path, "e2e.test_interview_photo_suggestions_http:gated_photo_provider") as origin,
          interview_case(real_db, origin) as case):
        sid = reach_section_review(case)
        deadline = time.monotonic() + 10
        while not gate.with_suffix(".started").exists():
            assert time.monotonic() < deadline, "photo provider was not invoked"
            time.sleep(0.02)
        before = case.get(sid)
        assert before["phase"] == "COLLECTING" and not photo_cards(before)
        question = before["questions"][0]
        accepted = case.answer(before)
        expected = {**question, "answered": True}
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["lastAnsweredQuestion"] == expected
        assert case.get(sid)["lastAnsweredQuestion"] == expected
        gate.write_text("release optional provider")
        case.wait(sid, lambda body: body["phase"] == "COLLECTING"
                  and body["currentIntentId"] != before["currentIntentId"])
        with Session(case.engine) as db:
            assert db.get(InterviewTurn, question["id"]).guidance_cards == question["guidanceCards"]
            answer = db.scalar(select(InterviewTurn).where(InterviewTurn.reply_to_question_turn_id == question["id"]))
            assert answer.guidance_cards == question["guidanceCards"]

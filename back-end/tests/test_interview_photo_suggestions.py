"""Post-commit optional photo guards; real API projections and independent DB reads."""
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db.models import BackgroundTask, InterviewSession, InterviewTurn
from app.interview.photos import suggest_current_question_photos
from app.tasks import TaskContext, drain
from tests.test_interview_api import build_ctx, media_root  # noqa: F401
from tests.test_interview_reviews import mysql_only, summaries  # noqa: F401


@pytest.fixture
def ctx(api, db_engine, fake_ai, media_root, monkeypatch):  # noqa: F811
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    fake_ai.on("summarize_intent", summaries)
    fake_ai.on("suggest_review_photos", suggestions)
    return build_ctx(api, db_engine)


def suggestions(data):
    return {"suggestions": [{"sectionId": section["id"], "title": "업무 사진", "footer": None,
                             "items": [{"label": section["title"], "description": None}]}
                            for section in data["structure"]["sections"][:5]]}


def pending_next_question(ctx):
    sid = ctx.started()
    ctx.answer_and_run(sid)
    assert ctx.answer(sid).status_code == 202
    drain(kinds=("EVALUATION",))
    return sid


def hook_context(ctx, sid):
    state = ctx.get(sid)
    with Session(ctx.engine) as db:
        task = next(task for task in db.scalars(select(BackgroundTask).where(
            BackgroundTask.subject_id == sid, BackgroundTask.kind == "INITIAL_QUESTION"))
                    if task.payload["intentId"] == state["currentIntentId"])
        return TaskContext(task.id, task.kind, sid, task.attempt, task.input_revision, task.payload, task.tries)


def unanswered_without_photos(ctx, fake_ai):
    sid = pending_next_question(ctx)
    fake_ai.on("suggest_review_photos", lambda _data: {"suggestions": []})
    ctx.run()
    fake_ai.on("suggest_review_photos", suggestions)
    return sid, hook_context(ctx, sid)


@pytest.mark.parametrize("first", ["REVIEW_UNDERSTANDING", "INITIAL_QUESTION"])
def test_both_commit_orders_deliver_once_with_immutable_task_payloads(ctx, fake_ai, first):
    sid = pending_next_question(ctx)
    before = ctx.get(sid)

    def assert_committed_before_call(data):
        with Session(ctx.engine) as db:
            from app.db.models import InterviewIntentReview

            source = db.get(InterviewIntentReview, (sid, ctx.intents[1]))
            assert source.status == "READY" and source.ready_content["summary"] == data["summary"]
            session = db.get(InterviewSession, sid)
            assert session.processing_task_id is None
            question = db.scalar(select(InterviewTurn).where(
                InterviewTurn.session_id == sid, InterviewTurn.intent_id == session.current_intent_id,
                InterviewTurn.turn_kind == "QUESTION"))
            assert question is not None
        return suggestions(data)

    fake_ai.on("suggest_review_photos", assert_committed_before_call)
    with Session(ctx.engine) as db:
        payloads = {task.id: deepcopy(task.payload) for task in db.scalars(select(BackgroundTask).where(
            BackgroundTask.subject_id == sid))}
    drain(kinds=(first,))
    ctx.run()
    state = ctx.get(sid)
    cards = state["questions"][0]["guidanceCards"]
    assert len(cards) == 1 and cards[0]["type"] == "PHOTO_SUGGESTIONS"
    source = ctx.review(sid, ctx.intents[1]).json()
    assert cards[0]["attachmentTarget"]["sectionId"] in {s["id"] for s in source["content"]["sections"]}
    assert len(fake_ai.calls_for("suggest_review_photos")) == 1
    assert fake_ai.calls_for("suggest_review_photos")[0].data["summary"] == source["content"]["summary"]
    assert ctx.get(sid) == state
    assert state["revision"] == before["revision"] + 2  # question commit, then optional card commit
    with Session(ctx.engine) as db:
        assert {task.id: task.payload for task in db.scalars(select(BackgroundTask).where(
            BackgroundTask.subject_id == sid))} == payloads
        assert db.get(InterviewTurn, state["questions"][0]["id"]).guidance_cards == cards
    suggest_current_question_photos(hook_context(ctx, sid))
    assert ctx.get(sid) == state and len(fake_ai.calls_for("suggest_review_photos")) == 1


def test_processing_or_failed_summary_never_blocks_question(ctx, fake_ai):
    sid = ctx.started()
    assert ctx.answer(sid).status_code == 202
    drain(kinds=("EVALUATION",))
    drain(kinds=("INITIAL_QUESTION",))
    state = ctx.get(sid)
    assert state["phase"] == "COLLECTING"
    assert ctx.review(sid, ctx.intents[0]).json()["status"] == "PROCESSING"
    fake_ai.script("summarize_intent", *[FakeOutcome.fail("timeout")] * 3)
    ctx.run()
    assert ctx.get(sid) == state
    assert ctx.review(sid, ctx.intents[0]).json()["status"] == "ERROR"


@pytest.mark.parametrize("failure", ["timeout", "invalid_output"])
def test_hook_failure_keeps_primary_succeeded_and_ready_review(ctx, fake_ai, failure):
    sid = pending_next_question(ctx)
    drain(kinds=("REVIEW_UNDERSTANDING",))
    task_id = ctx.get(sid)["processing"]["taskId"]
    fake_ai.script("generate_question", FakeOutcome.ok({"question": "정상 생성한 질문", "guidance": None,
                                                        "guidanceCards": []}))
    fake_ai.script("suggest_review_photos", FakeOutcome.fail(failure))
    runs = drain(kinds=("INITIAL_QUESTION",))
    assert runs[-1].outcome == "succeeded"
    state = ctx.get(sid)
    assert state["phase"] == "COLLECTING"
    assert state["questions"][0]["text"] == "정상 생성한 질문"
    assert state["questions"][0]["guidanceCards"] == []
    assert ctx.review(sid, ctx.intents[1]).json()["status"] == "READY"
    with Session(ctx.engine) as db:
        assert db.get(BackgroundTask, task_id).status == "SUCCEEDED"
        assert db.get(BackgroundTask, task_id).tries == 1
        assert db.get(InterviewTurn, state["questions"][0]["id"]).content == "정상 생성한 질문"


@pytest.mark.parametrize("count", [4, 5])
def test_mixed_cap_preserves_all_question_cards(ctx, fake_ai, count):
    sid = pending_next_question(ctx)
    fake_ai.script("generate_question", FakeOutcome.ok({
        "question": "업무를 알려 주세요.", "guidance": None, "guidanceCards": [
            {"type": "LIST", "title": f"질문 안내 {i}", "footer": None,
             "items": [{"id": None, "label": f"예시 {i}", "description": None, "status": None}]}
            for i in range(count)],
    }))
    ctx.run()
    cards = ctx.get(sid)["questions"][0]["guidanceCards"]
    assert len(cards) == 5
    assert [card["title"] for card in cards[:count]] == [f"질문 안내 {i}" for i in range(count)]
    assert len([card for card in cards if card["type"] == "PHOTO_SUGGESTIONS"]) == 5 - count


def test_source_correction_during_hook_discards_only_photos(ctx, fake_ai):
    sid, context = unanswered_without_photos(ctx, fake_ai)
    before = ctx.get(sid)

    def correct_while_photo_runs(data):
        current = ctx.review(sid, ctx.intents[1]).json()
        response = ctx.post(ctx.review_url(sid, ctx.intents[1], "corrections"), {
            "expectedRevision": current["revision"], "input": {"method": "TEXT", "text": "내용을 수정해 주세요."},
        })
        assert response.status_code == 202
        return suggestions(data)

    fake_ai.on("suggest_review_photos", correct_while_photo_runs)
    suggest_current_question_photos(context)
    assert ctx.get(sid) == before
    with Session(ctx.engine) as db:
        assert db.get(InterviewTurn, before["questions"][0]["id"]).guidance_cards == []


def test_answer_during_hook_preserves_accepted_question_snapshot(ctx, fake_ai):
    sid, context = unanswered_without_photos(ctx, fake_ai)
    before = ctx.get(sid)
    expected = {**before["questions"][0], "answered": True}

    def answer_while_photo_runs(data):
        response = ctx.answer(sid)
        assert response.status_code == 202 and response.json()["lastAnsweredQuestion"] == expected
        return suggestions(data)

    fake_ai.on("suggest_review_photos", answer_while_photo_runs)
    suggest_current_question_photos(context)
    assert ctx.get(sid)["lastAnsweredQuestion"] == expected
    with Session(ctx.engine) as db:
        assert db.get(InterviewTurn, expected["id"]).guidance_cards == expected["guidanceCards"]
        answer = db.scalar(select(InterviewTurn).where(InterviewTurn.reply_to_question_turn_id == expected["id"]))
        assert answer.guidance_cards == expected["guidanceCards"]


def test_overlapping_hooks_keep_first_committed_card_ids_and_revision(ctx, fake_ai):
    sid, context = unanswered_without_photos(ctx, fake_ai)
    before = ctx.get(sid)
    first_commit = []

    def second_hook_commits_first(data):
        fake_ai.on("suggest_review_photos", suggestions)
        suggest_current_question_photos(context)
        first_commit.append(ctx.get(sid))
        return suggestions(data)

    fake_ai.on("suggest_review_photos", second_hook_commits_first)
    suggest_current_question_photos(context)
    after = ctx.get(sid)
    assert after == first_commit[0]
    assert after["revision"] == before["revision"] + 1
    assert len(after["questions"][0]["guidanceCards"]) == 1
    with Session(ctx.engine) as db:
        assert db.get(InterviewSession, sid).revision == after["revision"]
        assert db.get(InterviewTurn, after["questions"][0]["id"]).guidance_cards == after["questions"][0]["guidanceCards"]


def test_final_intent_uses_manual_attachment_without_synthetic_question(ctx, fake_ai):
    sid = ctx.started()
    for _ in range(5):
        ctx.answer_and_run(sid)
    calls = len(fake_ai.calls_for("suggest_review_photos"))
    last_question = ctx.get(sid)["questions"][0]
    state = ctx.answer_and_run(sid)
    assert state["phase"] == "READY_TO_GENERATE" and state["questions"] == []
    assert len(fake_ai.calls_for("suggest_review_photos")) == calls
    assert ctx.review(sid, ctx.intents[-1]).json()["status"] == "READY"
    with Session(ctx.engine) as db:
        latest = db.scalars(select(InterviewTurn).where(
            InterviewTurn.session_id == sid, InterviewTurn.turn_kind == "QUESTION"
        ).order_by(InterviewTurn.turn_no.desc())).first()
        assert latest.id == last_question["id"]


def test_concurrent_hooks_apply_one_card_set_with_mysql_row_locks(ctx, fake_ai, mysql_only):  # noqa: F811
    import threading
    from concurrent.futures import ThreadPoolExecutor

    sid, context = unanswered_without_photos(ctx, fake_ai)
    before = ctx.get(sid)
    barrier = threading.Barrier(2)

    def simultaneous(data):
        barrier.wait(timeout=10)
        return suggestions(data)

    fake_ai.on("suggest_review_photos", simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(suggest_current_question_photos, context) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    after = ctx.get(sid)
    assert after["revision"] == before["revision"] + 1
    assert len(after["questions"][0]["guidanceCards"]) == 1
    assert ctx.get(sid) == after
    with Session(ctx.engine) as db:
        assert db.get(InterviewSession, sid).revision == after["revision"]
        assert db.get(InterviewTurn, after["questions"][0]["id"]).guidance_cards == after["questions"][0]["guidanceCards"]

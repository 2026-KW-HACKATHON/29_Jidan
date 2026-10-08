"""Persisted cards, scoped generation snapshots and evaluation-only answer snapshots."""
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db.models import BackgroundTask, InterviewTurn
from app.interview.common import question_body
from app.interview.settings import guidance_responses_enabled
from tests.test_interview_api import INSUFFICIENT, build_ctx, media_root  # noqa: F401


@pytest.fixture
def ctx(api, db_engine, media_root):  # noqa: F811
    return build_ctx(api, db_engine)


def raw_question(*, cards=True):
    return {"question": "어떤 순서로 일하나요?", "guidance": "실제 업무를 알려 주세요.",
            "guidanceCards": [{"type": "PROGRESS_CHECKLIST", "title": "실제 업무", "footer": None,
                               "items": [{"id": None, "label": "재고 정리", "description": None,
                                          "status": "CURRENT"}]}] if cards else []}


def test_guidance_persistence_and_projection_switch_do_not_mutate_rows(ctx, fake_ai, monkeypatch):
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "off")
    fake_ai.script("generate_question", FakeOutcome.ok(raw_question()))
    sid = ctx.started()
    hidden = ctx.get(sid)["questions"][0]
    assert "guidance" not in hidden and "guidanceCards" not in hidden
    with Session(ctx.engine) as db:
        turn = db.get(InterviewTurn, hidden["id"])
        saved = deepcopy(turn.guidance_cards)
        assert turn.guidance == raw_question()["guidance"] and len(saved) == 1
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    shown = ctx.get(sid)["questions"][0]
    assert shown["guidanceCards"] == saved
    assert ctx.get(sid)["questions"][0] == shown
    assert len(fake_ai.calls_for("generate_question")) == 1
    with Session(ctx.engine) as db:
        assert db.get(InterviewTurn, hidden["id"]).guidance_cards == saved


def test_answer_snapshot_stays_current_through_failure_retry_and_clears_on_next_question(ctx, fake_ai, monkeypatch):
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    fake_ai.script("generate_question", FakeOutcome.ok(raw_question()))
    sid = ctx.started()
    question = ctx.get(sid)["questions"][0]
    fake_ai.script("judge_sufficiency", *[FakeOutcome.fail("timeout")] * 3)
    response = ctx.answer(sid)
    assert response.status_code == 202
    expected = {**question, "answered": True}
    assert response.json()["lastAnsweredQuestion"] == expected
    assert response.json()["questions"] == []
    ctx.run()
    failed = ctx.get(sid)
    assert failed["lastAnsweredQuestion"] == expected and failed["phase"] == "ERROR"
    response = ctx.post(ctx.url(sid, "retries"), {"expectedRevision": failed["revision"]})
    assert response.status_code == 202 and response.json()["lastAnsweredQuestion"] == expected
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    ctx.run()
    state = ctx.get(sid)
    assert state["lastAnsweredQuestion"] is None and state["questions"][0]["id"] != question["id"]
    with Session(ctx.engine) as db:
        assert question_body(db.get(InterviewTurn, question["id"]), answered=True) == expected


def test_question_context_keeps_last_cards_after_omitted_turn_and_evaluation_snapshot(ctx, fake_ai, monkeypatch):
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    fake_ai.script("generate_question", FakeOutcome.ok(raw_question()), FakeOutcome.ok(raw_question(cards=False)))
    sid = ctx.started()
    initial = ctx.get(sid)["questions"][0]["guidanceCards"]
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT), FakeOutcome.ok(INSUFFICIENT))
    ctx.answer_and_run(sid)
    state = ctx.answer_and_run(sid)
    requests = fake_ai.calls_for("generate_question")
    assert requests[1].data["previous_cards"] == initial
    assert requests[2].data["previous_cards"] == initial
    assert requests[2].data["evaluation"] == INSUFFICIENT
    with Session(ctx.engine) as db:
        task = db.scalars(select(BackgroundTask).where(
            BackgroundTask.subject_id == sid, BackgroundTask.kind == "FOLLOWUP_GENERATION"
        ).order_by(BackgroundTask.created_at.desc())).first()
        assert task.payload["request"]["previous_cards"] == []
    assert state["questions"][0]["depth"] == 2


def test_omitted_item_uses_saved_history_without_rewriting_latest_card(ctx, fake_ai, monkeypatch):
    from app.interview.flow import question_card_history

    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    initial = raw_question()
    initial["guidanceCards"][0]["items"].append(
        {"id": None, "label": "시재 점검", "description": None, "status": "PENDING"})
    fake_ai.script("generate_question", FakeOutcome.ok(initial))
    sid = ctx.started()
    first = ctx.get(sid)["questions"][0]
    original = first["guidanceCards"][0]
    partial = raw_question()
    partial["guidanceCards"][0]["items"][0]["id"] = original["items"][0]["id"]
    fake_ai.script("generate_question", FakeOutcome.ok(partial))
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT), FakeOutcome.ok(INSUFFICIENT))
    second = ctx.answer_and_run(sid)["questions"][0]
    returning = deepcopy(initial)
    for item, saved in zip(returning["guidanceCards"][0]["items"], original["items"]):
        item["id"] = saved["id"]
    fake_ai.script("generate_question", FakeOutcome.ok(returning))
    third = ctx.answer_and_run(sid)["questions"][0]
    assert third["guidanceCards"] == first["guidanceCards"]
    # Actual immutable snapshots remain distinct, newest first. Omitted work is
    # not synthesized into the latest card to manufacture current facts.
    assert fake_ai.calls_for("generate_question")[-1].data["previous_cards"] == [
        *second["guidanceCards"], *first["guidanceCards"]]
    assert len(second["guidanceCards"][0]["items"]) == 1
    with Session(ctx.engine) as db:
        first_turn = db.get(InterviewTurn, first["id"])
        history = question_card_history(db, sid, first_turn.intent_id, third["depth"])
        assert history == [*second["guidanceCards"], *first["guidanceCards"]]
        assert question_card_history(db, str(uuid4()), first_turn.intent_id, third["depth"]) == []
        assert question_card_history(db, sid, str(uuid4()), third["depth"]) == []
        assert question_card_history(db, sid, first_turn.intent_id, 1) == first["guidanceCards"]
        assert first_turn.guidance_cards == first["guidanceCards"]
        assert db.get(InterviewTurn, second["id"]).guidance_cards == second["guidanceCards"]
    assert ctx.get(sid)["questions"][0] == third


def test_question_history_replay_excludes_future_answered_turns(ctx, fake_ai, monkeypatch):
    from app.interview.tasks import _question_execute
    from app.tasks import TaskContext

    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    fake_ai.script("generate_question", *[FakeOutcome.ok(raw_question())] * 3)
    fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(INSUFFICIENT)] * 2)
    sid = ctx.started()
    first = ctx.get(sid)["questions"][0]
    ctx.answer_and_run(sid)
    original_request = deepcopy(fake_ai.calls_for("generate_question")[-1].data)
    with Session(ctx.engine) as db:
        task = db.scalars(select(BackgroundTask).where(
            BackgroundTask.subject_id == sid, BackgroundTask.kind == "FOLLOWUP_GENERATION"
        )).one()
        task_ctx = TaskContext(task.id, task.kind, sid, task.attempt, task.input_revision, task.payload, 1)
    ctx.answer_and_run(sid)
    _question_execute(task_ctx)
    assert fake_ai.calls_for("generate_question")[-1].data == original_request
    assert original_request["previous_cards"] == first["guidanceCards"]


def test_large_card_history_is_rebuilt_without_task_payload_duplication(ctx, fake_ai, monkeypatch):
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    large = raw_question(cards=False)
    large["guidanceCards"] = [
        {"type": "LIST", "title": "설명", "footer": None,
         "items": [{"id": None, "label": f"예시 {i}", "description": "가" * 1000, "status": None}
                   for i in range(50)]}
        for _ in range(5)]
    fake_ai.script("generate_question", *[FakeOutcome.ok(large)] * 3)
    fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(INSUFFICIENT)] * 2)
    sid = ctx.started()
    ctx.answer_and_run(sid)
    ctx.answer_and_run(sid)
    history = fake_ai.calls_for("generate_question")[-1].data["previous_cards"]
    assert len(history) == 10  # output max-five does not truncate AI input history
    assert len(json.dumps(history, ensure_ascii=False).encode()) > 1_000_000
    with Session(ctx.engine) as db:
        tasks = list(db.scalars(select(BackgroundTask).where(
            BackgroundTask.subject_id == sid, BackgroundTask.kind == "FOLLOWUP_GENERATION")))
        assert len(tasks) == 2
        for task in tasks:
            assert task.payload["request"]["previous_cards"] == []
            assert len(json.dumps(task.payload, ensure_ascii=False).encode()) < 100_000


@pytest.mark.parametrize("value", ["true", "false", "", "invalid"])
def test_guidance_setting_rejects_unknown_values(monkeypatch, value):
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", value)
    with pytest.raises(ValueError, match="INTERVIEW_GUIDANCE_RESPONSES"):
        guidance_responses_enabled()


def test_question_and_cards_roll_back_together_after_write_failure(ctx, fake_ai, monkeypatch):
    from app.ai.contracts import QuestionRequest
    from app.interview import tasks
    from app.tasks import TaskContext

    sid = ctx.start().json()["id"]
    before = ctx.get(sid)
    with Session(ctx.engine) as db:
        task = db.get(BackgroundTask, before["processing"]["taskId"])
        task_ctx = TaskContext(task.id, task.kind, sid, task.attempt, task.input_revision, task.payload, 1)
    fake_ai.script("generate_question", FakeOutcome.ok(raw_question()))
    result = fake_ai.generate_question(QuestionRequest.model_validate(task_ctx.payload["request"]))
    original = tasks.write_question

    def fail_after_flush(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("question persistence failure")

    monkeypatch.setattr(tasks, "write_question", fail_after_flush)
    with pytest.raises(RuntimeError, match="persistence failure"), Session(ctx.engine) as db, db.begin():
        tasks._question_apply(db, task_ctx, result)
    assert ctx.get(sid) == before
    with Session(ctx.engine) as db:
        assert list(db.scalars(select(InterviewTurn).where(InterviewTurn.session_id == sid))) == []


def test_stale_question_result_cannot_replace_guidance(ctx, fake_ai, monkeypatch):
    from app.tasks import enqueue

    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    fake_ai.script("generate_question", FakeOutcome.ok(raw_question()))
    sid = ctx.started()
    before = ctx.get(sid)
    with Session(ctx.engine) as db:
        original = db.scalars(select(BackgroundTask).where(BackgroundTask.subject_id == sid)).one()
        enqueue(db, "INITIAL_QUESTION", sid, original.payload, input_revision=original.input_revision)
        db.commit()
    assert [run.outcome for run in ctx.run()] == ["cancelled"]
    assert ctx.get(sid) == before

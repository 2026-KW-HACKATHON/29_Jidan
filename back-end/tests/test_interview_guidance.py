"""Persisted cards, scoped generation snapshots and evaluation-only answer snapshots."""
from copy import deepcopy

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
        assert task.payload["request"]["previous_cards"] == initial
    assert state["questions"][0]["depth"] == 2


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

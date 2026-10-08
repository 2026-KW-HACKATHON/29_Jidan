"""Accepted optional guidance is immutable across rollout configuration changes."""
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db.models import BackgroundTask, InterviewTurn
from app.interview.common import answered_question_body
from tests.test_interview_api import build_ctx, media_root  # noqa: F401
from tests.test_interview_guidance import raw_question


@pytest.fixture
def ctx(api, db_engine, media_root):  # noqa: F811
    return build_ctx(api, db_engine)


@pytest.mark.parametrize("enabled,cards", [(True, True), (True, False), (False, True)])
def test_answer_copies_only_the_accepted_guidance_fields(ctx, fake_ai, monkeypatch, enabled, cards):
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on" if enabled else "off")
    raw = raw_question(cards=cards)
    if not cards:
        raw["guidance"] = None
    fake_ai.script("generate_question", FakeOutcome.ok(raw))
    sid = ctx.started()
    question = ctx.get(sid)["questions"][0]
    response = ctx.answer(sid)
    assert response.status_code == 202
    expected = {**question, "answered": True}
    for flag in ("off", "on"):
        monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", flag)
        assert ctx.get(sid)["lastAnsweredQuestion"] == expected
    with Session(ctx.engine) as db:
        answer = db.scalar(select(InterviewTurn).where(InterviewTurn.reply_to_question_turn_id == question["id"]))
        assert answer.guidance == question.get("guidance")
        assert answer.guidance_cards == question.get("guidanceCards")
        restored = answered_question_body(db.get(InterviewTurn, question["id"]), answer)
        assert restored == expected
        before = deepcopy(answer.guidance_cards)
        restored.get("guidanceCards", []).clear()
        assert answer.guidance_cards == before


def test_legacy_answer_does_not_infer_optional_fields_from_current_flag(monkeypatch):
    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    question = InterviewTurn(id="q", intent_id="i", question_kind="BASE", depth=0,
                             content="질문", guidance="과거 생성 안내", guidance_cards=[])
    answer = InterviewTurn(guidance=None, guidance_cards=None)
    restored = answered_question_body(question, answer)
    assert "guidance" not in restored and "guidanceCards" not in restored
    assert restored["answered"] is True


def test_answer_snapshot_rolls_back_if_evaluation_enqueue_fails(ctx, fake_ai, monkeypatch):
    from app.interview import routes

    monkeypatch.setenv("INTERVIEW_GUIDANCE_RESPONSES", "on")
    fake_ai.script("generate_question", FakeOutcome.ok(raw_question()))
    sid = ctx.started()
    before = ctx.get(sid)
    original = routes.enqueue_evaluation

    def fail_after_enqueue(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("forced failure after storing answer and task")

    monkeypatch.setattr(routes, "enqueue_evaluation", fail_after_enqueue)
    assert ctx.answer(sid).status_code == 500
    assert ctx.get(sid) == before
    with Session(ctx.engine) as db:
        assert db.scalar(select(InterviewTurn).where(
            InterviewTurn.session_id == sid, InterviewTurn.turn_kind == "ANSWER")) is None
        assert db.scalar(select(BackgroundTask).where(
            BackgroundTask.subject_id == sid, BackgroundTask.kind == "EVALUATION")) is None

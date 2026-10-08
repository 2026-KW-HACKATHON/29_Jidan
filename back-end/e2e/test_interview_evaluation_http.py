"""Sufficiency persistence through real HTTP and the background AI worker."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BackgroundTask, InterviewEvaluation, InterviewProbeBatch, InterviewSession
from e2e.interview_helpers import interview_case, scenario_server


def test_successful_evaluation_commits_empty_known_aspects_once(real_db, tmp_path):
    with scenario_server(tmp_path) as origin, interview_case(real_db, origin) as case:
        started = case.start()
        state = case.wait(started["id"], lambda body: body["phase"] == "COLLECTING")
        key = str(uuid.uuid4())
        response = case.answer(state, key=key)
        assert response.status_code == 202, response.text
        next_state = case.wait(state["id"], lambda body: body["currentIntentId"] != state["currentIntentId"])
        with Session(real_db) as db:
            [evaluation] = list(db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == state["id"])))
            assert evaluation.status == "SUCCEEDED" and evaluation.applied_at is not None
            assert evaluation.needs_follow_up is False
            assert evaluation.missing_aspects == []
            assert db.get(InterviewSession, state["id"]).revision >= next_state["revision"]
            evaluation_id = evaluation.id
        replay = case.answer(state, key=key)
        assert replay.status_code == 202 and replay.json() == response.json()
        with Session(real_db) as db:
            [evaluation] = list(db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == state["id"])))
            assert evaluation.id == evaluation_id and evaluation.missing_aspects == []
        assert case.get(state["id"])["intents"][0]["coverage"] == "COVERED"


MISSING = ["마감 순서", "청소 기준"]


def insufficient_provider():
    from app.ai.fake import FakeAiProvider

    return FakeAiProvider().on("judge_sufficiency", lambda _data: {
        "sufficient": False, "probability": 0.2, "missing_aspects": MISSING})


def failed_then_insufficient_provider():
    from app.ai.fake import FakeOutcome

    return insufficient_provider().script("judge_sufficiency", *[FakeOutcome.fail("timeout")] * 3)


def test_depth_five_preserves_missing_information_in_summary_input(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_interview_evaluation_http:insufficient_provider") as origin,
          interview_case(real_db, origin) as case):
        started = case.start()
        state = case.wait(started["id"], lambda body: body["phase"] == "COLLECTING")
        sid, intent_id = state["id"], state["currentIntentId"]
        for depth in range(6):
            question = state["questions"][0]
            assert question["depth"] == depth
            response = case.answer(state, f"depth {depth}: 아직 확인이 필요해요.")
            assert response.status_code == 202, response.text
            state = case.wait(sid, lambda body, question_id=question["id"]: body["phase"] == "COLLECTING"
                              and body["questions"][0]["id"] != question_id)
        assert state["currentIntentId"] != intent_id
        assert state["intents"][0]["coverage"] == "NEEDS_DETAIL"
        assert state["intents"][0]["depth"] == 5
        with Session(real_db) as db:
            evaluations = list(db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == sid).order_by(InterviewEvaluation.depth)))
            assert [row.depth for row in evaluations] == list(range(6))
            assert all(row.applied_at and row.missing_aspects == MISSING for row in evaluations)
            batches = list(db.scalars(select(InterviewProbeBatch).where(InterviewProbeBatch.session_id == sid)))
            assert sorted(row.depth for row in batches) == [1, 2, 3, 4, 5]
            tasks = list(db.scalars(select(BackgroundTask).where(BackgroundTask.subject_id == sid)))
            probes = [row for row in tasks if row.kind == "FOLLOWUP_GENERATION"]
            assert len(probes) == 5
            assert all(row.payload["request"]["missing_aspects"] == MISSING for row in probes)
            [summary] = [row for row in tasks if row.kind == "REVIEW_UNDERSTANDING"]
            assert summary.payload["request"]["needs_detail"] is True
            assert summary.payload["request"]["missing_aspects"] == MISSING
            assert len(summary.payload["request"]["dialogue"]) == 6
        review = case.client.get(case.url(sid, f"/intents/{intent_id}/review"))
        assert review.status_code == 200, review.text
        assert review.json()["content"]["needsDetail"] is True
        assert case.get(sid)["intents"][0]["coverage"] == "NEEDS_DETAIL"


def test_failed_evaluation_retry_reuses_answer_and_commits_missing_aspects_once(real_db, tmp_path):
    with (scenario_server(tmp_path, "e2e.test_interview_evaluation_http:failed_then_insufficient_provider") as origin,
          interview_case(real_db, origin) as case):
        started = case.start()
        state = case.wait(started["id"], lambda body: body["phase"] == "COLLECTING")
        sid = state["id"]
        response = case.answer(state, "마감 방법은 아직 확인이 필요해요.")
        assert response.status_code == 202, response.text
        failed = case.wait(sid, lambda body: body["phase"] == "ERROR", timeout=60)
        before = case.turns(sid)
        assert [row.turn_kind for row in before] == ["QUESTION", "ANSWER"]
        assert failed["lastAnsweredQuestion"] == response.json()["lastAnsweredQuestion"]
        retry = case.post(case.url(sid, "/retries"), {"expectedRevision": failed["revision"]})
        assert retry.status_code == 202, retry.text
        resumed = case.wait(sid, lambda body: body["phase"] == "COLLECTING")
        assert resumed["questions"][0]["depth"] == 1
        with Session(real_db) as db:
            tasks = list(db.scalars(select(BackgroundTask).where(
                BackgroundTask.subject_id == sid, BackgroundTask.kind == "EVALUATION")))
            assert len(tasks) == 2 and tasks[0].payload == tasks[1].payload
            rows = list(db.scalars(select(InterviewEvaluation).where(
                InterviewEvaluation.session_id == sid).order_by(InterviewEvaluation.attempt_no)))
            assert [row.status for row in rows] == ["FAILED", "SUCCEEDED"]
            assert rows[0].applied_at is None and rows[0].missing_aspects is None
            assert rows[1].applied_at is not None and rows[1].missing_aspects == MISSING
            assert rows[0].input_snapshot == rows[1].input_snapshot
            assert rows[0].evaluated_through_turn_id == rows[1].evaluated_through_turn_id == before[1].id
        after = case.turns(sid)
        assert [(row.id, row.content) for row in after[:2]] == [(row.id, row.content) for row in before]
        assert len([row for row in after if row.turn_kind == "ANSWER"]) == 1
        assert case.get(sid)["questions"] == resumed["questions"]

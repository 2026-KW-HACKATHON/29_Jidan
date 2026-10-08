"""Every manual operation that declares 503 and reserves a task answers 503 JOB_QUEUE_UNAVAILABLE
when the task INSERT is unavailable, rolls back everything (all tables, the session state) and
accepts the same Idempotency-Key once the queue is back (docs: team/reviews/manual-503-conditions.md)."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db.models import Base
from tests import media_samples as samples
from tests.test_interview_reviews import (  # noqa: F401 (fixture)
    WORK,
    answered,
    correct,
    flow,
    review,
)
from tests.test_task_enqueue_unavailable import failing_insert, unavailable


def all_counts(engine) -> dict[str, int]:
    with Session(engine) as db:
        return {table.name: db.scalar(select(func.count()).select_from(table))
                for table in Base.metadata.sorted_tables}


def upload_audio(drv) -> str:
    response = drv.api.post(f"/api/stores/{drv.store}/manual/media", headers=drv.auth.headers(str(uuid.uuid4())),
                            data={"purpose": "INTERVIEW_AUDIO"},
                            files={"file": ("a.wav", samples.wav_seconds(1), "audio/wav")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def answer(drv, fake_ai):
    sid = drv.started()
    return sid, lambda key: drv.answer(sid, key=key, revision=drv.get(sid)["revision"])


def completion(drv, fake_ai):
    sid = drv.started()
    drv.finish_all(sid, 6)
    return sid, lambda key: drv.complete(sid, key=key)


def review_correction(drv, fake_ai):
    sid = answered(drv)
    revision = review(drv, sid, WORK)["revision"]
    return sid, lambda key: correct(drv, sid, WORK, text="정정 지시", revision=revision, key=key)


def review_retry(drv, fake_ai):
    sid = answered(drv)
    fake_ai.script("revise_structure", *[FakeOutcome.raw("not json")] * 3)
    assert correct(drv, sid, WORK, text="정정 지시").status_code == 202
    drv.run()
    revision = review(drv, sid, WORK)["revision"]
    return sid, lambda key: drv.post(drv.review_url(sid, drv.intents[WORK], "retries"),
                                     {"expectedRevision": revision}, key)


def interview_retry(drv, fake_ai):
    sid = drv.started()
    fake_ai.script("judge_sufficiency", FakeOutcome.fail("refused"))
    drv.answer(sid)
    drv.run(rounds=1)
    state = drv.get(sid)
    assert state["status"] == "ERROR"
    return sid, lambda key: drv.post(drv.url(sid, "retries"), {"expectedRevision": state["revision"]}, key)


def transcription(drv, fake_ai):
    media_id = upload_audio(drv)
    return None, lambda key: drv.api.post(f"/api/stores/{drv.store}/manual/transcriptions",
                                          headers=drv.auth.headers(key), json={"mediaId": media_id})


SCENARIOS = {
    "answerManualInterviewQuestion": answer,
    "generateManualDraft": completion,
    "correctManualInterviewUnderstanding": review_correction,
    "retryManualIntentReview": review_retry,
    "retryManualInterviewProcessing": interview_retry,
    "createManualTranscription": transcription,
}


@pytest.mark.parametrize("operation", sorted(SCENARIOS))
def test_task_reservation_failure_is_503_and_rolls_back(flow, fake_ai, operation):  # noqa: F811
    sid, send = SCENARIOS[operation](flow, fake_ai)
    key = str(uuid.uuid4())
    before = all_counts(flow.engine)
    state = flow.get(sid) if sid else None
    with failing_insert(flow.engine, "background_tasks", unavailable()) as hits:
        response = send(key)
    assert (response.status_code, response.json()["code"]) == (503, "JOB_QUEUE_UNAVAILABLE"), response.text
    assert hits, "the operation reserved no task"
    assert all_counts(flow.engine) == before
    if sid:
        assert flow.get(sid) == state  # revision, phase and processing untouched
    retried = send(key)
    assert retried.status_code in (201, 202), retried.text
    assert all_counts(flow.engine)["background_tasks"] == before["background_tasks"] + 1

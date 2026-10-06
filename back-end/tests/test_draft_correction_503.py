"""Draft corrections reserve a DRAFT_CORRECTION task: an availability failure of that INSERT is
503 JOB_QUEUE_UNAVAILABLE with nothing kept, and the same key works afterwards (ddc9459)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db import utcnow
from app.db.models import ManualDraftCorrection, ManualVersion
from app.tasks import drain
from tests.test_manual_503_operations import all_counts
from tests.test_manual_corrections import correct, ctx, retry  # noqa: F401 (fixture)
from tests.test_task_enqueue_unavailable import failing_insert, unavailable


def create(ctx, fake_ai):  # noqa: F811
    return lambda key: correct(ctx, key=key)


def retry_failed(ctx, fake_ai):  # noqa: F811
    fake_ai.script("revise_structure", *[FakeOutcome.fail("timeout")] * 3)
    correction = correct(ctx).json()["id"]
    later = utcnow() + timedelta(minutes=5)
    for at in (None, later, later + timedelta(minutes=5)):
        drain(now=at) if at else drain()
    with Session(ctx.engine) as db:
        assert db.get(ManualDraftCorrection, correction).status == "ERROR"
    return lambda key: retry(ctx, correction, key=key)


SCENARIOS = {"createManualDraftCorrection": create, "retryManualDraftCorrection": retry_failed}


@pytest.mark.parametrize("operation", sorted(SCENARIOS))
def test_correction_task_reservation_failure_is_503_and_rolls_back(ctx, fake_ai, operation):  # noqa: F811
    send = SCENARIOS[operation](ctx, fake_ai)
    key = str(uuid.uuid4())
    before = all_counts(ctx.engine)
    with Session(ctx.engine) as db:
        revision = db.get(ManualVersion, ctx.draft).revision
    with failing_insert(ctx.engine, "background_tasks", unavailable()) as hits:
        response = send(key)
    assert (response.status_code, response.json()["code"]) == (503, "JOB_QUEUE_UNAVAILABLE"), response.text
    assert hits and all_counts(ctx.engine) == before
    with Session(ctx.engine) as db:
        assert db.get(ManualVersion, ctx.draft).revision == revision
    retried = send(key)
    assert retried.status_code == 202, retried.text
    assert all_counts(ctx.engine)["background_tasks"] == before["background_tasks"] + 1
    assert send(key).headers.get("Idempotent-Replayed") == "true"

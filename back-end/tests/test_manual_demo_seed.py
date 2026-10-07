"""Demo seed: the demo cafe's published manual (#122), readable by its worker and used by Q&A."""
import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import demo_seed, manual_demo_seed
from app.db import session_scope
from app.db.models import (
    ManualIssueAcknowledgement,
    ManualReviewIssue,
    ManualSection,
    ManualShift,
    ManualStep,
    ManualVersion,
    Notification,
    StoreManual,
)
from app.operator_cli import operator_cli
from app.tasks import drain
from e2e import ai_scenario
from tests.jobs_support import as_user


@pytest.fixture
def safe_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", os.getenv("DB_NAME") or "jidan_seed_test")


def counts(engine) -> dict[str, int]:
    with Session(engine) as db:
        tables = (StoreManual, ManualVersion, ManualShift, ManualSection, ManualStep, ManualReviewIssue,
                  ManualIssueAcknowledgement)
        result = {table.__tablename__: db.scalar(select(func.count()).select_from(table)) for table in tables}
        result["manual_published"] = db.scalar(select(func.count()).select_from(Notification)
                                               .where(Notification.event_type == "MANUAL_PUBLISHED"))
        return result


def test_demo_cafe_publishes_a_manual_its_worker_reads(api, db_engine, safe_env):
    ids = demo_seed.run()
    version_id = manual_demo_seed.manual_id("version:cafe:1")
    as_user(api, ids["owner"])
    state = api.get(f"/api/stores/{ids['cafe']}/manual").json()
    assert state["currentPublishedVersionId"] == version_id and state["draftVersionId"] is None

    as_user(api, ids["worker:jisu"])
    stores = api.get("/api/users/me/stores").json()["items"]
    assert [(s["store"]["id"], s["publishedVersionId"]) for s in stores] == [(ids["cafe"], version_id)]
    listing = api.get(f"/api/stores/{ids['cafe']}/manual/published")
    assert listing.status_code == 200  # the api client also checks the PublishedManualList schema
    body = listing.json()
    assert body["versionId"] == version_id and body["versionNumber"] == 1
    assert [s["name"] for s in body["shifts"]] == ["오픈조", "마감조"]
    assert [(s["title"], s["stepCount"]) for s in body["sections"]] == [
        ("포스 마감", 3), ("매장 청소", 3), ("음료 레시피", 0), ("오픈 준비", 2), ("복장 규정", 2)]
    assert [m["field"] for m in body["missingInformation"]] == ["steps"]
    pos = body["sections"][0]["id"]
    detail = api.get(f"/api/stores/{ids['cafe']}/manual/published/sections/{pos}?expectedVersionId={version_id}")
    assert detail.status_code == 200
    assert detail.json()["section"]["steps"][0]["instruction"] == "포스 화면에서 마감 정산 버튼을 눌러요."
    drinks = body["sections"][2]["id"]
    gaps = api.get(f"/api/stores/{ids['cafe']}/manual/published/sections/{drinks}").json()["missingInformation"]
    assert len(gaps) == 1 and "점주님께" in gaps[0]["description"]

    as_user(api, ids["worker:minjun"])  # favorite store, no access grant
    refused = api.get(f"/api/stores/{ids['cafe']}/manual/published")
    assert (refused.status_code, refused.json()["code"]) == (404, "STORE_NOT_FOUND")


def test_reseed_rebuilds_the_same_manual_and_reset_removes_it(db_engine, safe_env):
    demo_seed.run()
    first = counts(db_engine)
    assert first == {"store_manuals": 1, "manual_versions": 1, "manual_shifts": 2, "manual_sections": 5,
                     "manual_steps": 10, "manual_review_issues": 1, "manual_issue_acknowledgements": 1,
                     "manual_published": 1}
    with Session(db_engine) as db:
        version = db.get(ManualVersion, manual_demo_seed.manual_id("version:cafe:1"))
        assert (version.status, version.generation_status) == ("PUBLISHED", "READY")
        notification = db.scalars(select(Notification).where(Notification.event_type == "MANUAL_PUBLISHED")).one()
        assert notification.read_at is not None
    demo_seed.run()
    assert counts(db_engine) == first
    with operator_cli(), session_scope() as db:
        demo_seed.reset(db)
    assert set(counts(db_engine).values()) == {0}


def test_demo_questions_are_answered_from_the_seeded_manual(api, db_engine, safe_env, fake_ai):
    """With the E2E fake answerer, a POS question cites "포스 마감"; anything else asks the owner."""
    fake_ai.on("answer_question", ai_scenario.answer)
    ids = demo_seed.run()
    worker = as_user(api, ids["worker:jisu"])
    base = f"/api/stores/{ids['cafe']}/manual/qa/conversations"
    conversation = api.post(base, json={}, headers=worker.headers(str(uuid.uuid4()))).json()["id"]

    def ask(text: str) -> dict:
        body = {"kind": "TEXT", "text": text, "transcriptionId": None, "imageMediaIds": []}
        question = api.post(f"{base}/{conversation}/questions", json=body, headers=worker.headers(str(uuid.uuid4())))
        assert question.status_code == 202
        drain()
        return api.get(f"{base}/{conversation}/questions/{question.json()['id']}").json()

    grounded = ask("포스 마감은 어떻게 해요?")
    assert grounded["status"] == "READY" and grounded["answer"]["outcome"] == "ANSWERED"
    assert {(c["sectionTitle"], c["versionId"]) for c in grounded["answer"]["citations"]} == {
        ("포스 마감", manual_demo_seed.manual_id("version:cafe:1"))}
    other = ask("주차는 어디에 해요?")
    assert other["answer"]["outcome"] == "NEEDS_OWNER" and other["answer"]["citations"] == []

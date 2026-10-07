"""Interview tasks hand the owner's own words to the writing operations as evidence.

Summary evidence is frozen in the task payload, draft evidence in the generation snapshot, and
correction evidence is rebuilt identically from immutable turns; all of it comes from the same
session (store) only.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db.models import BackgroundTask, InterviewTurn, ManualVersion
from app.media.storage import LocalMediaStorage, set_media_storage
from tests.interview_factories import raw_section, raw_summary
from tests.test_interview_api import build_ctx, rows

WORK, COMMON = 0, 1


@pytest.fixture
def drv(api, db_engine, tmp_path):
    set_media_storage(LocalMediaStorage(tmp_path / "media"))
    yield build_ctx(api, db_engine)
    set_media_storage(None)


def answers(drv, sid):
    return {t.id: t for t in rows(drv, InterviewTurn, InterviewTurn.session_id == sid,
                                  InterviewTurn.turn_kind.in_(("ANSWER", "CORRECTION")))}


def review_url(drv, sid, index, action):
    return drv.review_url(sid, drv.intents[index], action)


def test_summary_evidence_is_the_sessions_own_answers_frozen_in_the_payload(drv, fake_ai):
    main_store, drv.store = drv.store, drv.second  # another store's interview must never leak in
    drv.answer_and_run(drv.started(), "다른 가게 비밀 레시피예요.")
    drv.store = main_store
    sid = drv.started()
    drv.answer_and_run(sid, "오픈조는 9시에 시작해요. 마감조는 15시에 시작해요.")

    [call] = [c for c in fake_ai.calls_for("summarize_intent") if c.data["intent"]["key"] == "WORK_STRUCTURE"
              and any("오픈조" in e["text"] for e in c.data["evidence"])]
    [answer] = [t for t in answers(drv, sid).values() if t.content.startswith("오픈조")]
    assert [(e["id"], e["text"]) for e in call.data["evidence"]] == [
        (f"{answer.id}#1", "오픈조는 9시에 시작해요."), (f"{answer.id}#2", "마감조는 15시에 시작해요.")]
    assert call.data["evidence"][0]["question"] and call.data["evidence"][1]["question"] is None
    assert "dialogue" not in call.data and not any("비밀" in e["text"] for e in call.data["evidence"])
    with Session(drv.engine) as db:
        payloads = [t.payload for t in db.scalars(select(BackgroundTask).where(
            BackgroundTask.kind == "REVIEW_UNDERSTANDING", BackgroundTask.subject_id == sid))]
    assert [p["request"]["evidence"] for p in payloads] == [call.data["evidence"]]
    # The default fake cites the answer, so the review keeps it as a step.
    content = drv.review(sid, drv.intents[WORK]).json()["content"]
    assert [s["instruction"] for s in content["sections"][0]["steps"]] == [answer.content]


def test_uncited_summary_step_becomes_missing_information(drv, fake_ai):
    unsupported = raw_summary("요약이에요.", sections=[raw_section("new-1", "마감", steps=[("new-2", "금고를 잠가요.")])])
    unsupported["structure"]["sections"][0]["steps"][0]["evidence_ids"] = []
    fake_ai.script("summarize_intent", FakeOutcome.ok(unsupported))
    sid = drv.started()
    drv.answer_and_run(sid, "네, 그렇게 해요.")
    content = drv.review(sid, drv.intents[WORK]).json()["content"]
    [section] = content["sections"]
    assert section["steps"] == []
    [entry] = content["missingInformation"]
    assert (entry["target"], entry["targetId"], entry["field"]) == ("SECTION", section["id"], "steps")


def test_correction_evidence_includes_the_correction_and_is_identical_on_retry(drv, fake_ai):
    sid = drv.started()
    drv.answer_and_run(sid, "오픈조는 9시에 시작해요.")
    drv.answer_and_run(sid, "손님께 인사해요.")  # a later answer of another intent
    revision = drv.review(sid, drv.intents[WORK]).json()["revision"]
    fake_ai.script("revise_structure", FakeOutcome.fail("timeout"))
    accepted = drv.post(review_url(drv, sid, WORK, "corrections"),
                        {"expectedRevision": revision, "input": {"method": "TEXT", "text": "오픈조는 8시에 시작해요."}})
    assert accepted.status_code == 202, accepted.text
    for _ in range(3):
        drv.run()
    first, second = fake_ai.calls_for("revise_structure")[:2]
    assert first.data["evidence"] == second.data["evidence"]
    turns = answers(drv, sid)
    [correction] = [t for t in turns.values() if t.turn_kind == "CORRECTION"]
    ids = [e["id"] for e in first.data["evidence"]]
    assert f"{correction.id}#1" in ids
    assert all(e["intent_key"] == "WORK_STRUCTURE" or "인사" in e["text"] for e in first.data["evidence"])
    assert {i.split("#")[0] for i in ids} <= set(turns)


def test_draft_evidence_is_frozen_in_the_generation_snapshot(drv, fake_ai):
    sid = drv.started()
    for text in ("오픈조와 마감조가 있어요.", "손님께 인사해요.", "오픈조는 문을 열어요.", "음식물은 버려요.",
                 "커피 머신을 닦아요.", "정전되면 사장님께 전화해요."):
        drv.answer_and_run(sid, text)
    response = drv.complete(sid)
    assert response.status_code == 202, response.text
    [version] = rows(drv, ManualVersion)
    frozen = version.generation_input_snapshot["evidence"]
    assert frozen and {e["id"].split("#")[0] for e in frozen} <= set(answers(drv, sid))
    drv.run()
    [call] = fake_ai.calls_for("compose_draft")
    assert call.data["evidence"] == frozen

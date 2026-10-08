"""Guidance cards through real HTTP, production task handlers and independent MySQL reads.

Discovered by the existing `pytest e2e` runner. Only owner/auth/approved-store setup and
the external AI response are fixtures; no handlers/dependencies or card arrays are replaced.
Production tasks run in a separate process with the real runner's manual mode. A conditional
test-DB trigger postpones only this store's tasks so another harness server cannot claim them;
the manual runner advances its clock. This makes PROCESSING/error/retry assertions deterministic.
"""

import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    InterviewIntentReview,
    InterviewSession,
    InterviewSessionIntent,
    InterviewTurn,
)
from e2e.review_helpers import local_env, server
from tests.api_contract import validate_response
from tests.factories import NOW, make_store, make_user
from tests.interview_factories import ensure_question_set

ANSWERS = {
    "WORK_STRUCTURE": "오전조는 09:00부터 15:00까지 근무해요.",
    "COMMON_TASKS": "포스 마감은 정산 버튼을 눌러요. 재고 정리는 선반에 물건을 놓아요.",
    "SHIFT_TASKS": "오전조는 매장 문을 열고 조명을 켜요.",
    "RULES": "앞치마를 착용하고 손을 씻어요.",
    "EQUIPMENT": "커피 머신을 닦아요.",
    "EXCEPTIONS": "손님 불만이 있으면 점주에게 전화해요.",
}


@contextmanager
def postpone_tasks(engine, store_id):
    # Fixture scheduling only, scoped by the newly created store; never touches other tasks.
    trigger = "guidance_tasks_" + uuid.uuid4().hex
    with engine.begin() as db:
        db.exec_driver_sql(f"""
            CREATE TRIGGER {trigger} BEFORE INSERT ON background_tasks FOR EACH ROW
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM interview_sessions s
                    JOIN manual_versions v ON v.id = s.manual_version_id
                    JOIN store_manuals m ON m.id = v.manual_id
                    WHERE s.id = NEW.subject_id AND m.store_id = '{store_id}'
                ) THEN
                    SET NEW.available_at = DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 1 DAY);
                END IF;
            END
        """)
    try:
        yield
    finally:
        with engine.begin() as db:
            db.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")


def tasks(env, *kinds, fail=False):
    task_env = {**env, "GUIDANCE_E2E_FAIL_EVALUATION": "true" if fail else "false"}
    result = subprocess.run([sys.executable, "-m", "e2e.test_guidance_cards_http", *kinds],
                            env=task_env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


class CardsHttp:
    def __init__(self, client, engine, env, store_id):
        self.client, self.engine, self.env = client, engine, env
        self.root = f"/api/stores/{store_id}/manual/interviews"

    def call(self, method, tail="", *, body=None, key=None, expect=200):
        headers = {}
        if method != "GET":
            csrf = self.client.get("/api/auth/csrf")
            assert csrf.status_code == 200, csrf.text
            headers = {"Origin": str(self.client.base_url).rstrip("/"),
                       "X-CSRF-Token": csrf.json()["csrfToken"],
                       "Idempotency-Key": key or str(uuid.uuid4())}
        response = self.client.request(method, self.root + tail, json=body, headers=headers)
        assert response.status_code == expect, response.text
        validate_response(response)
        return response.json()

    def get(self, sid):
        return self.call("GET", f"/{sid}")

    def question(self, sid):
        state = self.get(sid)
        [question] = state["questions"]
        assert state["phase"] == "COLLECTING" and state["lastAnsweredQuestion"] is None
        cards = question["guidanceCards"]
        assert 1 <= len(cards) <= 5
        assert len({c["id"] for c in cards}) == len(cards)
        assert sum(c["type"] == "PROGRESS_CHECKLIST" for c in cards) == 1
        progress = checklist(question)
        assert len(progress["items"]) == len(state["intents"])
        for item, intent in zip(progress["items"], state["intents"], strict=True):
            expected = {"COVERED": "COMPLETED", "NEEDS_DETAIL": "NEEDS_DETAIL"}.get(
                intent["coverage"], "CURRENT" if intent["id"] == question["intentId"] else "PENDING")
            assert item["status"] == expected
        with Session(self.engine) as db:
            session = db.get(InterviewSession, sid)
            row = db.get(InterviewTurn, question["id"])
            assert session.revision == state["revision"] and session.processing_kind is None
            assert (row.content, row.guidance, row.guidance_cards) == (
                question["text"], question["guidance"], cards)
            assert len(list(db.scalars(select(InterviewTurn).where(
                InterviewTurn.session_id == sid, InterviewTurn.turn_kind == "QUESTION",
                InterviewTurn.id == question["id"])))) == 1
        assert self.get(sid) == state
        return state, question

    def answer(self, sid, key):
        state, question = self.question(sid)
        intent = next(i for i in state["intents"] if i["id"] == question["intentId"])
        body = {"expectedRevision": state["revision"], "questionId": question["id"],
                "input": {"method": "TEXT", "text": ANSWERS[intent["key"]]}}
        accepted = self.call("POST", f"/{sid}/answers", body=body, key=key, expect=202)
        assert accepted["revision"] == state["revision"] + 1
        assert accepted["questions"] == [] and accepted["phase"] == "PROCESSING"
        assert accepted["lastAnsweredQuestion"] == {**question, "answered": True}
        assert self.get(sid) == accepted
        with Session(self.engine) as db:
            session = db.get(InterviewSession, sid)
            task = db.get(BackgroundTask, session.processing_task_id)
            assert session.revision == accepted["revision"] and task.status == "QUEUED"
            assert task.kind == "EVALUATION" and task.input_revision == session.revision
            assert task.payload["questionTurnId"] == question["id"]
            [answer] = list(db.scalars(select(InterviewTurn).where(
                InterviewTurn.reply_to_question_turn_id == question["id"])))
            assert answer.content == ANSWERS[intent["key"]]
            assert db.get(InterviewTurn, question["id"]).guidance_cards == question["guidanceCards"]
        assert self.call("POST", f"/{sid}/answers", body=body, key=key, expect=202) == accepted
        rejected = self.call("POST", f"/{sid}/answers", body={**body, "expectedRevision": accepted["revision"]},
                             expect=409)
        assert rejected["code"] == "QUESTION_ALREADY_ANSWERED"
        assert self.get(sid) == accepted
        with Session(self.engine) as db:
            assert db.get(InterviewSession, sid).revision == accepted["revision"]
            assert len(list(db.scalars(select(InterviewTurn).where(
                InterviewTurn.reply_to_question_turn_id == question["id"])))) == 1
            evaluation_tasks = list(db.scalars(select(BackgroundTask).where(
                BackgroundTask.subject_id == sid, BackgroundTask.kind == "EVALUATION")))
            assert sum(t.payload["questionTurnId"] == question["id"] for t in evaluation_tasks) == 1
            assert db.get(InterviewTurn, question["id"]).guidance_cards == question["guidanceCards"]
        return accepted, question

    def advance(self, sid, *, summarize=True):
        before = self.get(sid)
        tasks(self.env, "EVALUATION")
        evaluated = self.get(sid)
        assert evaluated["revision"] == before["revision"] + 1
        assert evaluated["lastAnsweredQuestion"] is None and evaluated["questions"] == []
        if summarize:
            tasks(self.env, "REVIEW_UNDERSTANDING")
        tasks(self.env, "INITIAL_QUESTION", "FOLLOWUP_GENERATION")
        after = self.get(sid)
        assert after["revision"] == evaluated["revision"] + 1
        return self.question(sid)


def checklist(question):
    return next(c for c in question["guidanceCards"] if c["type"] == "PROGRESS_CHECKLIST")


def photos(question):
    return [c for c in question["guidanceCards"] if c["type"] == "PHOTO_SUGGESTIONS"]


@pytest.mark.parametrize("guidance_enabled", [True, False], ids=["enabled", "disabled"])
def test_generated_cards_storage_retry_targets_and_revision(real_db, tmp_path, guidance_enabled):
    env, origin, port, _password = local_env()
    env.update(INTERVIEW_GUIDANCE_RESPONSES="true" if guidance_enabled else "false",
               TASK_RUNNER_MODE="manual", AI_PROVIDER="fake",
               MEDIA_ROOT=str(tmp_path / "media"))
    with Session(real_db) as db:
        ensure_question_set(db)
        suffix = uuid.uuid4().hex
        owner = make_user(db, "OWNER", google_sub=f"guidance-{suffix}", google_email=f"{suffix}@e2e.test")
        store = make_store(db, owner, approval_status="APPROVED", approved_at=NOW,
                           business_registration_number=f"{uuid.uuid4().int % 10**10:010d}")
        issued = auth.create_session(owner.id, db=db)
        db.commit()
        store_id = store.id
    with (postpone_tasks(real_db, store_id), server(env, port, tmp_path) as (process, log),
          httpx.Client(base_url=origin, timeout=10, trust_env=False) as client):
        client.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert process.poll() is None, log.read()
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("guidance HTTP server did not become healthy")
        api = CardsHttp(client, real_db, env, store_id)
        started = api.call("POST", body={}, expect=201)
        sid = started["id"]
        env["GUIDANCE_E2E_SESSION_ID"] = sid
        assert started["questions"] == []
        if guidance_enabled:
            assert started["lastAnsweredQuestion"] is None
        tasks(env, "INITIAL_QUESTION")
        if not guidance_enabled:
            state = api.get(sid)
            [asked] = state["questions"]
            assert "lastAnsweredQuestion" not in state
            assert not {"guidance", "guidanceCards"} & set(asked)
            with Session(real_db) as db:
                stored = db.get(InterviewTurn, asked["id"])
                assert [c["type"] for c in stored.guidance_cards] == [
                    "LIST", "PROGRESS_CHECKLIST", "PHOTO_SUGGESTIONS"]
                saved_cards = stored.guidance_cards
                assert [i["status"] for i in checklist({"guidanceCards": saved_cards})["items"]] == [
                    "CURRENT"] + ["PENDING"] * 5
            accepted = api.call("POST", f"/{sid}/answers", body={"expectedRevision": state["revision"],
                                "questionId": asked["id"], "input": {"method": "TEXT", "text": ANSWERS["WORK_STRUCTURE"]}},
                                expect=202)
            assert accepted["questions"] == [] and "lastAnsweredQuestion" not in accepted
            assert api.get(sid) == accepted
            with Session(real_db) as db:
                assert db.get(InterviewTurn, asked["id"]).guidance_cards == saved_cards
            tasks(env, "EVALUATION", "REVIEW_UNDERSTANDING", "INITIAL_QUESTION")
            common_checklist = common_photos = None
            for depth in range(3):
                current = api.get(sid)
                [asked] = current["questions"]
                assert "lastAnsweredQuestion" not in current
                assert not {"guidance", "guidanceCards"} & set(asked)
                assert asked["depth"] == depth and asked["kind"] == ("BASE" if depth == 0 else "PROBE")
                with Session(real_db) as db:
                    stored = db.get(InterviewTurn, asked["id"])
                    saved_cards = stored.guidance_cards
                    assert stored.content == asked["text"]
                    saved_question = {"guidanceCards": saved_cards}
                    assert [i["status"] for i in checklist(saved_question)["items"]] == [
                        "COMPLETED", "CURRENT"] + ["PENDING"] * 4
                    if depth == 0:
                        common_checklist, common_photos = checklist(saved_question), photos(saved_question)
                    else:
                        assert "포스 마감" in asked["text"] and "재고 정리" not in asked["text"]
                        assert checklist(saved_question) == common_checklist
                        assert photos(saved_question) == common_photos
                    assert any(c["type"] == "LIST" for c in saved_cards) == (depth < 2)
                assert api.get(sid) == current
                if depth == 2:
                    break
                answer_key = str(uuid.uuid4())
                answer_body = {"expectedRevision": current["revision"], "questionId": asked["id"],
                               "input": {"method": "TEXT", "text": ANSWERS["COMMON_TASKS"]}}
                accepted = api.call("POST", f"/{sid}/answers", body=answer_body, key=answer_key, expect=202)
                assert accepted["questions"] == [] and "lastAnsweredQuestion" not in accepted
                assert api.call("POST", f"/{sid}/answers", body=answer_body, key=answer_key, expect=202) == accepted
                assert api.get(sid) == accepted
                with Session(real_db) as db:
                    assert db.get(InterviewTurn, asked["id"]).guidance_cards == saved_cards
                    assert len(list(db.scalars(select(InterviewTurn).where(
                        InterviewTurn.reply_to_question_turn_id == asked["id"])))) == 1
                tasks(env, "EVALUATION", "REVIEW_UNDERSTANDING", "FOLLOWUP_GENERATION")
            return
        state, first = api.question(sid)
        assert [c["type"] for c in first["guidanceCards"]] == ["LIST", "PROGRESS_CHECKLIST", "PHOTO_SUGGESTIONS"]
        assert photos(first)[0]["attachmentTarget"] is None
        progress_ids = [i["id"] for i in checklist(first)["items"]]
        assert [i["status"] for i in checklist(first)["items"]] == ["CURRENT"] + ["PENDING"] * 5
        accepted, _question = api.answer(sid, str(uuid.uuid4()))
        tasks(env, "EVALUATION", fail=True)
        failed = api.get(sid)
        assert failed["phase"] == "ERROR" and failed["lastAnsweredQuestion"] == accepted["lastAnsweredQuestion"]
        with Session(real_db) as db:
            session = db.get(InterviewSession, sid)
            assert session.revision == failed["revision"] == accepted["revision"] + 1
            assert db.get(BackgroundTask, session.processing_task_id).status == "FAILED"
        retry_key = str(uuid.uuid4())
        retried = api.call("POST", f"/{sid}/retries", body={"expectedRevision": failed["revision"]},
                           key=retry_key, expect=202)
        assert retried["lastAnsweredQuestion"] == accepted["lastAnsweredQuestion"]
        assert api.call("POST", f"/{sid}/retries", body={"expectedRevision": failed["revision"]},
                        key=retry_key, expect=202) == retried
        assert api.get(sid) == retried
        with Session(real_db) as db:
            session = db.get(InterviewSession, sid)
            retry_task = db.get(BackgroundTask, session.processing_task_id)
            assert retry_task.attempt == 2 and retry_task.status == "QUEUED"
            assert retry_task.payload["questionTurnId"] == first["id"]
        state, common = api.advance(sid)
        assert checklist(common)["id"] == checklist(first)["id"]
        assert [i["id"] for i in checklist(common)["items"]] == progress_ids
        assert checklist(common)["items"][0]["status"] == "COMPLETED"
        structure_photo = photos(common)[0]
        assert structure_photo["id"] == photos(first)[0]["id"]
        assert structure_photo["items"][0]["id"] == photos(first)[0]["items"][0]["id"]
        assert structure_photo["attachmentTarget"] == {
            "intentId": first["intentId"], "target": "WORK_STRUCTURE", "sectionId": None}
        api.answer(sid, str(uuid.uuid4()))
        _state, generated_probe = api.advance(sid)
        assert generated_probe["kind"] == "PROBE"
        assert "포스 마감" in generated_probe["text"] and "재고 정리" not in generated_probe["text"]
        assert checklist(generated_probe) == checklist(common)
        api.answer(sid, str(uuid.uuid4()))
        _state, fallback = api.advance(sid)
        assert fallback["depth"] == 2 and not any(c["type"] == "LIST" for c in fallback["guidanceCards"])
        assert "포스 마감" in fallback["text"] and "재고 정리" not in fallback["text"]
        assert checklist(fallback) == checklist(generated_probe) == checklist(common)
        assert photos(fallback) == photos(generated_probe) == photos(common)
        api.answer(sid, str(uuid.uuid4()))
        state, shift = api.advance(sid)
        targets = photos(shift)
        assert len(targets) == 3 and len(shift["guidanceCards"]) == 5
        assert len({(c["attachmentTarget"]["intentId"], c["attachmentTarget"]["sectionId"])
                    for c in targets}) == 3
        common_id = common["intentId"]
        review_tail = f"/{sid}/intents/{common_id}/review"
        review = api.call("GET", review_tail)
        section_ids = [s["id"] for s in review["content"]["sections"]]
        section_targets = [c for c in targets if c["attachmentTarget"]["intentId"] == common_id]
        assert [c["attachmentTarget"]["sectionId"] for c in section_targets] == section_ids
        assert [c["items"][0]["label"] for c in section_targets] == [s["title"] for s in review["content"]["sections"]]
        with Session(real_db) as db:
            assert db.get(InterviewIntentReview, (sid, common_id)).ready_content == review["content"]
        # Actual photos API stage restriction and invalid/foreign/stale references preserve DB.
        for tail, body, expected, error in (
            (review_tail, {"target": "WORK_STRUCTURE", "sectionId": None}, 422, "VALIDATION_ERROR"),
            (review_tail, {"target": "SECTION", "sectionId": str(uuid.uuid4())}, 422, "VALIDATION_ERROR"),
            (review_tail, {"target": "SECTION", "sectionId": section_ids[0],
                           "expectedRevision": review["revision"] - 1}, 409, "REVISION_CONFLICT"),
            (review_tail.replace(sid, str(uuid.uuid4())), {"target": "SECTION", "sectionId": section_ids[0]},
             404, "MANUAL_RESOURCE_NOT_FOUND"),
        ):
            response = api.call("PUT", tail + "/photos", body={"expectedRevision": review["revision"],
                               "photos": [], **body}, expect=expected)
            assert response["code"] == error
            assert api.call("GET", review_tail) == review
            with Session(real_db) as db:
                stored = db.get(InterviewIntentReview, (sid, common_id))
                assert stored.revision == review["revision"] and stored.ready_content == review["content"]
        valid = api.call("PUT", review_tail + "/photos", body={"expectedRevision": review["revision"],
                         "target": "SECTION", "sectionId": section_ids[0], "photos": []})
        assert valid == review  # valid unchanged photos is a no-op
        correcting = api.call("POST", review_tail + "/corrections", body={
            "expectedRevision": review["revision"], "input": {"method": "TEXT", "text": "재고 정리는 삭제해요."}},
            expect=202)
        assert correcting["status"] == "PROCESSING" and correcting["content"] == review["content"]
        notready = api.call("PUT", review_tail + "/photos", body={"expectedRevision": correcting["revision"],
                            "target": "SECTION", "sectionId": section_ids[0], "photos": []}, expect=409)
        assert notready["code"] == "REVIEW_PROCESSING" and api.call("GET", review_tail) == correcting
        assert api.get(sid)["questions"][0] == shift  # existing stored targets are immutable
        api.answer(sid, str(uuid.uuid4()))
        _state, during_correction = api.advance(sid)
        assert len(photos(during_correction)) == 1  # retained content is not a READY target
        assert photos(during_correction)[0]["id"] == structure_photo["id"]
        tasks(env, "REVIEW_CORRECTION")
        corrected = api.call("GET", review_tail)
        assert corrected["status"] == "READY" and len(corrected["content"]["sections"]) == 1
        stale = api.call("PUT", review_tail + "/photos", body={"expectedRevision": corrected["revision"],
                         "target": "SECTION", "sectionId": section_ids[1], "photos": []}, expect=422)
        assert stale["code"] == "VALIDATION_ERROR"
        assert api.call("GET", review_tail) == corrected
        with Session(real_db) as db:
            assert db.get(InterviewIntentReview, (sid, common_id)).ready_content == corrected["content"]
        api.answer(sid, str(uuid.uuid4()))
        _state, after_correction = api.advance(sid)
        assert len(photos(after_correction)) == 2
        common_photo = next(c for c in photos(after_correction) if c["attachmentTarget"]["intentId"] == common_id)
        assert common_photo["id"] == section_targets[0]["id"]
        assert common_photo["attachmentTarget"]["sectionId"] == section_ids[0]
        # The equipment depth limit remains NEEDS_DETAIL when the next question is stored.
        equipment_photos = None
        for _ in range(12):
            current = api.get(sid)
            if next(i for i in current["intents"] if i["id"] == current["currentIntentId"])["key"] == "EXCEPTIONS":
                break
            api.answer(sid, str(uuid.uuid4()))
            state, latest = api.advance(sid)
            if next(i for i in state["intents"] if i["id"] == latest["intentId"])["key"] == "EQUIPMENT":
                rules_id = next(i["id"] for i in state["intents"] if i["key"] == "RULES")
                shift_id = shift["intentId"]
                prioritized = photos(latest)
                assert [c["attachmentTarget"]["intentId"] for c in prioritized] == [rules_id, rules_id, shift_id]
                assert len(latest["guidanceCards"]) == 5
                with Session(real_db) as db:
                    ready_reviews = list(db.scalars(select(InterviewIntentReview).where(
                        InterviewIntentReview.session_id == sid, InterviewIntentReview.status == "READY")))
                    assert sum(len(r.ready_content["sections"]) for r in ready_reviews) >= 4
                    rules_sections = db.get(InterviewIntentReview, (sid, rules_id)).ready_content["sections"]
                    assert [c["attachmentTarget"]["sectionId"] for c in prioritized[:2]] == [s["id"] for s in rules_sections]
                if equipment_photos is not None:
                    assert prioritized == equipment_photos
                equipment_photos = prioritized
        else:
            pytest.fail("equipment never reached NEEDS_DETAIL")
        equipment = next(i for i in state["intents"] if i["key"] == "EQUIPMENT")
        assert equipment["coverage"] == "NEEDS_DETAIL"
        assert equipment_photos  # the >=4 READY section regression actually ran
        assert checklist(latest)["items"][4]["status"] == "NEEDS_DETAIL"
        assert [i["id"] for i in checklist(latest)["items"]] == progress_ids
        with Session(real_db) as db:
            progress = db.get(InterviewSessionIntent, (sid, equipment["id"]))
            assert progress.coverage_status == "NEEDS_DETAIL" and progress.depth == 5
            assert progress.finished_at is not None


def run_fixture_tasks():
    """Separate production runner process; AI is the sole replaced external boundary."""
    import app.tasks.handlers  # noqa: F401
    from app.ai import set_ai_provider
    from app.ai.contracts import StructureSnapshot
    from app.ai.errors import AiError, AiErrorCode
    from app.ai.fake import FakeAiProvider, FakeOutcome, structure_to_raw
    from app.db import get_engine
    from app.tasks import drain

    def question(data):
        if data["intent"]["key"] == "COMMON_TASKS" and data["depth"] == 2:
            raise AiError(AiErrorCode.REFUSED)
        return {"question": data["intent"]["base_question"] if data["kind"] == "BASE"
                else data["target_aspect"] + "에 대해 알려주세요.",
                "guidance": "말씀하신 내용을 알려주세요.",
                "examples": [{"label": "업무 설명", "description": None}]}

    def judge(data):
        key, depth = data["intent"]["key"], data["dialogue"][-1]["depth"]
        aspects = (["포스 마감", "재고 정리"] if key == "COMMON_TASKS" and depth < 2 else
                   ["작업 순서"] if (key == "SHIFT_TASKS" and depth < 3) or key == "EQUIPMENT" else [])
        return {"sufficient": not aspects, "probability": 0.2 if aspects else 0.9, "missing_aspects": aspects}

    def summarize(data):
        key = data["intent"]["key"]
        if key == "WORK_STRUCTURE":
            shifts = [{"ref": "new-1", "name": "오전조", "start_time": "09:00", "end_time": "15:00",
                       "ends_next_day": False}]
            sections = []
        else:
            shifts = []
            entries = {
                "COMMON_TASKS": [("포스 마감", "정산 버튼을 눌러요."), ("재고 정리", "선반에 물건을 놓아요.")],
                "SHIFT_TASKS": [("문 열기", "매장 문을 열어요."), ("조명 켜기", "조명을 켜요.")],
                "RULES": [("앞치마 착용", "앞치마를 착용해요."), ("손 씻기", "손을 씻어요.")],
            }.get(key, [(key, ANSWERS[key])])
            sections = [{"ref": f"new-{index * 2 + 1}",
                         "category": "SHIFT_TASK" if key == "SHIFT_TASKS" else "COMMON_TASK",
                         "shift_ref": data["available_shifts"][0]["id"] if key == "SHIFT_TASKS" else None,
                         "title": title, "steps": [{"ref": f"new-{index * 2 + 2}", "instruction": instruction,
                                                    "checklist_item": False}]}
                        for index, (title, instruction) in enumerate(entries)]
        return {"summary": ANSWERS[key], "structure": {"shifts": shifts, "sections": sections,
                                                        "missing_information": []}}

    def revise(data):
        raw = structure_to_raw(StructureSnapshot.model_validate(data["current"]))
        raw["sections"] = raw["sections"][:1]
        return {"outcome": "APPLIED", "summary": "포스 마감은 정산 버튼을 눌러요.", "structure": raw}

    provider = (FakeAiProvider().on("generate_question", question).on("judge_sufficiency", judge)
                .on("summarize_intent", summarize).on("revise_structure", revise))
    if os.getenv("GUIDANCE_E2E_FAIL_EVALUATION") == "true":
        provider.script("judge_sufficiency", FakeOutcome.fail("refused"))
    set_ai_provider(provider)
    assert os.getenv("APP_ENV") == "local" and os.getenv("DB_NAME") == "jidan_e2e_test"
    session_id = os.environ["GUIDANCE_E2E_SESSION_ID"]
    # Production claim uses SKIP LOCKED: keep this manual clock from claiming another scenario's
    # queued tasks, including leftovers from a failed test. No row values are changed.
    with Session(get_engine()) as db:
        foreign_ids = list(db.scalars(select(BackgroundTask.id).where(
            BackgroundTask.subject_id != session_id, BackgroundTask.status == "QUEUED",
        )))
        for task_id in foreign_ids:
            # Exact primary-key locks avoid MySQL next-key locks on this session's eligible rows.
            db.execute(select(BackgroundTask.id).where(BackgroundTask.id == task_id).with_for_update()).all()
        runs = drain(kinds=tuple(sys.argv[1:]), now=utcnow() + timedelta(days=2))
        assert all(run.error_code in (None, "REFUSED") for run in runs), runs
        db.rollback()


if __name__ == "__main__":
    run_fixture_tasks()

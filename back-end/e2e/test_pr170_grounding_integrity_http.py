"""PR #170: actual HTTP, task application, independent MySQL commits and API re-reads.

The standard e2e runner discovers this test. A separate disposable *_test schema prevents
the main E2E server's task workers from consuming this scenario's AI tasks. Only model output
is scripted at the provider edge; app.main, handlers, dependencies and task writes are real.
Google identity/store approval are seeded; paid AI, deployment and browser flows are excluded.
"""
import argparse
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

import httpx
import pytest
from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app import auth
from app.ai.contracts import StructureSnapshot
from app.ai.fake import FakeAiProvider, structure_to_raw
from app.db.models import (
    BackgroundTask,
    InterviewIntentReview,
    InterviewTurn,
    ManualDraftCorrection,
    ManualPhotoAttachment,
    ManualStep,
    ManualVersion,
)
from e2e.review_helpers import local_env
from tests import media_samples
from tests.conftest import alembic_config
from tests.factories import NOW, make_store, make_user

MIXED = (
    "상황에 맞게 처리해요. 먼저 전원을 꺼요.",
    "전원을 껐다가 상황에 맞게 처리해요.",
    "정해진 규칙은 없어요. 점주에게 전화해요.",
    '"상황에 맞게 처리해요"라는 안내를 읽어요.',
)
INVENTED = "모든 주문에 50% 할인해요."
CITED_CHANGE = "첫 단계는 영수증을 확인해요."


def raw_step(ref, instruction, evidence_ids=()):
    return {"ref": ref, "instruction": instruction, "checklist_item": False,
            "evidence_ids": list(evidence_ids)}


def scripted_summary(data):
    ids = [data["evidence"][0]["id"]]
    structure = {"shifts": [], "sections": [], "missing_information": []}
    if data["intent"]["key"] == "WORK_STRUCTURE":
        structure["shifts"] = [{"ref": "new-1", "name": "오픈조", "start_time": "09:00",
                                "end_time": "15:00", "ends_next_day": False, "evidence_ids": ids}]
    if data["intent"]["key"] == "COMMON_TASKS":
        structure["sections"] = [
            {"ref": "new-1", "category": "COMMON_TASK", "shift_ref": None, "title": "대응",
             "steps": [*[raw_step(f"new-{i + 2}", instruction, ids) for i, instruction in enumerate(MIXED)],
                       raw_step("new-6", "알아서 해요.", ids)]},
            {"ref": "new-7", "category": "RULE", "shift_ref": None, "title": "규칙",
             "steps": [raw_step("new-8", "따로 정해 둔 매장 규칙은 없어요.", ids)]},
            {"ref": "new-9", "category": "COMMON_TASK", "shift_ref": None, "title": "미정",
             "steps": [raw_step("new-10", "상황에 맞게 처리해요.", ids)]},
        ]
    return {"summary": "점주 답변을 정리했어요.", "structure": structure}


def scripted_draft(data):
    raw = {"shifts": [], "sections": [], "missing_information": []}
    for review in data["reviews"]:
        part = structure_to_raw(StructureSnapshot.model_validate(review["structure"]))
        for key, items in raw.items():
            items.extend(part[key])
    steps = raw["sections"][0]["steps"]
    for step in steps:
        step["evidence_ids"] = []
    steps[0]["instruction"] = INVENTED  # existing ID, no citation: reviewed original must survive
    steps.extend([raw_step("new-1", "환불 금액을 두 배로 지급해요."),
                  raw_step("new-2", "알아서 해요.", [data["evidence"][0]["id"]])])
    return {"structure": raw}


UNCITED = "근거 없는 변경 검사"
revision_calls: dict[tuple[str, bool], int] = {}


def scripted_revision(data):
    raw = structure_to_raw(StructureSnapshot.model_validate(data["current"]))
    step = raw["sections"][0]["steps"][0]
    instruction = data["instruction"]
    review = data["summary"] is not None  # a review correction must return its summary
    key = (instruction, review)
    revision_calls[key] = revision_calls.get(key, 0) + 1
    if instruction == UNCITED and revision_calls[key] > 3:
        # The owner's retry after three failed attempts: the model now leaves the content alone.
        return {"outcome": "APPLIED", "summary": data["summary"], "structure": raw}
    summary = "점주 정정을 반영했어요." if review else None
    if instruction == "빈 응답 검사":
        step["instruction"] = ""
    elif instruction == "잘못된 참조 검사":
        step.update(instruction=INVENTED, evidence_ids=["invented-evidence"])
    elif instruction == CITED_CHANGE:
        step.update(instruction=CITED_CHANGE, evidence_ids=[data["evidence"][0]["id"]])
    elif instruction == "모호한 변경 검사":
        step.update(instruction="상황에 맞게 처리해요.", evidence_ids=[])
    elif instruction == "인용된 모호한 변경 검사":
        cited = next(chunk["id"] for chunk in data["evidence"] if chunk["text"] == instruction)
        step.update(instruction="상황에 맞게 알아서 처리해요.", evidence_ids=[cited])
    elif instruction == UNCITED:
        step.update(instruction=INVENTED, evidence_ids=[])
    return {"outcome": "APPLIED", "summary": summary, "structure": raw}


@contextmanager
def isolated_database(real_db):
    # real_db already checked local HTTP, APP_ENV=local and exactly jidan_e2e_test.
    assert real_db.url.database == "jidan_e2e_test" and real_db.url.username == "jidan"
    schema = f"jidan_pr170_grounding_{uuid.uuid4().hex}_test"
    root = create_engine(real_db.url.set(username="root", password="e2e-root"))
    engine = create_engine(real_db.url.set(database=schema))
    granted = False
    try:
        with root.begin() as db:
            db.execute(text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
            db.execute(text(f"GRANT ALL ON `{schema}`.* TO 'jidan'@'%'"))
            granted = True
        with engine.connect() as db:
            command.upgrade(alembic_config(db), "head")
            db.commit()
        yield engine, schema
    finally:
        engine.dispose()
        with root.begin() as db:
            db.execute(text(f"DROP DATABASE IF EXISTS `{schema}`"))
            if granted:
                db.execute(text(f"REVOKE ALL PRIVILEGES ON `{schema}`.* FROM 'jidan'@'%'"))
        root.dispose()


@contextmanager
def grounding_server(schema, tmp_path, split_first_draft=False):
    env, origin, port, _ = local_env()
    env.update(DB_NAME=schema, BACKGROUND_JOBS="on", TASK_RUNNER_MODE="background",
               TASK_RUNNER_POLL_SECONDS="0.05", AI_PROVIDER="fake", MEDIA_ROOT=str(tmp_path / "media"))
    with (tmp_path / "grounding-server.log").open("w+") as log:
        args = [sys.executable, "-m", "e2e.test_pr170_grounding_integrity_http", "--port", str(port)]
        if split_first_draft:
            args.append("--split-first-draft")
        process = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=origin, timeout=5, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    assert process.poll() is None, "grounding server exited"
                    try:
                        if client.get("/api/health").status_code == 200:
                            yield client, origin
                            return
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.05)
                pytest.fail("grounding server did not become healthy")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def poll(client, path, ready):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        result = response.json()
        if ready(result):
            return result
        time.sleep(0.05)
    pytest.fail("grounding task did not settle")


@pytest.mark.parametrize("cited_vague,split_first_draft", [(False, False), (True, False), (False, True)],
                         ids=["preserved", "cited-vague-removed", "rejected-split-retry"])
def test_pr170_actions_grounded_draft_corrections_and_committed_photos(
        real_db, tmp_path, cited_vague, split_first_draft):
    with isolated_database(real_db) as (engine, schema):
        with Session(engine) as db:
            owner = make_user(db, "OWNER")
            store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW)
            issued = auth.create_session(owner.id, db=db)
            store_id = store.id
            db.commit()
        with grounding_server(schema, tmp_path, split_first_draft) as (client, origin):
            client.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
            csrf = client.get("/api/auth/csrf").json()["csrfToken"]
            base = f"/api/stores/{store_id}/manual"

            def write(path, body, method="POST"):
                return client.request(method, base + path, json=body, headers={"Origin": origin,
                    "X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())})

            started = write("/interviews", {})
            assert started.status_code == 201, started.text
            sid = started.json()["id"]
            spath = f"{base}/interviews/{sid}"
            for _ in range(6):
                state = poll(client, spath, lambda s: s["phase"] in {"COLLECTING", "ERROR"})
                assert state["phase"] == "COLLECTING"
                question = state["questions"][0]
                response = write(f"/interviews/{sid}/answers", {"expectedRevision": state["revision"],
                    "questionId": question["id"], "input": {"method": "TEXT", "text":
                        "오픈조는 09:00부터 15:00까지예요. " + " ".join(MIXED)}})
                assert response.status_code == 202, response.text
            state = poll(client, spath, lambda s: s["phase"] in {"READY_TO_GENERATE", "ERROR"})
            assert state["phase"] == "READY_TO_GENERATE"
            listing = poll(client, spath + "/reviews", lambda s: len(s["items"]) == 6 and
                           all(item["status"] == "READY" for item in s["items"]))
            common_id = next(i["id"] for i in state["intents"] if i["key"] == "COMMON_TASKS")
            common = next(r for r in listing["items"] if r["intentId"] == common_id)
            sections = common["content"]["sections"]
            assert [s["title"] for s in sections] == ["대응", "미정"]
            assert [s["instruction"] for s in sections[0]["steps"]] == list(MIXED)
            assert sections[1]["steps"] == []
            original_ids = [s["id"] for s in sections[0]["steps"]]
            photo = client.post(base + "/media", data={"purpose": "MANUAL_PHOTO"},
                files={"file": ("grounding.jpg", media_samples.jpeg(), "image/jpeg")},
                headers={"Origin": origin, "X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())})
            assert photo.status_code == 201, photo.text
            photo_id = photo.json()["id"]
            attached = write(f"/interviews/{sid}/intents/{common['intentId']}/review/photos", {
                "expectedRevision": common["revision"], "target": "SECTION", "sectionId": sections[0]["id"],
                "photos": [{"mediaId": photo_id, "title": "근거 사진", "caption": "확인용"}]}, "PUT")
            assert attached.status_code == 200, attached.text
            rpath = spath + f"/intents/{common_id}/review"
            common = client.get(rpath).json()

            def committed_review():
                with engine.connect() as db:
                    row = db.execute(select(InterviewIntentReview.status, InterviewIntentReview.revision,
                        InterviewIntentReview.ready_content).where(InterviewIntentReview.session_id == sid,
                        InterviewIntentReview.intent_id == common_id)).one()
                    turns = db.execute(select(InterviewTurn.id, InterviewTurn.content).where(
                        InterviewTurn.session_id == sid, InterviewTurn.turn_kind == "CORRECTION").order_by(
                        InterviewTurn.turn_no)).all()
                    return row, turns

            baseline_review = committed_review()
            assert baseline_review[0].ready_content == common["content"]
            for bad_input in ({"method": "TEXT", "text": ""}, {"method": "TEXT", "text": None}, {}):
                refused = write(f"/interviews/{sid}/intents/{common_id}/review/corrections", {
                    "expectedRevision": common["revision"], "input": bad_input})
                assert refused.status_code == 422 and refused.json()["code"] == "VALIDATION_ERROR"
                assert committed_review() == baseline_review
                assert client.get(rpath).json() == common
            # An uncited change (restored, a vague replacement, a dropped step: all one rule) fails
            # the whole correction after the automatic attempts instead of reporting a restored
            # original as APPLIED with the model's changed summary. Content, summary and photos stay.
            previous = common["content"]
            accepted = write(f"/interviews/{sid}/intents/{common_id}/review/corrections", {
                "expectedRevision": common["revision"], "input": {"method": "TEXT", "text": UNCITED}})
            assert accepted.status_code == 202, accepted.text
            common = poll(client, rpath, lambda r: r["status"] != "PROCESSING")
            assert common["status"] == "ERROR" and common["error"]["code"] == "AI_PROCESSING_FAILED"
            assert common["error"]["retryable"] is True and common["content"] == previous
            row, turns = committed_review()
            assert row.status == "ERROR" and row.revision == common["revision"]
            assert row.ready_content == previous and turns[-1].content == UNCITED
            with engine.connect() as db:
                failed_task = db.execute(select(BackgroundTask.status, BackgroundTask.last_error_code).where(
                    BackgroundTask.subject_id == sid, BackgroundTask.kind == "REVIEW_CORRECTION")).one()
            assert failed_task == ("FAILED", "INVALID_OUTPUT")
            retried = write(f"/interviews/{sid}/intents/{common_id}/review/retries",
                            {"expectedRevision": common["revision"]})
            assert retried.status_code == 202, retried.text
            common = poll(client, rpath, lambda r: r["status"] != "PROCESSING")
            assert common["status"] == "READY" and common["content"] == previous
            row, _turns = committed_review()
            assert row.status == "READY" and row.revision == common["revision"] and row.ready_content == previous
            expected_actions = list(MIXED)
            if cited_vague:
                previous_revision = common["revision"]
                accepted = write(f"/interviews/{sid}/intents/{common_id}/review/corrections", {
                    "expectedRevision": previous_revision,
                    "input": {"method": "TEXT", "text": "인용된 모호한 변경 검사"}})
                assert accepted.status_code == 202, accepted.text
                common = poll(client, rpath, lambda r: r["status"] != "PROCESSING")
                # +1 when the correction is accepted (PROCESSING), +1 when it is applied (READY).
                assert common["status"] == "READY" and common["revision"] == previous_revision + 2
                expected_actions = list(MIXED[1:])
                original_ids = original_ids[1:]
                actual = common["content"]["sections"][0]
                assert [s["instruction"] for s in actual["steps"]] == expected_actions
                assert [s["id"] for s in actual["steps"]] == original_ids
                assert actual["photos"][0]["mediaId"] == photo_id
                row, turns = committed_review()
                assert row.ready_content == common["content"] and row.revision == common["revision"]
                assert turns[-1].content == "인용된 모호한 변경 검사"
                assert client.get(rpath).json()["content"] == row.ready_content
            listing = client.get(spath + "/reviews").json()
            completed = write(f"/interviews/{sid}/completion", {"expectedRevision": listing["sessionRevision"],
                "reviewRevisions": [{"intentId": r["intentId"], "revision": r["revision"]} for r in listing["items"]]})
            assert completed.status_code == 202, completed.text
            settled = poll(client, spath, lambda s: s["phase"] in {"COMPLETED", "ERROR"})
            if split_first_draft:
                assert settled["phase"] == "ERROR" and settled["error"]["code"] == "AI_PROCESSING_FAILED"
                failed_draft = client.get(base + "/draft").json()
                assert failed_draft["generationStatus"] == "ERROR"
                with engine.connect() as db:
                    version = db.execute(select(ManualVersion.generation_status, ManualVersion.id).where(
                        ManualVersion.id == failed_draft["versionId"])).one()
                    assert version.generation_status == "ERROR"
                    failed_task = db.execute(select(BackgroundTask.status, BackgroundTask.last_error_code).where(
                        BackgroundTask.subject_id == sid, BackgroundTask.kind == "DRAFT_GENERATION")).one()
                    assert failed_task == ("FAILED", "INVALID_OUTPUT")
                    assert db.execute(select(ManualStep.id)).all() == []  # no partial normalized draft
                failed_reviews = client.get(spath + "/reviews").json()
                assert failed_reviews["items"] == listing["items"]
                assert failed_reviews["sessionRevision"] == settled["revision"]
                retried = write(f"/interviews/{sid}/retries", {"expectedRevision": settled["revision"]})
                assert retried.status_code == 202, retried.text
                settled = poll(client, spath, lambda s: s["phase"] in {"COMPLETED", "ERROR"})
            assert settled["phase"] == "COMPLETED"
            draft = poll(client, base + "/draft", lambda d: d["generationStatus"] in {"READY", "ERROR"})
            assert draft["generationStatus"] == "READY"
            saved_content = draft["content"]
            assert [s["instruction"] for s in saved_content["sections"][0]["steps"]] == expected_actions
            assert [s["id"] for s in saved_content["sections"][0]["steps"]] == original_ids
            assert saved_content["sections"][0]["id"] == sections[0]["id"]
            assert saved_content["sections"][0]["photos"][0]["mediaId"] == photo_id

            def persisted():
                # A new connection sees actual commits, independently of request serialization.
                with engine.connect() as db:
                    steps = db.execute(select(ManualStep.id, ManualStep.instruction).where(
                        ManualStep.section_id == sections[0]["id"]).order_by(ManualStep.sort_order)).all()
                    revision = db.scalar(select(ManualVersion.revision).where(ManualVersion.id == draft["versionId"]))
                    photos = db.execute(select(ManualPhotoAttachment.media_id, ManualPhotoAttachment.title,
                        ManualPhotoAttachment.caption).where(ManualPhotoAttachment.version_id == draft["versionId"])).all()
                    return steps, revision, photos

            baseline = persisted()
            assert baseline[0] == list(zip(original_ids, expected_actions, strict=True))
            assert baseline[2] == [(photo_id, "근거 사진", "확인용")]
            for invalid_input in ({"method": "TEXT", "text": ""}, {"method": "TEXT", "text": None}, {}):
                refused = write("/draft/corrections", {"expectedVersionId": draft["versionId"],
                    "expectedRevision": draft["revision"], "target": {"kind": "MANUAL", "targetId": None},
                    "input": invalid_input})
                assert refused.status_code == 422 and refused.json()["code"] == "VALIDATION_ERROR"
                assert persisted() == baseline
                assert client.get(base + "/draft").json()["content"] == saved_content
                with engine.connect() as db:
                    assert db.execute(select(ManualDraftCorrection.id)).all() == []
            for instruction, expected in [(UNCITED, "ERROR"), ("모호한 변경 검사", "ERROR"),
                                          ("그대로 유지해요.", "SUCCEEDED"), ("빈 응답 검사", "ERROR"),
                                          ("잘못된 참조 검사", "ERROR")]:
                accepted = write("/draft/corrections", {"expectedVersionId": draft["versionId"],
                    "expectedRevision": draft["revision"], "target": {"kind": "MANUAL", "targetId": None},
                    "input": {"method": "TEXT", "text": instruction}})
                assert accepted.status_code == 202, accepted.text
                cid = accepted.json()["id"]
                status = poll(client, base + f"/draft/corrections/{cid}", lambda c: c["status"] != "RUNNING")
                assert status["status"] == expected
                assert persisted() == baseline
                assert client.get(base + "/draft").json()["content"] == saved_content
                with engine.connect() as db:
                    row = db.execute(select(ManualDraftCorrection.status, ManualDraftCorrection.error_code,
                        ManualDraftCorrection.result_revision).where(ManualDraftCorrection.id == cid)).one()
                if expected == "ERROR":
                    assert status["error"]["code"] == "AI_PROCESSING_FAILED" and status["error"]["retryable"]
                    assert tuple(row) == ("ERROR", "AI_PROCESSING_FAILED", None)
                else:
                    assert tuple(row) == ("SUCCEEDED", None, draft["revision"])  # NO_CHANGE
                if instruction == UNCITED:
                    retried = write(f"/draft/corrections/{cid}/retries", {
                        "expectedVersionId": draft["versionId"], "expectedRevision": draft["revision"]})
                    assert retried.status_code == 202, retried.text
                    status = poll(client, base + f"/draft/corrections/{cid}", lambda c: c["status"] != "RUNNING")
                    assert status["status"] == "SUCCEEDED" and status["resultRevision"] == draft["revision"]
                    assert persisted() == baseline
                    assert client.get(base + "/draft").json()["content"] == saved_content
            accepted = write("/draft/corrections", {"expectedVersionId": draft["versionId"],
                "expectedRevision": draft["revision"], "target": {"kind": "SECTION", "targetId": sections[0]["id"]},
                "input": {"method": "TEXT", "text": CITED_CHANGE}})
            assert accepted.status_code == 202, accepted.text
            status = poll(client, base + f"/draft/corrections/{accepted.json()['id']}", lambda c: c["status"] != "RUNNING")
            assert status["status"] == "SUCCEEDED"
            after = client.get(base + "/draft").json()
            assert after["revision"] == draft["revision"] + 1
            assert [s["instruction"] for s in after["content"]["sections"][0]["steps"]] == [CITED_CHANGE, *expected_actions[1:]]
            rows, revision, photos = persisted()
            assert rows == list(zip(original_ids, [CITED_CHANGE, *expected_actions[1:]], strict=True))
            assert revision == after["revision"] and photos == baseline[2]
            assert after["content"]["sections"][1:] == saved_content["sections"][1:]


if __name__ == "__main__":
    import uvicorn

    from app.ai import set_ai_provider

    assert os.getenv("APP_ENV") == "local"
    assert os.getenv("DB_NAME", "").startswith("jidan_pr170_grounding_") and os.environ["DB_NAME"].endswith("_test")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--split-first-draft", action="store_true")
    args = parser.parse_args()
    draft_calls = 0

    def draft_response(data):
        global draft_calls
        draft_calls += 1
        result = scripted_draft(data)
        if args.split_first_draft and draft_calls <= 3:
            # Same-ID uncited shortening plus a cited part would duplicate the restored original.
            steps = result["structure"]["sections"][0]["steps"]
            steps[0]["instruction"] = "상황에 맞게 처리해요."
            cited = next(chunk["id"] for chunk in data["evidence"] if chunk["text"] == "먼저 전원을 꺼요.")
            steps.append(raw_step("new-3", "먼저 전원을 꺼요.", [cited]))
        return result

    set_ai_provider(FakeAiProvider(auto_cite=False).on("summarize_intent", scripted_summary)
                    .on("compose_draft", draft_response).on("revise_structure", scripted_revision))
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")

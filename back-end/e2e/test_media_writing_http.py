"""PR 171: socket HTTP, real multipart/PyAV, asynchronous tasks and independent MySQL reads.

Only owner identity/approval, initial review/draft and AI output are fixtures. No HTTP handler,
DB dependency, decoder or task runner is replaced. Each scenario has its own disposable schema
so the standard E2E API cannot claim its tasks. Paid AI and deployment/browser checks are separate.
"""
import argparse
import io
import os
import struct
import subprocess
import sys
import time
import uuid
import wave
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app import auth
from app.ai.contracts import StructureSnapshot
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, structure_to_raw
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    InterviewIntentReview,
    InterviewSessionIntent,
    ManualDraftCorrection,
    ManualMedia,
    ManualMediaSnapshotRef,
    ManualPhotoAttachment,
    ManualSection,
    ManualShift,
    ManualStep,
)
from e2e.review_helpers import local_env
from tests import media_samples, video_samples
from tests.api_contract import validate_response
from tests.conftest import alembic_config
from tests.factories import NOW, make_interview, make_question_set, make_store, make_user
from tests.manual_factories import make_ready_draft, sample_content

WRITTEN = "결제 후 영수증과 진동벨을 함께 드려요."


@contextmanager
def media_database(real_db):
    assert real_db.url.database == "jidan_e2e_test" and os.getenv("APP_ENV") == "local"
    schema = f"jidan_pr171_{uuid.uuid4().hex}_test"
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


def wait_for(read, condition, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if condition(value):
            return value
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for state: {value}")


@pytest.fixture(params=["review", "draft"])
def media_flow(request, real_db, tmp_path):
    with media_database(real_db) as (engine, schema):
        with Session(engine) as db:
            owner = make_user(db, "OWNER")
            store = make_store(db, owner, approval_status="APPROVED", approved_at=NOW)
            other = make_store(db, owner, approval_status="APPROVED", approved_at=NOW)
            content = sample_content()
            draft = make_ready_draft(db, store, content)
            questions, intents = make_question_set(db)
            options = {} if request.param == "review" else {
                "status": "COMPLETED", "completed_at": utcnow(), "current_intent_id": None}
            interview = make_interview(db, draft, questions, intents, **options)
            progress = db.get(InterviewSessionIntent, (interview.id, intents[0].id))
            progress.coverage_status, progress.covered_at, progress.finished_at = "COVERED", NOW, NOW
            review_content = {**deepcopy(content), "intentId": intents[0].id,
                              "summary": "기존 업무를 정리했어요.", "needsDetail": False}
            db.add(InterviewIntentReview(session_id=interview.id, intent_id=intents[0].id,
                                        status="READY", ready_content=review_content))
            issued = auth.create_session(owner.id, db=db)
            db.commit()
            flow = SimpleNamespace(engine=engine, schema=schema, kind=request.param, store=store.id,
                other_store=other.id, version=draft.id, session=interview.id, intent=intents[0].id,
                content=content, review_content=review_content, section=content["sections"][0]["id"], token=issued.token)
        flow.base = f"/api/stores/{flow.store}/manual"
        flow.review_url = f"{flow.base}/interviews/{flow.session}/intents/{flow.intent}/review"
        yield flow


@contextmanager
def media_server(flow, tmp_path, mode="write"):
    env, origin, port, _ = local_env()
    gate = tmp_path / "release-model"
    gate.unlink(missing_ok=True)
    env.update(DB_NAME=flow.schema, AI_PROVIDER="fake", BACKGROUND_JOBS="on",
               TASK_RUNNER_MODE="background", TASK_RUNNER_POLL_SECONDS="0.05",
               MEDIA_ROOT=str(tmp_path / "media"))
    with (tmp_path / "media-server.log").open("w+") as log:
        args = [sys.executable, "-m", "e2e.test_media_writing_http", "--port", str(port),
                "--gate", str(gate), "--mode", mode]
        process = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=origin, trust_env=False, timeout=15,
                             headers={"Cookie": f"{auth.SESSION_COOKIE_NAME}={flow.token}"}) as client:
                def healthy():
                    assert process.poll() is None, "media server exited; inspect media-server.log"
                    try:
                        return client.get("/api/health").status_code == 200
                    except httpx.HTTPError:
                        return False
                wait_for(healthy, bool)
                client.event_hooks["response"] = [lambda response: (response.read(), validate_response(response))]
                flow.client, flow.env, flow.gate, flow.origin = client, env, gate, origin
                yield flow
        finally:
            gate.touch()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def headers(flow, key=None):
    csrf = flow.client.get("/api/auth/csrf")
    assert csrf.status_code == 200
    return {"Origin": flow.origin, "X-CSRF-Token": csrf.json()["csrfToken"],
            "Idempotency-Key": key or str(uuid.uuid4())}


def upload(flow, data, purpose, key=None, store=None):
    return flow.client.post(f"/api/stores/{store or flow.store}/manual/media", headers=headers(flow, key),
                           data={"purpose": purpose}, files={"file": ("input.bin", data, "application/octet-stream")})


def content(flow):
    response = flow.client.get(flow.review_url if flow.kind == "review" else flow.base + "/draft")
    assert response.status_code == 200, response.text
    return response.json()


def submit(flow, files, key=None, expected=1):
    if flow.kind == "review":
        url = flow.review_url + "/media-writing"
        body = {"expectedRevision": expected, "sectionId": flow.section, "mediaIds": files}
    else:
        url = flow.base + "/draft/corrections"
        body = {"expectedVersionId": flow.version, "expectedRevision": expected,
                "target": {"kind": "SECTION", "targetId": flow.section},
                "input": {"method": "MEDIA", "mediaIds": files}}
    return flow.client.post(url, json=body, headers=headers(flow, key))


def tasks(flow):
    kind = "REVIEW_MEDIA_WRITING" if flow.kind == "review" else "DRAFT_MEDIA_WRITING"
    with Session(flow.engine) as db:
        return list(db.scalars(select(BackgroundTask).where(BackgroundTask.kind == kind)
                               .order_by(BackgroundTask.attempt)))


def refs(flow):
    with Session(flow.engine) as db:
        return [(r.holder_id, r.media_id) for r in db.scalars(select(ManualMediaSnapshotRef))]


def status(flow, accepted):
    url = flow.review_url if flow.kind == "review" else f"{flow.base}/draft/corrections/{accepted['id']}"
    response = flow.client.get(url)
    assert response.status_code == 200
    return response.json()


def input_files(flow):
    clip = upload(flow, video_samples.video(seconds=2, audio="tone", audio_start=1, audio_seconds=1), "MANUAL_VIDEO")
    still = upload(flow, media_samples.jpeg(gps=False), "MANUAL_PHOTO")
    assert clip.status_code == still.status_code == 201, (clip.text, still.text)
    ids = [clip.json()["id"], still.json()["id"]]
    with Session(flow.engine) as db:
        assert [(db.get(ManualMedia, mid).kind, db.get(ManualMedia, mid).content_deleted_at) for mid in ids] == [
            ("VIDEO", None), ("IMAGE", None)]
    return ids


def assert_stored_content(flow, expected):
    after = content(flow)["content"]
    assert after == expected
    with Session(flow.engine) as db:
        if flow.kind == "review":
            assert db.get(InterviewIntentReview, (flow.session, flow.intent)).ready_content == expected
        else:
            shifts = list(db.scalars(select(ManualShift).where(ManualShift.version_id == flow.version)
                                    .order_by(ManualShift.sort_order)))
            sections = list(db.scalars(select(ManualSection).where(ManualSection.version_id == flow.version)
                                      .order_by(ManualSection.sort_order)))
            assert [s.id for s in shifts] == [s["id"] for s in expected["shifts"]]
            assert [s.id for s in sections] == [s["id"] for s in expected["sections"]]
            for section in expected["sections"]:
                steps = list(db.scalars(select(ManualStep).where(ManualStep.section_id == section["id"])
                                       .order_by(ManualStep.sort_order)))
                assert [(s.id, s.instruction, s.checklist_item) for s in steps] == [
                    (s["id"], s["instruction"], s["checklistItem"]) for s in section["steps"]]
        assert db.scalars(select(ManualPhotoAttachment)).all() == []


@pytest.mark.parametrize("mode", ["write", "reorder_only"])
def test_media_writing_upload_commit_order_and_retention(media_flow, tmp_path, mode):
    with media_server(media_flow, tmp_path, mode) as flow:
        before = content(flow)
        invalid_upload = upload(flow, video_samples.video(audio=None)[:40], "MANUAL_VIDEO")
        assert invalid_upload.status_code == 422 and invalid_upload.json()["code"] == "MEDIA_INVALID"
        with Session(flow.engine) as db:
            assert db.scalars(select(ManualMedia)).all() == []
        files = input_files(flow)
        foreign = upload(flow, video_samples.video(audio=None), "MANUAL_VIDEO", store=flow.other_store)
        audio = upload(flow, media_samples.wav_seconds(1), "INTERVIEW_AUDIO")
        assert foreign.status_code == audio.status_code == 201
        for invalid, expected in (([files[0], files[0]], 422), ([str(uuid.uuid4())], 404),
                                  ([foreign.json()["id"]], 404), ([audio.json()["id"]], 422)):
            response = submit(flow, invalid)
            assert response.status_code == expected, response.text
            assert content(flow) == before and tasks(flow) == [] and refs(flow) == []
        key = str(uuid.uuid4())
        response = submit(flow, files, key)
        assert response.status_code == 202, response.text
        accepted = response.json()
        assert submit(flow, files, key).json() == accepted
        assert submit(flow, list(reversed(files)), key).json()["code"] == "IDEMPOTENCY_KEY_REUSED"
        [task] = wait_for(lambda: tasks(flow), lambda rows: len(rows) == 1 and rows[0].status == "RUNNING")
        assert task.payload["mediaIds"] == files and {mid for _, mid in refs(flow)} == set(files)
        assert status(flow, accepted)["status"] == ("PROCESSING" if flow.kind == "review" else "RUNNING")
        # Real retention process, same isolated DB/storage: referenced video must survive 25 h.
        subprocess.run([sys.executable, "-c", ("from datetime import timedelta; from app.db import utcnow; "
            "from app.media.retention import purge_media_content; purge_media_content(now=utcnow()+timedelta(hours=25))")],
            env=flow.env, check=True, timeout=20, capture_output=True)
        with Session(flow.engine) as db:
            assert all(db.get(ManualMedia, mid).content_deleted_at is None for mid in files)
        for mid in files:
            assert flow.client.delete(f"{flow.base}/media/{mid}", headers=headers(flow)).status_code == 409
        flow.gate.touch()
        wait_for(lambda: tasks(flow), lambda rows: rows[0].status == "SUCCEEDED")
        assert refs(flow) == []
        done = content(flow)
        assert done["revision"] == before["revision"] + (2 if flow.kind == "review" else int(mode == "write"))
        expected = deepcopy(before["content"])
        if mode == "write":
            actual = done["content"]["sections"][0]["steps"][-1]
            assert actual["instruction"] == WRITTEN
            expected["sections"][0]["steps"].append(actual)
        assert_stored_content(flow, expected)
        for mid in files:
            assert flow.client.delete(f"{flow.base}/media/{mid}", headers=headers(flow)).status_code == 204


def test_media_writing_failure_retry_preserves_content_and_releases_inputs(media_flow, tmp_path):
    with media_server(media_flow, tmp_path, "fail_once") as flow:
        before = content(flow)
        files = input_files(flow)
        accepted = submit(flow, files).json()
        wait_for(lambda: tasks(flow), lambda rows: len(rows) == 1 and rows[0].status == "RUNNING")
        flow.gate.touch()
        wait_for(lambda: tasks(flow), lambda rows: rows[0].status == "FAILED")
        failed = status(flow, accepted)
        assert failed["status"] == "ERROR" and refs(flow) == []
        assert_stored_content(flow, before["content"])
        flow.gate.unlink()
        if flow.kind == "review":
            url, payload = flow.review_url + "/retries", {"expectedRevision": failed["revision"]}
        else:
            url = f"{flow.base}/draft/corrections/{accepted['id']}/retries"
            payload = {"expectedVersionId": flow.version, "expectedRevision": before["revision"]}
        retried = flow.client.post(url, json=payload, headers=headers(flow))
        assert retried.status_code == 202, retried.text
        wait_for(lambda: tasks(flow), lambda rows: len(rows) == 2 and rows[-1].status == "RUNNING")
        assert {mid for _, mid in refs(flow)} == set(files)
        flow.gate.touch()
        wait_for(lambda: tasks(flow), lambda rows: rows[-1].status == "SUCCEEDED")
        assert refs(flow) == [] and tasks(flow)[0].payload == tasks(flow)[1].payload
        expected = deepcopy(before["content"])
        expected["sections"][0]["steps"].append(content(flow)["content"]["sections"][0]["steps"][-1])
        assert expected["sections"][0]["steps"][-1]["instruction"] == WRITTEN
        assert_stored_content(flow, expected)


def test_stale_media_writing_cancels_without_partial_storage(media_flow, tmp_path):
    with media_server(media_flow, tmp_path) as flow:
        before = content(flow)
        files = input_files(flow)
        accepted = submit(flow, files).json()
        wait_for(lambda: tasks(flow), lambda rows: len(rows) == 1 and rows[0].status == "RUNNING")
        with Session(flow.engine) as db:
            if flow.kind == "review":
                db.get(InterviewIntentReview, (flow.session, flow.intent)).revision += 1
            else:
                row = db.get(ManualDraftCorrection, accepted["id"])
                row.status, row.completed_at, row.result_revision = "SUCCEEDED", utcnow(), row.base_revision
            db.commit()
        flow.gate.touch()
        wait_for(lambda: tasks(flow), lambda rows: rows[0].status == "CANCELLED")
        assert refs(flow) == []
        assert_stored_content(flow, before["content"])
        for mid in files:
            assert flow.client.delete(f"{flow.base}/media/{mid}", headers=headers(flow)).status_code == 204


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--mode", choices=("write", "reorder_only", "fail_once"), default="write")
    args = parser.parse_args()
    if os.getenv("APP_ENV") != "local" or not os.getenv("DB_NAME", "").startswith("jidan_pr171_"):
        raise RuntimeError("media E2E requires its disposable local test schema")
    gate, calls = Path(args.gate), 0
    provider = FakeAiProvider(auto_cite=False)

    def writing(data):
        nonlocal calls
        deadline = time.monotonic() + 30
        while not gate.exists():
            if time.monotonic() >= deadline:
                raise AiError(AiErrorCode.TIMEOUT)
            time.sleep(0.02)
        calls += 1
        kinds = {item["kind"] for item in data["media"]}
        assert {"PHOTO", "VIDEO_FRAME", "VIDEO_TRANSCRIPT"} <= kinds
        if args.mode == "fail_once" and calls == 1:
            raise AiError(AiErrorCode.INPUT_REJECTED)
        raw = structure_to_raw(StructureSnapshot.model_validate(data["current"]))
        if args.mode != "reorder_only":
            section = next(s for s in raw["sections"] if s["ref"] == data["target"]["target_id"])
            section["steps"].append({"ref": "new-1", "instruction": WRITTEN,
                "checklist_item": False, "evidence_ids": [data["media"][0]["id"]]})
        raw["sections"].reverse()
        raw["shifts"].reverse()
        return {"outcome": "APPLIED", "structure": raw, "removed_steps": []}

    def transcribe(request):
        # Real uploaded MP4 was decoded before this external STT boundary. Its audio
        # starts one second after the video: require preserved silence, not a shifted track.
        with wave.open(io.BytesIO(request.audio)) as sound:
            rate = sound.getframerate()
            values = struct.unpack(f"<{sound.getnframes()}h", sound.readframes(sound.getnframes()))
        assert max(abs(v) for v in values[:int(0.9 * rate)]) == 0
        assert max(abs(v) for v in values[int(1.1 * rate):]) > 1000
        return "결제 후 영수증과 진동벨을 함께 드려요."

    provider.on("transcribe", transcribe)
    provider.on("write_section_from_media", writing)
    from app.ai import set_ai_provider
    set_ai_provider(provider)
    import uvicorn

    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

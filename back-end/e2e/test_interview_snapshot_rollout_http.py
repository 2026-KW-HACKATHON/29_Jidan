"""Snapshot rollout/restart regressions against real HTTP and an independent MySQL connection."""
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BackgroundTask, IdempotencyRecord, InterviewSession, InterviewTurn
from app.tasks import cancel_tasks
from app.tasks.runner import MAX_PAYLOAD_BYTES
from e2e.interview_helpers import interview_case
from e2e.review_helpers import local_env


def snapshot_provider():
    from app.ai.fake import FakeAiProvider, FakeOutcome

    def question(_data):
        large = os.getenv("SNAPSHOT_LARGE_CARDS") == "1"
        return {"question": "업무 순서를 알려 주세요.", "guidance": "실제 업무를 알려 주세요.",
                "guidanceCards": [
                    {"type": "LIST", "title": "업무 예시", "footer": None, "items": [
                        {"id": None, "label": "😀" * 200 if large else "재고 정리",
                         "description": "😀" * 1000 if large else None, "status": None}
                        for _ in range(50 if large else 1)]}
                    for _ in range(5 if large else 1)]}

    provider = FakeAiProvider().on("generate_question", question)
    return provider.script("judge_sufficiency", *[FakeOutcome.fail("timeout")] * 3)


class RestartableServer:
    def __init__(self, tmp_path, large):
        self.env, self.origin, self.port, _ = local_env()
        self.env.update(TASK_RUNNER_POLL_SECONDS="0.1", SNAPSHOT_LARGE_CARDS="1" if large else "0")
        self.path, self.process, self.log = tmp_path, None, None

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.log.close()
            self.process = None

    def start(self, flag, *, background=False):
        self.stop()
        env = {**self.env, "INTERVIEW_GUIDANCE_RESPONSES": flag,
               "BACKGROUND_JOBS": "on" if background else "off",
               "TASK_RUNNER_MODE": "background" if background else "manual"}
        module = (["e2e.interview_server", "--provider",
                   "e2e.test_interview_snapshot_rollout_http:snapshot_provider", "--port", str(self.port)]
                  if background else ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)])
        self.log = (self.path / f"server-{uuid.uuid4()}.log").open("w+")
        self.process = subprocess.Popen([sys.executable, "-m", *module], env=env,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 20
        with httpx.Client(base_url=self.origin, timeout=1, trust_env=False) as client:
            while True:
                assert self.process.poll() is None, "snapshot server stopped; inspect server log"
                try:
                    if client.get("/api/health").status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.1)


@contextmanager
def snapshot_case(real_db, tmp_path, flag, large):
    server = RestartableServer(tmp_path, large)
    sid = None
    try:
        server.start(flag, background=True)
        with interview_case(real_db, server.origin) as case:
            sid = case.start()["id"]
            case.wait(sid, lambda body: body["phase"] == "COLLECTING")
            server.start(flag)  # keep the real evaluation queued across rollout restarts
            yield case, server, case.get(sid)
    finally:
        server.stop()
        if sid:
            with Session(real_db) as db:
                cancel_tasks(db, "EVALUATION", sid)
                db.commit()


@pytest.mark.parametrize("flag,large", [("on", False), ("off", False), ("on", True)])
def test_snapshot_survives_flag_changes_restart_error_and_retry(real_db, tmp_path, flag, large):
    with snapshot_case(real_db, tmp_path, flag, large) as (case, server, before):
        with Session(real_db) as db:
            question = db.get(InterviewTurn, before["questions"][0]["id"])
            original_guidance = question.guidance
            original_cards = deepcopy(question.guidance_cards)
        key = str(uuid.uuid4())
        response = case.answer(before, key=key)
        assert response.status_code == 202, response.text
        accepted = response.json()
        expected = {**before["questions"][0], "answered": True}
        assert accepted["lastAnsweredQuestion"] == expected
        if large:
            assert len(json.dumps(expected, ensure_ascii=False).encode()) > MAX_PAYLOAD_BYTES
        for changed in ("off", "on"):
            server.start(changed)
            state = case.get(before["id"])
            assert state["revision"] == accepted["revision"]
            assert state["questions"] == []
            assert state["lastAnsweredQuestion"] == expected
            replay = case.answer(before, key=key)
            assert replay.status_code == 202 and replay.json() == accepted
        server.start("off", background=True)
        deadline = time.monotonic() + 30
        while True:
            failed = case.get(before["id"])
            if failed["phase"] == "ERROR":
                break
            assert time.monotonic() < deadline, failed
            time.sleep(0.1)
        assert failed["lastAnsweredQuestion"] == expected
        server.start("on")
        assert case.get(before["id"])["lastAnsweredQuestion"] == expected
        retry = case.post(case.url(before["id"], "/retries"), {"expectedRevision": failed["revision"]})
        assert retry.status_code == 202, retry.text
        assert retry.json()["lastAnsweredQuestion"] == expected
        server.start("off")
        assert case.get(before["id"])["lastAnsweredQuestion"] == expected
        replay = case.answer(before, key=key)
        assert replay.status_code == 202 and replay.json() == accepted
        with Session(real_db) as db:
            tasks = list(db.scalars(select(BackgroundTask).where(
                BackgroundTask.subject_id == before["id"], BackgroundTask.kind == "EVALUATION")))
            assert len(tasks) == 2
            assert tasks[0].payload == tasks[1].payload
            assert all(len(json.dumps(task.payload, ensure_ascii=False).encode()) < MAX_PAYLOAD_BYTES
                       for task in tasks)
            question = db.get(InterviewTurn, before["questions"][0]["id"])
            assert question.guidance == original_guidance
            assert question.guidance_cards == original_cards
            answer = db.scalar(select(InterviewTurn).where(
                InterviewTurn.reply_to_question_turn_id == question.id))
            assert answer.guidance == expected.get("guidance")
            assert answer.guidance_cards == expected.get("guidanceCards")
            record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key))
            assert record.response_body == accepted


def test_failed_answer_rolls_back_snapshot_and_allows_same_key_retry(real_db, tmp_path):
    with snapshot_case(real_db, tmp_path, "on", False) as (case, _server, before):
        sid, key = before["id"], str(uuid.uuid4())
        trigger = "snapshot_fail_" + uuid.uuid4().hex
        with real_db.begin() as connection:
            connection.exec_driver_sql(f"""CREATE TRIGGER `{trigger}` BEFORE INSERT ON background_tasks
                FOR EACH ROW BEGIN IF NEW.subject_id = '{sid}' AND NEW.kind = 'EVALUATION' THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'fixture evaluation enqueue failure';
                END IF; END""")
        try:
            response = case.answer(before, key=key)
            assert response.status_code == 500, response.text
            assert case.get(sid) == before
            with Session(real_db) as db:
                assert db.scalar(select(InterviewTurn).where(
                    InterviewTurn.session_id == sid, InterviewTurn.turn_kind == "ANSWER")) is None
                assert db.scalar(select(BackgroundTask).where(
                    BackgroundTask.subject_id == sid, BackgroundTask.kind == "EVALUATION")) is None
                assert db.get(InterviewSession, sid).revision == before["revision"]
        finally:
            with real_db.begin() as connection:
                connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS `{trigger}`")
        response = case.answer(before, key=key)
        assert response.status_code == 202, response.text
        expected = {**before["questions"][0], "answered": True}
        assert response.json()["lastAnsweredQuestion"] == expected
        assert case.get(sid)["lastAnsweredQuestion"] == expected
        with Session(real_db) as db:
            [answer] = list(db.scalars(select(InterviewTurn).where(
                InterviewTurn.session_id == sid, InterviewTurn.turn_kind == "ANSWER")))
            assert answer.guidance_cards == expected["guidanceCards"]

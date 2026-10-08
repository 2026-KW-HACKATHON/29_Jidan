"""PR #170 regression through real HTTP, background workers and independent MySQL reads.

Only the external AI boundary is scripted. app.main, auth, routes, task runner and persistence
run unchanged; this file is discovered by testing/run-e2e.sh's existing e2e suite.
"""
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.ai import set_ai_provider
from app.ai.aspects import confirmation_question
from app.ai.contracts import SufficiencyRequest
from app.ai.decisions import Thresholds
from app.ai.errors import AiError, AiErrorCode
from app.ai.fake import FakeAiProvider, FakeOutcome
from app.ai.prompts import PROMPT_VERSION
from app.ai.provider import FallbackAiProvider
from app.db.models import (
    BackgroundTask,
    InterviewEvaluation,
    InterviewIntentReview,
    InterviewProbeBatch,
    InterviewSessionIntent,
    InterviewTurn,
)
from e2e.ai_scenario import FAIL_ONCE, CallBudget, RoutedAiProvider, install
from e2e.review_helpers import local_env
from tests.api_contract import validate_response
from tests.factories import NOW, make_store, make_user
from tests.interview_factories import ensure_question_set

THRESHOLDS = Thresholds(0.701, 0.804)
LONG_QUESTION = "업무에서 직원이 하는 동작을 차례로 설명해 주시겠어요? " + "질문 맥락 " * 260
SENTENCES = [f"{i}번 작업은 커피 주문서를 확인하고 해당 컵에 표시된 음료를 담아 픽업대에 올려요."
             for i in range(36)]


def _response(na=0.0, confirmed=0.0, sufficient=True):
    return {"sufficient": sufficient, "probability": 0.9 if sufficient else 0.2,
            "missing_aspects": [] if sufficient else ["근무조의 구성"],
            "not_applicable_probability": na, "not_applicable_confirmed_probability": confirmed}


class _LiveFixture(FakeAiProvider):
    provider_name, model = "openai-fixture", "fixture-live-model"

    def effort_for(self, operation):
        return "high" if operation == "judge_sufficiency" else None


class _ConcurrentFixture(FakeAiProvider):
    def __init__(self):
        super().__init__()
        self._barrier = threading.Barrier(2)

    def _judge_backend(self, request: SufficiencyRequest):
        backend = super()._judge_backend(request)
        self._barrier.wait(timeout=10)
        return backend


def create_app():
    """Uvicorn test factory: install the AI boundary before starting the real application."""
    scenario = os.environ["PR170_JUDGE_SCENARIO"]
    backend = os.environ["PR170_JUDGE_BACKEND"]
    fake, live = FakeAiProvider(judge_backend=backend), _LiveFixture(judge_backend=backend)
    selected = live if os.environ["PR170_JUDGE_ROUTE"] == "live" else fake
    selected.judge_thresholds = THRESHOLDS
    if scenario == "failed":
        success = FakeOutcome.predicates() if backend == "decisions" else FakeOutcome.ok(_response())
        selected.script("judge_sufficiency", FakeOutcome.fail("refused"), success)
    elif scenario == "installed":
        # The demo's real handler chooses Responses even with the default Decisions setting.
        fake = install(FakeAiProvider())
        fake.judge_thresholds = THRESHOLDS
    elif scenario in ("confirmation", "reversal", "uncertain"):
        second = (0.1, 0.1, False) if scenario == "reversal" else (0.94, 0.1, True) if (
            scenario == "uncertain") else (0.94, 0.94, True)
        second_response = _response(*second)
        if scenario == "confirmation":
            # A confirmed absent work structure has no ordinary work aspects. The server's
            # not-applicable gate must run before the empty ordinary missing list check.
            second_response.update(sufficient=False, probability=0.1, missing_aspects=[])
        selected.script("judge_sufficiency", FakeOutcome.ok(_response(0.94, 0.1)),
                        FakeOutcome.ok(second_response))
        def question(data):
            return {
                "question": data["intent"]["base_question"] if data["kind"] == "BASE" else (
                    confirmation_question(data["target_aspect"]) or f"{data['target_aspect']}을 알려주시겠어요?"),
                "guidance": None, "examples": []}

        selected.on("generate_question", question)
        if os.environ.get("PR170_JUDGE_FALLBACK") == "1":
            def timeout(_data):
                raise AiError(AiErrorCode.TIMEOUT)

            primary = FakeAiProvider().on("judge_sufficiency", timeout).on("generate_question", question)
            selected = FallbackAiProvider(primary, selected)
            if os.environ["PR170_JUDGE_ROUTE"] == "live":
                live = selected
            else:
                fake = selected
    elif scenario == "concurrent":
        fake = _ConcurrentFixture().script("judge_sufficiency", FakeOutcome.ok(_response()),
                                           FakeOutcome.predicates())
        fake.judge_thresholds = THRESHOLDS
    elif scenario == "retrieval":
        fake.on("generate_question", lambda _: {"question": LONG_QUESTION, "guidance": None, "examples": []})

        def summary(data):
            return {"summary": "점주가 설명한 동작을 정리했어요.", "structure": {
                "shifts": [], "missing_information": [], "sections": [{
                    "ref": "new-1", "category": "COMMON_TASK", "shift_ref": None, "title": "커피 작업",
                    "steps": [{"ref": f"new-{i + 2}", "instruction": chunk["text"],
                               "checklist_item": False, "evidence_ids": [chunk["id"]]}
                              for i, chunk in enumerate(data["evidence"])],
                }],
            }}
        fake.on("summarize_intent", summary)
    else:
        raise ValueError("unknown PR170 scenario")
    ops = ("judge_sufficiency",) if os.environ["PR170_JUDGE_ROUTE"] == "live" else ()
    set_ai_provider(RoutedAiProvider(fake, live, ops, CallBudget(20, None)))
    from app.main import app

    return app


@pytest.fixture
def runtime(real_db, tmp_path):
    @contextmanager
    def start(scenario, backend="responses", route="fake", fallback=False):
        env, origin, port, _password = local_env()
        env.update(BACKGROUND_JOBS="on", TASK_RUNNER_MODE="background", TASK_RUNNER_WORKERS="2",
                   TASK_RUNNER_POLL_SECONDS="0.02", AI_PROVIDER="fake",
                   PR170_JUDGE_SCENARIO=scenario, PR170_JUDGE_BACKEND=backend, PR170_JUDGE_ROUTE=route,
                   PR170_JUDGE_FALLBACK="1" if fallback else "0")
        with (tmp_path / "pr170-uvicorn.log").open("w+") as log:
            process = subprocess.Popen([
                sys.executable, "-m", "uvicorn", "e2e.test_pr170_judgement_metadata_http:create_app",
                "--factory", "--host", "127.0.0.1", "--port", str(port)],
                env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                with httpx.Client(base_url=origin, timeout=3, trust_env=False) as client:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            log.seek(0)
                            pytest.fail(log.read())
                        try:
                            if client.get("/api/health").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.05)
                    else:
                        pytest.fail("PR170 real API did not become healthy")
                yield origin
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return start


class HttpInterview:
    def __init__(self, engine, origin):
        self.engine = engine
        with Session(engine) as db:
            ensure_question_set(db)
            owner = make_user(db, "OWNER", google_sub=f"pr170-{uuid.uuid4()}",
                              google_email=f"{uuid.uuid4()}@e2e.test")
            store = make_store(db, owner, approval_status="APPROVED", approved_at=NOW,
                               business_registration_number=f"{uuid.uuid4().int % 10**10:010d}")
            token = auth.create_session(owner.id, db=db).token
            db.commit()
            self.store_id = store.id
        self.client = httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=5, trust_env=False)
        self.client.cookies.set(auth.SESSION_COOKIE_NAME, token)
        self.csrf = self.call("GET", "/api/auth/csrf")["csrfToken"]
        self.base = f"/api/stores/{self.store_id}/manual/interviews"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.client.close()

    def call(self, method, path, *, body=None, status=200):
        headers = {} if method == "GET" else {"X-CSRF-Token": self.csrf, "Idempotency-Key": str(uuid.uuid4())}
        response = self.client.request(method, path, json=body, headers=headers)
        validate_response(response)
        assert response.status_code == status, response.text
        return response.json()

    def get(self, sid):
        return self.call("GET", f"{self.base}/{sid}")

    def wait(self, sid, predicate):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = self.get(sid)
            if predicate(state):
                return state
            time.sleep(0.02)
        pytest.fail(f"interview did not reach expected state: {state}")

    def start(self):
        sid = self.call("POST", self.base, body={}, status=201)["id"]
        state = self.wait(sid, lambda s: s["questions"] and s["processing"] is None)
        return sid, state

    def answer(self, sid, state, answer):
        return self.call("POST", f"{self.base}/{sid}/answers", status=202, body={
            "expectedRevision": state["revision"], "questionId": state["questions"][0]["id"],
            "input": {"method": "TEXT", "text": answer}})

    def rows(self, model, sid):
        with Session(self.engine) as db:
            return list(db.scalars(select(model).where(model.session_id == sid)))

    def probe_aspects(self, sid, depth):
        with Session(self.engine) as db:
            tasks = list(db.scalars(select(BackgroundTask).where(
                BackgroundTask.subject_id == sid, BackgroundTask.kind == "FOLLOWUP_GENERATION")))
            [task] = [t for t in tasks if t.payload["depth"] == depth]
            return task.payload["request"]["missing_aspects"]

    def review(self, sid, intent):
        path = f"{self.base}/{sid}/intents/{intent}/review"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            review = self.call("GET", path)
            if review["status"] == "READY":
                return review
            assert review["status"] != "ERROR", review
            time.sleep(0.02)
        pytest.fail("review did not finish")


@pytest.mark.parametrize("route", ["fake", "live"])
@pytest.mark.parametrize("backend", ["decisions", "responses"])
def test_failed_evaluation_metadata_and_retry_are_committed(real_db, runtime, route, backend):
    with runtime("failed", backend, route) as origin, HttpInterview(real_db, origin) as flow:
        sid, initial = flow.start()
        question = initial["questions"][0]
        flow.answer(sid, initial, "오픈조는 09:00부터 18:00까지 근무해요.")
        failed = flow.wait(sid, lambda s: s["status"] == "ERROR")
        assert failed["error"]["code"] == "AI_PROCESSING_FAILED"
        assert failed["questions"] == []
        assert failed["lastAnsweredQuestion"]["id"] == question["id"]
        before = flow.rows(InterviewTurn, sid)
        [answer] = [r for r in before if r.turn_kind == "ANSWER"]
        assert len(before) == 2 and answer.content == "오픈조는 09:00부터 18:00까지 근무해요."
        [row] = flow.rows(InterviewEvaluation, sid)
        provider, model = ("openai-fixture", "fixture-live-model") if route == "live" else ("fake", "fake-llm")
        assert (row.status, row.error_code, row.provider) == ("FAILED", "REFUSED", provider)
        assert row.evaluation_config_version.startswith(f"{provider}:{model}:{PROMPT_VERSION}:{backend}:")
        assert THRESHOLDS.tag in row.evaluation_config_version
        if backend == "responses" and route == "live":
            assert row.evaluation_config_version.endswith(":effort=high")
        assert row.applied_at is None and row.probability is None
        assert flow.rows(InterviewIntentReview, sid) == [] and flow.rows(InterviewProbeBatch, sid) == []
        [work] = [r for r in flow.rows(InterviewSessionIntent, sid) if r.intent_id == question["intentId"]]
        assert (work.coverage_status, work.depth) == ("PENDING", 0)
        # Re-fetch the error and the preserved answer through public API before retrying.
        assert flow.get(sid)["status"] == "ERROR"
        turns = flow.call("GET", f"{flow.base}/{sid}/turns")["items"]
        assert len(turns) == 2
        flow.call("POST", f"{flow.base}/{sid}/retries", body={"expectedRevision": failed["revision"]}, status=202)
        flow.wait(sid, lambda s: s["currentIntentId"] != question["intentId"] and s["processing"] is None)
        rows = sorted(flow.rows(InterviewEvaluation, sid), key=lambda r: r.attempt_no)
        assert [(r.status, r.attempt_no) for r in rows] == [("FAILED", 1), ("SUCCEEDED", 2)]
        assert rows[0].evaluation_config_version == row.evaluation_config_version
        assert rows[1].provider == provider and THRESHOLDS.tag in rows[1].evaluation_config_version
        assert len([r for r in flow.rows(InterviewTurn, sid) if r.turn_kind == "ANSWER"]) == 1


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("scenario", ["confirmation", "reversal", "uncertain"])
def test_no_staff_work_requires_real_confirmation_before_coverage(real_db, runtime, fallback, scenario):
    with runtime(scenario, fallback=fallback) as origin, HttpInterview(real_db, origin) as flow:
        sid, initial = flow.start()
        work_id = initial["currentIntentId"]
        flow.answer(sid, initial, "매장 운영은 전부 자동이고 사람이 일하는 근무 자체가 없어요.")
        probe = flow.wait(sid, lambda s: s["processing"] is None and s["questions"] and
                          s["questions"][0]["depth"] == 1)
        assert probe["currentIntentId"] == work_id
        [evaluation] = flow.rows(InterviewEvaluation, sid)
        assert evaluation.status == "SUCCEEDED" and evaluation.needs_follow_up
        [batch] = flow.rows(InterviewProbeBatch, sid)
        assert batch.status == "READY" and batch.depth == 1
        assert flow.probe_aspects(sid, 1) == ["근무가 없는 매장인지의 재확인"]
        assert probe["questions"][0]["text"] == confirmation_question("근무가 없는 매장인지의 재확인")
        assert flow.rows(InterviewIntentReview, sid) == []
        answer = {"confirmation": "네, 그 설명이 정확해요.", "reversal": "주말에는 직원이 와요.",
                  "uncertain": "정확히는 잘 모르겠어요."}[scenario]
        flow.answer(sid, probe, answer)
        if scenario == "confirmation":
            flow.wait(sid, lambda s: s["processing"] is None and s["currentIntentId"] != work_id)
            review = flow.review(sid, work_id)
            assert all(review["content"][field] == [] for field in ("shifts", "sections", "missingInformation"))
            [stored] = flow.rows(InterviewIntentReview, sid)
            assert stored.status == "READY" and stored.ready_content == review["content"]
            [work] = [r for r in flow.rows(InterviewSessionIntent, sid) if r.intent_id == work_id]
            assert work.coverage_status == "COVERED"
        else:
            state = flow.wait(sid, lambda s: s["processing"] is None and s["questions"] and
                              s["questions"][0]["depth"] == 2)
            assert state["currentIntentId"] == work_id and flow.rows(InterviewIntentReview, sid) == []
            [latest] = [r for r in flow.rows(InterviewProbeBatch, sid) if r.depth == 2]
            assert latest.status == "READY"
            expected = "근무조의 구성" if scenario == "reversal" else "근무가 없는 매장인지의 재확인"
            assert flow.probe_aspects(sid, 2) == [expected]
        evaluations = sorted(flow.rows(InterviewEvaluation, sid), key=lambda r: r.depth)
        assert len(evaluations) == 2 and all(r.status == "SUCCEEDED" for r in evaluations)
        turns = flow.call("GET", f"{flow.base}/{sid}/turns")["items"]
        assert len([r for r in flow.rows(InterviewTurn, sid) if r.turn_kind == "ANSWER"]) == 2
        assert len(turns) == 5


def test_two_workers_apply_mixed_backend_results_once(real_db, runtime):
    with (runtime("concurrent") as origin, HttpInterview(real_db, origin) as first,
          HttpInterview(real_db, origin) as second):
        a, state_a = first.start()
        b, state_b = second.start()
        first.answer(a, state_a, "오픈조는 09:00부터 18:00까지 근무해요.")
        second.answer(b, state_b, "마감조는 18:00부터 23:00까지 근무해요.")
        configs = []
        for flow, sid, initial in ((first, a, state_a), (second, b, state_b)):
            state = flow.wait(sid, lambda s, intent=initial["currentIntentId"]: s["processing"] is None and
                              s["currentIntentId"] != intent)
            assert state["status"] == "IN_PROGRESS"
            [row] = flow.rows(InterviewEvaluation, sid)
            assert row.status == "SUCCEEDED" and row.applied_at is not None
            assert row.attempt_no == 1 and not row.needs_follow_up
            configs.append(row.evaluation_config_version)
            assert len([r for r in flow.rows(InterviewTurn, sid) if r.turn_kind == "ANSWER"]) == 1
            assert flow.call("GET", f"{flow.base}/{sid}/turns")["items"]
        assert sum(":responses:" in config for config in configs) == 1
        assert sum(":decisions:" in config for config in configs) == 1


def test_installed_demo_handler_failure_records_its_actual_responses_backend(real_db, runtime):
    with runtime("installed", backend="decisions") as origin, HttpInterview(real_db, origin) as flow:
        sid, initial = flow.start()
        answer_text = f"{FAIL_ONCE} 오픈조는 09:00부터 18:00까지 근무해요."
        flow.answer(sid, initial, answer_text)
        failed = flow.wait(sid, lambda s: s["status"] == "ERROR")
        assert failed["lastAnsweredQuestion"]["id"] == initial["questions"][0]["id"]
        [row] = flow.rows(InterviewEvaluation, sid)
        expected = f"fake:fake-llm:{PROMPT_VERSION}:responses:{THRESHOLDS.tag}"
        assert (row.status, row.error_code, row.provider, row.evaluation_config_version) == (
            "FAILED", "REFUSED", "fake", expected)
        assert row.probability is None and row.applied_at is None
        assert flow.rows(InterviewIntentReview, sid) == [] and flow.rows(InterviewProbeBatch, sid) == []
        [answer] = [r for r in flow.rows(InterviewTurn, sid) if r.turn_kind == "ANSWER"]
        assert answer.content == answer_text
        assert flow.get(sid)["status"] == "ERROR"
        assert len(flow.call("GET", f"{flow.base}/{sid}/turns")["items"]) == 2
        flow.call("POST", f"{flow.base}/{sid}/retries", body={"expectedRevision": failed["revision"]}, status=202)
        flow.wait(sid, lambda s: s["processing"] is None and s["currentIntentId"] != initial["currentIntentId"])
        rows = sorted(flow.rows(InterviewEvaluation, sid), key=lambda r: r.attempt_no)
        assert [(r.status, r.evaluation_config_version) for r in rows] == [
            ("FAILED", expected), ("SUCCEEDED", expected)]
        assert len([r for r in flow.rows(InterviewTurn, sid) if r.turn_kind == "ANSWER"]) == 1


def test_long_question_does_not_discard_same_turn_evidence_from_persisted_review(real_db, runtime):
    with runtime("retrieval") as origin, HttpInterview(real_db, origin) as flow:
        sid, initial = flow.start()
        work_id = initial["currentIntentId"]
        flow.answer(sid, initial, " ".join(SENTENCES))
        flow.wait(sid, lambda s: s["processing"] is None and s["currentIntentId"] != work_id)
        review = flow.review(sid, work_id)
        [stored] = flow.rows(InterviewIntentReview, sid)
        assert stored.ready_content == review["content"]
        steps = review["content"]["sections"][0]["steps"]
        assert [s["instruction"] for s in steps] == SENTENCES
        [evaluation] = flow.rows(InterviewEvaluation, sid)
        assert evaluation.status == "SUCCEEDED" and not evaluation.needs_follow_up
        assert flow.call("GET", f"{flow.base}/{sid}/turns")["items"]

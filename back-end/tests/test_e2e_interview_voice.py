"""Opt-in voice evaluation: real API/committed DB, only external AI responses are fake.

SQLite/MySQL API tests here complement the real socket/MySQL whole runner in
test_e2e_interview_eval. No say, paid key or provider implementation replacement is required.
"""
import hashlib
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.ai.errors import AiErrorCode
from app.ai.fake import FakeOutcome
from app.db.models import (
    InterviewEvaluation,
    InterviewIntentReview,
    InterviewSession,
    InterviewTurn,
    ManualStep,
    MediaTranscription,
)
from app.media.storage import LocalMediaStorage, set_media_storage
from e2e import ai_scenario, interview_eval, interview_report, owner_persona
from e2e.demo_scenario import StepFailed
from tests import media_samples
from tests.test_interview_api import build_ctx


class ApiOwner:
    def __init__(self, driver):
        self.driver = driver

    def call(self, method, path, *, expect, **kwargs):
        if method != "GET":
            kwargs["headers"] = self.driver.auth.headers(kwargs.pop("key", None))
        response = self.driver.api.request(method, path, **kwargs)
        assert response.status_code == expect, response.text
        return response


@pytest.fixture
def voice_case(api, db_engine, fake_ai, tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    interview_eval.install_voice_fake(owner_persona.install_fake(ai_scenario.install(fake_ai)))
    driver = build_ctx(api, db_engine)
    sid = driver.started()
    for key, depth in [("WORK_STRUCTURE", 0), ("WORK_STRUCTURE", 1), ("COMMON_TASKS", 0)]:
        driver.answer_and_run(sid, owner_persona.answer(key, depth))
    state = driver.get(sid)
    [question] = state["questions"]
    assert question["intentId"] == driver.intents[1] and question["depth"] == 1
    scenario = interview_eval.InterviewEval("http://localhost", "http://localhost", "pw", voice=True)
    scenario.read_engine = create_engine(db_engine.url, poolclass=NullPool, hide_parameters=True)
    scenario.media_root = str(tmp_path / "media")
    scenario.audio_path = tmp_path / "report.voice.m4a"
    scenario.state.update(owner=ApiOwner(driver), store_id=driver.store, interview=sid)
    yield SimpleNamespace(driver=driver, scenario=scenario, question=question, sid=sid, fake=fake_ai)
    scenario.read_engine.dispose()
    set_media_storage(None)


def stored_interview(case):
    with Session(case.driver.engine) as db:
        session = db.get(InterviewSession, case.sid)
        turns = list(db.scalars(select(InterviewTurn).where(InterviewTurn.session_id == case.sid)
                                .order_by(InterviewTurn.turn_no)))
        evaluations = list(db.scalars(select(InterviewEvaluation).where(InterviewEvaluation.session_id == case.sid)))
        return (session.revision, session.current_intent_id, session.processing_kind,
                [(t.id, t.content, t.input_method, t.transcription_id) for t in turns],
                [e.id for e in evaluations])


def upload_and_transcribe(case, *, outcome=None):
    scenario = case.scenario
    data = media_samples.wav_seconds(3)
    media = scenario.upload_manual(data, "INTERVIEW_AUDIO", "speech.wav", "audio/wav").json()
    if outcome is not None:
        # Repeat the malformed result through retries; exhaustion must never yield a READY row.
        case.fake.script("transcribe", *([outcome] * 5))
    started = scenario.owner_write("POST", "/transcriptions", {"mediaId": media["id"]}, 202).json()
    case.driver.run()
    ready = scenario.state["owner"].call("GET", scenario.manual_url(f"/transcriptions/{started['id']}"),
                                          expect=200).json()
    scenario.record["voice"] = {"media": media, "transcription": ready, "question": case.question,
                                "audioBytes": len(data), "audioSha256": hashlib.sha256(data).hexdigest()}
    return ready


@pytest.mark.parametrize("outcome", [FakeOutcome.ok(""), FakeOutcome.ok(" \n\t"), FakeOutcome.ok(None),
                                     FakeOutcome.ok({"text": "malformed"}),
                                     FakeOutcome.fail(AiErrorCode.REFUSED)],
                         ids=["empty", "whitespace", "null", "malformed", "provider-failure"])
def test_bad_stt_cannot_be_submitted_and_leaves_no_partial_answer(voice_case, outcome):
    case = voice_case
    ready = upload_and_transcribe(case, outcome=outcome)
    assert ready["status"] == "ERROR" and ready["text"] is None
    assert ready["error"]["code"] == "TRANSCRIPTION_FAILED"
    before = stored_interview(case)
    response = case.driver.answer(case.sid, data={"method": "VOICE", "transcriptionId": ready["id"]})
    assert (response.status_code, response.json()["code"]) == (409, "TRANSCRIPTION_NOT_READY")
    assert stored_interview(case) == before
    state = case.driver.get(case.sid)
    assert state["phase"] == "COLLECTING" and state["questions"][0]["id"] == case.question["id"]
    with Session(case.driver.engine) as db:
        row = db.get(MediaTranscription, ready["id"])
        assert row.status == "ERROR" and row.text is None and row.completed_at is not None


def test_malformed_voice_id_is_rejected_without_scheduling_an_answer(voice_case):
    case = voice_case
    before = stored_interview(case)
    response = case.driver.answer(case.sid, data={"method": "VOICE", "transcriptionId": "not-a-uuid"})
    assert response.status_code == 422
    assert stored_interview(case) == before
    assert case.driver.get(case.sid)["phase"] == "COLLECTING"


@pytest.mark.parametrize("broken", [None, "content", "method"])
def test_independent_voice_verifier_detects_persisted_corruption(voice_case, broken):
    case = voice_case
    ready = upload_and_transcribe(case)
    assert ready["status"] == "READY" and ready["text"] == interview_eval.VOICE_TEXT
    response = case.driver.answer(case.sid, data={"method": "VOICE", "transcriptionId": ready["id"]})
    assert response.status_code == 202
    case.scenario._verify_voice_answer(case.question)
    case.driver.run()
    case.scenario._verify_voice_answer(case.question)
    assert any(t["inputMethod"] == "VOICE" and t["content"] == ready["text"]
               for t in case.scenario.record["sourceDialogue"])
    if broken:
        with Session(case.driver.engine) as db:
            row = db.scalars(select(InterviewTurn).where(
                InterviewTurn.reply_to_question_turn_id == case.question["id"])).one()
            if broken == "content":
                row.content = "전사와 다른 내용을 저장했어요."
            else:
                row.input_method, row.transcription_id = "TEXT", None
            db.commit()
        with pytest.raises(StepFailed, match="committed VOICE answer"):
            case.scenario._verify_voice_answer(case.question)


def test_ready_transcript_is_reused_and_never_automatically_answers(voice_case):
    case = voice_case
    before = stored_interview(case)
    ready = upload_and_transcribe(case)
    assert stored_interview(case) == before  # STT alone does not submit an answer.
    again = case.scenario.owner_write("POST", "/transcriptions", {"mediaId": ready["mediaId"]}, 200).json()
    assert again == ready
    assert len(case.fake.calls_for("transcribe")) == 1


def test_fake_voice_records_and_submits_without_macos_say(voice_case, monkeypatch):
    case = voice_case
    original_poll = case.scenario.poll

    def poll_after_tasks(*args, **kwargs):
        case.driver.run()
        return original_poll(*args, **kwargs)

    def refuse_speech(_text):
        pytest.fail("fake voice evaluation must never require macOS say")

    monkeypatch.setattr(case.scenario, "poll", poll_after_tasks)
    monkeypatch.setattr("e2e.manual_scenario.speech", refuse_speech)
    ready = case.scenario._record_voice(case.question)
    assert ready["status"] == "READY" and ready["text"] == interview_eval.VOICE_TEXT
    assert case.driver.answer(case.sid, data={"method": "VOICE", "transcriptionId": ready["id"]}).status_code == 202
    case.scenario._verify_voice_answer(case.question)
    assert case.scenario.audio_path.with_suffix(".wav").read_bytes() == media_samples.wav_seconds(3)


@pytest.mark.parametrize("skip", [False, True])
def test_committed_reviews_and_draft_keep_voice_and_correction_and_detect_changed_rows(voice_case, skip):
    case = voice_case
    ready = upload_and_transcribe(case)
    assert case.driver.answer(case.sid, data={"method": "VOICE", "transcriptionId": ready["id"]}).status_code == 202
    case.scenario._verify_voice_answer(case.question)
    case.driver.run()
    keys = {i["id"]: i["key"] for i in case.driver.get(case.sid)["intents"]}
    for _ in range(36):
        state = case.driver.get(case.sid)
        if state["phase"] == "READY_TO_GENERATE":
            break
        assert state["phase"] == "COLLECTING"
        [question] = state["questions"]
        case.driver.answer_and_run(case.sid, owner_persona.answer(keys[question["intentId"]], question["depth"],
                                                                 skip_depth_five=skip))
    else:
        pytest.fail("the fake interview did not complete")
    case.scenario.record["reviews"] = {keys[r["intentId"]]: r for r in case.driver.reviews(case.sid)["items"]}
    for key, review in case.scenario.record["reviews"].items():
        case.scenario._verify_review(key, review)
    work = case.scenario.record["reviews"]["WORK_STRUCTURE"]
    assert work["content"]["shifts"][1]["endTime"] == "22:30"
    url = case.driver.review_url(case.sid, work["intentId"], "corrections")
    assert case.driver.post(url, {"expectedRevision": work["revision"],
                                 "input": {"method": "TEXT", "text": owner_persona.REVIEW_CORRECTION}}).status_code == 202
    case.driver.run()
    work = case.driver.review(case.sid, work["intentId"]).json()
    case.scenario.record["reviews"]["WORK_STRUCTURE"] = work
    case.scenario._verify_review("WORK_STRUCTURE", work)
    case.scenario._check_closing_shift(work["content"]["shifts"])
    assert case.driver.complete(case.sid).status_code == 202
    case.driver.run()
    draft = case.scenario.draft()
    case.scenario._verify_draft(draft)
    case.scenario._check_closing_shift(draft["content"]["shifts"])
    interview_eval.check_voice_actions(draft["content"], ready["text"])
    assert not case.scenario.record["reviews"]["RULES"]["content"]["sections"]
    exceptions = case.scenario.record["reviews"]["EXCEPTIONS"]["content"]
    if skip:
        assert exceptions["sections"] and not exceptions["needsDetail"]
    else:
        assert not exceptions["sections"] and exceptions["missingInformation"] and exceptions["needsDetail"]
    case.scenario._verify_voice_answer(case.question)
    assert case.scenario.record["sourceDialogueComplete"] is True
    with Session(case.driver.engine) as db:
        step = db.get(ManualStep, draft["content"]["sections"][0]["steps"][0]["id"])
        step.instruction = "DB에 잘못 저장된 단계"
        db.commit()
    with pytest.raises(StepFailed, match="normalized draft steps"):
        case.scenario._verify_draft(draft)
    with Session(case.driver.engine) as db:
        row = db.get(InterviewIntentReview, (case.sid, work["intentId"]))
        row.ready_content = {**row.ready_content, "summary": "DB에 잘못 저장된 검토"}
        db.commit()
    with pytest.raises(StepFailed, match="committed READY review"):
        case.scenario._verify_review("WORK_STRUCTURE", work)


def test_voice_report_keeps_actual_transcript_and_answer_separately_from_script():
    record = {"voice_enabled": True, "voice": {
        "speechText": "발화 스크립트", "transcription": {"text": "인식된 실제 원문", "status": "READY", "id": "t"},
        "answer": {"content": "저장된 실제 답변", "inputMethod": "VOICE", "id": "a"}}}
    rendered = interview_report.render(record)
    assert all(text in rendered for text in ("발화 스크립트", "인식된 실제 원문", "저장된 실제 답변", "PENDING"))


def test_action_checks_allow_paraphrase_but_require_voice_steps_and_reject_new_quantities():
    section = {"category": "COMMON_TASK", "steps": [{"instruction":
        "POS에서 주문 메뉴 선택 후 결제한다. 레시피 카드를 따른다. 픽업대에서 주문번호를 안내한다. "
        "테이블의 컵 및 쓰레기를 제거하고 행주로 닦는다."}]}
    content = {"sections": [section]}
    assert interview_eval.check_voice_actions(content, interview_eval.VOICE_TEXT)["semanticAudit"] == "PENDING"
    with pytest.raises(StepFailed, match="voice actions missing"):
        interview_eval.check_voice_actions({"sections": []}, interview_eval.VOICE_TEXT)
    invented = deepcopy(content)
    invented["sections"][0]["steps"].append({"instruction": "음료에 시럽을 20ml 넣는다."})
    with pytest.raises(StepFailed, match="introduced quantities"):
        interview_eval.check_voice_actions(invented, interview_eval.VOICE_TEXT)


def test_voice_live_cannot_hide_structured_writing_behind_fake(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan_e2e_test")
    monkeypatch.setenv("JIDAN_E2E_OPENAI", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-never-used")
    assert interview_eval.main(["--ai", "live", "--voice", "--ai-live-ops", "transcribe"]) == 2
    assert "every interview operation" in capsys.readouterr().err


@pytest.mark.parametrize("keys,expected", [
    ("OPENAI_KEY='sk-alias'", "sk-alias"),
    ("OPENAI_API_KEY=sk-canonical\nOPENAI_KEY=sk-alias", "sk-canonical"),
    ("OPENAI_KEY=sk-alias\nOPENAI_API_KEY=sk-canonical", "sk-canonical"),
])
def test_voice_env_file_alias_loads_only_key_and_keeps_model_settings(monkeypatch, tmp_path, capsys, keys, expected):
    monkeypatch.setenv("OPENAI_API_KEY", "old")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "60")
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    path = tmp_path / "api.env"
    path.write_text(keys + "\nOPENAI_MODEL=unrequested-model\nOPENAI_TIMEOUT_SECONDS=1\nDB_PASSWORD=secret\n",
                    encoding="utf-8")
    assert interview_eval.load_env_file(str(path), key_only=True) == ["OPENAI_API_KEY"]
    assert os.environ["OPENAI_API_KEY"] == expected and "OPENAI_KEY" not in os.environ
    assert os.environ["OPENAI_MODEL"] == "gpt-6-luna" and os.environ["OPENAI_TIMEOUT_SECONDS"] == "60"
    out = capsys.readouterr()
    assert "sk-" not in out.out + out.err

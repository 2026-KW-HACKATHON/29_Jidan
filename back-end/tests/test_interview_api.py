"""#120 owner AI interview: start, question progress, answers, Jev failures and retries.

Every response is checked against openapi.yaml by the `api` client; every test runs on SQLite
and MySQL. AI calls go to the scripted FakeAiProvider and run in-thread through `drain()`.
"""

import logging
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    InterviewEvaluation,
    InterviewProbeBatch,
    InterviewSession,
    InterviewSessionIntent,
    InterviewTurn,
    InterviewTurnPhoto,
    ManualDraftCorrection,
    ManualMedia,
    ManualVersion,
    MediaTranscription,
    StoreManual,
)
from app.interview.question_set import INTENTS_V1
from app.media.storage import LocalMediaStorage, set_media_storage
from app.tasks import enqueue
from tests.api_contract import login
from tests.factories import NOW, make_store, make_user, make_worker
from tests.interview_factories import InterviewDriver, ensure_question_set

INSUFFICIENT = {"sufficient": False, "probability": 0.2, "missing_aspects": ["마감 순서", "청소 기준"]}
SUFFICIENT = {"sufficient": True, "probability": 0.9, "missing_aspects": []}


@pytest.fixture
def media_root(tmp_path):
    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    yield storage
    set_media_storage(None)


@pytest.fixture
def ctx(api, db_engine, media_root):
    return build_ctx(api, db_engine)


def build_ctx(api, db_engine) -> InterviewDriver:
    with Session(db_engine) as db:
        intents = ensure_question_set(db)
        owner = make_user(db, "OWNER")
        store = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW, name="월계 카페")
        second = make_store(db, owner=owner, approval_status="APPROVED", approved_at=NOW, name="둘째 가게",
                            industry="RESTAURANT")
        pending = make_store(db, owner=owner)
        other_owner = make_user(db, "OWNER")
        other_store = make_store(db, owner=other_owner, approval_status="APPROVED", approved_at=NOW)
        db.commit()
        ids = {"owner": owner.id, "store": store.id, "second": second.id, "pending": pending.id,
               "other_owner": other_owner.id, "other_store": other_store.id,
               "intents": [i.id for i in intents]}
    driver = InterviewDriver(api, login(api, ids["owner"]), db_engine, ids["store"])
    driver.__dict__.update(ids)
    return driver


def code(response) -> str:
    return response.json()["code"]


def rows(ctx, model, *where):
    with Session(ctx.engine) as db:
        return list(db.scalars(select(model).where(*where)))


def count(ctx, model, *where) -> int:
    with Session(ctx.engine) as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def media(ctx, kind="IMAGE", store=None) -> str:
    with Session(ctx.engine) as db:
        row = ManualMedia(store_id=store or ctx.store, uploaded_by_owner_id=ctx.owner, kind=kind,
                          object_key=f"manual/{uuid.uuid4()}", byte_size=10,
                          mime_type="image/png" if kind == "IMAGE" else "audio/wav",
                          duration_ms=None if kind == "IMAGE" else 1000, created_at=NOW,
                          expires_at=utcnow().replace(year=2099))
        db.add(row)
        db.commit()
        return row.id


# --- start ----------------------------------------------------------------------------------------


def test_start_creates_a_draft_and_a_session_with_the_fixed_question_set(ctx, fake_ai):
    response = ctx.start()
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["status"], body["phase"], body["revision"], body["questionSetVersion"]) == (
        "IN_PROGRESS", "PROCESSING", 1, 1)
    assert body["processing"]["kind"] == "INITIAL_QUESTION" and body["processing"]["attempt"] == 1
    assert [i["key"] for i in body["intents"]] == [d.key for d in INTENTS_V1]
    assert [i["stage"] for i in body["intents"]] == [
        "WORK_STRUCTURE", "COMMON_TASKS", "SHIFT_TASKS", "COMPLEMENTS", "COMPLEMENTS", "COMPLEMENTS"]
    assert all(i["coverage"] == "PENDING" and i["depth"] == 0 for i in body["intents"])
    assert body["currentIntentId"] == ctx.intents[0] and body["questions"] == []
    version = rows(ctx, ManualVersion)[0]
    assert (body["draftVersionId"], version.revision_no, version.status, version.generation_status) == (
        version.id, 1, "DRAFT", "NOT_STARTED")

    ctx.run()
    state = ctx.get(body["id"])
    assert (state["phase"], state["revision"], state["processing"]) == ("COLLECTING", 2, None)
    [question] = state["questions"]
    assert (question["kind"], question["depth"], question["batchId"], question["answered"]) == (
        "BASE", 0, None, False)
    assert question["text"] == INTENTS_V1[0].base_question  # fake: the base question as is
    [call] = fake_ai.calls_for("generate_question")
    assert call.data["kind"] == "BASE" and call.data["store"] == {"name": "월계 카페", "industry": "카페"}
    assert call.data["dialogue"] == [] and call.data["missing_aspects"] == []


def test_one_interview_per_store_and_key_replay(ctx):
    key = str(uuid.uuid4())
    first = ctx.start(key=key)
    replay = ctx.start(key=key)
    assert replay.status_code == 201 and replay.headers.get("Idempotent-Replayed") == "true"
    assert replay.json()["id"] == first.json()["id"]
    again = ctx.start()
    assert (again.status_code, code(again)) == (409, "INTERVIEW_ALREADY_EXISTS")
    other = ctx.start(key=key, store=ctx.second)  # same key on another endpoint
    assert (other.status_code, code(other)) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert ctx.start(store=ctx.second).status_code == 201  # another store has its own interview
    assert count(ctx, InterviewSession) == 2


def test_start_body_must_be_an_empty_object(ctx):
    response = ctx.post(ctx.url(), {"questionSetVersion": 1})
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")
    assert count(ctx, InterviewSession) == 0


def test_start_after_a_publication_makes_the_next_version(ctx):
    with Session(ctx.engine) as db:
        manual = StoreManual(store_id=ctx.store)
        db.add(manual)
        db.flush()
        published = ManualVersion(manual_id=manual.id, revision_no=1, created_by_owner_id=ctx.owner,
                                  status="PUBLISHED", generation_status="READY", published_at=NOW,
                                  published_by_owner_id=ctx.owner)
        db.add(published)
        db.flush()
        manual.current_published_version_id = published_id = published.id
        db.commit()
    body = ctx.start().json()
    version = rows(ctx, ManualVersion, ManualVersion.id == body["draftVersionId"])[0]
    assert version.revision_no == 2 and version.status == "DRAFT"
    assert rows(ctx, StoreManual)[0].current_published_version_id == published_id  # kept


def test_start_is_refused_while_a_draft_correction_runs(ctx):
    sid = ctx.started()
    with Session(ctx.engine) as db:
        version = db.get(ManualVersion, ctx.get(sid)["draftVersionId"])
        db.add(ManualDraftCorrection(version_id=version.id, base_revision=1, target_kind="MANUAL",
                                     input_method="TEXT", input_text="고쳐 주세요", task_id=str(uuid.uuid4()),
                                     requested_by_owner_id=ctx.owner))
        db.commit()
    response = ctx.start()
    assert (response.status_code, code(response)) == (409, "MANUAL_CORRECTION_IN_PROGRESS")


@pytest.mark.parametrize("which,status,error", [
    ("other_store", 404, "STORE_NOT_FOUND"), ("pending", 403, "STORE_APPROVAL_REQUIRED"),
    (str(uuid.uuid4()), 404, "STORE_NOT_FOUND"),
])
def test_start_store_scoping(ctx, which, status, error):
    response = ctx.start(store=getattr(ctx, which, which))
    assert (response.status_code, code(response)) == (status, error)


def test_sessions_are_scoped_to_the_store(ctx):
    sid = ctx.started()
    for url in (ctx.url(sid, store=ctx.second), ctx.url(str(uuid.uuid4())),
                ctx.url(sid, "turns", store=ctx.second), ctx.url(sid, "reviews", store=ctx.second)):
        response = ctx.api.get(url)
        assert (response.status_code, code(response)) == (404, "MANUAL_RESOURCE_NOT_FOUND"), url
    response = ctx.api.get(ctx.url(sid, store=ctx.other_store))
    assert (response.status_code, code(response)) == (404, "STORE_NOT_FOUND")
    other = login(ctx.api, ctx.other_owner)
    response = ctx.api.get(ctx.url(sid, store=ctx.store))
    assert (response.status_code, code(response)) == (404, "STORE_NOT_FOUND")
    answer = ctx.post(ctx.url(sid, "answers", store=ctx.other_store),
                      {"expectedRevision": 2, "questionId": str(uuid.uuid4()),
                       "input": {"method": "TEXT", "text": "a"}}, auth=other)
    assert (answer.status_code, code(answer)) == (404, "MANUAL_RESOURCE_NOT_FOUND")


def test_workers_cannot_read_interviews(ctx):
    sid = ctx.started()
    with Session(ctx.engine) as db:
        worker = make_worker(db)
        db.commit()
        worker_id = worker.id
    ctx.api.cookies.clear()
    login(ctx.api, worker_id)
    response = ctx.api.get(ctx.url(sid))
    assert (response.status_code, code(response)) == (403, "FORBIDDEN")


# --- progress ---------------------------------------------------------------------------------


def test_sufficient_answers_move_through_every_intent_without_confirmation(ctx, fake_ai):
    sid = ctx.started()
    for index, intent_id in enumerate(ctx.intents):
        state = ctx.get(sid)
        assert state["currentIntentId"] == intent_id and state["questions"][0]["kind"] == "BASE"
        before = state["revision"]
        answered = ctx.answer(sid, f"{index}번 답변이에요.")
        assert answered.status_code == 202, answered.text
        body = answered.json()
        assert (body["phase"], body["revision"], body["processing"]["kind"]) == (
            "PROCESSING", before + 1, "EVALUATION")
        assert body["questions"] == []
        ctx.run()
    state = ctx.get(sid)
    assert (state["phase"], state["currentIntentId"], state["processing"]) == ("READY_TO_GENERATE", None, None)
    assert all(i["coverage"] == "COVERED" and i["depth"] == 0 and i["finishedAt"] for i in state["intents"])
    assert len(fake_ai.calls_for("judge_sufficiency")) == 6
    # Each finished intent got its own review; none was confirmed and nothing waited for it.
    listing = ctx.reviews(sid)
    assert [r["intentId"] for r in listing["items"]] == ctx.intents
    assert all(r["status"] == "READY" and r["confirmedAt"] is None for r in listing["items"])
    assert listing["sessionRevision"] == state["revision"]


def test_insufficient_answers_probe_up_to_depth_five_then_needs_detail(ctx, fake_ai):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(INSUFFICIENT)] * 6)
    for depth in range(6):
        state = ctx.get(sid)
        [question] = state["questions"]
        assert question["depth"] == depth and state["intents"][0]["depth"] == depth
        assert question["kind"] == ("BASE" if depth == 0 else "PROBE")
        assert (question["batchId"] is None) == (depth == 0)
        if depth:
            assert question["text"] == "마감 순서에 대해 조금 더 자세히 알려 주세요."  # fake PROBE wording
        ctx.answer_and_run(sid, f"depth {depth} 답변")
    state = ctx.get(sid)
    first, second = state["intents"][0], state["intents"][1]
    assert (first["coverage"], first["depth"]) == ("NEEDS_DETAIL", 5) and first["finishedAt"]
    assert (state["phase"], state["currentIntentId"]) == ("COLLECTING", second["id"])  # moved on
    assert state["questions"][0]["kind"] == "BASE" and second["coverage"] == "PENDING"
    # No depth 6: five probe batches, one applied evaluation per depth, all READY.
    batches = rows(ctx, InterviewProbeBatch, InterviewProbeBatch.session_id == sid)
    assert sorted(b.depth for b in batches) == [1, 2, 3, 4, 5] and {b.status for b in batches} == {"READY"}
    evaluations = rows(ctx, InterviewEvaluation, InterviewEvaluation.session_id == sid)
    assert sorted(e.depth for e in evaluations) == [0, 1, 2, 3, 4, 5]
    assert all(e.applied_at and e.needs_follow_up and e.status == "SUCCEEDED" for e in evaluations)
    assert all(e.missing_aspects == INSUFFICIENT["missing_aspects"] for e in evaluations)
    assert fake_ai.calls_for("summarize_intent")[0].data["missing_aspects"] == INSUFFICIENT["missing_aspects"]
    # Jev saw the whole dialogue of the intent, BASE first.
    last = fake_ai.calls_for("judge_sufficiency")[5].data
    assert [t["depth"] for t in last["dialogue"]] == [0, 1, 2, 3, 4, 5] and "depth" not in last
    probe = fake_ai.calls_for("generate_question")[-2].data  # depth-5 probe (before the next BASE)
    assert probe["kind"] == "PROBE" and probe["missing_aspects"] == INSUFFICIENT["missing_aspects"]
    progress = rows(ctx, InterviewSessionIntent, InterviewSessionIntent.intent_id == ctx.intents[0])[0]
    assert progress.covered_at is None and progress.coverage_note == "마감 순서\n청소 기준"
    review = ctx.review(sid, ctx.intents[0]).json()
    assert review["content"]["needsDetail"] is True


def test_sufficient_at_a_probe_depth_covers_the_intent(ctx, fake_ai):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT), FakeOutcome.ok(INSUFFICIENT),
                   FakeOutcome.ok(SUFFICIENT))
    for _ in range(3):
        ctx.answer_and_run(sid)
    intent = ctx.get(sid)["intents"][0]
    assert (intent["coverage"], intent["depth"]) == ("COVERED", 2)


def test_turns_list_questions_answers_in_order_with_paging(ctx, fake_ai):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    photo = media(ctx)
    ctx.answer(sid, "첫 답변", photo_ids=[photo])
    ctx.run()
    ctx.answer_and_run(sid, "추가 답변")
    page = ctx.api.get(ctx.url(sid, "turns"), params={"size": 2}).json()
    assert (page["totalItems"], page["totalPages"], page["page"], page["size"]) == (5, 3, 0, 2)
    everything = ctx.api.get(ctx.url(sid, "turns")).json()["items"]
    assert [t["sequence"] for t in everything] == [1, 2, 3, 4, 5]
    assert [(t["kind"], t["questionKind"]) for t in everything] == [
        ("QUESTION", "BASE"), ("ANSWER", None), ("QUESTION", "PROBE"), ("ANSWER", None), ("QUESTION", "BASE")]
    assert everything[1]["replyToQuestionId"] == everything[0]["id"] and everything[1]["photoIds"] == [photo]
    assert everything[3]["batchId"] == everything[2]["batchId"] is not None and everything[3]["depth"] == 1
    beyond = ctx.api.get(ctx.url(sid, "turns"), params={"page": 9}).json()
    assert beyond["items"] == [] and beyond["totalItems"] == 5
    for params in ({"size": 0}, {"size": 101}, {"page": -1}, {"page": "x"}):
        response = ctx.api.get(ctx.url(sid, "turns"), params=params)
        assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")


# --- answer rules -----------------------------------------------------------------------------------


def test_answer_checks_question_state_and_revision(ctx):
    sid = ctx.started()
    state = ctx.get(sid)
    question, revision = state["questions"][0]["id"], state["revision"]
    unknown = ctx.answer(sid, question=str(uuid.uuid4()), revision=revision)
    assert (unknown.status_code, code(unknown)) == (409, "INTERVIEW_STATE_CONFLICT")
    stale = ctx.answer(sid, question=question, revision=revision - 1)
    assert (stale.status_code, code(stale)) == (409, "REVISION_CONFLICT")
    assert ctx.answer(sid, question=question, revision=revision).status_code == 202
    again = ctx.answer(sid, question=question, revision=revision + 1)  # another key, while PROCESSING
    assert (again.status_code, code(again)) == (409, "QUESTION_ALREADY_ANSWERED")
    ctx.run()
    # The other store's session question is not this session's.
    ctx2 = InterviewDriver(ctx.api, ctx.auth, ctx.engine, ctx.second)
    other_sid = ctx2.started()
    foreign = ctx2.get(other_sid)["questions"][0]["id"]
    response = ctx.answer(sid, question=foreign, revision=ctx.get(sid)["revision"])
    assert (response.status_code, code(response)) == (409, "INTERVIEW_STATE_CONFLICT")
    assert count(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER") == 1


def test_same_key_replays_the_answer_once(ctx):
    sid = ctx.started()
    key = str(uuid.uuid4())
    state = ctx.get(sid)
    first = ctx.answer(sid, "답변", key=key, question=state["questions"][0]["id"], revision=state["revision"])
    replay = ctx.answer(sid, "답변", key=key, question=state["questions"][0]["id"], revision=state["revision"])
    assert replay.status_code == 202 and replay.headers.get("Idempotent-Replayed") == "true"
    assert replay.json() == first.json()
    changed = ctx.answer(sid, "다른 답변", key=key, question=state["questions"][0]["id"], revision=state["revision"])
    assert (changed.status_code, code(changed)) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert count(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER") == 1
    assert count(ctx, BackgroundTask, BackgroundTask.kind == "EVALUATION") == 1


@pytest.mark.parametrize("data", [
    {"method": "TEXT", "text": ""}, {"method": "TEXT", "text": "   \n"}, {"method": "TEXT", "text": "가" * 10001},
    {"method": "TEXT"}, {"method": "AUDIO", "text": "a"}, {"method": "VOICE", "transcriptionId": "x"},
    {"method": "TEXT", "text": "a", "transcriptionId": str(uuid.uuid4())}, {"method": "TEXT", "text": 3},
])
def test_answer_input_validation(ctx, data):
    sid = ctx.started()
    response = ctx.answer(sid, data=data)
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")


def test_answer_text_at_the_length_limit_is_accepted(ctx):
    sid = ctx.started()
    assert ctx.answer(sid, "가" * 10000).status_code == 202


def test_answer_body_validation(ctx):
    sid = ctx.started()
    state = ctx.get(sid)
    base = {"expectedRevision": state["revision"], "questionId": state["questions"][0]["id"],
            "input": {"method": "TEXT", "text": "a"}}
    photo = media(ctx)
    for body in ({**base, "expectedRevision": 0}, {**base, "expectedRevision": "2"},
                 {**base, "questionId": "nope"}, {**base, "photoIds": [photo, photo]},
                 {**base, "photoIds": [media(ctx) for _ in range(21)]}, {**base, "extra": 1},
                 {k: v for k, v in base.items() if k != "input"}):
        response = ctx.post(ctx.url(sid, "answers"), body)
        assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR"), body


def test_answer_photos_must_be_this_stores_images(ctx):
    sid = ctx.started()
    foreign = ctx.answer(sid, photo_ids=[media(ctx, store=ctx.other_store)])
    assert (foreign.status_code, code(foreign)) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    audio = ctx.answer(sid, photo_ids=[media(ctx, "AUDIO")])
    assert (audio.status_code, code(audio)) == (422, "VALIDATION_ERROR")
    photos = [media(ctx), media(ctx)]
    assert ctx.answer(sid, photo_ids=photos).status_code == 202
    linked = rows(ctx, InterviewTurnPhoto)
    assert [p.media_id for p in sorted(linked, key=lambda p: p.sort_order)] == photos


def _transcription(ctx, status, store=None, text="음성으로 답했어요."):
    audio = media(ctx, "AUDIO", store=store)
    with Session(ctx.engine) as db:
        row = MediaTranscription(store_id=store or ctx.store, manual_media_id=audio, status=status,
                                 text=text if status == "READY" else None,
                                 error_code="TRANSCRIPTION_FAILED" if status == "ERROR" else None,
                                 task_id=str(uuid.uuid4()), completed_at=None if status == "RUNNING" else NOW)
        db.add(row)
        db.commit()
        return row.id


def test_voice_answers_use_the_ready_transcript_of_this_store(ctx, fake_ai):
    sid = ctx.started()
    for status in ("RUNNING", "ERROR"):
        response = ctx.answer(sid, data={"method": "VOICE", "transcriptionId": _transcription(ctx, status)})
        assert (response.status_code, code(response)) == (409, "TRANSCRIPTION_NOT_READY")
    foreign = ctx.answer(sid, data={"method": "VOICE",
                                    "transcriptionId": _transcription(ctx, "READY", store=ctx.other_store)})
    assert (foreign.status_code, code(foreign)) == (404, "MANUAL_RESOURCE_NOT_FOUND")
    ready = _transcription(ctx, "READY")
    assert ctx.answer(sid, data={"method": "VOICE", "transcriptionId": ready.upper()}).status_code == 202
    [answer] = rows(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER")
    assert (answer.input_method, answer.content, answer.transcription_id) == ("VOICE", "음성으로 답했어요.", ready)
    ctx.run()
    assert fake_ai.calls_for("judge_sufficiency")[0].data["dialogue"][0]["answer"] == "음성으로 답했어요."


def test_answer_text_never_reaches_the_logs(ctx, caplog):
    sid = ctx.started()
    with caplog.at_level(logging.DEBUG):
        ctx.answer(sid, "비밀스러운-답변-문장")
        ctx.run()
    assert "비밀스러운-답변-문장" not in caplog.text


# --- failures, fallback and retries --------------------------------------------------------------


@pytest.mark.parametrize("contradiction", [
    {"sufficient": True, "probability": 0.9, "missing_aspects": ["근무 시간"]},
    {"sufficient": True, "probability": 0.2, "missing_aspects": []},
])
def test_contradictory_judgement_preserves_answer_and_retries_instead_of_covering(ctx, fake_ai, contradiction):
    sid = ctx.started()
    question_id = ctx.get(sid)["questions"][0]["id"]
    fake_ai.script("judge_sufficiency", *[FakeOutcome.ok(contradiction)] * 3)
    state = ctx.answer_and_run(sid, "근무 시간을 확인해 봐야 해요.")
    assert (state["status"], state["phase"]) == ("ERROR", "ERROR")
    assert state["error"]["code"] == "AI_PROCESSING_FAILED"
    assert state["currentIntentId"] == ctx.intents[0]
    assert state["intents"][0]["coverage"] != "COVERED"
    [evaluation] = rows(ctx, InterviewEvaluation)
    assert (evaluation.status, evaluation.error_code, evaluation.applied_at) == (
        "FAILED", "INVALID_OUTPUT", None)
    [answer] = rows(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER")
    assert answer.reply_to_question_turn_id == question_id
    assert len(fake_ai.calls_for("judge_sufficiency")) == 3
    assert ctx.post(ctx.url(sid, "retries"), {"expectedRevision": state["revision"]}).status_code == 202
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    ctx.run()
    recovered = ctx.get(sid)
    assert recovered["phase"] == "COLLECTING" and recovered["questions"][0]["kind"] == "PROBE"
    assert recovered["currentIntentId"] == ctx.intents[0]
    assert count(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER") == 1
    assert fake_ai.calls_for("judge_sufficiency")[-1].data["dialogue"][0]["answer"] == "근무 시간을 확인해 봐야 해요."


def test_jev_failure_after_retries_is_a_public_error_that_keeps_the_answer(ctx, fake_ai):
    sid = ctx.started()
    question = ctx.get(sid)["questions"][0]
    fake_ai.script("judge_sufficiency", FakeOutcome.fail("timeout"), FakeOutcome.raw('{"sufficient": '),
                   FakeOutcome.delay(5.0))
    ctx.answer_and_run(sid, "보존될 답변")
    state = ctx.get(sid)
    assert (state["status"], state["phase"]) == ("ERROR", "ERROR")
    assert state["error"] == {"code": "AI_PROCESSING_FAILED", "retryable": True,
                              "message": state["error"]["message"]}
    assert state["processing"]["kind"] == "EVALUATION" and state["processing"]["attempt"] == 1
    assert state["currentIntentId"] == ctx.intents[0] and state["intents"][0]["depth"] == 0
    [failed] = rows(ctx, InterviewEvaluation)
    assert (failed.status, failed.error_code, failed.applied_at) == ("FAILED", "TIMEOUT", None)
    assert failed.missing_aspects is None
    assert "timeout" not in str(state).lower()  # internal codes stay internal
    # Answering is not possible in ERROR; retry needs the current revision.
    stale = ctx.post(ctx.url(sid, "retries"), {"expectedRevision": state["revision"] - 1})
    assert (stale.status_code, code(stale)) == (409, "REVISION_CONFLICT")
    retried = ctx.post(ctx.url(sid, "retries"), {"expectedRevision": state["revision"]})
    assert retried.status_code == 202, retried.text
    body = retried.json()
    assert (body["status"], body["phase"], body["error"]) == ("IN_PROGRESS", "PROCESSING", None)
    assert body["processing"]["kind"] == "EVALUATION" and body["processing"]["attempt"] == 2
    ctx.run()
    state = ctx.get(sid)
    assert state["intents"][0]["coverage"] == "COVERED" and state["currentIntentId"] == ctx.intents[1]
    # The retry evaluated the same answer; one answer, two evaluation attempts, one applied.
    assert count(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER") == 1
    evaluations = sorted(rows(ctx, InterviewEvaluation), key=lambda e: e.attempt_no)
    assert [(e.attempt_no, e.status, e.applied_at is not None) for e in evaluations] == [
        (1, "FAILED", False), (2, "SUCCEEDED", True)]
    assert [e.missing_aspects for e in evaluations] == [None, []]
    assert fake_ai.calls_for("judge_sufficiency")[-1].data["dialogue"][0]["answer"] == "보존될 답변"
    assert question["id"] == rows(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER")[0].reply_to_question_turn_id


def test_non_retryable_jev_failure_stops_at_once(ctx, fake_ai):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", FakeOutcome.fail("refused"))
    ctx.answer(sid)
    ctx.run(rounds=1)
    assert ctx.get(sid)["status"] == "ERROR"
    assert rows(ctx, InterviewEvaluation)[0].error_code == "REFUSED"


def test_question_generation_failure_falls_back_without_stopping(ctx, fake_ai):
    fake_ai.script("generate_question", *[FakeOutcome.fail("unavailable")] * 3)
    sid = ctx.started()
    state = ctx.get(sid)
    assert state["phase"] == "COLLECTING" and state["questions"][0]["text"] == INTENTS_V1[0].base_question
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    fake_ai.script("generate_question", FakeOutcome.fail("refused"))  # PROBE fails, not retryable
    state = ctx.answer_and_run(sid)
    [question] = state["questions"]
    assert (question["kind"], question["depth"]) == ("PROBE", 1)
    assert question["text"] == "마감 순서에 대해 조금 더 자세히 알려 주시겠어요?"
    [batch] = rows(ctx, InterviewProbeBatch)
    assert (batch.status, batch.generator_source) == ("READY", "fallback:template")
    assert state["status"] == "IN_PROGRESS"  # never "sufficient", never ERROR


def test_retry_outside_error_is_a_state_conflict(ctx):
    sid = ctx.started()
    response = ctx.post(ctx.url(sid, "retries"), {"expectedRevision": ctx.get(sid)["revision"]})
    assert (response.status_code, code(response)) == (409, "INTERVIEW_STATE_CONFLICT")


def test_late_or_duplicated_task_results_change_nothing(ctx):
    sid = ctx.started()
    before = ctx.get(sid)
    with Session(ctx.engine) as db:  # a duplicate of the first question task, now stale
        original = db.scalars(select(BackgroundTask).where(BackgroundTask.kind == "INITIAL_QUESTION")).one()
        enqueue(db, "INITIAL_QUESTION", sid, original.payload, input_revision=1)
        db.commit()
    runs = ctx.run()
    assert [r.outcome for r in runs] == ["cancelled"]
    assert ctx.get(sid) == before
    assert count(ctx, InterviewTurn) == 1


def test_lost_lease_result_is_discarded(ctx, fake_ai):
    from datetime import timedelta

    from app.tasks import claim, drain, recover_expired, run_claimed

    ctx.start()
    [claimed] = claim()
    assert recover_expired(now=utcnow() + timedelta(minutes=10)) == 1
    drain(now=utcnow() + timedelta(minutes=11))  # the re-queued task writes the question
    assert run_claimed(claimed).outcome == "discarded"
    assert count(ctx, InterviewTurn) == 1


@pytest.mark.parametrize("aspects", [
    ["마감 순서", "청소 기준", "마감 순서"],
    [f"부족 측면 {i}" for i in range(5)],
])
def test_successful_evaluation_keeps_normalized_aspects_in_order(ctx, fake_ai, aspects):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", FakeOutcome.ok({
        "sufficient": False, "probability": 0.2, "missing_aspects": aspects,
    }))
    ctx.answer_and_run(sid)
    [evaluation] = rows(ctx, InterviewEvaluation)
    assert evaluation.missing_aspects == list(dict.fromkeys(aspects))
    ctx.run()
    [reloaded] = rows(ctx, InterviewEvaluation)
    assert reloaded.id == evaluation.id and reloaded.missing_aspects == evaluation.missing_aspects


def test_evaluation_apply_failure_rolls_back_aspects_and_progress(ctx, fake_ai, monkeypatch):
    from app.ai.contracts import SufficiencyRequest
    from app.interview import tasks
    from app.tasks import TaskContext

    sid = ctx.started()
    assert ctx.answer(sid).status_code == 202
    before = ctx.get(sid)
    with Session(ctx.engine) as db:
        task = db.get(BackgroundTask, before["processing"]["taskId"])
        task_ctx = TaskContext(task.id, task.kind, sid, task.attempt, task.input_revision, task.payload, 1)
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    judgement = fake_ai.judge_sufficiency(SufficiencyRequest.model_validate(task_ctx.payload["request"]))

    def fail_after_evaluation_flush(*args):
        raise RuntimeError("injected progress failure")

    monkeypatch.setattr(tasks, "apply_judgement", fail_after_evaluation_flush)
    with pytest.raises(RuntimeError, match="injected progress"), Session(ctx.engine) as db, db.begin():
        tasks._evaluation_apply(db, task_ctx, judgement)
    assert count(ctx, InterviewEvaluation) == 0
    assert ctx.get(sid) == before
    assert count(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER") == 1


def test_stale_evaluation_cannot_replace_saved_aspects(ctx, fake_ai):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    ctx.answer_and_run(sid)
    before = ctx.get(sid)
    [saved] = rows(ctx, InterviewEvaluation)
    with Session(ctx.engine) as db:
        original = db.get(BackgroundTask, saved.task_id)
        enqueue(db, "EVALUATION", sid, original.payload, input_revision=original.input_revision)
        db.commit()
    assert [run.outcome for run in ctx.run()] == ["cancelled"]
    [reloaded] = rows(ctx, InterviewEvaluation)
    assert reloaded.id == saved.id and reloaded.missing_aspects == INSUFFICIENT["missing_aspects"]
    assert ctx.get(sid) == before

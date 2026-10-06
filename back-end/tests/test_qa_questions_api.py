"""#121 worker AI Q&A questions, grounded answers and retries (SQLite and MySQL)."""

import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    ManualQa,
    ManualQaCitation,
    ManualQaConversation,
    ManualStep,
    QaMedia,
    Store,
    StoreManual,
)
from app.tasks import drain
from tests import media_samples as samples
from tests import test_qa_conversations_api as conversations_api
from tests.qa_factories import end_access, make_published_manual, mysql_only
from tests.test_qa_conversations_api import (
    ask,
    asked,
    conversation,
    detail,
    read_question,
    retry,
    text_input,
)
from tests.test_qa_media_api import _parallel, code, delete, expire, transcribe, uploaded

media_root = conversations_api.media_root
ctx = conversations_api.ctx

LATER = timedelta(minutes=5)


def steps_of(ctx, section_id) -> list[str]:
    with Session(ctx.engine) as db:
        return list(db.scalars(select(ManualStep.id).where(ManualStep.section_id == section_id)
                               .order_by(ManualStep.sort_order)))


def answered(section_id, step_ids, text="먼저 들어온 제품을 앞쪽에 진열하세요."):
    return FakeOutcome.ok({"outcome": "ANSWERED", "answer": text,
                           "citations": [{"section_id": section_id, "step_ids": list(step_ids)}]})


def publish_new_version(ctx, sections=(("재고 정리", "COMMON_TASK", ("유통기한이 짧은 제품을 앞에 둡니다.",)),)):
    with Session(ctx.engine) as db:
        version, section_ids = make_published_manual(db, db.get(Store, ctx.store), sections)
        db.commit()
        return version.id, section_ids


def test_other_workers_conversation_is_hidden_everywhere(ctx):
    theirs = conversation(ctx, auth=ctx.other_auth)
    question = asked(ctx, theirs, auth=ctx.other_auth)["id"]
    for response in (
        detail(ctx, theirs), ask(ctx, theirs), read_question(ctx, theirs, question),
        retry(ctx, theirs, question), detail(ctx, str(uuid.uuid4())),
        detail(ctx, theirs, store=ctx.other_store, auth=ctx.other_auth),
    ):
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND"), response.text


def test_asking_requires_current_access(ctx):
    conversation_id = conversation(ctx)
    with Session(ctx.engine) as db:
        end_access(db, ctx.store, ctx.worker)
    response = ask(ctx, conversation_id)
    assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")


# --- asking -----------------------------------------------------------------------------------


def test_question_is_accepted_running_and_answered_from_the_published_manual(ctx, fake_ai):
    conversation_id = conversation(ctx)
    section = ctx.sections[1]
    step = steps_of(ctx, section)[0]
    fake_ai.script("answer_question", answered(section, [step]))
    body = asked(ctx, conversation_id, text_input("  재고를 어떤 순서로 진열하나요?  "))
    assert (body["status"], body["sequence"], body["manualVersionId"]) == ("RUNNING", 1, ctx.version)
    assert (body["text"], body["imageMediaIds"], body["answer"], body["error"]) == (
        "재고를 어떤 순서로 진열하나요?", [], None, None)
    assert read_question(ctx, conversation_id, body["id"]).json()["status"] == "RUNNING"
    drain()
    result = read_question(ctx, conversation_id, body["id"]).json()
    assert result["status"] == "READY" and result["completedAt"] is not None
    assert result["answer"] == {
        "outcome": "ANSWERED", "text": "먼저 들어온 제품을 앞쪽에 진열하세요.",
        "citations": [{"versionId": ctx.version, "sectionId": section, "sectionTitle": "재고 정리",
                       "excerpt": "먼저 들어온 제품을 앞쪽에 진열하세요."}],
    }
    call = fake_ai.calls_for("answer_question")[0]
    assert call.data["question"] == "재고를 어떤 순서로 진열하나요?"
    assert [s["id"] for s in call.data["manual"]["sections"]] == ctx.sections
    with Session(ctx.engine) as db:
        assert db.get(ManualQaConversation, conversation_id).updated_at >= db.get(ManualQa, body["id"]).asked_at


def test_excerpt_is_built_by_the_server_from_the_cited_steps(ctx, fake_ai):
    conversation_id = conversation(ctx)
    section = ctx.sections[2]
    fake_ai.script("answer_question", FakeOutcome.ok({
        "outcome": "ANSWERED", "answer": "마감 30분 전에 세척해요.",
        "citations": [{"section_id": section, "step_ids": []}, {"section_id": section, "step_ids": []}],
    }))
    question = asked(ctx, conversation_id)["id"]
    drain()
    citations = read_question(ctx, conversation_id, question).json()["answer"]["citations"]
    assert citations == [{"versionId": ctx.version, "sectionId": section, "sectionTitle": "커피 머신 세척",
                          "excerpt": "마감 30분 전에 그룹 헤드를 세척합니다.\n세척 후 물을 두 번 흘려보냅니다."}]


def test_without_grounds_the_answer_asks_for_the_owner(ctx, fake_ai):
    conversation_id = conversation(ctx)
    question = asked(ctx, conversation_id, text_input("주차 요금은 누가 내요?"))["id"]
    drain()  # the fake's default is NEEDS_OWNER
    answer = read_question(ctx, conversation_id, question).json()["answer"]
    assert answer["outcome"] == "NEEDS_OWNER" and answer["citations"] == []
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualQaCitation)) == 0


@pytest.mark.parametrize("raw", [
    {"outcome": "ANSWERED", "answer": "근거 없는 답", "citations": []},
    {"outcome": "ANSWERED", "answer": "다른 매장 답", "citations": [{"section_id": "unknown", "step_ids": []}]},
    "not json",
])
def test_ungrounded_model_output_is_never_stored(ctx, fake_ai, raw):
    conversation_id = conversation(ctx)
    outcome = FakeOutcome.raw(raw) if isinstance(raw, str) else FakeOutcome.ok(raw)
    fake_ai.script("answer_question", outcome, outcome, outcome)
    question = asked(ctx, conversation_id)["id"]
    drain()
    drain(now=utcnow() + LATER)
    drain(now=utcnow() + 2 * LATER)
    result = read_question(ctx, conversation_id, question).json()
    assert result["status"] == "ERROR" and result["error"]["code"] == "AI_PROCESSING_FAILED"
    assert result["answer"] is None and len(fake_ai.calls_for("answer_question")) == 3
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualQaCitation)) == 0


def test_transient_failure_is_retried_automatically(ctx, fake_ai):
    conversation_id = conversation(ctx)
    fake_ai.script("answer_question", FakeOutcome.fail("timeout"))
    question = asked(ctx, conversation_id)["id"]
    drain()
    assert read_question(ctx, conversation_id, question).json()["status"] == "RUNNING"
    drain(now=utcnow() + LATER)
    assert read_question(ctx, conversation_id, question).json()["status"] == "READY"


def test_unanswerable_missing_information_reaches_the_model(ctx, fake_ai):
    from app.db.models import Store
    from tests.manual_factories import make_published, sample_content

    with Session(ctx.engine) as db:
        version = make_published(db, db.get(Store, ctx.store), sample_content())
        db.commit()
        version_id = version.id
    conversation_id = conversation(ctx)
    asked(ctx, conversation_id, text_input("야간 근무는 몇 시에 끝나요?"))
    drain()
    data = fake_ai.calls_for("answer_question")[0].data["manual"]
    assert data["missing_information"], "unknown values are passed as missing information"
    with Session(ctx.engine) as db:
        assert db.scalars(select(ManualQa)).one().published_version_id == version_id


def test_one_running_question_per_conversation(ctx):
    conversation_id = conversation(ctx)
    first = asked(ctx, conversation_id)
    response = ask(ctx, conversation_id, text_input("두 번째 질문"))
    assert (response.status_code, code(response)) == (409, "QA_BUSY")
    other = conversation(ctx)  # another conversation is independent
    assert asked(ctx, other)["sequence"] == 1
    drain()
    second = asked(ctx, conversation_id, text_input("두 번째 질문"))
    assert (second["sequence"], first["sequence"]) == (2, 1)


def test_same_key_replays_the_question_and_another_body_conflicts(ctx):
    conversation_id = conversation(ctx)
    key = str(uuid.uuid4())
    first = ask(ctx, conversation_id, key=key)
    again = ask(ctx, conversation_id, key=key)
    assert again.status_code == 202 and again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    other = ask(ctx, conversation_id, text_input("다른 질문"), key=key)
    assert (other.status_code, code(other)) == (409, "IDEMPOTENCY_KEY_REUSED")
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualQa)) == 1
        assert db.scalar(select(func.count()).select_from(BackgroundTask)) == 1


@pytest.mark.parametrize("body,field", [
    ({"kind": "TEXT", "text": None, "transcriptionId": None, "imageMediaIds": []}, "text"),
    ({"kind": "TEXT", "text": "   \n", "transcriptionId": None, "imageMediaIds": []}, "text"),
    ({"kind": "TEXT", "text": "", "transcriptionId": None, "imageMediaIds": []}, "text"),
    ({"kind": "TEXT", "text": "가" * 2001, "transcriptionId": None, "imageMediaIds": []}, "text"),
    ({"kind": "TEXT", "text": "질문", "transcriptionId": str(uuid.uuid4()), "imageMediaIds": []}, "transcriptionId"),
    ({"kind": "VOICE", "text": None, "transcriptionId": None, "imageMediaIds": []}, "transcriptionId"),
    ({"kind": "VOICE", "text": "질문", "transcriptionId": str(uuid.uuid4()), "imageMediaIds": []}, "text"),
    ({"kind": "IMAGE", "text": "질문", "transcriptionId": None, "imageMediaIds": []}, "kind"),
    ({"kind": "TEXT", "text": "질문", "transcriptionId": None}, "imageMediaIds"),
    ({"kind": "TEXT", "text": "질문", "imageMediaIds": []}, "transcriptionId"),
    ({"kind": "TEXT", "text": "질문", "transcriptionId": None, "imageMediaIds": ["x"]}, "imageMediaIds"),
    ({"kind": "TEXT", "text": "질문", "transcriptionId": None,
      "imageMediaIds": [str(uuid.uuid4()) for _ in range(4)]}, "imageMediaIds"),
    ({"kind": "TEXT", "text": "질문", "transcriptionId": None, "imageMediaIds": [], "model": "x"}, "model"),
])
def test_question_input_rules(ctx, body, field):
    conversation_id = conversation(ctx)
    response = ask(ctx, conversation_id, body)
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR"), response.text
    fields = [error["field"].split(".")[-1] if not error["field"].startswith("imageMediaIds") else "imageMediaIds"
              for error in response.json()["fieldErrors"]]
    assert field in fields, fields


def test_duplicate_photos_and_photo_only_questions_are_rejected(ctx):
    conversation_id = conversation(ctx)
    photo = uploaded(ctx)
    response = ask(ctx, conversation_id, text_input(images=[photo, photo.upper()]))
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")
    response = ask(ctx, conversation_id, {"kind": "TEXT", "text": None, "transcriptionId": None,
                                          "imageMediaIds": [photo]})
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")


def test_longest_question_is_accepted(ctx):
    conversation_id = conversation(ctx)
    assert asked(ctx, conversation_id, text_input("가" * 2000))["text"] == "가" * 2000


# --- photos -----------------------------------------------------------------------------------


def test_photos_are_attached_in_order_and_given_to_the_model(ctx, fake_ai):
    conversation_id = conversation(ctx)
    photos = [uploaded(ctx, samples.png()), uploaded(ctx, samples.jpeg())]
    body = asked(ctx, conversation_id, text_input("이 기계 어떻게 켜요?", photos))
    assert body["imageMediaIds"] == photos
    drain()
    assert fake_ai.calls_for("answer_question")[0].image_count == 2
    restored = detail(ctx, conversation_id).json()["turns"][0]
    assert restored["imageMediaIds"] == photos
    response = delete(ctx, photos[0])
    assert (response.status_code, code(response)) == (409, "MEDIA_IN_USE")


def test_photo_rules(ctx):
    conversation_id = conversation(ctx)
    audio = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO")
    response = ask(ctx, conversation_id, text_input(images=[audio]))
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")
    expired = uploaded(ctx)
    expire(ctx, expired)
    response = ask(ctx, conversation_id, text_input(images=[expired]))
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")
    theirs = uploaded(ctx, auth=ctx.other_auth)
    elsewhere = uploaded(ctx, store=ctx.other_store)
    gone = uploaded(ctx)
    assert delete(ctx, gone).status_code == 204
    for media_id in (theirs, elsewhere, gone, str(uuid.uuid4())):
        response = ask(ctx, conversation_id, text_input(images=[media_id]))
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")
    used = uploaded(ctx)
    asked(ctx, conversation_id, text_input(images=[used]))
    drain()
    response = ask(ctx, conversation_id, text_input("다시", images=[used]))
    assert (response.status_code, code(response)) == (409, "MEDIA_IN_USE")
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualQa)) == 1


# --- voice questions --------------------------------------------------------------------------


def _transcript(ctx, fake_ai, text="포스기 마감은 어떻게 해요?", *, run=True, auth=None) -> str:
    media_id = uploaded(ctx, samples.wav_seconds(1), "QUESTION_AUDIO", auth=auth)
    fake_ai.script("transcribe", FakeOutcome.ok(text))
    transcription = transcribe(ctx, media_id, auth=auth).json()["id"]
    if run:
        drain()
    return transcription


def voice(transcription_id, images=()):
    return {"kind": "VOICE", "text": None, "transcriptionId": transcription_id, "imageMediaIds": list(images)}


def test_voice_question_uses_the_ready_transcript(ctx, fake_ai):
    conversation_id = conversation(ctx)
    transcription = _transcript(ctx, fake_ai)
    body = asked(ctx, conversation_id, voice(transcription.upper()))
    assert body["text"] == "포스기 마감은 어떻게 해요?"
    with Session(ctx.engine) as db:
        row = db.get(ManualQa, body["id"])
        assert (row.input_method, row.transcription_id) == ("VOICE", transcription)
    drain()
    assert fake_ai.calls_for("answer_question")[0].data["question"] == "포스기 마감은 어떻게 해요?"


def test_voice_question_rules(ctx, fake_ai):
    conversation_id = conversation(ctx)
    running = _transcript(ctx, fake_ai, run=False)
    response = ask(ctx, conversation_id, voice(running))
    assert (response.status_code, code(response)) == (409, "TRANSCRIPTION_NOT_READY")
    drain()
    theirs = _transcript(ctx, fake_ai, auth=ctx.other_auth)
    for transcription in (theirs, str(uuid.uuid4())):
        response = ask(ctx, conversation_id, voice(transcription))
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")
    long = _transcript(ctx, fake_ai, "가" * 2001)
    response = ask(ctx, conversation_id, voice(long))
    assert (response.status_code, code(response)) == (422, "VALIDATION_ERROR")


def test_transcript_survives_deletion_of_the_recording(ctx, fake_ai):
    conversation_id = conversation(ctx)
    transcription = _transcript(ctx, fake_ai)
    with Session(ctx.engine) as db:
        from app.db.models import MediaTranscription

        media_id = db.get(MediaTranscription, transcription).qa_media_id
    assert delete(ctx, media_id).status_code == 204
    assert asked(ctx, conversation_id, voice(transcription))["text"] == "포스기 마감은 어떻게 해요?"


# --- fixed version ----------------------------------------------------------------------------


def test_past_answers_keep_their_version_title_and_excerpt_after_republishing(ctx, fake_ai):
    conversation_id = conversation(ctx)
    section = ctx.sections[1]
    fake_ai.script("answer_question", answered(section, steps_of(ctx, section)))
    old = asked(ctx, conversation_id)["id"]
    drain()
    before = read_question(ctx, conversation_id, old).json()
    new_version, new_sections = publish_new_version(ctx)
    after = read_question(ctx, conversation_id, old).json()
    assert after == before and after["manualVersionId"] == ctx.version
    assert after["answer"]["citations"][0]["excerpt"] == "먼저 들어온 제품을 앞쪽에 진열하세요."
    fake_ai.script("answer_question", answered(new_sections[0], steps_of(ctx, new_sections[0])))
    new = asked(ctx, conversation_id)
    assert new["manualVersionId"] == new_version and new["sequence"] == 2
    drain()
    citation = read_question(ctx, conversation_id, new["id"]).json()["answer"]["citations"][0]
    assert (citation["versionId"], citation["excerpt"]) == (new_version, "유통기한이 짧은 제품을 앞에 둡니다.")


def test_publication_during_processing_does_not_move_the_question(ctx, fake_ai):
    conversation_id = conversation(ctx)
    question = asked(ctx, conversation_id)["id"]
    publish_new_version(ctx)
    drain()
    data = fake_ai.calls_for("answer_question")[0].data
    assert [s["id"] for s in data["manual"]["sections"]] == ctx.sections
    assert read_question(ctx, conversation_id, question).json()["manualVersionId"] == ctx.version


def test_store_without_published_manual_refuses_questions(ctx):
    conversation_id = conversation(ctx)
    with Session(ctx.engine) as db:
        db.scalars(select(StoreManual).where(StoreManual.store_id == ctx.store)).one() \
            .current_published_version_id = None
        db.commit()
    response = ask(ctx, conversation_id)
    assert (response.status_code, code(response)) == (404, "MANUAL_NOT_PUBLISHED")


# --- access while answering -------------------------------------------------------------------


def test_access_ended_before_processing_fails_without_calling_the_model(ctx, fake_ai):
    conversation_id = conversation(ctx)
    question = asked(ctx, conversation_id)["id"]
    with Session(ctx.engine) as db:
        end_access(db, ctx.store, ctx.worker)
    drain()
    assert fake_ai.calls_for("answer_question") == []
    with Session(ctx.engine) as db:
        row = db.get(ManualQa, question)
        assert (row.status, row.answer) == ("ERROR", None)
    response = read_question(ctx, conversation_id, question)
    assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")


def test_access_ended_while_the_model_answers_discards_the_answer(ctx, fake_ai):
    conversation_id = conversation(ctx)
    question = asked(ctx, conversation_id)["id"]
    section = ctx.sections[1]
    steps = steps_of(ctx, section)

    def answer_then_lose_access(_data):
        with Session(ctx.engine) as db:
            end_access(db, ctx.store, ctx.worker)
        return {"outcome": "ANSWERED", "answer": "비밀 절차", "citations": [{"section_id": section, "step_ids": steps}]}

    fake_ai.on("answer_question", answer_then_lose_access)
    drain()
    with Session(ctx.engine) as db:
        row = db.get(ManualQa, question)
        assert (row.status, row.answer, row.public_error_code) == ("ERROR", None, "AI_PROCESSING_FAILED")
        assert db.scalar(select(func.count()).select_from(ManualQaCitation)) == 0


def test_question_and_answer_text_never_reach_the_logs(ctx, fake_ai, caplog):
    conversation_id = conversation(ctx)
    section = ctx.sections[1]
    fake_ai.script("answer_question", answered(section, steps_of(ctx, section), "금고 비밀번호는 4321"))
    with caplog.at_level("DEBUG"):
        question = asked(ctx, conversation_id, text_input("제 주민번호 900101-1234567 괜찮나요?"))["id"]
        drain()
        read_question(ctx, conversation_id, question)
    assert "900101" not in caplog.text and "4321" not in caplog.text


# --- retries ----------------------------------------------------------------------------------


def _failed(ctx, fake_ai, conversation_id, body=None) -> str:
    fake_ai.script("answer_question", FakeOutcome.fail("refused"))
    question = asked(ctx, conversation_id, body)["id"]
    drain()
    assert read_question(ctx, conversation_id, question).json()["status"] == "ERROR"
    return question


def test_failed_question_is_retried_in_place(ctx, fake_ai):
    conversation_id = conversation(ctx)
    photo = uploaded(ctx)
    question = _failed(ctx, fake_ai, conversation_id, text_input("이건 어디에 둬요?", [photo]))
    before = read_question(ctx, conversation_id, question).json()
    publish_new_version(ctx)  # a retry never moves to the newer manual
    key = str(uuid.uuid4())
    response = retry(ctx, conversation_id, question, key=key)
    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["id"], body["sequence"], body["manualVersionId"], body["text"], body["imageMediaIds"]) == (
        question, 1, ctx.version, before["text"], [photo])
    assert (body["status"], body["error"], body["completedAt"]) == ("RUNNING", None, None)
    replay = retry(ctx, conversation_id, question, key=key)
    assert replay.status_code == 202 and replay.headers["Idempotent-Replayed"] == "true"
    running = retry(ctx, conversation_id, question)
    assert (running.status_code, code(running)) == (409, "QA_NOT_RETRYABLE")
    drain()
    result = read_question(ctx, conversation_id, question).json()
    assert result["status"] == "READY" and result["manualVersionId"] == ctx.version
    ready = retry(ctx, conversation_id, question)
    assert (ready.status_code, code(ready)) == (409, "QA_NOT_RETRYABLE")
    with Session(ctx.engine) as db:
        assert db.get(ManualQa, question).attempt == 2
        assert db.scalar(select(func.count()).select_from(ManualQa)) == 1
    data = fake_ai.calls_for("answer_question")[-1].data
    assert [s["id"] for s in data["manual"]["sections"]] == ctx.sections


def test_retry_waits_for_the_running_question_of_the_conversation(ctx, fake_ai):
    conversation_id = conversation(ctx)
    failed = _failed(ctx, fake_ai, conversation_id)
    asked(ctx, conversation_id, text_input("다음 질문"))
    response = retry(ctx, conversation_id, failed)
    assert (response.status_code, code(response)) == (409, "QA_BUSY")


def test_retry_with_an_expired_photo_needs_a_new_question(ctx, fake_ai):
    conversation_id = conversation(ctx)
    photo = uploaded(ctx)
    question = _failed(ctx, fake_ai, conversation_id, text_input(images=[photo]))
    expire(ctx, photo)
    response = retry(ctx, conversation_id, question)
    assert (response.status_code, code(response)) == (410, "QA_INPUT_EXPIRED")
    with Session(ctx.engine) as db:
        assert db.get(ManualQa, question).status == "ERROR"


def test_retry_of_unknown_question_or_after_access_ended(ctx, fake_ai):
    conversation_id = conversation(ctx)
    question = _failed(ctx, fake_ai, conversation_id)
    other = conversation(ctx)
    for response in (retry(ctx, other, question), retry(ctx, conversation_id, str(uuid.uuid4()))):
        assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")
    with Session(ctx.engine) as db:
        end_access(db, ctx.store, ctx.worker)
    response = retry(ctx, conversation_id, question)
    assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")


def test_late_result_of_a_superseded_attempt_is_discarded(ctx, fake_ai):
    """Attempt 1 is still with the model when the question fails and is retried (attempt 2):
    attempt 1's late answer must not overwrite attempt 2."""
    from app.tasks import claim, run_claimed

    conversation_id = conversation(ctx)
    question = asked(ctx, conversation_id)["id"]
    stale = claim()[0]
    section = ctx.sections[0]
    steps = steps_of(ctx, section)

    def superseded_while_answering(_data):
        with Session(ctx.engine) as db:  # as the lease recovery would: fail it publicly
            row = db.get(ManualQa, question)
            row.status, row.public_error_code, row.completed_at = "ERROR", "AI_PROCESSING_FAILED", utcnow()
            db.commit()
        assert retry(ctx, conversation_id, question).status_code == 202
        return {"outcome": "ANSWERED", "answer": "늦은 답", "citations": [{"section_id": section, "step_ids": steps}]}

    fake_ai.on("answer_question", superseded_while_answering)
    assert run_claimed(stale).outcome == "cancelled"
    with Session(ctx.engine) as db:
        row = db.get(ManualQa, question)
        assert (row.status, row.attempt, row.answer) == ("RUNNING", 2, None)
    fake_ai.on("answer_question", lambda _data: {"outcome": "NEEDS_OWNER", "answer": "새 답", "citations": []})
    drain()
    assert read_question(ctx, conversation_id, question).json()["answer"]["text"] == "새 답"


# --- MySQL races ------------------------------------------------------------------------------


@mysql_only
def test_concurrent_questions_in_one_conversation_accept_exactly_one(ctx):
    conversation_id = conversation(ctx)
    results = _parallel(5, lambda: ask(ctx, conversation_id, text_input(f"질문 {uuid.uuid4()}")))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [202, 409, 409, 409, 409], [r.text for r in results]
    assert {code(r) for r in results if r.status_code == 409} == {"QA_BUSY"}
    with Session(ctx.engine) as db:
        assert db.scalar(select(func.count()).select_from(ManualQa)) == 1
        assert db.scalar(select(func.count()).select_from(BackgroundTask)) == 1


@mysql_only
def test_concurrent_retries_restart_once(ctx, fake_ai):
    conversation_id = conversation(ctx)
    question = _failed(ctx, fake_ai, conversation_id)
    results = _parallel(4, lambda: retry(ctx, conversation_id, question))
    assert sorted(r.status_code for r in results) == [202, 409, 409, 409], [r.text for r in results]
    assert {code(r) for r in results if r.status_code == 409} == {"QA_NOT_RETRYABLE"}
    with Session(ctx.engine) as db:
        assert db.get(ManualQa, question).attempt == 2
        assert db.scalar(select(func.count()).select_from(BackgroundTask)
                         .where(BackgroundTask.status == "QUEUED")) == 1


def _delete_and_link(ctx):
    conversation_id = conversation(ctx)
    photo = uploaded(ctx)
    calls = iter([lambda: delete(ctx, photo), lambda: ask(ctx, conversation_id, text_input(images=[photo]))])
    lock = threading.Lock()

    def send():
        with lock:
            action = next(calls)
        return action()

    results = {r.request.method: r for r in _parallel(2, send)}
    return conversation_id, photo, results["DELETE"], results["POST"]


@mysql_only
def test_photo_delete_and_question_link_race_never_dangles(ctx):
    for _ in range(3):
        conversation_id, photo, removed, linked = _delete_and_link(ctx)
        with Session(ctx.engine) as db:
            media = db.get(QaMedia, photo)
            questions = db.scalar(select(func.count()).select_from(ManualQa)
                                  .where(ManualQa.conversation_id == conversation_id))
        if removed.status_code == 204:
            assert (linked.status_code, code(linked), questions) == (404, "RESOURCE_NOT_FOUND", 0)
            assert media.deleted_at is not None
        else:
            assert (removed.status_code, code(removed), linked.status_code, questions) == (
                409, "MEDIA_IN_USE", 202, 1)
            assert media.deleted_at is None
        drain()


def test_suspended_owner_is_403_for_workers_with_access_and_hides_answers(ctx):
    from app.db.models import User

    conversation_id = conversation(ctx)
    question = asked(ctx, conversation_id)["id"]
    drain()
    with Session(ctx.engine) as db:
        db.get(User, ctx.owner).status = "SUSPENDED"
        db.commit()
    for response in (ask(ctx, conversation_id), read_question(ctx, conversation_id, question),
                     detail(ctx, conversation_id), retry(ctx, conversation_id, question)):
        assert (response.status_code, code(response)) == (403, "STORE_APPROVAL_REQUIRED"), response.text
    with Session(ctx.engine) as db:
        end_access(db, ctx.store, ctx.worker)
    response = read_question(ctx, conversation_id, question)
    assert (response.status_code, code(response)) == (404, "RESOURCE_NOT_FOUND")

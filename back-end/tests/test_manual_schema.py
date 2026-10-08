"""Database rules of manuals, interviews, reviews and draft corrections (SQLite and MySQL).

Concurrency rules the services rely on are unique constraints here, so a lost race surfaces as
an IntegrityError instead of a duplicate question, answer, applied evaluation or draft.
"""

import uuid
from datetime import time, timedelta

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.models import (
    InterviewEvaluation,
    InterviewIntentReview,
    InterviewProbeBatch,
    InterviewSessionIntent,
    InterviewTurn,
    ManualDraftCorrection,
    ManualMedia,
    ManualPhotoAttachment,
    ManualReviewIssue,
    ManualSection,
    ManualShift,
    ManualStep,
    MediaTranscription,
    StoreManual,
)
from tests.factories import (
    NOW,
    make_interview,
    make_manual_draft,
    make_question_set,
    make_store,
    make_user,
)


def rejected(session, build):
    with pytest.raises((IntegrityError, OperationalError)), session.begin_nested():
        build()
        session.flush()


@pytest.fixture
def store(session):
    return make_store(session, owner=make_user(session, "OWNER"), approval_status="APPROVED", approved_at=NOW)


@pytest.fixture
def draft(session, store):
    return make_manual_draft(session, store)


def publish(session, version, store):
    version.status = "PUBLISHED"
    version.generation_status = "READY"
    version.published_at = NOW
    version.published_by_owner_id = store.owner_id
    session.flush()


# --- versions ---------------------------------------------------------------------------------


def test_one_draft_per_manual_and_publication_pointer(session, store, draft):
    rejected(session, lambda: make_manual_draft(session, store))
    publish(session, draft, store)
    manual = session.get(StoreManual, draft.manual_id)
    manual.current_published_version_id = draft.id  # the store_manuals <-> versions cycle
    session.flush()
    second = make_manual_draft(session, store)
    assert second.revision_no == 2


@pytest.mark.parametrize("changes", [
    {"status": "PUBLISHED", "generation_status": "READY"},  # no publication time/owner
    {"status": "PUBLISHED", "generation_status": "RUNNING", "published_at": NOW},
    {"published_at": NOW},  # a draft has no publication time
    {"revision": 0},
    {"generation_status": "DONE"},
])
def test_version_state_rules(session, draft, store, changes):
    def apply():
        if "published_at" in changes and changes.get("status") == "PUBLISHED":
            draft.published_by_owner_id = store.owner_id
        for key, value in changes.items():
            setattr(draft, key, value)
    rejected(session, apply)


# --- content ----------------------------------------------------------------------------------


def shift(session, version, order=0, **values):
    row = ManualShift(version_id=version.id, sort_order=order, name="야간", **values)
    session.add(row)
    session.flush()
    return row


def section(session, version, order=0, **values):
    values.setdefault("category", "COMMON_TASK")
    row = ManualSection(version_id=version.id, sort_order=order, title="업무", **values)
    session.add(row)
    session.flush()
    return row


@pytest.mark.parametrize("values,ok", [
    ({}, True),  # unknown times are NULL with a missing-information issue
    ({"start_time": time(22), "end_time": time(7), "ends_next_day": True}, True),
    ({"start_time": time(9), "end_time": time(9), "ends_next_day": True}, True),  # 24 hours
    ({"start_time": time(9), "end_time": time(18), "ends_next_day": False}, True),
    ({"start_time": time(9), "end_time": time(9), "ends_next_day": False}, False),
    ({"start_time": time(22), "end_time": time(7), "ends_next_day": False}, False),
    ({"start_time": time(9), "end_time": time(18), "ends_next_day": True}, False),
    ({"name": " "}, False),
])
def test_shift_rules(session, draft, values, ok):
    def build():
        session.add(ManualShift(version_id=draft.id, sort_order=0, **{"name": "야간", **values}))
        session.flush()
    if ok:
        build()
    else:
        rejected(session, build)


def test_sections_reference_shifts_of_the_same_version_only(session, store, draft):
    night = shift(session, draft)
    section(session, draft, category="SHIFT_TASK", shift_id=night.id)
    rejected(session, lambda: session.add(ManualSection(
        version_id=draft.id, sort_order=1, title="x", category="SHIFT_TASK")))
    rejected(session, lambda: session.add(ManualSection(
        version_id=draft.id, sort_order=1, title="x", category="RULE", shift_id=night.id)))
    publish(session, draft, store)
    other = make_manual_draft(session, store)
    rejected(session, lambda: session.add(ManualSection(
        version_id=other.id, sort_order=0, title="x", category="SHIFT_TASK", shift_id=night.id)))


def test_step_order_and_text(session, draft):
    parent = section(session, draft)
    session.add(ManualStep(section_id=parent.id, sort_order=0, instruction="불을 켜요"))
    session.flush()
    rejected(session, lambda: session.add(ManualStep(section_id=parent.id, sort_order=0, instruction="x")))
    rejected(session, lambda: session.add(ManualStep(section_id=parent.id, sort_order=1, instruction="\n ")))


def photo(session, store):
    row = ManualMedia(
        store_id=store.id, uploaded_by_owner_id=store.owner_id, kind="IMAGE", object_key=f"m/{uuid.uuid4()}",
        mime_type="image/png", byte_size=10, created_at=NOW, expires_at=NOW + timedelta(days=1),
    )
    session.add(row)
    session.flush()
    return row


def attach(session, version, media, order=0, section_id=None):
    session.add(ManualPhotoAttachment(
        version_id=version.id, section_id=section_id, media_id=media.id, sort_order=order, title="사진 1",
    ))
    session.flush()


def test_photo_attachment_lists_are_unique_per_scope(session, store, draft):
    first, second = section(session, draft, 0), section(session, draft, 1)
    image = photo(session, store)
    attach(session, draft, image)  # structure photo
    attach(session, draft, image, section_id=first.id)  # the same file may illustrate a section too
    attach(session, draft, image, section_id=second.id)
    rejected(session, lambda: attach(session, draft, image, order=1))  # same file twice in a list
    rejected(session, lambda: attach(session, draft, photo(session, store), order=0, section_id=first.id))
    publish(session, draft, store)
    other = make_manual_draft(session, store)
    rejected(session, lambda: attach(session, other, image, section_id=first.id))  # other version


# --- interview progress -----------------------------------------------------------------------


@pytest.fixture
def interview(session, draft):
    question_set, intents = make_question_set(session)
    return make_interview(session, draft, question_set, intents), intents


def turn(session, interview, number, **values):
    row = InterviewTurn(session_id=interview.id, turn_no=number, content="내용", **values)
    session.add(row)
    session.flush()
    return row


def base_question(session, interview, intent, number=1):
    return turn(session, interview, number, speaker="AI", turn_kind="QUESTION", question_kind="BASE",
                intent_id=intent.id)


def test_one_base_question_per_intent_and_one_answer_per_question(session, interview):
    session_row, intents = interview
    question = base_question(session, session_row, intents[0])
    rejected(session, lambda: base_question(session, session_row, intents[0], 2))
    base_question(session, session_row, intents[1], 2)
    answer = {"speaker": "OWNER", "turn_kind": "ANSWER", "intent_id": intents[0].id,
              "reply_to_question_turn_id": question.id, "input_method": "TEXT"}
    turn(session, session_row, 3, **answer)
    rejected(session, lambda: turn(session, session_row, 4, **answer))
    rejected(session, lambda: turn(session, session_row, 3, speaker="AI", turn_kind="QUESTION",
                                   question_kind="BASE", intent_id=intents[2].id))  # turn_no taken


def test_one_question_per_probe_batch_and_depth_rules(session, interview):
    session_row, intents = interview
    intent = intents[0]
    batch = InterviewProbeBatch(session_id=session_row.id, intent_id=intent.id, depth=1)
    session.add(batch)
    session.flush()
    probe = {"speaker": "AI", "turn_kind": "QUESTION", "question_kind": "PROBE", "intent_id": intent.id,
             "probe_batch_id": batch.id, "depth": 1}
    turn(session, session_row, 1, **probe)
    rejected(session, lambda: turn(session, session_row, 2, **probe))
    rejected(session, lambda: session.add(InterviewProbeBatch(
        session_id=session_row.id, intent_id=intent.id, depth=1)))
    rejected(session, lambda: session.add(InterviewProbeBatch(
        session_id=session_row.id, intent_id=intent.id, depth=6)))
    rejected(session, lambda: turn(session, session_row, 2, **{**probe, "depth": 0}))
    rejected(session, lambda: turn(session, session_row, 2, **{**probe, "intent_id": intents[1].id}))


@pytest.mark.parametrize("values", [
    {"speaker": "OWNER", "turn_kind": "QUESTION", "question_kind": "BASE"},
    {"speaker": "AI", "turn_kind": "QUESTION", "question_kind": "BASE", "input_method": "TEXT"},
    {"speaker": "OWNER", "turn_kind": "CORRECTION", "input_method": "VOICE"},  # VOICE needs a transcript
    {"speaker": "OWNER", "turn_kind": "CORRECTION", "input_method": None},
    {"speaker": "OWNER", "turn_kind": "ANSWER", "input_method": "TEXT"},  # an answer needs its question
])
def test_turn_shape_rules(session, interview, values):
    session_row, intents = interview
    rejected(session, lambda: turn(session, session_row, 1, intent_id=intents[0].id, **values))


def test_voice_correction_keeps_its_transcription(session, store, interview):
    session_row, intents = interview
    audio = ManualMedia(
        store_id=store.id, uploaded_by_owner_id=store.owner_id, kind="AUDIO", object_key="a/1",
        mime_type="audio/webm", byte_size=10, duration_ms=1000, created_at=NOW, expires_at=NOW + timedelta(days=1),
    )
    session.add(audio)
    session.flush()
    transcript = MediaTranscription(store_id=store.id, manual_media_id=audio.id, status="READY",
                                    text="정정", attempt=1, completed_at=NOW)
    session.add(transcript)
    session.flush()
    turn(session, session_row, 1, speaker="OWNER", turn_kind="CORRECTION", intent_id=intents[0].id,
         input_method="VOICE", transcription_id=transcript.id)


def test_needs_detail_requires_depth_five(session, interview):
    session_row, intents = interview
    progress = session.get(InterviewSessionIntent, (session_row.id, intents[0].id))

    def mark(depth):
        progress.coverage_status, progress.depth, progress.finished_at = "NEEDS_DETAIL", depth, NOW

    rejected(session, lambda: mark(4))
    mark(5)
    session.flush()


def test_one_applied_evaluation_per_depth(session, interview):
    session_row, intents = interview
    intent = intents[0]
    question = base_question(session, session_row, intent)
    answer = turn(session, session_row, 2, speaker="OWNER", turn_kind="ANSWER", intent_id=intent.id,
                  reply_to_question_turn_id=question.id, input_method="TEXT")

    def evaluation(attempt, **values):
        session.add(InterviewEvaluation(
            session_id=session_row.id, intent_id=intent.id, depth=0, attempt_no=attempt,
            evaluated_through_turn_id=answer.id, input_snapshot={"turns": []},
            evaluation_config_version="fake:fake-llm:v", provider="fake", **values,
        ))
        session.flush()

    evaluation(1, status="FAILED", error_code="TIMEOUT")
    evaluation(2, status="SUCCEEDED", needs_follow_up=False, probability=0.9, applied_at=NOW)
    rejected(session, lambda: evaluation(2, status="SUCCEEDED", needs_follow_up=False, probability=0.9))
    rejected(session, lambda: evaluation(3, status="SUCCEEDED", needs_follow_up=True, probability=0.2,
                                         applied_at=NOW))
    rejected(session, lambda: evaluation(4, status="FAILED", error_code="TIMEOUT", applied_at=NOW))
    rejected(session, lambda: evaluation(5, status="SUCCEEDED", needs_follow_up=True, probability=1.5))
    evaluation(6, status="SUCCEEDED", needs_follow_up=True, probability=0.3)  # recorded, not applied


def test_review_states(session, interview, store):
    session_row, intents = interview

    def review(intent, **values):
        session.add(InterviewIntentReview(session_id=session_row.id, intent_id=intent.id, **values))
        session.flush()

    rejected(session, lambda: review(intents[0], status="READY"))  # READY needs content
    rejected(session, lambda: review(intents[0], status="PROCESSING"))  # PROCESSING needs its task
    rejected(session, lambda: review(intents[0], status="ERROR", error_code="TIMEOUT"))
    review(intents[0], status="READY", ready_content={"summary": "요약"}, confirmed_at=NOW,
           confirmed_by_owner_id=store.owner_id)
    review(intents[1], status="PROCESSING", processing_kind="UNDERSTANDING",
           processing_task_id=str(uuid.uuid4()), processing_attempt=1)


# --- issues and draft corrections -------------------------------------------------------------


@pytest.mark.parametrize("values,ok", [
    ({}, True),
    ({"target_kind": "MANUAL", "field_name": "shifts", "public_description": "근무조 미정"}, True),
    ({"target_kind": "SHIFT", "target_id": str(uuid.uuid4()), "field_name": "endTime",
      "public_description": "종료 미정"}, True),
    ({"target_kind": "MANUAL", "target_id": str(uuid.uuid4()), "field_name": "shifts",
      "public_description": "x"}, False),
    ({"target_kind": "SHIFT", "target_id": str(uuid.uuid4()), "field_name": "steps",
      "public_description": "x"}, False),
    ({"target_kind": "SECTION", "target_id": str(uuid.uuid4()), "field_name": "steps"}, False),
    ({"field_name": "steps"}, False),
])
def test_issue_target_shape(session, draft, values, ok):
    def build():
        session.add(ManualReviewIssue(version_id=draft.id, description="부족한 정보", **values))
        session.flush()
    if ok:
        build()
    else:
        rejected(session, build)


def test_one_running_correction_per_draft(session, draft, store):
    def correction(**values):
        session.add(ManualDraftCorrection(
            version_id=draft.id, base_revision=1, target_kind="MANUAL", input_method="TEXT",
            input_text="야간조 종료는 7시", requested_by_owner_id=store.owner_id, **values,
        ))
        session.flush()

    correction(status="RUNNING", task_id=str(uuid.uuid4()))
    rejected(session, lambda: correction(status="RUNNING", task_id=str(uuid.uuid4())))
    correction(status="ERROR", error_code="AI_PROCESSING_FAILED", completed_at=NOW)
    rejected(session, lambda: correction(status="SUCCEEDED", completed_at=NOW))  # needs result revision
    rejected(session, lambda: correction(status="ERROR", error_code="TIMEOUT", completed_at=NOW))

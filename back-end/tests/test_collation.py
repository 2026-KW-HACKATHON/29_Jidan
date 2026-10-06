"""Case-sensitive enum and identifier columns (MySQL's default collation is case-insensitive)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import (
    ApplicationSelectionEffect,
    AvailabilityDay,
    AvailabilityRule,
    BackgroundTask,
    Base,
    IdempotencyRecord,
    InvitationMailOutbox,
    ManualMedia,
    MediaTranscription,
    QaMedia,
    RegistrationSession,
    StoreApprovalRequest,
)
from tests.factories import (
    NOW,
    make_application,
    make_interview,
    make_invitation,
    make_job,
    make_manual_draft,
    make_notification,
    make_question_set,
    make_request,
    make_store,
    make_user,
    make_worker,
)
from tests.schema_checks import (
    collation_differences,
    database_collations,
    enum_columns,
    model_collations,
)

ENUM_COLUMNS = enum_columns(Base.metadata)
IDENTIFIER_COLUMNS = [
    ("oauth_transactions", "token_hash"), ("oauth_transactions", "state_hash"),
    ("oauth_transactions", "nonce_hash"),
    ("users", "google_sub"), ("store_invitations", "token_hash"),
    ("auth_sessions", "token_hash"), ("registration_sessions", "token_hash"),
    ("registration_sessions", "google_sub"),
    ("idempotency_records", "subject_id"), ("idempotency_records", "idempotency_key"),
    ("idempotency_records", "endpoint"), ("idempotency_records", "request_hash"),
    ("notifications", "dedupe_key"),
    ("background_tasks", "last_error_code"),
    ("manual_media", "object_key"), ("manual_media", "mime_type"),
    ("qa_media", "object_key"), ("qa_media", "mime_type"),
    ("media_transcriptions", "error_code"),
]
CASE_SENSITIVE = [*ENUM_COLUMNS, *IDENTIFIER_COLUMNS]
EMAIL_COLUMNS = [
    ("users", "google_email"), ("store_invitations", "invited_email"),
    ("registration_sessions", "google_email"),
]


def test_every_enum_check_column_is_discovered():
    assert len(ENUM_COLUMNS) == 52  # a new enum column must be added to this count consciously
    assert ("users", "role") in ENUM_COLUMNS and ("application_selection_effects", "previous_status") in ENUM_COLUMNS
    assert ("idempotency_records", "state") in ENUM_COLUMNS


def test_models_ask_for_case_sensitive_collation_exactly_where_intended():
    collations = model_collations(Base.metadata)
    for key in CASE_SENSITIVE:
        assert collations[key] == "utf8mb4_0900_as_cs", key
    for key in EMAIL_COLUMNS:  # case-insensitive, accent-sensitive (see docs/erd/README.md)
        assert collations[key] == "utf8mb4_0900_as_ci", key


def test_collation_comparison_notices_drift():
    model = model_collations(Base.metadata)
    database = {key: (value or "utf8mb4_0900_ai_ci") for key, value in model.items()}
    assert collation_differences(model, database) == []
    database[("users", "role")] = "utf8mb4_0900_ai_ci"
    database[("users", "google_email")] = "utf8mb4_0900_ai_ci"  # the accent-folding default
    problems = collation_differences(model, database)
    assert len(problems) == 2
    assert "users.role" in problems[1] or "users.role" in problems[0]
    assert any("users.google_email" in problem for problem in problems)


@pytest.mark.mysql
def test_mysql_columns_have_the_collation_the_models_declare(mysql_engine):
    with mysql_engine.connect() as connection:
        actual = database_collations(connection)
    model = model_collations(Base.metadata)
    assert collation_differences(model, actual) == []
    for key in CASE_SENSITIVE:
        assert actual[key] == "utf8mb4_0900_as_cs", key
    for key in EMAIL_COLUMNS:
        assert actual[key] == "utf8mb4_0900_as_ci", key


@pytest.fixture
def enum_rows(session):
    """One valid row in every table that has an enum-like column."""
    owner = make_user(session, "OWNER")
    worker = make_worker(session)
    store = make_store(session, owner=owner)
    session.add(StoreApprovalRequest(store_id=store.id))
    job = make_job(session, store)
    application = make_application(session, job, worker)
    request = make_request(session, application, owner.id)
    session.add(ApplicationSelectionEffect(
        request_id=request.id, application_id=application.id, previous_status="APPLIED",
        applied_revision=1,
    ))
    rule = AvailabilityRule(
        worker_id=worker.id, sort_order=0, start_time=NOW.time(), end_time=NOW.time(),
        ends_next_day=True,
    )
    session.add(rule)
    session.flush()
    session.add(AvailabilityDay(rule_id=rule.id, weekday="MON"))
    session.add(IdempotencyRecord(
        subject_id="a" * 64, idempotency_key=str(uuid.uuid4()), endpoint="POST /api/x",
        request_hash="b" * 64, state="PROCESSING", expires_at=NOW,
    ))
    make_notification(session, worker)
    session.add(InvitationMailOutbox(
        invitation_id=make_invitation(session, store).id, status="QUEUED", payload="x",
    ))
    session.add(BackgroundTask(
        kind="TRANSCRIPTION", subject_id=str(uuid.uuid4()), attempt=1, status="QUEUED", tries=0,
        max_tries=3, payload={},
    ))
    media = ManualMedia(
        store_id=store.id, uploaded_by_owner_id=owner.id, kind="AUDIO", object_key="a/" + str(uuid.uuid4()),
        mime_type="audio/wav", byte_size=10, duration_ms=1000, created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    session.add_all([media, QaMedia(
        store_id=store.id, worker_id=worker.id, kind="IMAGE", object_key="q/" + str(uuid.uuid4()),
        mime_type="image/png", byte_size=10, created_at=NOW, expires_at=NOW + timedelta(days=1),
    )])
    session.flush()
    session.add(MediaTranscription(
        store_id=store.id, manual_media_id=media.id, status="ERROR", error_code="TRANSCRIPTION_FAILED",
        attempt=1, completed_at=NOW,
    ))
    session.flush()
    add_manual_rows(session, store)
    return session


def add_manual_rows(session, store):
    """Manual/interview rows where every enum column holds a valid value somewhere."""
    from app.db.models import (
        InterviewEvaluation,
        InterviewIntentReview,
        InterviewProbeBatch,
        InterviewTurn,
        ManualDraftCorrection,
        ManualMediaSnapshotRef,
        ManualReviewIssue,
        ManualSection,
    )

    question_set, intents = make_question_set(session)
    version = make_manual_draft(session, store)
    task = str(uuid.uuid4())
    interview = make_interview(
        session, version, question_set, intents, status="ERROR", error_code="AI_PROCESSING_FAILED",
        processing_kind="EVALUATION", processing_task_id=task, processing_attempt=1,
    )
    intent = intents[0]
    session.add(InterviewIntentReview(
        session_id=interview.id, intent_id=intent.id, status="ERROR", error_code="AI_PROCESSING_FAILED",
        processing_kind="CORRECTION", processing_task_id=task, processing_attempt=1,
    ))
    batch = InterviewProbeBatch(session_id=interview.id, intent_id=intent.id, depth=1, status="ERROR",
                                error_code="TIMEOUT")
    session.add(batch)
    session.flush()
    question = InterviewTurn(session_id=interview.id, turn_no=1, speaker="AI", turn_kind="QUESTION",
                             question_kind="BASE", intent_id=intent.id, content="질문")
    session.add(question)
    session.flush()
    answer = InterviewTurn(session_id=interview.id, turn_no=2, speaker="OWNER", turn_kind="ANSWER",
                           intent_id=intent.id, reply_to_question_turn_id=question.id,
                           input_method="TEXT", content="답변")
    session.add(answer)
    session.flush()
    session.add_all([
        InterviewEvaluation(
            session_id=interview.id, intent_id=intent.id, depth=0, attempt_no=1,
            evaluated_through_turn_id=answer.id, input_snapshot={}, evaluation_config_version="v",
            provider="fake", status="FAILED", error_code="INVALID_OUTPUT",
        ),
        ManualSection(version_id=version.id, sort_order=0, category="RULE", title="복장"),
        ManualReviewIssue(version_id=version.id, description="부족", target_kind="MANUAL",
                          field_name="shifts", public_description="근무조 미정"),
        ManualDraftCorrection(
            version_id=version.id, base_revision=1, target_kind="MANUAL", input_method="TEXT",
            input_text="고쳐 줘", status="ERROR", error_code="CORRECTION_CLARIFICATION_REQUIRED",
            requested_by_owner_id=store.owner_id, completed_at=NOW,
        ),
        ManualMediaSnapshotRef(media_id=session.scalars(text("SELECT id FROM manual_media")).first(),
                               holder_kind="INTENT_REVIEW", holder_id=interview.id,
                               holder_intent_id=intent.id),
    ])
    session.flush()
    add_qa_rows(session, store, version)


def add_qa_rows(session, store, version):
    from app.db.models import ManualQa, ManualQaConversation, User

    version.status, version.generation_status = "PUBLISHED", "READY"
    version.published_at, version.published_by_owner_id = NOW, store.owner_id
    worker = session.scalars(select(User).where(User.role == "WORKER")).first()
    conversation = ManualQaConversation(store_id=store.id, worker_id=worker.id)
    session.add(conversation)
    session.flush()
    common = {"conversation_id": conversation.id, "published_version_id": version.id,
              "input_method": "TEXT", "question": "마감은 언제 해요?", "completed_at": NOW}
    session.add_all([
        ManualQa(sequence=1, status="READY", outcome="NEEDS_OWNER", answer="점주 확인이 필요해요.", **common),
        ManualQa(sequence=2, status="ERROR", public_error_code="AI_PROCESSING_FAILED", **common),
    ])
    session.flush()


@pytest.mark.parametrize("table,column", ENUM_COLUMNS)
@pytest.mark.parametrize("variant", ["lower", "title", "padded"])
def test_enum_column_rejects_other_letter_case(enum_rows, table, column, variant):
    session = enum_rows
    # Optional enum columns (e.g. question_kind of an answer) may be NULL in some rows.
    stored = session.execute(
        text(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT 1")).scalar_one()
    assert stored == stored.upper(), "fixture must hold a valid upper-case value"
    wrong = {"lower": stored.lower(), "title": stored.title(), "padded": stored + " "}[variant]
    assert wrong != stored
    # MySQL silently drops spaces beyond the column length (weekday VARCHAR(3): 'MON ' -> 'MON').
    truncates = variant == "padded" and len(wrong) > column_length(table, column)
    try:
        with session.begin_nested():
            session.execute(
                text(f"UPDATE {table} SET {column} = :value WHERE {column} IS NOT NULL"), {"value": wrong})
    except DBAPIError as error:
        # SQLite: IntegrityError; MySQL: OperationalError 3819 (CHECK violated).
        assert "CHECK" in str(error).upper() or error.orig.args[0] == 3819
    else:
        assert truncates, f"{wrong!r} was accepted by {table}.{column}"
    after = session.execute(
        text(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT 1")).scalar_one()
    assert after == stored


def column_length(table, column) -> int:
    return Base.metadata.tables[table].c[column].type.length


def test_idempotency_and_session_identifiers_are_case_sensitive(session):
    """Distinct-case values are distinct rows; only the app (not the DB) lower-cases the key."""
    lower = "0b9a3c1e-5d2f-4a6b-8c7d-1e2f3a4b5c6d"
    for index, key in enumerate((lower, lower.upper())):
        session.add(IdempotencyRecord(
            subject_id="a" * 64, idempotency_key=key, endpoint="POST /api/stores",
            request_hash=f"{index}" * 64, state="PROCESSING", expires_at=NOW,
        ))
    session.add(IdempotencyRecord(
        subject_id="a" * 64, idempotency_key=str(uuid.uuid4()), endpoint="POST /api/Stores",
        request_hash="b" * 64, state="PROCESSING", expires_at=NOW,
    ))
    session.flush()  # no unique violation: 'abc…' and 'ABC…' differ
    count = session.execute(text(
        "SELECT COUNT(*) FROM idempotency_records WHERE endpoint = 'POST /api/stores'")).scalar_one()
    assert count == 2


def test_google_sub_and_token_hash_are_case_sensitive(session):
    make_user(session, google_sub="AbCdEf")
    make_user(session, google_sub="abcdef")  # a distinct subject, not a duplicate
    store = make_store(session)
    make_invitation(session, store, token_hash="A" * 64)
    make_invitation(session, store, token_hash="a" * 64)
    make_user(session, google_sub="abcdef ")  # trailing space is a different value (NO PAD)
    found = session.execute(
        text("SELECT COUNT(*) FROM users WHERE google_sub = 'abcdef'")).scalar_one()
    assert found == 1  # a lookup by the exact value finds exactly that subject


def test_emails_stay_case_insensitive_on_mysql(session):
    """Intentional: invitation emails are compared case-insensitively (normalized in the service)."""
    make_user(session, google_email="Person@Example.com")
    count = session.execute(
        text("SELECT COUNT(*) FROM users WHERE google_email = 'person@example.com'")).scalar_one()
    assert count == (0 if session.get_bind().dialect.name == "sqlite" else 1)


@pytest.mark.parametrize("stored, other, same_on_mysql", [
    ("JOSE@x.com", "jose@x.com", True),  # case still folds (the ERD decision)
    ("josé@x.com", "jose@x.com", False),  # accents no longer fold
    ("JOSÉ@x.com", "josé@x.com", True),  # case folds on accented letters too
    ("straße@x.com", "strasse@x.com", False),  # nor does ß = ss
    ("jose@x.com ", "jose@x.com", False),  # NO PAD
    ("ｊｏｓｅ@x.com", "jose@x.com", True),  # known: full-width still equal (email_is covers it)
    ("jo\u200bse@x.com", "jose@x.com", True),  # known: zero-width still ignored (email_is covers it)
])
def test_email_columns_compare_case_but_not_accent_insensitively(session, stored, other, same_on_mysql):
    store = make_store(session)
    make_user(session, google_email=stored)
    make_invitation(session, store, invited_email=stored)
    session.add(RegistrationSession(
        token_hash=uuid.uuid4().hex * 2, google_sub="sub-registration", google_email=stored,
        email_verified=True, expires_at=NOW,
    ))
    session.flush()
    mysql = session.get_bind().dialect.name == "mysql"
    for table, column in EMAIL_COLUMNS:
        count = session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :other"), {"other": other}).scalar_one()
        assert count == (1 if mysql and same_on_mysql else 0), (table, stored)


def test_inspector_sees_the_enum_checks(engine):
    assert any(c["name"] == "ck_users_role" for c in inspect(engine).get_check_constraints("users"))

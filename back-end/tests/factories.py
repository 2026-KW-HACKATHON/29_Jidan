import itertools
from datetime import UTC, date, datetime, time, timedelta

from app.db.models import (
    JobApplication,
    JobPosting,
    ShiftAssignment,
    Store,
    StoreInvitation,
    User,
    WorkerProfile,
    WorkRequest,
)

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
# Unbounded: the full suite creates more than 10,000 factory rows, and a bounded range raised
# StopIteration in whichever tests happened to run last.
_counter = itertools.count(1)


def make_user(session, role="WORKER", **overrides) -> User:
    n = next(_counter)
    user = User(
        google_sub=f"sub-{n}", google_email=f"user{n}@example.com", email_verified=True,
        role=role, name="테스터", phone_number="01012345678",
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    session.add(user)
    session.flush()
    return user


def make_worker(session) -> User:
    user = make_user(session, "WORKER")
    session.add(WorkerProfile(
        user_id=user.id, birth_date=date(2000, 1, 1), gender="FEMALE", experience_level="NEW",
    ))
    session.flush()
    return user


def make_store(session, owner=None, **overrides) -> Store:
    n = next(_counter)
    store = Store(
        owner_id=(owner or make_user(session, "OWNER")).id, name="월계 카페", industry="CAFE",
        postal_code="01234", address="서울 노원구 월계1동", business_registration_number=f"{n:010d}",
        phone_number="021234567",
    )
    for key, value in overrides.items():
        setattr(store, key, value)
    session.add(store)
    session.flush()
    return store


def make_job(session, store=None, **overrides) -> JobPosting:
    store = store or make_store(session)
    job = JobPosting(
        store_id=store.id, created_by_owner_id=store.owner_id, title="저녁 대타",
        duty_description="홀 서빙", work_part="WEEKDAY_CLOSE", work_date=date(2026, 10, 10),
        start_time=time(18, 0), end_time=time(22, 0), ends_next_day=False, hourly_wage_krw=11000,
        payment_timing="WORK_DAY",
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    session.add(job)
    session.flush()
    return job


def make_application(session, job, worker=None, **overrides) -> JobApplication:
    application = JobApplication(
        job_id=job.id, worker_id=(worker or make_worker(session)).id, introduction="열심히 하겠습니다",
        applicant_name="테스터", age_at_submission=25, experience_level="NEW",
    )
    for key, value in overrides.items():
        setattr(application, key, value)
    session.add(application)
    session.flush()
    return application


def make_request(session, application, owner_id, **overrides) -> WorkRequest:
    request = WorkRequest(
        application_id=application.id, requested_by_owner_id=owner_id, requested_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    for key, value in overrides.items():
        setattr(request, key, value)
    session.add(request)
    session.flush()
    return request


def make_shift(session, job, request, worker_id, **overrides) -> ShiftAssignment:
    shift = ShiftAssignment(
        job_id=job.id, work_request_id=request.id, worker_id=worker_id, confirmed_at=NOW,
    )
    for key, value in overrides.items():
        setattr(shift, key, value)
    session.add(shift)
    session.flush()
    return shift


def make_invitation(session, store, **overrides) -> StoreInvitation:
    n = next(_counter)
    invitation = StoreInvitation(
        store_id=store.id, inviter_owner_id=store.owner_id, invited_email="worker@example.com",
        token_hash=f"{n:064x}", created_at=NOW, last_sent_at=NOW, expires_at=NOW + timedelta(days=7),
    )
    for key, value in overrides.items():
        setattr(invitation, key, value)
    session.add(invitation)
    session.flush()
    return invitation


# --- store domain (#107) ---------------------------------------------------------------------

def make_store_with_request(session, owner=None, *, approved_at=None, submitted_at=None, **overrides):
    """A store and its approval request, PENDING or (with `approved_at`) APPROVED on both rows."""
    from app.db.models import StoreApprovalRequest

    status = "APPROVED" if approved_at is not None else "PENDING"
    store = make_store(session, owner, approval_status=status, approved_at=approved_at, **overrides)
    request = StoreApprovalRequest(
        store_id=store.id, status=status, approved_at=approved_at,
        submitted_at=submitted_at or store.created_at,
    )
    session.add(request)
    session.flush()
    return store, request


def make_regular_grant(session, store, worker, *, granted_at=NOW, **overrides):
    """A REGULAR access grant from an invitation `worker` accepted at `granted_at`."""
    from app.db.models import StoreAccessGrant

    created = granted_at - timedelta(hours=1)
    invitation = make_invitation(
        session, store, invited_email=worker.google_email, created_at=created, last_sent_at=created,
        expires_at=created + timedelta(days=7), accepted_by_worker_id=worker.id, accepted_at=granted_at,
        access_expires_at=overrides.get("valid_until"),
    )
    grant = StoreAccessGrant(
        store_id=store.id, worker_id=worker.id, invitation_id=invitation.id, granted_at=granted_at,
    )
    for key, value in overrides.items():
        setattr(grant, key, value)
    session.add(grant)
    session.flush()
    return grant

def make_notification(session, recipient, **overrides):
    """A WORK_REQUEST_RECEIVED notification through the real recording function."""
    from app.notifications import NotificationType, WorkRequestTarget, record_notification

    n = next(_counter)
    arguments = {
        "recipient_user_id": recipient.id, "type": NotificationType.WORK_REQUEST_RECEIVED,
        "target": WorkRequestTarget(
            request_id=f"00000000-0000-4000-8000-{n:012d}", store_id=f"00000000-0000-4000-9000-{n:012d}",
            job_id=f"00000000-0000-4000-a000-{n:012d}",
        ),
        "event_key": f"event-{n}", "body": "월계 카페 10월 10일 18:00–22:00", "created_at": NOW,
    }
    arguments.update(overrides)
    return record_notification(session, **arguments)


def make_temporary_grant(session, store, worker, *, granted_at=NOW, valid_until, title="저녁 대타", **overrides):
    """A TEMPORARY access grant from a confirmed shift of a job posting titled `title`."""
    from app.db.models import StoreAccessGrant

    job = make_job(session, store, title=title)
    application = make_application(session, job, worker)
    request = make_request(session, application, store.owner_id, status="ACCEPTED", responded_at=NOW,
                           ended_at=NOW)
    shift = make_shift(session, job, request, worker.id)
    grant = StoreAccessGrant(
        store_id=store.id, worker_id=worker.id, assignment_id=shift.id, granted_at=granted_at,
        valid_until=valid_until,
    )
    for key, value in overrides.items():
        setattr(grant, key, value)
    session.add(grant)
    session.flush()
    return grant
# --- manuals and AI interviews (ai-core) ---------------------------------------------------------

DEFAULT_INTENTS = (
    ("work_structure", "WORK_STRUCTURE", "근무조와 근무 시간을 알려 주세요."),
    ("common_tasks", "COMMON_TASKS", "모든 근무조가 공통으로 하는 일을 알려 주세요."),
    ("closing_tasks", "SHIFT_TASKS", "마감할 때 어떤 일을 하나요?"),
)


def make_question_set(session, intents=DEFAULT_INTENTS, revision_no=None):
    """A question set and its ordered intents: (InterviewQuestionSet, [InterviewIntent])."""
    from app.db.models import InterviewIntent, InterviewQuestionSet

    # Above the versions seeded by migrations (0040 seeds revision 1).
    question_set = InterviewQuestionSet(revision_no=revision_no or 1000 + next(_counter))
    session.add(question_set)
    session.flush()
    rows = []
    for order, (key, stage, question) in enumerate(intents):
        rows.append(InterviewIntent(
            question_set_id=question_set.id, sort_order=order, intent_key=key, stage=stage,
            base_question=question, coverage_criteria=f"{key}의 순서, 기준, 예외",
        ))
    session.add_all(rows)
    session.flush()
    return question_set, rows


def make_manual_draft(session, store, **overrides):
    """The store's manual row (created on first use) and a new DRAFT version of it."""
    from sqlalchemy import func, select

    from app.db.models import ManualVersion, StoreManual

    manual = session.scalars(select(StoreManual).where(StoreManual.store_id == store.id)).first()
    if manual is None:
        manual = StoreManual(store_id=store.id)
        session.add(manual)
        session.flush()
    number = session.scalar(
        select(func.coalesce(func.max(ManualVersion.revision_no), 0)).where(ManualVersion.manual_id == manual.id))
    version = ManualVersion(
        manual_id=manual.id, revision_no=number + 1, created_by_owner_id=store.owner_id,
    )
    for key, value in overrides.items():
        setattr(version, key, value)
    session.add(version)
    session.flush()
    return version


def make_interview(session, version, question_set, intents, **overrides):
    """An IN_PROGRESS session on `version` with a PENDING progress row per intent."""
    from app.db.models import InterviewSession, InterviewSessionIntent, Store, StoreManual

    manual = session.get(StoreManual, version.manual_id)
    owner_id = session.get(Store, manual.store_id).owner_id
    interview = InterviewSession(
        manual_version_id=version.id, owner_id=owner_id, question_set_id=question_set.id,
        current_intent_id=intents[0].id,
    )
    for key, value in overrides.items():
        setattr(interview, key, value)
    session.add(interview)
    session.flush()
    session.add_all([InterviewSessionIntent(session_id=interview.id, intent_id=i.id) for i in intents])
    session.flush()
    return interview

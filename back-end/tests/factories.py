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
_counter = iter(range(1, 10_000))


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

"""What each domain transition notifies: recipients, text, typed target and event key.

Every function here is called inside the transition's own transaction, after the transition's
domain locks and writes, and never commits. Lock order (docs/notification-design.md "연결 지점과
잠금"): the domain locks first (store/approval request, or posting -> request -> application ->
worker -> shift -> grant), then `record_notification`, which only inserts a `notifications` row
(plus the FK's shared lock on the recipient's `users` row) and locks that new row. It never
locks a domain row, so it cannot close a cycle with the domain lock order.

Bodies carry the store name, posting title and Seoul date/time only, never a person's name,
e-mail or phone. When a store name or title trips the sensitive-text filter, a neutral body is
used so a notification can never fail the transition it belongs to.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    JobApplication,
    JobPosting,
    ManualVersion,
    ShiftAssignment,
    Store,
    StoreAccessGrant,
    StoreInvitation,
    User,
    WorkRequest,
)
from app.email_match import email_is
from app.notifications import (
    JobApplicationTarget,
    ManualTarget,
    NotificationType,
    StoreInvitationTarget,
    StoreTarget,
    WorkRequestTarget,
    WorkScheduleTarget,
    body_or,
    notification_event_key,
    record_notification,
)
from app.store_access import valid_grant_clause


def job_when(job: JobPosting) -> str:
    """`10월 10일 09:00–14:00`, with `(익일)` when the shift ends the next day (Seoul wall clock)."""
    end = f"{job.end_time:%H:%M}" + ("(익일)" if job.ends_next_day else "")
    return f"{job.work_date.month}월 {job.work_date.day}일 {job.start_time:%H:%M}–{end}"


def _shift_body(store: Store, job: JobPosting, suffix: str = "") -> str:
    when = job_when(job)
    return body_or(f"{store.name} {when}{suffix}", f"{when}{suffix or ' 근무'}")


def _request_target(request: WorkRequest, job: JobPosting) -> WorkRequestTarget:
    return WorkRequestTarget(request_id=request.id, store_id=job.store_id, job_id=job.id)


# --- stores and invitations -------------------------------------------------------------------


def store_approved(db: Session, store: Store, *, at=None) -> None:
    """STORE_APPROVED to the owner when the store first becomes APPROVED."""
    record_notification(
        db, recipient_user_id=store.owner_id, type=NotificationType.STORE_APPROVED,
        target=StoreTarget(store_id=store.id), event_key=store.id,
        body=body_or(f"{store.name} 매장 승인이 완료됐어요", "매장 승인이 완료됐어요"), created_at=at,
    )


def invitees(db: Session, invited_email: str) -> list[str]:
    """Members who can open this invitation in their inbox: ACTIVE WORKERs with a verified Google
    e-mail equal to the invited (normalized) e-mail -- the same match the inbox API applies."""
    rows = db.scalars(
        select(User.id).where(
            User.role == "WORKER", User.status == "ACTIVE", User.email_verified.is_(True),
            # Exact equality, not LIKE ('_' and '%' are literal) nor the collation's accent folding;
            # TRIM + lower like the inbox's normalize_email.
            email_is(func.lower(func.trim(User.google_email)), invited_email),
        )
    ).all()
    return sorted(rows)


def store_invited(db: Session, invitation: StoreInvitation, store: Store) -> None:
    """STORE_INVITED to each registered invitee, once per send (create and every resend).

    No row for an e-mail without a member: a notification needs a recipient, and the invitee
    reaches the invitation through the e-mail link or, after signing up, the inbox.
    """
    for user_id in invitees(db, invitation.invited_email):
        record_notification(
            db, recipient_user_id=user_id, type=NotificationType.STORE_INVITED,
            target=StoreInvitationTarget(invitation_id=invitation.id),
            event_key=notification_event_key(invitation.id, invitation.last_sent_at),
            body=body_or(f"{store.name}에서 근무자로 초대했어요", "매장에서 근무자로 초대했어요"),
            created_at=invitation.last_sent_at,
        )


def invitation_accepted(db: Session, invitation: StoreInvitation, store: Store) -> None:
    """INVITATION_ACCEPTED to the store owner; the worker's name is not part of the text."""
    record_notification(
        db, recipient_user_id=store.owner_id, type=NotificationType.INVITATION_ACCEPTED,
        target=StoreTarget(store_id=store.id), event_key=invitation.id,
        body=body_or(f"{store.name} 근무자 초대가 수락됐어요", "근무자 초대가 수락됐어요"),
        created_at=invitation.accepted_at,
    )


def invitation_expired(db: Session, invitation: StoreInvitation, store: Store, deadline) -> None:
    """INVITATION_EXPIRED to the store owner. An invitation expires at most once (an expired one
    cannot be resent), so the invitation ID is the event."""
    record_notification(
        db, recipient_user_id=store.owner_id, type=NotificationType.INVITATION_EXPIRED,
        target=StoreTarget(store_id=store.id), event_key=invitation.id,
        body=body_or(f"{store.name} 근무자 초대가 응답 없이 만료됐어요", "근무자 초대가 응답 없이 만료됐어요"),
        created_at=deadline,
    )


# --- jobs ---------------------------------------------------------------------------------------


def new_application(db: Session, application: JobApplication, job: JobPosting) -> None:
    """NEW_APPLICATION to the store owner (applicant name stays out of the text)."""
    store = db.get(Store, job.store_id)
    record_notification(
        db, recipient_user_id=store.owner_id, type=NotificationType.NEW_APPLICATION,
        target=JobApplicationTarget(application_id=application.id, store_id=store.id, job_id=job.id),
        event_key=application.id,
        body=body_or(f"{job.title} 공고에 새 지원이 있어요", f"{job_when(job)} 공고에 새 지원이 있어요"),
        created_at=application.applied_at,
    )


def work_request_received(db: Session, request: WorkRequest, application: JobApplication, job: JobPosting) -> None:
    store = db.get(Store, job.store_id)
    record_notification(
        db, recipient_user_id=application.worker_id, type=NotificationType.WORK_REQUEST_RECEIVED,
        target=_request_target(request, job), event_key=request.id,
        body=_shift_body(store, job), created_at=request.requested_at,
    )


def work_request_withdrawn(db: Session, request: WorkRequest, application: JobApplication, job: JobPosting) -> None:
    store = db.get(Store, job.store_id)
    record_notification(
        db, recipient_user_id=application.worker_id, type=NotificationType.WORK_REQUEST_WITHDRAWN,
        target=_request_target(request, job), event_key=request.id,
        body=_shift_body(store, job), created_at=request.ended_at,
    )


def work_request_no_response(db: Session, request: WorkRequest) -> None:
    """WORK_REQUEST_NO_RESPONSE to the owner who sent the request, stamped at its deadline."""
    application = db.get(JobApplication, request.application_id)
    job = db.get(JobPosting, application.job_id)
    store = db.get(Store, job.store_id)
    record_notification(
        db, recipient_user_id=request.requested_by_owner_id, type=NotificationType.WORK_REQUEST_NO_RESPONSE,
        target=_request_target(request, job), event_key=request.id,
        body=_shift_body(store, job, " 요청이 응답 없이 만료됐어요"), created_at=request.expires_at,
    )


def work_confirmed(db: Session, request: WorkRequest, application: JobApplication, job: JobPosting) -> None:
    """WORK_CONFIRMED to the worker and the store owner; both open the confirmed shift."""
    store = db.get(Store, job.store_id)
    shift = db.scalar(select(ShiftAssignment).where(ShiftAssignment.work_request_id == request.id))
    target = WorkScheduleTarget(event_id=shift.id, store_id=store.id, work_date=job.work_date)
    for recipient in (application.worker_id, store.owner_id):
        record_notification(
            db, recipient_user_id=recipient, type=NotificationType.WORK_CONFIRMED, target=target,
            event_key=shift.id, body=_shift_body(store, job), created_at=shift.confirmed_at,
        )


def work_confirmation_withdrawn(db: Session, request: WorkRequest, application: JobApplication,
                                job: JobPosting) -> None:
    """WORK_CONFIRMATION_WITHDRAWN to the worker; it opens the ended request, not the schedule."""
    store = db.get(Store, job.store_id)
    record_notification(
        db, recipient_user_id=application.worker_id, type=NotificationType.WORK_CONFIRMATION_WITHDRAWN,
        target=_request_target(request, job), event_key=request.id,
        body=_shift_body(store, job), created_at=request.ended_at,
    )


# --- manuals ------------------------------------------------------------------------------------


def manual_readers(db: Session, store: Store, at) -> list[str]:
    """ACTIVE WORKERs holding a grant of `store` valid at `at` (regular or temporary): the
    people who can open the published manual right now (app.store_access rules)."""
    return sorted(set(db.scalars(
        select(StoreAccessGrant.worker_id)
        .join(User, User.id == StoreAccessGrant.worker_id)
        .where(StoreAccessGrant.store_id == store.id, valid_grant_clause(at),
               User.role == "WORKER", User.status == "ACTIVE")
    )))


def manual_published(db: Session, store: Store, version: ManualVersion, *, at) -> None:
    """MANUAL_PUBLISHED to every current reader, once per published version (event key)."""
    for worker_id in manual_readers(db, store, at):
        record_notification(
            db, recipient_user_id=worker_id, type=NotificationType.MANUAL_PUBLISHED,
            target=ManualTarget(store_id=store.id), event_key=version.id,
            body=body_or(f"{store.name} 매뉴얼이 새로 게시됐어요", "매장 매뉴얼이 새로 게시됐어요"),
            created_at=at,
        )

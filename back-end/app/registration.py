"""Atomic account registration and idempotent retries for the verified Google subject."""
from datetime import time

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app import auth
from app.auth_views import session_body
from app.csrf import CsrfMemberOrRegistration
from app.db import SessionDep, utcnow
from app.db.models import (
    AvailabilityDay,
    AvailabilityRule,
    RegistrationSession,
    Store,
    StoreApprovalRequest,
    User,
    WorkerCareer,
    WorkerProfile,
)
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, is_lock_contention, run_idempotent
from app.registration_inputs import OwnerInput, WorkerInput
from app.store_address import verify_store_address

router = APIRouter(prefix="/api/auth/registrations")

# Attempts of the whole signup transaction when MySQL picks it as a deadlock victim.
REGISTRATION_ATTEMPTS = 3


def new_user(db, principal, body, role):
    if db.scalar(select(User.id).where(User.google_sub == principal.google_sub)) is not None:
        raise ApiError(409, ErrorCode.ALREADY_REGISTERED)
    if not isinstance(principal, auth.RegistrationPrincipal):
        raise ApiError(409, ErrorCode.ALREADY_REGISTERED)
    row = db.scalar(select(RegistrationSession).where(
        RegistrationSession.id == principal.registration_id,
    ).with_for_update())
    if row is None or row.consumed_at is not None or row.expires_at <= utcnow():
        raise ApiError(401, ErrorCode.SESSION_EXPIRED)
    if not row.email_verified:
        raise ApiError(401, ErrorCode.GOOGLE_IDENTITY_INVALID)
    user = User(google_sub=row.google_sub, google_email=row.google_email, email_verified=True,
                role=role, status="ACTIVE", name=body.name, phone_number=body.phoneNumber)
    db.add(user)
    db.flush()
    return user


def register(db, principal, key, path, body, create):
    def work():
        nonlocal issued
        user = create()
        if not auth.consume_registration_session(principal.registration_id, db=db):
            raise ApiError(401, ErrorCode.SESSION_EXPIRED)
        issued = auth.create_session(user.id, db=db)
        return IdempotentResult(201, session_body(db, user, min(issued.expires_at, utcnow() + auth.SESSION_IDLE_TIMEOUT)))

    def revalidate():
        user = db.scalar(select(User).where(User.google_sub == principal.google_sub))
        if user is None or user.status != "ACTIVE":
            raise ApiError(403, ErrorCode.ACCOUNT_SUSPENDED)

    for attempt in range(1, REGISTRATION_ATTEMPTS + 1):
        issued = None
        try:
            response = run_idempotent(db=db, principal=principal, key=key, method="POST",
                                      path=path, body=body, handler=work, revalidate=revalidate)
            break
        except OperationalError as error:
            # Signups waiting on the same new business number hold shared locks on it; when the
            # one that inserted it rolls back they deadlock and MySQL kills all but one (1213).
            # run_idempotent rolled everything back (registration session included) and
            # released the key, so the whole signup runs again and sees the survivor (409).
            if not is_lock_contention(error) or attempt == REGISTRATION_ATTEMPTS:
                raise
        except IntegrityError:
            # run_idempotent rolled back before this fresh read. Only known unique conflicts map
            # to public codes; unrelated database errors are never presented as duplicate accounts.
            if db.scalar(select(User.id).where(User.google_sub == principal.google_sub)) is not None:
                raise ApiError(409, ErrorCode.ALREADY_REGISTERED) from None
            if hasattr(body, "store") and db.scalar(select(Store.id).where(
                Store.business_registration_number == body.store.businessRegistrationNumber,
            )) is not None:
                raise ApiError(409, ErrorCode.STORE_ALREADY_REGISTERED) from None
            raise ApiError(500, ErrorCode.INTERNAL_ERROR) from None
    if issued is not None:
        # A replay deliberately does not rotate/create sessions or replay Set-Cookie.
        auth.set_session_cookie(response, issued)
        auth.clear_registration_cookie(response)
    return response


@router.post("/workers")
def register_worker(body: WorkerInput, principal: CsrfMemberOrRegistration,
                    db: SessionDep, key: IdempotencyKey):
    def create():
        user = new_user(db, principal, body, "WORKER")
        db.add(WorkerProfile(user_id=user.id, birth_date=body.birthDate,
                             gender=body.gender, experience_level=body.experienceLevel))
        db.flush()
        for index, career in enumerate(body.careers):
            db.add(WorkerCareer(worker_id=user.id, sort_order=index, industry=career.industry,
                               duties=career.duties, store_name=career.storeName,
                               start_month=career.startMonth, end_month=career.endMonth,
                               is_current=career.isCurrent))
        for index, availability in enumerate(body.availabilities):
            rule = AvailabilityRule(worker_id=user.id, sort_order=index,
                                    start_time=time.fromisoformat(availability.startTime),
                                    end_time=time.fromisoformat(availability.endTime),
                                    ends_next_day=availability.endsNextDay)
            db.add(rule)
            db.flush()
            db.add_all(AvailabilityDay(rule_id=rule.id, weekday=day) for day in availability.days)
        db.flush()
        return user
    return register(db, principal, key, "/api/auth/registrations/workers", body, create)


@router.post("/owners")
def register_owner(body: OwnerInput, principal: CsrfMemberOrRegistration,
                   db: SessionDep, key: IdempotencyKey):
    verified: list[str] = []

    def create():
        # Only the first idempotent execution reaches the provider (a deadlock retry reuses its
        # answer). No account write or registration-row lock is held while waiting for it.
        if not isinstance(principal, auth.RegistrationPrincipal):
            raise ApiError(409, ErrorCode.ALREADY_REGISTERED)
        store = body.store
        if not verified:
            verified.append(verify_store_address(store))
        canonical_address = verified[0]
        user = new_user(db, principal, body, "OWNER")
        if db.scalar(select(Store.id).where(
            Store.business_registration_number == store.businessRegistrationNumber,
        )) is not None:
            raise ApiError(409, ErrorCode.STORE_ALREADY_REGISTERED)
        row = Store(owner_id=user.id, name=store.name, industry=store.industry,
                    postal_code=store.postalCode, address=canonical_address,
                    detail_address=store.detailAddress,
                    business_registration_number=store.businessRegistrationNumber,
                    phone_number=store.phoneNumber, approval_status="PENDING")
        db.add(row)
        db.flush()
        db.add(StoreApprovalRequest(store_id=row.id, status="PENDING"))
        db.flush()
        return user
    return register(db, principal, key, "/api/auth/registrations/owners", body, create)

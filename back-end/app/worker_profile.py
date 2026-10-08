"""Session-owned worker profiles, serialized to the authoritative OpenAPI contract."""
from datetime import time
from functools import wraps

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth import CurrentWorker
from app.auth_views import identity
from app.csrf import CsrfWorker
from app.db import SessionDep, new_uuid, utcnow
from app.db.keyed import delete_by_key
from app.db.models import (
    WEEKDAYS,
    AvailabilityDay,
    AvailabilityRule,
    User,
    WorkerCareer,
    WorkerProfile,
)
from app.errors import ApiError, ErrorCode
from app.idempotency import is_lock_contention
from app.profile_inputs import AvailabilitiesInput, BasicInput, CareersInput

router = APIRouter(prefix="/api/users/me/profile")


def profile_rows(db: Session, user_id: str):
    profile = db.get(WorkerProfile, user_id)
    careers = db.scalars(select(WorkerCareer).where(WorkerCareer.worker_id == user_id)
                         .order_by(WorkerCareer.sort_order)).all()
    rules = db.scalars(select(AvailabilityRule).where(AvailabilityRule.worker_id == user_id)
                       .order_by(AvailabilityRule.sort_order)).all()
    days = db.scalars(select(AvailabilityDay)
                      .where(AvailabilityDay.rule_id.in_([r.id for r in rules]))).all()
    return profile, careers, rules, days


def profile_body(db: Session, user: User) -> dict:
    profile, careers, rules, days = profile_rows(db, user.id)
    return {
        "id": user.id, "role": "WORKER", "name": user.name,
        "phoneNumber": user.phone_number, "identity": identity(user.google_email),
        "birthDate": profile.birth_date.isoformat(), "gender": profile.gender,
        "experienceLevel": profile.experience_level, "updatedAt": user.updated_at.isoformat(),
        "careers": [
            {"industry": c.industry, "duties": c.duties, "startMonth": c.start_month,
             "endMonth": c.end_month, "isCurrent": c.is_current,
             **({"storeName": c.store_name} if c.store_name is not None else {})}
            for c in careers
        ],
        "availabilities": [
            {"days": sorted([d.weekday for d in days if d.rule_id == r.id], key=WEEKDAYS.index),
             "startTime": r.start_time.strftime("%H:%M"), "endTime": r.end_time.strftime("%H:%M"),
             "endsNextDay": r.ends_next_day}
            for r in rules
        ],
    }


@router.get("")
def get_profile(member: CurrentWorker, db: SessionDep) -> dict:
    return profile_body(db, db.get(User, member.user_id))


def lock_worker(db: Session, user_id: str) -> User:
    # The parent row lock serializes every profile area of one worker. End the authentication
    # read first so the REPEATABLE READ snapshot starts after the lock: children are then read
    # without locking reads, whose next-key locks on worker_id deadlock other workers' inserts.
    db.rollback()
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise ApiError(401, ErrorCode.SESSION_EXPIRED)
    if user.status != "ACTIVE":
        raise ApiError(403, ErrorCode.ACCOUNT_SUSPENDED)
    if user.role != "WORKER":
        raise ApiError(403, ErrorCode.FORBIDDEN)
    return user


def rewrite(db: Session, model, rows: list, worker_id: str, values: list[dict]) -> list:
    # Reuse rows by position so (worker_id, sort_order) keys are never deleted and reinserted;
    # remove only the surplus, by primary key. Neither takes gap locks shared with other workers.
    for row, value in zip(rows, values, strict=False):
        for column, item in value.items():
            setattr(row, column, item)
    if surplus := [row.id for row in rows[len(values):]]:
        delete_by_key(db, model, surplus, synchronize="auto")
    added = [model(id=new_uuid(), worker_id=worker_id, sort_order=index, **value)
             for index, value in enumerate(values) if index >= len(rows)]
    db.add_all(added)
    return [*rows[:len(values)], *added]


def commit_profile(db: Session, user: User) -> dict:
    db.flush()
    body = profile_body(db, user)
    # Construct the full response while the lock is held, but return only after durability.
    db.commit()
    return body


# Attempts of a whole profile write when MySQL picks it as a deadlock victim.
WRITE_ATTEMPTS = 3


def retry_lock_contention(handler):
    """Re-run a profile write chosen as a deadlock / lock wait victim (1213/1205).

    Children are rewritten in place by primary key, but a (rule_id, weekday) or
    (worker_id, sort_order) key deleted by an earlier save may still exist as a delete-marked
    record until purge. Re-inserting it makes InnoDB's duplicate check take a shared gap lock
    there, which can cross another worker's insert into the same gap. Nothing of the failed
    attempt is kept, and each attempt starts by re-locking the worker, so it is safe to repeat.
    """
    @wraps(handler)
    def run(**kwargs):
        for attempt in range(1, WRITE_ATTEMPTS + 1):
            try:
                return handler(**kwargs)
            except OperationalError as error:
                if not is_lock_contention(error) or attempt == WRITE_ATTEMPTS:
                    raise
                kwargs["db"].rollback()
        raise AssertionError("unreachable")
    return run


@router.patch("/basic")
@retry_lock_contention
def update_basic(body: BasicInput, member: CsrfWorker, db: SessionDep) -> dict:
    user = lock_worker(db, member.user_id)
    profile = db.get(WorkerProfile, user.id)
    changed = False
    targets = {"name": (user, "name"), "phoneNumber": (user, "phone_number"),
               "birthDate": (profile, "birth_date"), "gender": (profile, "gender")}
    for key, value in body.model_dump(exclude_unset=True).items():
        row, column = targets[key]
        if getattr(row, column) != value:
            setattr(row, column, value)
            changed = True
    if changed:
        user.updated_at = utcnow()
    return commit_profile(db, user)


@router.put("/careers")
@retry_lock_contention
def replace_careers(body: CareersInput, member: CsrfWorker, db: SessionDep) -> dict:
    user = lock_worker(db, member.user_id)
    current = profile_body(db, user)
    careers = [c.model_dump(exclude={"storeName"} if c.storeName is None else set())
               for c in body.careers]
    if current["experienceLevel"] != body.experienceLevel or current["careers"] != careers:
        profile, rows, _, _ = profile_rows(db, user.id)
        profile.experience_level = body.experienceLevel
        rewrite(db, WorkerCareer, list(rows), user.id, [
            {"industry": c.industry, "duties": c.duties, "store_name": c.storeName,
             "start_month": c.startMonth, "end_month": c.endMonth, "is_current": c.isCurrent}
            for c in body.careers
        ])
        user.updated_at = utcnow()
    return commit_profile(db, user)


@router.put("/availabilities")
@retry_lock_contention
def replace_availabilities(body: AvailabilitiesInput, member: CsrfWorker, db: SessionDep) -> dict:
    user = lock_worker(db, member.user_id)
    current = profile_body(db, user)
    groups = [{**group.model_dump(), "days": sorted(group.days, key=WEEKDAYS.index)}
              for group in body.availabilities]
    if current["availabilities"] != groups:
        _, _, old_rules, old_days = profile_rows(db, user.id)
        kept = {rule.id for rule in old_rules[:len(groups)]}
        # No cascading deletes: remove FK children of dropped rules and dropped weekdays first.
        wanted = {(rule.id, day) for rule, group in zip(old_rules, groups, strict=False)
                  for day in group["days"]}
        stale = [d.id for d in old_days if d.rule_id not in kept or (d.rule_id, d.weekday) not in wanted]
        if stale:
            delete_by_key(db, AvailabilityDay, stale, synchronize="auto")
        rules = rewrite(db, AvailabilityRule, list(old_rules), user.id, [
            {"start_time": time.fromisoformat(g.startTime), "end_time": time.fromisoformat(g.endTime),
             "ends_next_day": g.endsNextDay}
            for g in body.availabilities
        ])
        db.flush()  # new rules exist before their days reference them
        existing = {(d.rule_id, d.weekday) for d in old_days if d.rule_id in kept}
        db.add_all(AvailabilityDay(id=new_uuid(), rule_id=rule.id, weekday=day)
                   for rule, group in zip(rules, groups, strict=True) for day in group["days"]
                   if (rule.id, day) not in existing)
        user.updated_at = utcnow()
    return commit_profile(db, user)

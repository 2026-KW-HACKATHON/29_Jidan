"""Session-owned worker profiles, serialized to the authoritative OpenAPI contract."""
from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentWorker
from app.auth_views import identity
from app.db import SessionDep
from app.db.models import (
    WEEKDAYS,
    AvailabilityDay,
    AvailabilityRule,
    User,
    WorkerCareer,
    WorkerProfile,
)

router = APIRouter(prefix="/api/users/me/profile")


def profile_body(db: Session, user: User, *, lock: bool = False) -> dict:
    # Locking reads see current committed data even after authentication established a
    # MySQL REPEATABLE READ snapshot. Refresh objects already loaded by dependencies.
    def rows(statement):
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return db.scalars(statement).all()

    profile = rows(select(WorkerProfile).where(WorkerProfile.user_id == user.id))[0]
    careers = rows(select(WorkerCareer).where(WorkerCareer.worker_id == user.id)
                   .order_by(WorkerCareer.sort_order))
    rules = rows(select(AvailabilityRule).where(AvailabilityRule.worker_id == user.id)
                 .order_by(AvailabilityRule.sort_order))
    days = rows(select(AvailabilityDay).where(AvailabilityDay.rule_id.in_([r.id for r in rules])))
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

"""Baseline schema (P0 domains) transcribed from docs/erd/{auth,worker,store,access,jobs}.md.

Conventions: CHAR(36) UUID keys, UTC DATETIME(6), enum-like values as VARCHAR + CHECK.
Rules that need other tables (role of a referenced user, same-store consistency, overlap
checks, row-count limits, 30-minute alignment) are enforced in the service layer, not here.
Text rules whose meaning differs between SQLite and MySQL (digits-only, not-blank) use
app.db.checks so both databases enforce the same thing.
So is invited_email normalization: MySQL's default collation compares case-insensitively,
which makes a CHECK on it ineffective.
"""

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates

from app.db.checks import digits_only, not_blank
from app.db.types import UtcDateTime, cs_char, cs_string, new_uuid, normalize_optional_text, utcnow

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


ROLES = ("WORKER", "OWNER")
USER_STATUSES = ("ACTIVE", "SUSPENDED")
EXPERIENCE_LEVELS = ("NEW", "EXPERIENCED")
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
APPROVAL_STATUSES = ("PENDING", "APPROVED")
JOB_STATUSES = ("RECRUITING", "CLOSED")
WORK_PARTS = ("WEEKDAY_OPEN", "WEEKDAY_CLOSE", "WEEKEND_OPEN", "WEEKEND_CLOSE", "OTHER")
PAYMENT_TIMINGS = ("WORK_DAY", "NEXT_DAY", "NEGOTIABLE")
APPLICATION_STATUSES = (
    "APPLIED", "REQUESTED", "CONFIRMED", "WITHDRAWN", "NOT_SELECTED", "COMPLETED",
)
WORK_REQUEST_STATUSES = (
    "PENDING", "ACCEPTED", "DECLINED", "EXPIRED", "CANCELLED", "CONFIRMATION_WITHDRAWN",
)

# A shift ends the next day exactly when its end time is not after its start time;
# end == start with ends_next_day is the 24-hour shift.
SHIFT_SPAN = (
    "(ends_next_day = 0 AND end_time > start_time) OR (ends_next_day = 1 AND end_time <= start_time)"
)


def _id() -> Mapped[str]:
    return mapped_column(CHAR(36), primary_key=True, default=new_uuid)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", ROLES), name="role"),
        CheckConstraint(_in("status", USER_STATUSES), name="status"),
    )

    id: Mapped[str] = _id()
    google_sub: Mapped[str] = mapped_column(cs_string(255), unique=True)
    google_email: Mapped[str] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(Boolean)
    role: Mapped[str] = mapped_column(cs_string(16))
    status: Mapped[str] = mapped_column(cs_string(16), default="ACTIVE")
    name: Mapped[str] = mapped_column(String(100))
    phone_number: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class WorkerProfile(Base):
    __tablename__ = "worker_profiles"
    __table_args__ = (
        CheckConstraint(_in("gender", ("MALE", "FEMALE")), name="gender"),
        CheckConstraint(_in("experience_level", EXPERIENCE_LEVELS), name="experience_level"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    birth_date: Mapped[date] = mapped_column(Date)
    gender: Mapped[str] = mapped_column(cs_string(8))
    experience_level: Mapped[str] = mapped_column(cs_string(16))


class WorkerCareer(Base):
    __tablename__ = "worker_careers"
    __table_args__ = (
        UniqueConstraint("worker_id", "sort_order"),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(not_blank("store_name", nullable=True), name="store_name_not_blank"),
        CheckConstraint(
            "(is_current = 1 AND end_month IS NULL) OR (is_current = 0 AND end_month IS NOT NULL)",
            name="current_end_month",
        ),
        CheckConstraint("end_month IS NULL OR start_month <= end_month", name="month_order"),
    )

    id: Mapped[str] = _id()
    worker_id: Mapped[str] = mapped_column(ForeignKey("worker_profiles.user_id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    industry: Mapped[str] = mapped_column(String(50))
    duties: Mapped[str] = mapped_column(String(500))
    store_name: Mapped[str | None] = mapped_column(String(100))  # optional in the API
    start_month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    end_month: Mapped[str | None] = mapped_column(String(7))
    is_current: Mapped[bool] = mapped_column(Boolean)

    @validates("store_name")
    def _normalize_store_name(self, _key: str, value: str | None) -> str | None:
        return normalize_optional_text(value)


class AvailabilityRule(Base):
    __tablename__ = "availability_rules"
    __table_args__ = (
        UniqueConstraint("worker_id", "sort_order"),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(SHIFT_SPAN, name="time_span"),
    )

    id: Mapped[str] = _id()
    worker_id: Mapped[str] = mapped_column(ForeignKey("worker_profiles.user_id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    ends_next_day: Mapped[bool] = mapped_column(Boolean)


class AvailabilityDay(Base):
    __tablename__ = "availability_days"
    __table_args__ = (
        UniqueConstraint("rule_id", "weekday"),
        CheckConstraint(_in("weekday", WEEKDAYS), name="weekday"),
    )

    id: Mapped[str] = _id()
    rule_id: Mapped[str] = mapped_column(ForeignKey("availability_rules.id"))
    weekday: Mapped[str] = mapped_column(cs_string(3))


class Store(Base):
    __tablename__ = "stores"
    __table_args__ = (
        CheckConstraint(_in("approval_status", APPROVAL_STATUSES), name="approval_status"),
        CheckConstraint(
            "(approval_status = 'PENDING' AND approved_at IS NULL)"
            " OR (approval_status = 'APPROVED' AND approved_at IS NOT NULL)",
            name="approval_consistency",
        ),
        CheckConstraint(digits_only("business_registration_number", 10), name="brn_digits"),
        Index("ix_stores_owner_id", "owner_id"),
    )

    id: Mapped[str] = _id()
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    industry: Mapped[str] = mapped_column(String(50))
    postal_code: Mapped[str] = mapped_column(String(10))
    address: Mapped[str] = mapped_column(String(255))
    detail_address: Mapped[str | None] = mapped_column(String(255))
    # Digits only: the service strips hyphens before insert.
    business_registration_number: Mapped[str] = mapped_column(String(10), unique=True)
    phone_number: Mapped[str] = mapped_column(String(20))
    approval_status: Mapped[str] = mapped_column(cs_string(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class StoreApprovalRequest(Base):
    __tablename__ = "store_approval_requests"
    __table_args__ = (
        CheckConstraint(_in("status", APPROVAL_STATUSES), name="status"),
        CheckConstraint(
            "(status = 'PENDING' AND approved_at IS NULL)"
            " OR (status = 'APPROVED' AND approved_at IS NOT NULL)",
            name="approval_consistency",
        ),
    )

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), unique=True)
    status: Mapped[str] = mapped_column(cs_string(16), default="PENDING")
    submitted_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        CheckConstraint(_in("work_part", WORK_PARTS), name="work_part"),
        CheckConstraint(_in("payment_timing", PAYMENT_TIMINGS), name="payment_timing"),
        CheckConstraint("min_experience_months IN (0, 3, 6, 12)", name="min_experience"),
        CheckConstraint("headcount = 1", name="headcount"),
        CheckConstraint("hourly_wage_krw > 0", name="hourly_wage"),
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint(
            "(status = 'RECRUITING' AND closed_at IS NULL)"
            " OR (status = 'CLOSED' AND closed_at IS NOT NULL)",
            name="closed_consistency",
        ),
        CheckConstraint(SHIFT_SPAN, name="time_span"),
        Index("ix_job_postings_store_id_status", "store_id", "status"),
        Index("ix_job_postings_status_work_date", "status", "work_date"),
    )

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    created_by_owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(100))
    duty_description: Mapped[str] = mapped_column(Text)
    work_part: Mapped[str] = mapped_column(cs_string(16))
    work_date: Mapped[date] = mapped_column(Date)  # Asia/Seoul calendar date
    start_time: Mapped[time] = mapped_column(Time)  # Asia/Seoul wall clock
    end_time: Mapped[time] = mapped_column(Time)
    ends_next_day: Mapped[bool] = mapped_column(Boolean)
    headcount: Mapped[int] = mapped_column(Integer, default=1)
    min_experience_months: Mapped[int] = mapped_column(Integer, default=0)
    extra_requirements: Mapped[str | None] = mapped_column(Text)
    hourly_wage_krw: Mapped[int] = mapped_column(Integer)
    payment_timing: Mapped[str] = mapped_column(cs_string(16))
    pay_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(cs_string(16), default="RECRUITING")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        # active_worker_id is NULL once an application ends, so only live ones compete.
        UniqueConstraint("job_id", "active_worker_id"),
        CheckConstraint(_in("status", APPLICATION_STATUSES), name="status"),
        CheckConstraint(_in("experience_level", EXPERIENCE_LEVELS), name="experience_level"),
        CheckConstraint(not_blank("introduction"), name="introduction"),
        CheckConstraint("age_at_submission >= 0", name="age"),
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint(
            "(status = 'WITHDRAWN') = (withdrawn_at IS NOT NULL)", name="withdrawn_consistency",
        ),
        Index("ix_job_applications_worker_id", "worker_id"),
    )

    id: Mapped[str] = _id()
    job_id: Mapped[str] = mapped_column(ForeignKey("job_postings.id"))
    worker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    introduction: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(cs_string(16), default="APPLIED")
    applied_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    applicant_name: Mapped[str] = mapped_column(String(100))
    age_at_submission: Mapped[int] = mapped_column(Integer)
    experience_level: Mapped[str] = mapped_column(cs_string(16))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    active_worker_id: Mapped[str | None] = mapped_column(
        CHAR(36),
        Computed(
            "CASE WHEN status IN ('APPLIED', 'REQUESTED', 'CONFIRMED') THEN worker_id END",
            persisted=True,
        ),
    )


class ApplicationCareer(Base):
    __tablename__ = "application_careers"
    __table_args__ = (
        UniqueConstraint("application_id", "sort_order"),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(not_blank("store_name", nullable=True), name="store_name_not_blank"),
        CheckConstraint(
            "(is_current = 1 AND end_month IS NULL) OR (is_current = 0 AND end_month IS NOT NULL)",
            name="current_end_month",
        ),
    )

    id: Mapped[str] = _id()
    application_id: Mapped[str] = mapped_column(ForeignKey("job_applications.id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    industry: Mapped[str] = mapped_column(String(50))
    duties: Mapped[str] = mapped_column(String(500))
    store_name: Mapped[str | None] = mapped_column(String(100))  # optional in the API
    start_month: Mapped[str] = mapped_column(String(7))
    end_month: Mapped[str | None] = mapped_column(String(7))
    is_current: Mapped[bool] = mapped_column(Boolean)

    @validates("store_name")
    def _normalize_store_name(self, _key: str, value: str | None) -> str | None:
        return normalize_optional_text(value)


class WorkRequest(Base):
    __tablename__ = "work_requests"
    __table_args__ = (
        # At most one live PENDING request per application; per-job exclusivity needs the job lock.
        UniqueConstraint("pending_application_id"),
        CheckConstraint(_in("status", WORK_REQUEST_STATUSES), name="status"),
        CheckConstraint("expires_at > requested_at", name="expiry"),
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint("(status = 'PENDING') = (ended_at IS NULL)", name="ended_consistency"),
        CheckConstraint(
            "status NOT IN ('ACCEPTED', 'DECLINED', 'CONFIRMATION_WITHDRAWN')"
            " OR responded_at IS NOT NULL",
            name="responded_consistency",
        ),
        Index("ix_work_requests_application_id", "application_id"),
    )

    id: Mapped[str] = _id()
    application_id: Mapped[str] = mapped_column(ForeignKey("job_applications.id"))
    requested_by_owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(cs_string(32), default="PENDING")
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    responded_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    pending_application_id: Mapped[str | None] = mapped_column(
        CHAR(36),
        Computed("CASE WHEN status = 'PENDING' THEN application_id END", persisted=True),
    )


class ShiftAssignment(Base):
    __tablename__ = "shift_assignments"
    __table_args__ = (
        # active_job_id is NULL after withdrawal: one live confirmation per posting.
        UniqueConstraint("active_job_id"),
        CheckConstraint("withdrawn_at IS NULL OR withdrawn_at >= confirmed_at", name="withdrawn_after"),
        Index("ix_shift_assignments_job_id", "job_id"),
        Index("ix_shift_assignments_worker_id", "worker_id"),
    )

    id: Mapped[str] = _id()
    job_id: Mapped[str] = mapped_column(ForeignKey("job_postings.id"))
    work_request_id: Mapped[str] = mapped_column(ForeignKey("work_requests.id"), unique=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    active_job_id: Mapped[str | None] = mapped_column(
        CHAR(36),
        Computed("CASE WHEN withdrawn_at IS NULL THEN job_id END", persisted=True),
    )


class ApplicationSelectionEffect(Base):
    __tablename__ = "application_selection_effects"
    __table_args__ = (
        CheckConstraint(_in("previous_status", APPLICATION_STATUSES), name="previous_status"),
        CheckConstraint("applied_revision >= 1", name="applied_revision"),
    )

    request_id: Mapped[str] = mapped_column(ForeignKey("work_requests.id"), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("job_applications.id"), primary_key=True,
    )
    previous_status: Mapped[str] = mapped_column(cs_string(16))
    applied_revision: Mapped[int] = mapped_column(Integer)
    restored_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class StoreInvitation(Base):
    __tablename__ = "store_invitations"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="link_expiry"),
        CheckConstraint(
            "access_expires_at IS NULL OR access_expires_at > created_at", name="access_expiry",
        ),
        # accepted/declined/canceled are mutually exclusive outcomes.
        CheckConstraint(
            "(accepted_at IS NOT NULL) + (declined_at IS NOT NULL) + (canceled_at IS NOT NULL) <= 1",
            name="single_outcome",
        ),
        CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by_worker_id IS NULL)", name="accepted_actor",
        ),
        CheckConstraint(
            "(declined_at IS NULL) = (declined_by_worker_id IS NULL)", name="declined_actor",
        ),
        Index("ix_store_invitations_store_id_invited_email", "store_id", "invited_email"),
    )

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    inviter_owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    invited_email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(cs_string(64), unique=True)  # hex SHA-256 only
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_sent_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    access_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    accepted_by_worker_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    declined_by_worker_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    declined_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    canceled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class StoreAccessGrant(Base):
    __tablename__ = "store_access_grants"
    __table_args__ = (
        # Exactly one source: an accepted invitation (REGULAR) or a confirmed shift (TEMPORARY).
        CheckConstraint("(invitation_id IS NULL) <> (assignment_id IS NULL)", name="source_xor"),
        CheckConstraint("valid_until IS NULL OR valid_until > granted_at", name="validity"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at", name="revoked_after_grant",
        ),
        Index("ix_store_access_grants_store_id_worker_id", "store_id", "worker_id"),
    )

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    worker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    invitation_id: Mapped[str | None] = mapped_column(
        ForeignKey("store_invitations.id"), unique=True,
    )
    assignment_id: Mapped[str | None] = mapped_column(
        ForeignKey("shift_assignments.id"), unique=True,
    )
    duty_label: Mapped[str | None] = mapped_column(String(100))
    granted_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    valid_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class AuthSession(Base):
    """Server-side member session. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_id", "user_id"),)

    id: Mapped[str] = _id()
    token_hash: Mapped[str] = mapped_column(cs_string(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)  # absolute limit
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class RegistrationSession(Base):
    """Google-verified identity that has not registered yet; fixed 10 minute lifetime."""

    __tablename__ = "registration_sessions"
    __table_args__ = (Index("ix_registration_sessions_expires_at", "expires_at"),)

    id: Mapped[str] = _id()
    token_hash: Mapped[str] = mapped_column(cs_string(64), unique=True)
    google_sub: Mapped[str] = mapped_column(cs_string(255))
    google_email: Mapped[str] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


IDEMPOTENCY_STATES = ("PROCESSING", "COMPLETED")


class IdempotencyRecord(Base):
    """One `Idempotency-Key` per subject, kept 24 hours (app.idempotency).

    The request body is stored only as a hash. A PROCESSING row is a short lease held by the
    request that is doing the work; COMPLETED rows carry the response to replay.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("subject_id", "idempotency_key"),
        CheckConstraint(_in("state", IDEMPOTENCY_STATES), name="state"),
        CheckConstraint(
            "(state = 'PROCESSING' AND response_status IS NULL)"
            " OR (state = 'COMPLETED' AND response_status IS NOT NULL)",
            name="state_consistency",
        ),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    id: Mapped[str] = _id()
    # SHA-256 hex of the Google `sub` (app.idempotency.subject_id_for): the same value before
    # and after registration, so records follow the person from registration session to member.
    subject_id: Mapped[str] = mapped_column(cs_string(64))
    # Stored as a lower-case UUID (app.idempotency.idempotency_key normalizes); cs_char keeps
    # MySQL from folding case if something bypasses that.
    idempotency_key: Mapped[str] = mapped_column(cs_char(36))
    endpoint: Mapped[str] = mapped_column(cs_string(255))  # "METHOD /path"; paths are case-sensitive
    request_hash: Mapped[str] = mapped_column(cs_string(64))
    state: Mapped[str] = mapped_column(cs_string(16))
    lock_token: Mapped[str | None] = mapped_column(CHAR(36))
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any | None] = mapped_column(JSON)
    # Allow-listed headers only (app.idempotency.REPLAY_HEADERS); never cookies or secrets.
    response_headers: Mapped[dict[str, str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

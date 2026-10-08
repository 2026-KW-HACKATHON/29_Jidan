"""Baseline schema (P0 domains) transcribed from docs/erd/{auth,worker,store,access,jobs}.md.

Conventions: CHAR(36) UUID keys, UTC DATETIME(6), enum-like values as VARCHAR + CHECK.
Rules that need other tables (role of a referenced user, same-store consistency, overlap
checks, row-count limits, 30-minute alignment) are enforced in the service layer, not here.
Text rules whose meaning differs between SQLite and MySQL (digits-only, not-blank) use
app.db.checks so both databases enforce the same thing.
So is invited_email normalization: e-mail columns (email_string, utf8mb4_0900_as_ci on MySQL)
compare case-insensitively, which makes a CHECK on it ineffective.
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
    Float,
    ForeignKey,
    ForeignKeyConstraint,
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
from app.db.types import (
    UtcDateTime,
    cs_char,
    cs_string,
    email_string,
    new_uuid,
    normalize_optional_text,
    utcnow,
)

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
    google_email: Mapped[str] = mapped_column(email_string(320))
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
        # Deadline sweeps: status = 'PENDING' (or EXPIRED) AND expires_at <= now.
        Index("ix_work_requests_status_expires_at", "status", "expires_at"),
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
    invited_email: Mapped[str] = mapped_column(email_string(320))
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
    google_email: Mapped[str] = mapped_column(email_string(320))
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


class OAuthTransaction(Base):
    """Five minute browser-bound, single-use Google login; no provider tokens stored."""

    __tablename__ = "oauth_transactions"
    __table_args__ = (Index("ix_oauth_transactions_expires_at", "expires_at"),)

    id: Mapped[str] = _id()
    token_hash: Mapped[str] = mapped_column(cs_string(64), unique=True)
    state_hash: Mapped[str] = mapped_column(cs_string(64), unique=True)
    nonce_hash: Mapped[str] = mapped_column(cs_string(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    issued_session_id: Mapped[str | None] = mapped_column(CHAR(36))
    issued_registration_id: Mapped[str | None] = mapped_column(CHAR(36))


NOTIFICATION_TYPES = (
    "NEW_APPLICATION", "INVITATION_ACCEPTED", "STORE_APPROVED", "INVITATION_EXPIRED",
    "WORK_REQUEST_RECEIVED", "WORK_REQUEST_NO_RESPONSE", "WORK_CONFIRMED", "STORE_INVITED",
    "WORK_REMINDER", "MANUAL_PUBLISHED", "WORK_REQUEST_WITHDRAWN", "WORK_CONFIRMATION_WITHDRAWN",
)
NOTIFICATION_TARGET_KINDS = (
    "JOB_APPLICATION", "WORK_REQUEST", "STORE_INVITATION", "STORE", "MANUAL", "WORK_SCHEDULE",
)


class Notification(Base):
    """An in-app notification, written in the transaction of the change that caused it.

    Rows are created only through app.notifications.record_notification, which validates the
    typed target. target_kind/target_id point at another domain's row without a FK (several
    tables); target_context is the API target snapshot. dedupe_key is "<TYPE>:<event key>" and is
    unique per recipient, so replaying the same domain event cannot notify twice.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("recipient_user_id", "dedupe_key"),
        CheckConstraint(_in("event_type", NOTIFICATION_TYPES), name="event_type"),
        CheckConstraint(_in("target_kind", NOTIFICATION_TARGET_KINDS), name="target_kind"),
        CheckConstraint(not_blank("title"), name="title_not_blank"),
        CheckConstraint(not_blank("body"), name="body_not_blank"),
        Index("ix_notifications_recipient_user_id_created_at_id", "recipient_user_id", "created_at", "id"),
        Index("ix_notifications_recipient_user_id_read_at", "recipient_user_id", "read_at"),
    )

    id: Mapped[str] = _id()
    recipient_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(cs_string(32))
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(String(500))
    target_kind: Mapped[str] = mapped_column(cs_string(32))
    target_id: Mapped[str] = mapped_column(CHAR(36))
    target_context: Mapped[dict[str, Any]] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(cs_string(255))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class FavoriteStore(Base):
    """A worker's saved (관심) store. Only APPROVED stores can be saved; it grants no access."""

    __tablename__ = "favorite_stores"
    __table_args__ = (
        Index("ix_favorite_stores_worker_id_saved_at", "worker_id", "saved_at"),
        Index("ix_favorite_stores_store_id", "store_id"),
    )

    worker_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    saved_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


INVITATION_MAIL_STATUSES = ("QUEUED", "SENT", "FAILED", "DISCARDED")


class InvitationMailOutbox(Base):
    """Durable request to e-mail an invitation link, written with the invitation (app.invitation_mail).

    The recipient, store name and link (which carries the raw token) exist only inside `payload`,
    encrypted with the server key. The payload is cleared as soon as the row leaves QUEUED
    (sent, given up, or discarded by a resend/cancel), so finished rows hold no secrets.
    """

    __tablename__ = "invitation_mail_outbox"
    __table_args__ = (
        CheckConstraint(_in("status", INVITATION_MAIL_STATUSES), name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint("(status = 'QUEUED') = (payload IS NOT NULL)", name="payload_while_queued"),
        CheckConstraint("(status = 'QUEUED') = (processed_at IS NULL)", name="processed_consistency"),
        # A delivery attempt claims the row with a lease instead of holding a lock while it talks
        # to the mail server; only queued rows can be claimed.
        CheckConstraint("(claim_token IS NULL) = (claimed_until IS NULL)", name="claim_pair"),
        CheckConstraint("status = 'QUEUED' OR claim_token IS NULL", name="claim_while_queued"),
        Index("ix_invitation_mail_outbox_invitation_id", "invitation_id"),
        Index("ix_invitation_mail_outbox_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = _id()
    invitation_id: Mapped[str] = mapped_column(ForeignKey("store_invitations.id"))
    status: Mapped[str] = mapped_column(cs_string(16), default="QUEUED")
    payload: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # NULL: due now (0020)
    claim_token: Mapped[str | None] = mapped_column(CHAR(36))
    claimed_until: Mapped[datetime | None] = mapped_column(UtcDateTime)
# --- AI/STT background tasks (app.tasks) ---------------------------------------------------------

TASK_KINDS = (
    "TRANSCRIPTION", "INITIAL_QUESTION", "EVALUATION", "FOLLOWUP_GENERATION", "DRAFT_GENERATION",
    "REVIEW_UNDERSTANDING", "REVIEW_CORRECTION", "DRAFT_CORRECTION", "QA_ANSWER",
)
TASK_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")


class BackgroundTask(Base):
    """One execution unit of an external AI/STT call, run outside request transactions.

    `id` is the task ID that domain rows remember (e.g. `processing_task_id`) so that a result
    is applied only by the task the row is still waiting for. `attempt` is the domain-visible
    attempt (a user retry creates a new task with attempt + 1); `tries` counts executions of
    this task (automatic retries, lease recovery). `payload` is the immutable input snapshot.
    A RUNNING row holds a lease; only the holder of `lease_token` may finish it.
    """

    __tablename__ = "background_tasks"
    __table_args__ = (
        CheckConstraint(_in("kind", TASK_KINDS), name="kind"),
        CheckConstraint(_in("status", TASK_STATUSES), name="status"),
        CheckConstraint("attempt >= 1", name="attempt"),
        CheckConstraint("tries >= 0 AND max_tries >= 1 AND tries <= max_tries", name="tries"),
        CheckConstraint(
            "(status = 'RUNNING') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_consistency",
        ),
        CheckConstraint(
            "(status IN ('SUCCEEDED', 'FAILED', 'CANCELLED')) = (finished_at IS NOT NULL)",
            name="finished_consistency",
        ),
        Index("ix_background_tasks_status_available_at", "status", "available_at"),
        Index("ix_background_tasks_kind_subject_id", "kind", "subject_id"),
    )

    id: Mapped[str] = _id()
    kind: Mapped[str] = mapped_column(cs_string(32))
    subject_id: Mapped[str] = mapped_column(CHAR(36))  # the domain row the task works for
    input_revision: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(cs_string(16), default="QUEUED")
    tries: Mapped[int] = mapped_column(Integer, default=0)
    max_tries: Mapped[int] = mapped_column(Integer)
    payload: Mapped[Any] = mapped_column(JSON)
    available_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    lease_token: Mapped[str | None] = mapped_column(CHAR(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error_code: Mapped[str | None] = mapped_column(cs_string(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


# --- manual / Q&A media and transcriptions (docs/erd/manual.md, docs/erd/qa.md) ------------------
# Rows are tombstoned (`deleted_at`), never deleted, so a repeated DELETE can still prove the
# original store/owner. `content_deleted_at` records that the stored bytes were purged by the
# retention task (unattached files after 24 h, audio originals 24 h after transcription ends).

MEDIA_KINDS = ("IMAGE", "AUDIO")
IMAGE_MIME_TYPES = ("image/jpeg", "image/png", "image/webp")
AUDIO_MIME_TYPES = ("audio/mpeg", "audio/mp4", "audio/webm", "audio/wav")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_AUDIO_MILLISECONDS = 120_000
MEDIA_SHAPE = (
    f"(kind = 'IMAGE' AND {_in('mime_type', IMAGE_MIME_TYPES)} AND byte_size <= {MAX_IMAGE_BYTES}"
    " AND duration_ms IS NULL)"
    f" OR (kind = 'AUDIO' AND {_in('mime_type', AUDIO_MIME_TYPES)} AND byte_size <= {MAX_AUDIO_BYTES}"
    f" AND duration_ms IS NOT NULL AND duration_ms >= 1 AND duration_ms <= {MAX_AUDIO_MILLISECONDS})"
)
TRANSCRIPTION_STATUSES = ("RUNNING", "READY", "ERROR")


def _media_columns_args(table: str) -> tuple:
    return (
        CheckConstraint(_in("kind", MEDIA_KINDS), name="kind"),
        CheckConstraint(MEDIA_SHAPE, name="media_shape"),
        CheckConstraint("byte_size >= 1", name="byte_size"),
        CheckConstraint("expires_at > created_at", name="expiry"),
        Index(f"ix_{table}_store_id", "store_id"),
        Index(f"ix_{table}_content_deleted_at_expires_at", "content_deleted_at", "expires_at"),
    )


class ManualMedia(Base):
    """An owner's private interview photo (IMAGE) or answer recording (AUDIO).

    API purpose MANUAL_PHOTO <-> IMAGE, INTERVIEW_AUDIO <-> AUDIO. `object_key` locates the
    bytes in app.media storage and is never returned by the API.
    """

    __tablename__ = "manual_media"
    __table_args__ = _media_columns_args("manual_media")

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    uploaded_by_owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(cs_string(8))
    object_key: Mapped[str] = mapped_column(cs_string(200), unique=True)
    mime_type: Mapped[str] = mapped_column(cs_string(32))
    byte_size: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    content_deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class QaMedia(Base):
    """A worker's private question photo (QUESTION_IMAGE) or recording (QUESTION_AUDIO)."""

    __tablename__ = "qa_media"
    __table_args__ = _media_columns_args("qa_media")

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    worker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(cs_string(8))
    object_key: Mapped[str] = mapped_column(cs_string(200), unique=True)
    mime_type: Mapped[str] = mapped_column(cs_string(32))
    byte_size: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    content_deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class MediaTranscription(Base):
    """The single transcription task of one recording (manual or Q&A), kept after the audio
    bytes are purged. `task_id` is the background task the row currently waits for; a retry
    increments `attempt` and replaces it."""

    __tablename__ = "media_transcriptions"
    __table_args__ = (
        CheckConstraint("(manual_media_id IS NULL) <> (qa_media_id IS NULL)", name="media_xor"),
        CheckConstraint(_in("status", TRANSCRIPTION_STATUSES), name="status"),
        # A single value: MySQL stores `IN ('X')` as `= 'X'`, so it is written that way.
        CheckConstraint("error_code = 'TRANSCRIPTION_FAILED'", name="error_code"),
        CheckConstraint("attempt >= 1", name="attempt"),
        CheckConstraint(
            "(status = 'RUNNING' AND text IS NULL AND error_code IS NULL AND completed_at IS NULL"
            " AND task_id IS NOT NULL)"
            " OR (status = 'READY' AND text IS NOT NULL AND error_code IS NULL"
            " AND completed_at IS NOT NULL)"
            " OR (status = 'ERROR' AND text IS NULL AND error_code IS NOT NULL"
            " AND completed_at IS NOT NULL)",
            name="status_consistency",
        ),
        CheckConstraint(not_blank("text", nullable=True), name="text_not_blank"),
        Index("ix_media_transcriptions_store_id", "store_id"),
    )

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    manual_media_id: Mapped[str | None] = mapped_column(ForeignKey("manual_media.id"), unique=True)
    qa_media_id: Mapped[str | None] = mapped_column(ForeignKey("qa_media.id"), unique=True)
    status: Mapped[str] = mapped_column(cs_string(16), default="RUNNING")
    text: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(cs_string(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    task_id: Mapped[str | None] = mapped_column(CHAR(36))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


# --- manuals, interviews, reviews, drafts (docs/erd/manual.md, ai-interview-flow.md) -------------
# Content is normalized (shifts/sections/steps/photo attachments) per version. A draft becomes the
# published version in place, so section IDs cited by Q&A stay valid and published rows are never
# rewritten. Long constraint names are spelled out (MySQL limits identifiers to 64 characters).

MANUAL_VERSION_STATUSES = ("DRAFT", "PUBLISHED")
GENERATION_STATUSES = ("NOT_STARTED", "RUNNING", "READY", "ERROR")
SECTION_CATEGORIES = ("COMMON_TASK", "SHIFT_TASK", "RULE", "EQUIPMENT")
INTENT_STAGES = ("WORK_STRUCTURE", "COMMON_TASKS", "SHIFT_TASKS", "COMPLEMENTS")
SESSION_STATUSES = ("IN_PROGRESS", "ERROR", "COMPLETED")
SESSION_PROCESSING_KINDS = ("INITIAL_QUESTION", "EVALUATION", "FOLLOWUP_GENERATION", "DRAFT_GENERATION")
COVERAGE_STATUSES = ("PENDING", "NEEDS_DETAIL", "COVERED")
REVIEW_STATUSES = ("PROCESSING", "READY", "ERROR")
REVIEW_PROCESSING_KINDS = ("UNDERSTANDING", "CORRECTION")
PUBLIC_PROCESSING_ERRORS = ("AI_PROCESSING_FAILED", "TRANSCRIPTION_FAILED")
PROBE_STATUSES = ("GENERATING", "READY", "ERROR")
SPEAKERS = ("AI", "OWNER")
TURN_KINDS = ("QUESTION", "ANSWER", "CORRECTION")
QUESTION_KINDS = ("BASE", "PROBE")
INPUT_METHODS = ("TEXT", "VOICE")
EVALUATION_STATUSES = ("SUCCEEDED", "FAILED")
# app.tasks.task_error_code(): upper-cased app.ai.errors.AiErrorCode values plus the runner's own.
TASK_ERROR_CODES = (
    "TIMEOUT", "RATE_LIMITED", "UNAVAILABLE", "INVALID_OUTPUT", "REFUSED", "INPUT_REJECTED",
    "NOT_CONFIGURED", "EMPTY_TRANSCRIPT", "INTERNAL", "LEASE_EXPIRED",
)
MISSING_TARGETS = ("MANUAL", "SHIFT", "SECTION")
CORRECTION_STATUSES = ("RUNNING", "SUCCEEDED", "ERROR")
CORRECTION_ERRORS = (
    "AI_PROCESSING_FAILED", "CORRECTION_CLARIFICATION_REQUIRED", "MANUAL_REFERENCE_CONFLICT",
    "MANUAL_VERSION_CONFLICT", "REVISION_CONFLICT",
)
SNAPSHOT_HOLDERS = ("INTENT_REVIEW", "REVIEW_CONFIRMATION", "DRAFT_GENERATION", "DRAFT_CORRECTION")
# A processing triple is either fully set (a task is running or failed) or fully empty.
# `processing_attempt IS NOT NULL` is spelled out: `NULL >= 1` is UNKNOWN, which a CHECK lets
# through (migration 0041).
PROCESSING_TRIPLE = (
    "(processing_kind IS NULL AND processing_task_id IS NULL AND processing_attempt IS NULL)"
    " OR (processing_kind IS NOT NULL AND processing_task_id IS NOT NULL"
    " AND processing_attempt IS NOT NULL AND processing_attempt >= 1)"
)


class StoreManual(Base):
    """One manual per store; the row every manual change locks (draft swap, publication)."""

    __tablename__ = "store_manuals"

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), unique=True)
    # use_alter: manual_versions also references store_manuals (a cycle); the FK is added after
    # both tables exist and dropped first.
    current_published_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("manual_versions.id", use_alter=True,
                   name="fk_store_manuals_current_published_version_id"),
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class ManualVersion(Base):
    """A draft or published manual. `revision_no` is the API versionNumber; `revision` guards
    concurrent edits of this draft; `content_revision` changes only with content/issue
    definitions so acknowledgements of unchanged content survive other revisions."""

    __tablename__ = "manual_versions"
    __table_args__ = (
        UniqueConstraint("manual_id", "revision_no"),
        # At most one DRAFT per manual: the generated column is NULL for published versions.
        UniqueConstraint("active_draft_manual_id"),
        CheckConstraint(_in("status", MANUAL_VERSION_STATUSES), name="status"),
        CheckConstraint(_in("generation_status", GENERATION_STATUSES), name="generation_status"),
        CheckConstraint("revision_no >= 1 AND revision >= 1 AND content_revision >= 1", name="revisions"),
        CheckConstraint(
            "(status = 'DRAFT' AND published_at IS NULL AND published_by_owner_id IS NULL)"
            " OR (status = 'PUBLISHED' AND published_at IS NOT NULL AND published_by_owner_id IS NOT NULL"
            " AND generation_status = 'READY')",
            name="publication",
        ),
    )

    id: Mapped[str] = _id()
    manual_id: Mapped[str] = mapped_column(ForeignKey("store_manuals.id"))
    revision_no: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content_revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(cs_string(16), default="DRAFT")
    generation_status: Mapped[str] = mapped_column(cs_string(16), default="NOT_STARTED")
    # Fixed input of draft generation (selected review revisions, contents, photos, gaps).
    generation_input_snapshot: Mapped[Any | None] = mapped_column(JSON)
    created_by_owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    published_by_owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    active_draft_manual_id: Mapped[str | None] = mapped_column(
        CHAR(36), Computed("CASE WHEN status = 'DRAFT' THEN manual_id END", persisted=True),
    )


class ManualShift(Base):
    __tablename__ = "manual_shifts"
    __table_args__ = (
        UniqueConstraint("version_id", "sort_order"),
        UniqueConstraint("id", "version_id"),  # target of same-version composite FKs
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(not_blank("name"), name="name_not_blank"),
        # Unknown times are NULL (with a missing-information issue); known spans are 0 < d <= 24h.
        CheckConstraint(
            f"start_time IS NULL OR end_time IS NULL OR ends_next_day IS NULL OR ({SHIFT_SPAN})",
            name="time_span",
        ),
    )

    id: Mapped[str] = _id()
    version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(50))
    start_time: Mapped[time | None] = mapped_column(Time)  # Asia/Seoul wall clock, minute precision
    end_time: Mapped[time | None] = mapped_column(Time)
    ends_next_day: Mapped[bool | None] = mapped_column(Boolean)


class ManualSection(Base):
    __tablename__ = "manual_sections"
    __table_args__ = (
        UniqueConstraint("version_id", "sort_order"),
        UniqueConstraint("id", "version_id"),
        ForeignKeyConstraint(
            ["shift_id", "version_id"], ["manual_shifts.id", "manual_shifts.version_id"],
            name="fk_manual_sections_shift_same_version",
        ),
        CheckConstraint(_in("category", SECTION_CATEGORIES), name="category"),
        CheckConstraint("(category = 'SHIFT_TASK') = (shift_id IS NOT NULL)", name="shift_scope"),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(not_blank("title"), name="title_not_blank"),
    )

    id: Mapped[str] = _id()
    version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"))
    shift_id: Mapped[str | None] = mapped_column(CHAR(36))
    sort_order: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(cs_string(16))
    title: Mapped[str] = mapped_column(String(100))


class ManualStep(Base):
    __tablename__ = "manual_steps"
    __table_args__ = (
        UniqueConstraint("section_id", "sort_order"),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(not_blank("instruction"), name="instruction_not_blank"),
    )

    id: Mapped[str] = _id()
    section_id: Mapped[str] = mapped_column(ForeignKey("manual_sections.id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    instruction: Mapped[str] = mapped_column(Text)  # API limit 3000 characters
    checklist_item: Mapped[bool] = mapped_column(Boolean, default=False)


class ManualPhotoAttachment(Base):
    """A photo shown in a version: structure photo (section_id NULL) or a section photo.

    `scope_id` (section, else version) gives the nullable section_id a real UNIQUE meaning:
    order and media are unique within each attachment list.
    """

    __tablename__ = "manual_photo_attachments"
    __table_args__ = (
        UniqueConstraint("scope_id", "sort_order"),
        UniqueConstraint("scope_id", "media_id"),
        ForeignKeyConstraint(
            ["section_id", "version_id"], ["manual_sections.id", "manual_sections.version_id"],
            name="fk_manual_photo_attachments_section_same_version",
        ),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(not_blank("title"), name="title_not_blank"),
        Index("ix_manual_photo_attachments_media_id", "media_id"),
    )

    id: Mapped[str] = _id()
    version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"))
    section_id: Mapped[str | None] = mapped_column(CHAR(36))
    media_id: Mapped[str] = mapped_column(ForeignKey("manual_media.id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(100))
    caption: Mapped[str | None] = mapped_column(String(300))
    scope_id: Mapped[str] = mapped_column(
        CHAR(36), Computed("COALESCE(section_id, version_id)", persisted=True),
    )


class ManualMediaSnapshotRef(Base):
    """Photo references held inside JSON snapshots (review content, confirmation history,
    generation/correction input). Rows exist exactly while the snapshot exists, so deletion and
    retention can check every live reference with indexed queries (app.media references)."""

    __tablename__ = "manual_media_snapshot_refs"
    __table_args__ = (
        CheckConstraint(_in("holder_kind", SNAPSHOT_HOLDERS), name="holder_kind"),
        Index("ix_manual_media_snapshot_refs_media_id", "media_id"),
        Index("ix_manual_media_snapshot_refs_holder", "holder_kind", "holder_id", "holder_intent_id"),
    )

    id: Mapped[str] = _id()
    media_id: Mapped[str] = mapped_column(ForeignKey("manual_media.id"))
    holder_kind: Mapped[str] = mapped_column(cs_string(32))
    holder_id: Mapped[str] = mapped_column(CHAR(36))  # session, confirmation, version or correction
    holder_intent_id: Mapped[str | None] = mapped_column(CHAR(36))  # INTENT_REVIEW: the intent
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class InterviewQuestionSet(Base):
    __tablename__ = "interview_question_sets"
    __table_args__ = (CheckConstraint("revision_no >= 1", name="revision_no"),)

    id: Mapped[str] = _id()
    revision_no: Mapped[int] = mapped_column(Integer, unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class InterviewIntent(Base):
    __tablename__ = "interview_intents"
    __table_args__ = (
        UniqueConstraint("question_set_id", "sort_order"),
        UniqueConstraint("question_set_id", "intent_key"),
        CheckConstraint(_in("stage", INTENT_STAGES), name="stage"),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        CheckConstraint(not_blank("base_question"), name="base_question_not_blank"),
        CheckConstraint(not_blank("coverage_criteria"), name="coverage_criteria_not_blank"),
    )

    id: Mapped[str] = _id()
    question_set_id: Mapped[str] = mapped_column(ForeignKey("interview_question_sets.id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    intent_key: Mapped[str] = mapped_column(cs_string(100))
    stage: Mapped[str] = mapped_column(cs_string(16))
    base_question: Mapped[str] = mapped_column(Text)
    coverage_criteria: Mapped[str] = mapped_column(Text)


class InterviewSession(Base):
    """Question progress of one draft. `processing_*` is the task the session waits for
    (API `processing`); `error_code` is the public error after retries and fallback failed."""

    __tablename__ = "interview_sessions"
    __table_args__ = (
        CheckConstraint(_in("status", SESSION_STATUSES), name="status"),
        CheckConstraint(_in("processing_kind", SESSION_PROCESSING_KINDS), name="processing_kind"),
        CheckConstraint(_in("error_code", PUBLIC_PROCESSING_ERRORS), name="error_code"),
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint(PROCESSING_TRIPLE, name="processing"),
        CheckConstraint("(status = 'COMPLETED') = (completed_at IS NOT NULL)", name="completed"),
        CheckConstraint(
            "(status = 'ERROR') = (error_code IS NOT NULL)"
            " AND (status <> 'ERROR' OR processing_task_id IS NOT NULL)"
            " AND (status <> 'COMPLETED' OR (processing_task_id IS NULL AND current_intent_id IS NULL))",
            name="status_consistency",
        ),
        Index("ix_interview_sessions_owner_id", "owner_id"),
    )

    id: Mapped[str] = _id()
    manual_version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"), unique=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    question_set_id: Mapped[str] = mapped_column(ForeignKey("interview_question_sets.id"))
    status: Mapped[str] = mapped_column(cs_string(16), default="IN_PROGRESS")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    current_intent_id: Mapped[str | None] = mapped_column(ForeignKey("interview_intents.id"))
    processing_kind: Mapped[str | None] = mapped_column(cs_string(32))
    processing_task_id: Mapped[str | None] = mapped_column(CHAR(36))
    processing_attempt: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(cs_string(32))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class InterviewSessionIntent(Base):
    __tablename__ = "interview_session_intents"
    __table_args__ = (
        CheckConstraint(_in("coverage_status", COVERAGE_STATUSES), name="coverage_status"),
        CheckConstraint("depth >= 0 AND depth <= 5", name="depth"),
        CheckConstraint(
            "(coverage_status = 'PENDING' AND finished_at IS NULL AND covered_at IS NULL)"
            " OR (coverage_status = 'COVERED' AND finished_at IS NOT NULL AND covered_at IS NOT NULL)"
            " OR (coverage_status = 'NEEDS_DETAIL' AND depth = 5 AND finished_at IS NOT NULL"
            " AND covered_at IS NULL)",
            name="coverage_consistency",
        ),
    )

    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id"), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("interview_intents.id"), primary_key=True)
    coverage_status: Mapped[str] = mapped_column(cs_string(16), default="PENDING")
    depth: Mapped[int] = mapped_column(Integer, default=0)
    coverage_note: Mapped[str | None] = mapped_column(Text)  # internal; never shown to workers
    covered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class InterviewIntentReview(Base):
    """Summary/review of one finished intent, with its own revision (independent of the
    session's). `ready_content` keeps the last READY content while a correction runs/fails."""

    __tablename__ = "interview_intent_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "intent_id"],
            ["interview_session_intents.session_id", "interview_session_intents.intent_id"],
            name="fk_interview_intent_reviews_session_intent",
        ),
        CheckConstraint(_in("status", REVIEW_STATUSES), name="status"),
        CheckConstraint(_in("processing_kind", REVIEW_PROCESSING_KINDS), name="processing_kind"),
        CheckConstraint(_in("error_code", PUBLIC_PROCESSING_ERRORS), name="error_code"),
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint(PROCESSING_TRIPLE, name="processing"),
        CheckConstraint("(confirmed_at IS NULL) = (confirmed_by_owner_id IS NULL)", name="confirmation"),
        CheckConstraint(
            "(status = 'ERROR') = (error_code IS NOT NULL)"
            " AND (status <> 'PROCESSING' OR (processing_task_id IS NOT NULL AND confirmed_at IS NULL))"
            " AND (status <> 'READY' OR (ready_content IS NOT NULL AND processing_task_id IS NULL))",
            name="status_consistency",
        ),
    )

    session_id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    intent_id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(cs_string(16), default="PROCESSING")
    ready_content: Mapped[Any | None] = mapped_column(JSON)
    confirmed_by_owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    processing_kind: Mapped[str | None] = mapped_column(cs_string(16))
    processing_task_id: Mapped[str | None] = mapped_column(CHAR(36))
    processing_attempt: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(cs_string(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class InterviewReviewConfirmation(Base):
    """Immutable history of review confirmations (never updated or deleted)."""

    __tablename__ = "interview_review_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "intent_id", "confirmed_revision",
            name="uq_interview_review_confirmations_revision",
        ),
        ForeignKeyConstraint(
            ["session_id", "intent_id"],
            ["interview_intent_reviews.session_id", "interview_intent_reviews.intent_id"],
            name="fk_interview_review_confirmations_review",
        ),
        CheckConstraint(
            "reviewed_revision >= 1 AND confirmed_revision > reviewed_revision", name="revisions",
        ),
    )

    id: Mapped[str] = _id()
    session_id: Mapped[str] = mapped_column(CHAR(36))
    intent_id: Mapped[str] = mapped_column(CHAR(36))
    reviewed_revision: Mapped[int] = mapped_column(Integer)
    confirmed_revision: Mapped[int] = mapped_column(Integer)
    confirmed_content: Mapped[Any] = mapped_column(JSON)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class InterviewProbeBatch(Base):
    """Generation unit of exactly one follow-up question (API batchId), depth 1..5."""

    __tablename__ = "interview_probe_batches"
    __table_args__ = (
        UniqueConstraint("session_id", "intent_id", "depth"),
        UniqueConstraint("id", "session_id", "intent_id"),
        ForeignKeyConstraint(
            ["session_id", "intent_id"],
            ["interview_session_intents.session_id", "interview_session_intents.intent_id"],
            name="fk_interview_probe_batches_session_intent",
        ),
        CheckConstraint(_in("status", PROBE_STATUSES), name="status"),
        CheckConstraint(_in("error_code", TASK_ERROR_CODES), name="error_code"),
        CheckConstraint("depth >= 1 AND depth <= 5", name="depth"),
        CheckConstraint("(status = 'ERROR') = (error_code IS NOT NULL)", name="error_consistency"),
    )

    id: Mapped[str] = _id()
    session_id: Mapped[str] = mapped_column(CHAR(36))
    intent_id: Mapped[str] = mapped_column(CHAR(36))
    depth: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(cs_string(16), default="GENERATING")
    generator_source: Mapped[str | None] = mapped_column(String(200))  # provider config version
    error_code: Mapped[str | None] = mapped_column(cs_string(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class InterviewTurn(Base):
    """AI questions, owner answers and corrections in order. Generated columns make the
    single-BASE-question, single-question-per-batch and single-answer-per-question rules
    database constraints."""

    __tablename__ = "interview_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_no"),
        UniqueConstraint("session_id", "base_question_intent_id",
                         name="uq_interview_turns_session_id_base_question"),
        UniqueConstraint("probe_question_batch_id"),
        UniqueConstraint("reply_to_question_turn_id"),
        ForeignKeyConstraint(
            ["session_id", "intent_id"],
            ["interview_session_intents.session_id", "interview_session_intents.intent_id"],
            name="fk_interview_turns_session_intent",
        ),
        ForeignKeyConstraint(
            ["probe_batch_id", "session_id", "intent_id"],
            ["interview_probe_batches.id", "interview_probe_batches.session_id",
             "interview_probe_batches.intent_id"],
            name="fk_interview_turns_probe_batch",
        ),
        CheckConstraint(_in("speaker", SPEAKERS), name="speaker"),
        CheckConstraint(_in("turn_kind", TURN_KINDS), name="turn_kind"),
        CheckConstraint(_in("question_kind", QUESTION_KINDS), name="question_kind"),
        CheckConstraint(_in("input_method", INPUT_METHODS), name="input_method"),
        CheckConstraint("turn_no >= 1", name="turn_no"),
        CheckConstraint("depth >= 0 AND depth <= 5", name="depth"),
        CheckConstraint(
            "(turn_kind = 'QUESTION' AND speaker = 'AI' AND question_kind IS NOT NULL"
            " AND reply_to_question_turn_id IS NULL AND input_method IS NULL)"
            " OR (turn_kind = 'ANSWER' AND speaker = 'OWNER' AND question_kind IS NULL"
            " AND reply_to_question_turn_id IS NOT NULL AND input_method IS NOT NULL)"
            " OR (turn_kind = 'CORRECTION' AND speaker = 'OWNER' AND question_kind IS NULL"
            " AND reply_to_question_turn_id IS NULL AND input_method IS NOT NULL)",
            name="kind_consistency",
        ),
        CheckConstraint(
            "question_kind IS NULL"
            " OR (question_kind = 'BASE' AND depth = 0 AND probe_batch_id IS NULL)"
            " OR (question_kind = 'PROBE' AND depth >= 1 AND probe_batch_id IS NOT NULL)",
            name="question_depth",
        ),
        CheckConstraint("(input_method = 'VOICE') = (transcription_id IS NOT NULL)", name="voice_source"),
        CheckConstraint(not_blank("content"), name="content_not_blank"),
    )

    id: Mapped[str] = _id()
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id"))
    turn_no: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(cs_string(8))
    turn_kind: Mapped[str] = mapped_column(cs_string(16))
    question_kind: Mapped[str | None] = mapped_column(cs_string(8))
    intent_id: Mapped[str] = mapped_column(CHAR(36))
    depth: Mapped[int] = mapped_column(Integer, default=0)
    probe_batch_id: Mapped[str | None] = mapped_column(CHAR(36))
    reply_to_question_turn_id: Mapped[str | None] = mapped_column(ForeignKey("interview_turns.id"))
    input_method: Mapped[str | None] = mapped_column(cs_string(8))
    content: Mapped[str] = mapped_column(Text)  # submitted text or the READY transcript text
    guidance: Mapped[str | None] = mapped_column(Text)
    guidance_cards: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    transcription_id: Mapped[str | None] = mapped_column(ForeignKey("media_transcriptions.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    base_question_intent_id: Mapped[str | None] = mapped_column(
        CHAR(36), Computed("CASE WHEN question_kind = 'BASE' THEN intent_id END", persisted=True),
    )
    probe_question_batch_id: Mapped[str | None] = mapped_column(
        CHAR(36), Computed("CASE WHEN turn_kind = 'QUESTION' THEN probe_batch_id END", persisted=True),
    )


class InterviewTurnPhoto(Base):
    __tablename__ = "interview_turn_photos"
    __table_args__ = (
        UniqueConstraint("turn_id", "sort_order"),
        CheckConstraint("sort_order >= 0", name="sort_order"),
        Index("ix_interview_turn_photos_media_id", "media_id"),
    )

    turn_id: Mapped[str] = mapped_column(ForeignKey("interview_turns.id"), primary_key=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("manual_media.id"), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer)


class InterviewEvaluation(Base):
    """Every Jev/fallback attempt with its immutable input snapshot and config version.
    `applied_depth` makes "one applied result per depth" a unique constraint."""

    __tablename__ = "interview_evaluations"
    __table_args__ = (
        UniqueConstraint("session_id", "intent_id", "depth", "attempt_no",
                         name="uq_interview_evaluations_attempt"),
        UniqueConstraint("session_id", "intent_id", "applied_depth",
                         name="uq_interview_evaluations_applied"),
        ForeignKeyConstraint(
            ["session_id", "intent_id"],
            ["interview_session_intents.session_id", "interview_session_intents.intent_id"],
            name="fk_interview_evaluations_session_intent",
        ),
        ForeignKeyConstraint(
            ["probe_batch_id", "session_id", "intent_id"],
            ["interview_probe_batches.id", "interview_probe_batches.session_id",
             "interview_probe_batches.intent_id"],
            name="fk_interview_evaluations_probe_batch",
        ),
        CheckConstraint(_in("status", EVALUATION_STATUSES), name="status"),
        CheckConstraint(_in("error_code", TASK_ERROR_CODES), name="error_code"),
        CheckConstraint("depth >= 0 AND depth <= 5 AND attempt_no >= 1", name="depth_attempt"),
        CheckConstraint("(depth = 0) = (probe_batch_id IS NULL)", name="batch_depth"),
        CheckConstraint("probability IS NULL OR (probability >= 0 AND probability <= 1)", name="probability"),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND needs_follow_up IS NOT NULL AND probability IS NOT NULL"
            " AND error_code IS NULL)"
            " OR (status = 'FAILED' AND needs_follow_up IS NULL AND error_code IS NOT NULL"
            " AND applied_at IS NULL)",
            name="status_consistency",
        ),
    )

    id: Mapped[str] = _id()
    session_id: Mapped[str] = mapped_column(CHAR(36))
    intent_id: Mapped[str] = mapped_column(CHAR(36))
    probe_batch_id: Mapped[str | None] = mapped_column(CHAR(36))
    depth: Mapped[int] = mapped_column(Integer)
    attempt_no: Mapped[int] = mapped_column(Integer)
    evaluated_through_turn_id: Mapped[str] = mapped_column(
        ForeignKey("interview_turns.id", name="fk_interview_evaluations_evaluated_through_turn"),
    )
    input_snapshot: Mapped[Any] = mapped_column(JSON)
    evaluation_config_version: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(cs_string(16))
    needs_follow_up: Mapped[bool | None] = mapped_column(Boolean)
    probability: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(cs_string(32))
    task_id: Mapped[str | None] = mapped_column(CHAR(36))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    applied_depth: Mapped[int | None] = mapped_column(
        Integer, Computed("CASE WHEN applied_at IS NOT NULL THEN depth END", persisted=True),
    )


class ManualReviewIssue(Base):
    """A gap shown on a draft/published version. With a target it is also a missingInformation
    entry (same ID). Resolved issues stay for the acknowledgement history."""

    __tablename__ = "manual_review_issues"
    __table_args__ = (
        CheckConstraint(_in("target_kind", MISSING_TARGETS), name="target_kind"),
        CheckConstraint(
            "(target_kind IS NULL AND target_id IS NULL AND field_name IS NULL"
            " AND public_description IS NULL)"
            " OR (target_kind = 'MANUAL' AND target_id IS NULL AND field_name IN ('shifts', 'sections')"
            " AND public_description IS NOT NULL)"
            " OR (target_kind = 'SHIFT' AND target_id IS NOT NULL"
            " AND field_name IN ('startTime', 'endTime', 'endsNextDay') AND public_description IS NOT NULL)"
            " OR (target_kind = 'SECTION' AND target_id IS NOT NULL AND field_name = 'steps'"
            " AND public_description IS NOT NULL)",
            name="target_shape",
        ),
        CheckConstraint(not_blank("description"), name="description_not_blank"),
        CheckConstraint(not_blank("public_description", nullable=True), name="public_description_not_blank"),
        Index("ix_manual_review_issues_version_id", "version_id"),
    )

    id: Mapped[str] = _id()
    version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"))
    intent_id: Mapped[str | None] = mapped_column(ForeignKey("interview_intents.id"))
    description: Mapped[str] = mapped_column(String(1000))
    target_kind: Mapped[str | None] = mapped_column(cs_string(8))
    target_id: Mapped[str | None] = mapped_column(CHAR(36))
    field_name: Mapped[str | None] = mapped_column(cs_string(16))
    public_description: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class ManualIssueAcknowledgement(Base):
    __tablename__ = "manual_issue_acknowledgements"
    __table_args__ = (
        UniqueConstraint("issue_id", "version_revision"),
        CheckConstraint("version_revision >= 1 AND content_revision >= 1", name="revisions"),
        CheckConstraint(not_blank("owner_note", nullable=True), name="owner_note_not_blank"),
    )

    id: Mapped[str] = _id()
    issue_id: Mapped[str] = mapped_column(ForeignKey("manual_review_issues.id"))
    version_revision: Mapped[int] = mapped_column(Integer)
    content_revision: Mapped[int] = mapped_column(Integer)
    acknowledged_snapshot: Mapped[Any] = mapped_column(JSON)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    owner_note: Mapped[str | None] = mapped_column(String(2000))
    acknowledged_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ManualDraftCorrection(Base):
    """Voice/text correction of a generated draft (API ManualDraftCorrection). The input text
    is a snapshot, so retries survive audio purge. One RUNNING correction per draft."""

    __tablename__ = "manual_draft_corrections"
    __table_args__ = (
        UniqueConstraint("running_version_id"),
        CheckConstraint(_in("target_kind", MISSING_TARGETS), name="target_kind"),
        CheckConstraint(_in("input_method", INPUT_METHODS), name="input_method"),
        CheckConstraint(_in("status", CORRECTION_STATUSES), name="status"),
        CheckConstraint(_in("error_code", CORRECTION_ERRORS), name="error_code"),
        CheckConstraint("base_revision >= 1 AND attempt >= 1", name="revisions"),
        CheckConstraint("(target_kind = 'MANUAL') = (target_id IS NULL)", name="target"),
        CheckConstraint("(input_method = 'VOICE') = (transcription_id IS NOT NULL)", name="voice_source"),
        CheckConstraint(
            "(status = 'RUNNING' AND result_revision IS NULL AND error_code IS NULL"
            " AND completed_at IS NULL AND task_id IS NOT NULL)"
            " OR (status = 'SUCCEEDED' AND result_revision IS NOT NULL AND error_code IS NULL"
            " AND completed_at IS NOT NULL)"
            " OR (status = 'ERROR' AND result_revision IS NULL AND error_code IS NOT NULL"
            " AND completed_at IS NOT NULL)",
            name="status_consistency",
        ),
        CheckConstraint(not_blank("input_text"), name="input_text_not_blank"),
        Index("ix_manual_draft_corrections_version_id_created_at", "version_id", "created_at"),
    )

    id: Mapped[str] = _id()
    version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"))
    base_revision: Mapped[int] = mapped_column(Integer)
    target_kind: Mapped[str] = mapped_column(cs_string(8))
    target_id: Mapped[str | None] = mapped_column(CHAR(36))
    input_method: Mapped[str] = mapped_column(cs_string(8))
    input_text: Mapped[str] = mapped_column(Text)
    transcription_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_transcriptions.id", name="fk_manual_draft_corrections_transcription_id"),
    )
    status: Mapped[str] = mapped_column(cs_string(16), default="RUNNING")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    task_id: Mapped[str | None] = mapped_column(CHAR(36))
    result_revision: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(cs_string(40))
    requested_by_owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    running_version_id: Mapped[str | None] = mapped_column(
        CHAR(36), Computed("CASE WHEN status = 'RUNNING' THEN version_id END", persisted=True),
    )


# --- worker AI Q&A (docs/erd/qa.md) ----------------------------------------------------------------

QA_STATUSES = ("RUNNING", "READY", "ERROR")
QA_OUTCOMES = ("ANSWERED", "NEEDS_OWNER")


class ManualQaConversation(Base):
    __tablename__ = "manual_qa_conversations"
    __table_args__ = (
        Index("ix_manual_qa_conversations_store_worker_updated", "store_id", "worker_id", "updated_at"),
    )

    id: Mapped[str] = _id()
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    worker_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class ManualQa(Base):
    """One question fixed to the published version current when it was asked. One RUNNING
    question per conversation (QA_BUSY) is a unique constraint on a generated column."""

    __tablename__ = "manual_qa"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence"),
        UniqueConstraint("running_conversation_id"),
        CheckConstraint(_in("status", QA_STATUSES), name="status"),
        CheckConstraint(_in("outcome", QA_OUTCOMES), name="outcome"),
        CheckConstraint(_in("input_method", INPUT_METHODS), name="input_method"),
        CheckConstraint(_in("public_error_code", PUBLIC_PROCESSING_ERRORS), name="public_error_code"),
        CheckConstraint("sequence >= 1 AND attempt >= 1", name="sequence_attempt"),
        CheckConstraint("(input_method = 'VOICE') = (transcription_id IS NOT NULL)", name="voice_source"),
        CheckConstraint(
            "(status = 'RUNNING' AND outcome IS NULL AND answer IS NULL AND public_error_code IS NULL"
            " AND completed_at IS NULL AND task_id IS NOT NULL)"
            " OR (status = 'READY' AND outcome IS NOT NULL AND answer IS NOT NULL"
            " AND public_error_code IS NULL AND completed_at IS NOT NULL)"
            " OR (status = 'ERROR' AND outcome IS NULL AND answer IS NULL"
            " AND public_error_code IS NOT NULL AND completed_at IS NOT NULL)",
            name="status_consistency",
        ),
        CheckConstraint(not_blank("question"), name="question_not_blank"),
        CheckConstraint(not_blank("answer", nullable=True), name="answer_not_blank"),
    )

    id: Mapped[str] = _id()
    conversation_id: Mapped[str] = mapped_column(ForeignKey("manual_qa_conversations.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    published_version_id: Mapped[str] = mapped_column(ForeignKey("manual_versions.id"))
    input_method: Mapped[str] = mapped_column(cs_string(8))
    question: Mapped[str] = mapped_column(Text)  # typed text or the READY transcript (<= 2000)
    transcription_id: Mapped[str | None] = mapped_column(ForeignKey("media_transcriptions.id"))
    status: Mapped[str] = mapped_column(cs_string(16), default="RUNNING")
    outcome: Mapped[str | None] = mapped_column(cs_string(16))
    answer: Mapped[str | None] = mapped_column(Text)  # <= 3000
    public_error_code: Mapped[str | None] = mapped_column(cs_string(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    task_id: Mapped[str | None] = mapped_column(CHAR(36))
    asked_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    running_conversation_id: Mapped[str | None] = mapped_column(
        CHAR(36), Computed("CASE WHEN status = 'RUNNING' THEN conversation_id END", persisted=True),
    )


class ManualQaCitation(Base):
    """A cited section of the question's fixed version with the server-built excerpt."""

    __tablename__ = "manual_qa_citations"
    __table_args__ = (
        UniqueConstraint("qa_id", "sort_order"),
        UniqueConstraint("qa_id", "section_id"),
        CheckConstraint("sort_order >= 0 AND sort_order < 10", name="sort_order"),
        CheckConstraint(not_blank("excerpt"), name="excerpt_not_blank"),
        Index("ix_manual_qa_citations_section_id", "section_id"),
    )

    id: Mapped[str] = _id()
    qa_id: Mapped[str] = mapped_column(ForeignKey("manual_qa.id"))
    section_id: Mapped[str] = mapped_column(ForeignKey("manual_sections.id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(Text)  # <= 1000


class ManualQaPhoto(Base):
    __tablename__ = "manual_qa_photos"
    __table_args__ = (
        UniqueConstraint("qa_id", "sort_order"),
        CheckConstraint("sort_order >= 0 AND sort_order < 3", name="sort_order"),
        Index("ix_manual_qa_photos_media_id", "media_id"),
    )

    qa_id: Mapped[str] = mapped_column(ForeignKey("manual_qa.id"), primary_key=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("qa_media.id"), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer)

"""Home summaries and monthly substitute-shift calendars (#117).

Every count and list in one response is evaluated at the same `asOf` (the jobs domain clock,
`app.jobs.common.now`). Calendars show only live or completed TEMPORARY_WORK confirmations;
regular access periods and availability are never calendar events.
"""
import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.auth import CurrentOwner, CurrentWorker
from app.db import SessionDep
from app.db.models import (
    AvailabilityDay,
    AvailabilityRule,
    FavoriteStore,
    Notification,
    Store,
    StoreAccessGrant,
    StoreApprovalRequest,
    User,
)
from app.errors import ApiError, ErrorCode
from app.jobs import common, queries
from app.jobs.search import worker_job_bodies
from app.store_access import APPROVED, is_uuid, load_owned_store, normalize_uuid, valid_grant_clause
from app.stores import owner_store_body

router = APIRouter()

HOME_STORE_LIMIT = 100
RECOMMENDATION_LIMIT = 3
CALENDAR_LIMIT = 1000
MONTH = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")  # date.weekday() order


# --- query parameters ----------------------------------------------------------------------


def _single(request: Request, name: str) -> str | None:
    values = request.query_params.getlist(name)
    if len(values) > 1:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": name, "code": "INVALID_FORMAT", "message": "한 번만 입력해 주세요."},
        ])
    return values[0] if values else None


def optional_store_id(request: Request) -> str | None:
    value = _single(request, "storeId")
    if value is None:
        return None
    if not is_uuid(value):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
            {"field": "storeId", "code": "INVALID_FORMAT", "message": "매장 ID 형식을 확인해 주세요."},
        ])
    return normalize_uuid(value)


def seoul_month(request: Request) -> tuple[str, datetime, datetime]:
    """(YYYY-MM, start, end): the Seoul month [00:00 on the 1st, 00:00 on the next 1st) in UTC."""
    value = _single(request, "month")
    error = ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
        {"field": "month", "code": "REQUIRED" if value is None else "INVALID_FORMAT",
         "message": "조회할 연월을 YYYY-MM 형식으로 입력해 주세요."},
    ])
    if value is None or not MONTH.match(value):
        raise error
    year, month = int(value[:4]), int(value[5:])
    try:
        first = date(year, month, 1)
        following = date(year + month // 12, month % 12 + 1, 1)
        start = datetime.combine(first, datetime.min.time(), common.SEOUL).astimezone(UTC)
        end = datetime.combine(following, datetime.min.time(), common.SEOUL).astimezone(UTC)
    except (ValueError, OverflowError):  # year 0000, or the edges of the calendar
        raise error from None
    return value, start, end


StoreFilter = Annotated[str | None, Depends(optional_store_id)]
Month = Annotated[tuple[str, datetime, datetime], Depends(seoul_month)]


# --- shared counts -------------------------------------------------------------------------


def unread_notification_count(db: Session, user_id: str, at: datetime) -> int:
    """Same rule as the notification list's unreadCount."""
    return db.scalar(select(func.count()).select_from(Notification).where(
        Notification.recipient_user_id == user_id, Notification.created_at <= at,
        Notification.read_at.is_(None)))


def favorite_store_count(db: Session, worker_id: str, at: datetime) -> int:
    """Same rule as the favorite store list's totalItems (APPROVED stores only)."""
    return db.scalar(
        select(func.count()).select_from(FavoriteStore).join(Store, Store.id == FavoriteStore.store_id)
        .where(FavoriteStore.worker_id == worker_id, FavoriteStore.saved_at <= at,
               Store.approval_status == APPROVED))


def regular_store_count(db: Session, worker_id: str, at: datetime) -> int:
    """Distinct stores where the worker has a usable REGULAR (invitation) grant now: the store
    APPROVED with an ACTIVE owner, the same rule as the worker store list and material access
    (`has_worker_store_access`), so a suspended owner's store is not counted."""
    owner = aliased(User)
    return db.scalar(
        select(func.count(func.distinct(StoreAccessGrant.store_id)))
        .join(Store, Store.id == StoreAccessGrant.store_id)
        .join(owner, owner.id == Store.owner_id)
        .where(StoreAccessGrant.worker_id == worker_id, StoreAccessGrant.invitation_id.is_not(None),
               valid_grant_clause(at), Store.approval_status == APPROVED,
               owner.role == "OWNER", owner.status == "ACTIVE"))


# --- availability match --------------------------------------------------------------------


def availability_windows(db: Session, worker_id: str) -> list[tuple[set[str], object, object, bool]]:
    """Weekly availability rules as (weekdays, start, end, ends_next_day)."""
    rules = list(db.scalars(select(AvailabilityRule).where(AvailabilityRule.worker_id == worker_id)))
    days: dict[str, set[str]] = {rule.id: set() for rule in rules}
    if rules:
        for rule_id, weekday in db.execute(select(AvailabilityDay.rule_id, AvailabilityDay.weekday)
                                           .where(AvailabilityDay.rule_id.in_(list(days)))):
            days[rule_id].add(weekday)
    return [(days[rule.id], rule.start_time, rule.end_time, rule.ends_next_day) for rule in rules]


def covers(windows, start_at: datetime, end_at: datetime) -> bool:
    """True when the union of availability, expanded onto real Seoul dates, covers the whole
    shift [start_at, end_at). The day before is included so a late-night window (e.g. SUN
    22:00 to MON 06:00) applies; adjacent windows join, touching only at an end is no cover."""
    first = common.seoul_today(start_at) - timedelta(days=1)
    last = common.seoul_today(end_at)
    spans = []
    day = first
    while day <= last:
        weekday = WEEKDAYS[day.weekday()]
        for weekdays, start, end, next_day in windows:
            if weekday in weekdays:
                times = common.shift_times(day, start, end, next_day)
                spans.append((times.start_at, times.end_at))
        day += timedelta(days=1)
    reach = start_at
    for span_start, span_end in sorted(spans):
        if span_start > reach:
            break
        reach = max(reach, span_end)
        if reach >= end_at:
            return True
    return False


def recommended_jobs(db: Session, worker_id: str, at: datetime) -> list[dict]:
    """Up to 3 open postings without the worker's live application: availability match
    first, then the nearest start, then id.

    Candidates arrive in (startAt, id) order, so the first 3 matches are the top matches and
    the first 3 non-matches the top non-matches. Reading stops once 3 matches are found (or 3
    candidates when there is no availability, so nothing can match); otherwise every candidate
    is read, exactly as a full sort would.
    """
    windows = availability_windows(db, worker_id)
    matched, unmatched = [], []
    for candidate in queries.recommendation_candidates_by_start(db, worker_id, at):
        times = common.shift_times(candidate.work_date, candidate.start_time, candidate.end_time,
                                   candidate.ends_next_day)
        if windows and covers(windows, times.start_at, times.end_at):
            matched.append((candidate.id, True))
            if len(matched) == RECOMMENDATION_LIMIT:
                break
        elif len(unmatched) < RECOMMENDATION_LIMIT:
            unmatched.append((candidate.id, False))
            if not windows and len(unmatched) == RECOMMENDATION_LIMIT:
                break
    top = (matched + unmatched)[:RECOMMENDATION_LIMIT]
    # The details re-run the scan's eligibility filter for the chosen IDs and rely on seeing the
    # same rows: the read-only home request keeps one transaction, and its snapshot (MySQL's
    # default REPEATABLE READ, SQLite's single transaction) cannot lose a scanned posting. Under
    # READ COMMITTED (`begin_transition`) or after a commit between the two reads, a posting closed
    # or applied to in between would be missing and `details[job_id]` would raise KeyError.
    details = queries.recommendation_details(db, worker_id, at, [job_id for job_id, _ in top])
    bodies = worker_job_bodies(db, worker_id, [details[job_id] for job_id, _ in top], at)
    return [{**body, "matchesAvailability": match} for body, (_, match) in zip(bodies, top, strict=True)]


# --- calendar ------------------------------------------------------------------------------


def calendar_body(month: str, shifts: list[queries.ConfirmedShift], at: datetime) -> dict:
    if len(shifts) > CALENDAR_LIMIT:
        raise ApiError(422, ErrorCode.CALENDAR_RANGE_TOO_LARGE, "한 달 일정이 너무 많습니다. 매장을 선택해 조회해 주세요.")
    return {
        "month": month,
        "timezone": "Asia/Seoul",
        "events": [{
            "id": item.shift.id,
            "store": common.store_card(item.store),
            "kind": "TEMPORARY_WORK",
            "title": item.job.title,
            "startAt": common.iso(item.start_at),
            "endAt": common.iso(item.end_at),
            "allDay": False,
            "workerId": item.shift.worker_id,
            "workerName": item.worker_name,
            "jobId": item.job.id,
            "revision": item.request_revision,
            "editable": False,
        } for item in shifts],
        "asOf": common.iso(at),
    }


# --- endpoints -----------------------------------------------------------------------------


@router.get("/api/owners/me/home")
def get_owner_home(owner: CurrentOwner, db: SessionDep, store_id: StoreFilter):
    at = common.now()
    total = db.scalar(select(func.count()).select_from(Store).where(Store.owner_id == owner.user_id))
    if total > HOME_STORE_LIMIT:
        raise ApiError(422, ErrorCode.HOME_STORE_LIMIT, "매장이 많아 홈에 모두 표시할 수 없습니다. 매장 목록에서 확인해 주세요.")
    rows = db.execute(
        select(Store, StoreApprovalRequest.id)
        .join(StoreApprovalRequest, StoreApprovalRequest.store_id == Store.id)
        .where(Store.owner_id == owner.user_id)
        .order_by(Store.created_at, Store.id)
    ).all()
    if store_id is not None:
        selected = load_owned_store(db, owner.user_id, store_id, require_approved=False,
                                    not_found=ErrorCode.RESOURCE_NOT_FOUND)
    else:
        selected = rows[0][0] if rows else None
    recruiting_count, recruiting = 0, []
    if selected is not None and selected.approval_status == APPROVED:
        recruiting_count, recruiting = queries.recruiting_jobs(db, selected.id, at, limit=3)
    return {
        "name": db.get(User, owner.user_id).name,
        "stores": [owner_store_body(store, request_id) for store, request_id in rows],
        "selectedStoreId": selected.id if selected is not None else None,
        "recruitingCount": recruiting_count,
        "recruitingJobs": recruiting,
        "unreadNotificationCount": unread_notification_count(db, owner.user_id, at),
        "asOf": common.iso(at),
    }


@router.get("/api/users/me/home")
def get_worker_home(worker: CurrentWorker, db: SessionDep):
    at = common.now()
    return {
        "name": db.get(User, worker.user_id).name,
        "pendingApplicationCount": queries.pending_application_count(db, worker.user_id, at),
        "favoriteStoreCount": favorite_store_count(db, worker.user_id, at),
        "regularStoreCount": regular_store_count(db, worker.user_id, at),
        "unreadNotificationCount": unread_notification_count(db, worker.user_id, at),
        "recommendedJobs": recommended_jobs(db, worker.user_id, at),
        "asOf": common.iso(at),
    }


@router.get("/api/users/me/calendar/events")
def get_worker_calendar(worker: CurrentWorker, db: SessionDep, month: Month):
    label, start, end = month
    at = common.now()
    return calendar_body(label, queries.confirmed_shifts(db, start, end, worker_id=worker.user_id), at)


@router.get("/api/owners/me/calendar/events")
def get_owner_calendar(owner: CurrentOwner, db: SessionDep, month: Month, store_id: StoreFilter):
    label, start, end = month
    at = common.now()
    if store_id is not None:
        store_ids = [load_owned_store(db, owner.user_id, store_id, not_found=ErrorCode.RESOURCE_NOT_FOUND).id]
    else:
        store_ids = list(db.scalars(select(Store.id).where(Store.owner_id == owner.user_id,
                                                           Store.approval_status == APPROVED)))
    return calendar_body(label, queries.confirmed_shifts(db, start, end, store_ids=store_ids), at)

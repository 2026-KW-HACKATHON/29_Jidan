"""Manual versions: lookup, reader authorization and API/AI serialization (#118).

Shared by the owner draft API (`app.manual_drafts`), the published manual API
(`app.manual_published`), draft corrections and the worker AI Q&A (#121). Content is stored
normalized per version (shifts, sections, steps, photo attachments) and gaps are
`manual_review_issues`; a published version is the same rows frozen, so IDs stay valid.

    store = require_manual_reader(db, member, store_id)          # OWNER or WORKER, 404/403
    version = current_published_version(db, store.id)            # None: not published
    content = content_body(db, version.id)                       # API ManualContent
    snapshot = structure_snapshot(db, version.id)                # app.ai StructureSnapshot
"""

from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.ai.contracts import MissingItem, SectionItem, ShiftItem, StepItem, StructureSnapshot
from app.auth import ROLE_OWNER, MemberPrincipal
from app.db import iso_utc, utcnow
from app.db.models import (
    ManualPhotoAttachment,
    ManualReviewIssue,
    ManualSection,
    ManualShift,
    ManualStep,
    ManualVersion,
    Store,
    StoreAccessGrant,
    StoreManual,
)
from app.errors import ApiError, ErrorCode
from app.store_access import (
    STORE_APPROVAL_REQUIRED_MESSAGE,
    STORE_NOT_FOUND_MESSAGE,
    has_worker_store_access,
    load_owned_store,
    normalize_uuid,
    store_operating,
    valid_grant_clause,
)

MANUAL_NOT_FOUND_MESSAGE = "매뉴얼 리소스를 찾을 수 없습니다."
MANUAL_NOT_PUBLISHED_MESSAGE = "아직 게시된 매뉴얼이 없습니다."
MANUAL_VERSION_CHANGED_MESSAGE = "게시 매뉴얼이 갱신되었습니다. 최신 목록을 다시 확인해 주세요."

# Display order of missing-information fields within one target.
_FIELD_ORDER = {"shifts": 0, "sections": 1, "startTime": 0, "endTime": 1, "endsNextDay": 2, "steps": 0}


def iso(value: datetime | None) -> str | None:
    return iso_utc(value)


def hhmm(value: time | None) -> str | None:
    return None if value is None else f"{value:%H:%M}"


def manual_not_found() -> ApiError:
    return ApiError(404, ErrorCode.MANUAL_RESOURCE_NOT_FOUND, MANUAL_NOT_FOUND_MESSAGE)


# --- lookup -----------------------------------------------------------------------------------


def store_manual(db: Session, store_id: str, *, lock: bool = False) -> StoreManual | None:
    """The store's manual row. `lock=True` is the manual lock every draft change, correction,
    publication and draft replacement takes first (docs/ai-foundation.md)."""
    statement = select(StoreManual).where(StoreManual.store_id == store_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return db.scalars(statement).first()


def active_draft(db: Session, manual_id: str, *, refresh: bool = False) -> ManualVersion | None:
    """The manual's single DRAFT version, if any.

    Pass `refresh=True` under the manual lock: it is then a locking read (`FOR UPDATE`), which
    sees the latest commit even under MySQL REPEATABLE READ, and refreshes identity-map copies.
    A plain read after the lock would still see the snapshot of the transaction's first read
    (e.g. the authentication dependency's), so a path that judges *other* rows (draft content,
    issues) after the lock must end that snapshot first: `db.commit()` before
    `store_manual(lock=True)`, or start the transaction in READ COMMITTED
    (`app.jobs.state.begin_transition`, as `app.manual_drafts.lock_current_draft` does).
    """
    # active_draft_manual_id is manual_id while the version is the DRAFT: the unique key keeps
    # the lock on one row whatever plan MySQL picks (app.jobs.state.lock_each explains why).
    statement = select(ManualVersion).where(ManualVersion.active_draft_manual_id == manual_id)
    if refresh:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return db.scalars(statement).first()


def current_published_version(db: Session, store_id: str) -> ManualVersion | None:
    """The version `store_manuals.current_published_version_id` points at, None if unpublished."""
    return db.scalars(
        select(ManualVersion)
        .join(StoreManual, StoreManual.current_published_version_id == ManualVersion.id)
        .where(StoreManual.store_id == store_id)
    ).first()


# --- reader authorization ---------------------------------------------------------------------


def require_manual_reader(
    db: Session, member: MemberPrincipal, store_id: str, *, now: datetime | None = None,
) -> Store:
    """The store whose published manual `member` may read now.

    OWNER: owns the store and it is APPROVED (404 STORE_NOT_FOUND / 403 STORE_APPROVAL_REQUIRED).
    WORKER: `has_worker_store_access` at `now` (ACTIVE worker, APPROVED store with an ACTIVE
    owner, a grant valid now). A store that is not operating (approval lost or owner not
    ACTIVE) while the worker still holds a valid grant is 403 STORE_APPROVAL_REQUIRED (openapi:
    매장 승인 중단은 403; invitations treat an inactive owner the same); anything else is 404
    STORE_NOT_FOUND, so a store without access looks like a missing one.
    """
    if member.role == ROLE_OWNER:
        return load_owned_store(db, member.user_id, store_id)
    now = now or utcnow()
    store = db.get(Store, normalize_uuid(store_id))
    if store is not None and not store_operating(db, store) and db.scalar(select(exists().where(
        StoreAccessGrant.store_id == store.id, StoreAccessGrant.worker_id == member.user_id,
        valid_grant_clause(now),
    ))):
        raise ApiError(403, ErrorCode.STORE_APPROVAL_REQUIRED, STORE_APPROVAL_REQUIRED_MESSAGE)
    if store is None or not has_worker_store_access(db, member.user_id, store.id, now):
        raise ApiError(404, ErrorCode.STORE_NOT_FOUND, STORE_NOT_FOUND_MESSAGE)
    return store


def require_published_version(
    db: Session, store_id: str, expected_version_id: str | None = None,
) -> ManualVersion:
    """The current published version after the reader check: 404 MANUAL_NOT_PUBLISHED when none,
    409 MANUAL_VERSION_CHANGED when the client's `expected_version_id` is no longer current."""
    version = current_published_version(db, store_id)
    if version is None:
        raise ApiError(404, ErrorCode.MANUAL_NOT_PUBLISHED, MANUAL_NOT_PUBLISHED_MESSAGE)
    if expected_version_id is not None and normalize_uuid(expected_version_id) != version.id:
        raise ApiError(409, ErrorCode.MANUAL_VERSION_CHANGED, MANUAL_VERSION_CHANGED_MESSAGE)
    return version


# --- loading ----------------------------------------------------------------------------------


@dataclass
class VersionRows:
    """Every content row of one version, in display order."""

    shifts: list[ManualShift]
    sections: list[ManualSection]
    steps: dict[str, list[ManualStep]]                       # section id -> steps
    photos: dict[str | None, list[ManualPhotoAttachment]]    # section id (None: structure) -> photos
    missing: list[ManualReviewIssue]                         # open issues with a target, ordered


def load_rows(db: Session, version_id: str) -> VersionRows:
    shifts = list(db.scalars(
        select(ManualShift).where(ManualShift.version_id == version_id).order_by(ManualShift.sort_order)))
    sections = list(db.scalars(
        select(ManualSection).where(ManualSection.version_id == version_id).order_by(ManualSection.sort_order)))
    steps: dict[str, list[ManualStep]] = {section.id: [] for section in sections}
    if sections:
        for step in db.scalars(
            select(ManualStep).where(ManualStep.section_id.in_(list(steps)))
            .order_by(ManualStep.section_id, ManualStep.sort_order)
        ):
            steps[step.section_id].append(step)
    photos: dict[str | None, list[ManualPhotoAttachment]] = {}
    for photo in db.scalars(
        select(ManualPhotoAttachment).where(ManualPhotoAttachment.version_id == version_id)
        .order_by(ManualPhotoAttachment.scope_id, ManualPhotoAttachment.sort_order)
    ):
        photos.setdefault(photo.section_id, []).append(photo)
    issues = db.scalars(select(ManualReviewIssue).where(
        ManualReviewIssue.version_id == version_id, ManualReviewIssue.resolved_at.is_(None),
        ManualReviewIssue.target_kind.is_not(None),
    )).all()
    return VersionRows(shifts, sections, steps, photos, order_missing(issues, shifts, sections))


def order_missing(issues, shifts, sections) -> list[ManualReviewIssue]:
    """Missing information in content order: the manual level, then shifts, then sections."""
    position = {"MANUAL": {None: (0, 0)}}
    position["SHIFT"] = {shift.id: (1, index) for index, shift in enumerate(shifts)}
    position["SECTION"] = {section.id: (2, index) for index, section in enumerate(sections)}

    def key(issue: ManualReviewIssue):
        place = position[issue.target_kind].get(issue.target_id, (3, 0))
        return (*place, _FIELD_ORDER[issue.field_name], issue.id)

    return sorted(issues, key=key)


# --- API serialization ------------------------------------------------------------------------


def photo_body(photo: ManualPhotoAttachment) -> dict:
    return {"mediaId": photo.media_id, "title": photo.title, "caption": photo.caption}


def shift_body(shift: ManualShift) -> dict:
    return {
        "id": shift.id, "name": shift.name, "startTime": hhmm(shift.start_time),
        "endTime": hhmm(shift.end_time), "endsNextDay": shift.ends_next_day,
    }


def section_body(rows: VersionRows, section: ManualSection) -> dict:
    return {
        "id": section.id, "category": section.category, "shiftId": section.shift_id,
        "title": section.title,
        "steps": [
            {"id": step.id, "instruction": step.instruction, "checklistItem": step.checklist_item}
            for step in rows.steps[section.id]
        ],
        "photos": [photo_body(photo) for photo in rows.photos.get(section.id, [])],
    }


def missing_body(issue: ManualReviewIssue) -> dict:
    return {
        "id": issue.id, "target": issue.target_kind, "targetId": issue.target_id,
        "field": issue.field_name, "description": issue.public_description,
    }


def rows_content_body(rows: VersionRows) -> dict:
    return {
        "shifts": [shift_body(shift) for shift in rows.shifts],
        "sections": [section_body(rows, section) for section in rows.sections],
        "structurePhotos": [photo_body(photo) for photo in rows.photos.get(None, [])],
        "missingInformation": [missing_body(issue) for issue in rows.missing],
    }


def content_body(db: Session, version_id: str) -> dict:
    """The API `ManualContent` of a version (draft or published)."""
    return rows_content_body(load_rows(db, version_id))


def structure_snapshot(db: Session, version_id: str) -> StructureSnapshot:
    """The version as the AI sees it (`app.ai.contracts.StructureSnapshot`): no photos."""
    rows = load_rows(db, version_id)
    return StructureSnapshot(
        shifts=tuple(
            ShiftItem(id=s.id, name=s.name, start_time=hhmm(s.start_time), end_time=hhmm(s.end_time),
                      ends_next_day=s.ends_next_day)
            for s in rows.shifts
        ),
        sections=tuple(
            SectionItem(
                id=s.id, category=s.category, shift_id=s.shift_id, title=s.title,
                steps=tuple(StepItem(id=t.id, instruction=t.instruction, checklist_item=t.checklist_item)
                            for t in rows.steps[s.id]),
            )
            for s in rows.sections
        ),
        missing_information=tuple(
            MissingItem(id=m.id, target=m.target_kind, target_id=m.target_id, field=m.field_name,
                        description=m.public_description)
            for m in rows.missing
        ),
    )


def published_structure(db: Session, version_id: str, *, store_id: str | None = None) -> StructureSnapshot | None:
    """The AI view of a PUBLISHED version (Q&A evidence for the version a question is pinned
    to); None when the version does not exist, is not published or, with `store_id`, belongs
    to another store's manual. Same rows and order as the published API."""
    statement = select(ManualVersion).where(
        ManualVersion.id == normalize_uuid(version_id), ManualVersion.status == "PUBLISHED")
    if store_id is not None:
        statement = statement.join(StoreManual, StoreManual.id == ManualVersion.manual_id).where(
            StoreManual.store_id == normalize_uuid(store_id))
    version = db.scalars(statement).first()
    return None if version is None else structure_snapshot(db, version.id)


# --- published views (worker list and section detail) -----------------------------------------

COMMON_CATEGORIES = ("COMMON_TASK", "RULE", "EQUIPMENT")


def published_header(store_id: str, version: ManualVersion) -> dict:
    return {
        "versionId": version.id, "storeId": store_id, "ownerConfirmed": True,
        "publishedAt": iso(version.published_at),
    }


def published_list_body(
    db: Session, store_id: str, version: ManualVersion, *, filter: str = "ALL",
    shift_id: str | None = None,
) -> dict:
    """The API `PublishedManualList`. `filter=COMMON` keeps common tasks, rules and equipment;
    `filter=SHIFT` adds the tasks of `shift_id` (a shift of this version, else 404)."""
    rows = load_rows(db, version.id)
    if shift_id is not None and shift_id not in {shift.id for shift in rows.shifts}:
        raise manual_not_found()
    sections = rows.sections
    if filter == "COMMON":
        sections = [s for s in sections if s.category in COMMON_CATEGORIES]
    elif filter == "SHIFT":
        sections = [s for s in sections if s.category in COMMON_CATEGORIES or s.shift_id == shift_id]
    return {
        **published_header(store_id, version),
        "versionNumber": version.revision_no,
        "shifts": [shift_body(shift) for shift in rows.shifts],
        "sections": [
            {
                "id": s.id, "category": s.category, "shiftId": s.shift_id, "title": s.title,
                "stepCount": len(rows.steps[s.id]), "photoCount": len(rows.photos.get(s.id, [])),
            }
            for s in sections
        ],
        "structurePhotos": [photo_body(photo) for photo in rows.photos.get(None, [])],
        "missingInformation": [missing_body(issue) for issue in rows.missing],
    }


def published_section_body(db: Session, store_id: str, version: ManualVersion, section_id: str) -> dict:
    """The API `PublishedManualSectionDetail`: the section of this version (else 404) and the
    missing information of the section and of the shift it belongs to."""
    rows = load_rows(db, version.id)
    section = next((s for s in rows.sections if s.id == normalize_uuid(section_id)), None)
    if section is None:
        raise manual_not_found()
    related = {("SECTION", section.id)}
    if section.shift_id is not None:
        related.add(("SHIFT", section.shift_id))
    return {
        **published_header(store_id, version),
        "section": section_body(rows, section),
        "missingInformation": [
            missing_body(issue) for issue in rows.missing if (issue.target_kind, issue.target_id) in related
        ],
    }

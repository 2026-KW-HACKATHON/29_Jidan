"""Changing a draft's content: server-side validation, issue sync and acknowledgements (#118).

`ManualContent` arrives from the owner (full edit) or from an AI correction. Both go through
`prepare_content` (everything the JSON Schema cannot say) and `replace_content`, inside the
manual lock (`app.manual_content.store_manual(lock=True)`) and after the draft checks of
`app.manual_drafts.lock_current_draft`.

Gaps: every unknown time (null) and empty required list has exactly one missingInformation entry,
and that entry *is* an open `manual_review_issues` row with the same ID. A content change
resolves the rows of filled gaps, opens new ones, bumps `revision` and `content_revision` and so
invalidates acknowledgements (they are bound to a content revision); an identical edit changes
nothing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.keyed import delete_by_key
from app.db.models import (
    ManualIssueAcknowledgement,
    ManualPhotoAttachment,
    ManualReviewIssue,
    ManualSection,
    ManualShift,
    ManualStep,
    ManualVersion,
)
from app.errors import ApiError, ErrorCode
from app.manual_attachments import AttachError, canonical_items, lock_attachable, referenced_ids
from app.manual_content import content_body, iso, load_rows, order_missing
from app.media.references import release_unreferenced
from app.store_access import UUID_PATTERN

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
NOT_BLANK = r"\S"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhotoIn(_In):
    """API `ManualPhotoAttachment`. `kind`/`posterMediaId` (0.12.0) may be echoed back from a
    response; the server re-derives both from the file (app.manual_attachments)."""

    media_id: str = Field(alias="mediaId", pattern=UUID_PATTERN)
    caption: str | None = Field(max_length=300)
    title: str = Field(min_length=1, max_length=100, pattern=NOT_BLANK)
    kind: Literal["PHOTO", "VIDEO"] | None = None
    poster_media_id: str | None = Field(default=None, alias="posterMediaId", pattern=UUID_PATTERN)


class ShiftIn(_In):
    id: str = Field(pattern=UUID_PATTERN)
    name: str = Field(min_length=1, max_length=50, pattern=NOT_BLANK)
    start_time: str | None = Field(alias="startTime", pattern=HHMM)
    end_time: str | None = Field(alias="endTime", pattern=HHMM)
    ends_next_day: bool | None = Field(alias="endsNextDay")


class StepIn(_In):
    id: str = Field(pattern=UUID_PATTERN)
    instruction: str = Field(min_length=1, max_length=3000, pattern=NOT_BLANK)
    checklist_item: bool = Field(alias="checklistItem")


class SectionIn(_In):
    id: str = Field(pattern=UUID_PATTERN)
    category: str = Field(pattern=r"^(COMMON_TASK|SHIFT_TASK|RULE|EQUIPMENT)$")
    shift_id: str | None = Field(alias="shiftId", pattern=UUID_PATTERN)
    title: str = Field(min_length=1, max_length=100, pattern=NOT_BLANK)
    steps: list[StepIn] = Field(max_length=100)
    photos: list[PhotoIn] = Field(max_length=20)

    @model_validator(mode="after")
    def _shift_scope(self):
        if (self.category == "SHIFT_TASK") != (self.shift_id is not None):
            raise ValueError("SHIFT_TASK needs shiftId; other categories have shiftId=null")
        return self


class MissingIn(_In):
    id: str = Field(pattern=UUID_PATTERN)
    target: str = Field(pattern=r"^(MANUAL|SHIFT|SECTION)$")
    target_id: str | None = Field(alias="targetId", pattern=UUID_PATTERN)
    field: str = Field(pattern=r"^(shifts|sections|startTime|endTime|endsNextDay|steps)$")
    description: str = Field(min_length=1, max_length=300, pattern=NOT_BLANK)

    @model_validator(mode="after")
    def _shape(self):
        allowed = {"MANUAL": ("shifts", "sections"), "SHIFT": ("startTime", "endTime", "endsNextDay"),
                   "SECTION": ("steps",)}[self.target]
        if self.field not in allowed or (self.target == "MANUAL") != (self.target_id is None):
            raise ValueError("target, targetId and field do not match")
        return self


class ContentIn(_In):
    """API `ManualContent` (request side)."""

    shifts: list[ShiftIn] = Field(max_length=20)
    sections: list[SectionIn] = Field(max_length=200)
    structure_photos: list[PhotoIn] = Field(default_factory=list, alias="structurePhotos", max_length=20)
    missing_information: list[MissingIn] = Field(
        default_factory=list, alias="missingInformation", max_length=200)


# --- validation -------------------------------------------------------------------------------


class ContentInvalid(Exception):
    """Content that breaks a server rule; `errors` are API fieldErrors (no submitted values)."""

    def __init__(self, errors: list[dict]):
        super().__init__("invalid manual content")
        self.errors = errors


def validation_error(errors: list[dict]) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=errors)


def _error(field: str, message: str, code: str = "INVALID_FORMAT") -> dict:
    return {"field": field, "code": code, "message": message}


def _clock(value: str | None) -> time | None:
    return None if value is None else time.fromisoformat(value)


def span_ok(start: time, end: time, next_day: bool) -> bool:
    """0 < duration <= 24h: same-day shifts end after they start, overnight ones at or before."""
    return end <= start if next_day else end > start


@dataclass(frozen=True)
class PreparedContent:
    """Validated content in the API owner shape with lower-case IDs (`body`), ready to write.
    `photo_ids`: every file it keeps alive (photos, videos and their posters)."""

    body: dict
    photo_ids: list[str]


def prepare_content(db: Session, version: ManualVersion, store_id: str, content: ContentIn,
                    *, prefix: str = "content") -> PreparedContent:
    """Check the rules the schema cannot express and return the normalized body.

    Raises ContentInvalid. Photos and videos are locked (`lock_attachable`) so a concurrent
    delete cannot remove a file this edit links; videos only go to sections (0.12.0).
    """
    errors: list[dict] = []
    body = _normalized(content)
    shifts, sections = body["shifts"], body["sections"]
    shift_ids = {shift["id"] for shift in shifts}
    section_ids = {section["id"] for section in sections}

    seen: dict[str, str] = {}
    for path, _kind, item_id in _all_ids(body, prefix):
        if item_id in seen:
            errors.append(_error(path, "ID가 중복되었습니다."))
        seen[item_id] = path

    for index, shift in enumerate(shifts):
        times = (shift["startTime"], shift["endTime"], shift["endsNextDay"])
        if None not in times and not span_ok(_clock(times[0]), _clock(times[1]), times[2]):
            errors.append(_error(f"{prefix}.shifts.{index}.endTime", "근무 시간은 0시간 초과 24시간 이하여야 합니다."))
    for index, section in enumerate(sections):
        if section["shiftId"] is not None and section["shiftId"] not in shift_ids:
            errors.append(_error(f"{prefix}.sections.{index}.shiftId", "같은 매뉴얼의 근무조를 선택해 주세요."))
        _duplicate_photos(section["photos"], f"{prefix}.sections.{index}.photos", errors)
    _duplicate_photos(body["structurePhotos"], f"{prefix}.structurePhotos", errors)

    # missingInformation must match the unknown values exactly.
    required = {("MANUAL", None, "shifts")} if not shifts else set()
    if not sections:
        required.add(("MANUAL", None, "sections"))
    for shift in shifts:
        for field in ("startTime", "endTime", "endsNextDay"):
            if shift[field] is None:
                required.add(("SHIFT", shift["id"], field))
    for section in sections:
        if not section["steps"]:
            required.add(("SECTION", section["id"], "steps"))
    given: set[tuple] = set()
    for index, item in enumerate(body["missingInformation"]):
        key = (item["target"], item["targetId"], item["field"])
        path = f"{prefix}.missingInformation.{index}"
        known = {"SHIFT": shift_ids, "SECTION": section_ids}.get(item["target"])
        if known is not None and item["targetId"] not in known:
            errors.append(_error(f"{path}.targetId", "현재 내용의 항목을 가리켜야 합니다."))
        elif key in given:
            errors.append(_error(path, "같은 미확정 항목이 중복되었습니다."))
        elif key not in required:
            errors.append(_error(path, "이미 정해진 값에는 미확정 정보를 둘 수 없습니다."))
        given.add(key)
    for target, target_id, field in sorted(required - given, key=str):
        errors.append(_error(f"{prefix}.missingInformation", f"{target} {field} 미확정 사유가 필요합니다.", "REQUIRED"))

    if not errors:
        errors.extend(_foreign_ids(db, version.id, body, prefix))
    media_ids = list(dict.fromkeys(
        [p["mediaId"] for p in body["structurePhotos"]]
        + [p["mediaId"] for section in sections for p in section["photos"]]))
    rows = {}
    if not errors:
        try:
            rows = lock_attachable(db, store_id, media_ids)
        except AttachError:
            errors.append(_error(f"{prefix}.photos", "같은 매장에 올린 사진·영상만 연결할 수 있습니다."))
    if not errors and any(rows[p["mediaId"]].kind == "VIDEO" for p in body["structurePhotos"]):
        errors.append(_error(f"{prefix}.structurePhotos", "근무 구조에는 사진만 연결할 수 있습니다."))
    if errors:
        raise ContentInvalid(errors)
    body["structurePhotos"] = canonical_items(body["structurePhotos"], rows)
    for section in sections:
        section["photos"] = canonical_items(section["photos"], rows)
    every = body["structurePhotos"] + [p for section in sections for p in section["photos"]]
    return PreparedContent(body, referenced_ids(every))


def _normalized(content: ContentIn) -> dict:
    def photo(p: PhotoIn) -> dict:
        return {"mediaId": p.media_id.lower(), "title": p.title, "caption": p.caption}

    return {
        "shifts": [
            {"id": s.id.lower(), "name": s.name, "startTime": s.start_time, "endTime": s.end_time,
             "endsNextDay": s.ends_next_day}
            for s in content.shifts
        ],
        "sections": [
            {"id": s.id.lower(), "category": s.category, "shiftId": s.shift_id and s.shift_id.lower(),
             "title": s.title,
             "steps": [{"id": t.id.lower(), "instruction": t.instruction, "checklistItem": t.checklist_item}
                       for t in s.steps],
             "photos": [photo(p) for p in s.photos]}
            for s in content.sections
        ],
        "structurePhotos": [photo(p) for p in content.structure_photos],
        "missingInformation": [
            {"id": m.id.lower(), "target": m.target, "targetId": m.target_id and m.target_id.lower(),
             "field": m.field, "description": m.description}
            for m in content.missing_information
        ],
    }


def _all_ids(body: dict, prefix: str):
    """(path, kind, id) of every client-chosen ID."""
    for index, shift in enumerate(body["shifts"]):
        yield f"{prefix}.shifts.{index}.id", "shift", shift["id"]
    for index, section in enumerate(body["sections"]):
        yield f"{prefix}.sections.{index}.id", "section", section["id"]
        for step_index, step in enumerate(section["steps"]):
            yield f"{prefix}.sections.{index}.steps.{step_index}.id", "step", step["id"]
    for index, item in enumerate(body["missingInformation"]):
        yield f"{prefix}.missingInformation.{index}.id", "issue", item["id"]


def _duplicate_photos(photos: list[dict], path: str, errors: list[dict]) -> None:
    media = [p["mediaId"] for p in photos]
    if len(media) != len(set(media)):
        errors.append(_error(path, "같은 사진을 한 목록에 두 번 연결할 수 없습니다."))


def _foreign_ids(db: Session, version_id: str, body: dict, prefix: str) -> list[dict]:
    """IDs may be new or reused from this draft for the same kind of item; never another
    version's (or another kind's) item, and a gap ID never moves to another target."""
    owner: dict[str, tuple[str, str]] = {}  # id -> (kind, version)
    ids = [item_id for _path, _kind, item_id in _all_ids(body, prefix)]
    for item_id, vid in db.execute(select(ManualShift.id, ManualShift.version_id).where(ManualShift.id.in_(ids))):
        owner[item_id] = ("shift", vid)
    for item_id, vid in db.execute(
            select(ManualSection.id, ManualSection.version_id).where(ManualSection.id.in_(ids))):
        owner[item_id] = ("section", vid)
    for item_id, vid in db.execute(
        select(ManualStep.id, ManualSection.version_id)
        .join(ManualSection, ManualSection.id == ManualStep.section_id).where(ManualStep.id.in_(ids))
    ):
        owner[item_id] = ("step", vid)
    issues = {issue.id: issue for issue in db.scalars(
        select(ManualReviewIssue).where(ManualReviewIssue.id.in_(ids)))}
    gaps = {item["id"]: item for item in body["missingInformation"]}
    errors = []
    for path, kind, item_id in _all_ids(body, prefix):
        found, issue = owner.get(item_id), issues.get(item_id)
        if kind == "issue":
            item = gaps[item_id]
            clash = found is not None or (issue is not None and (
                issue.version_id != version_id
                or (issue.target_kind, issue.target_id, issue.field_name)
                != (item["target"], item["targetId"], item["field"])))
        else:
            clash = issue is not None or (found is not None and found != (kind, version_id))
        if clash:
            errors.append(_error(path, "다른 항목이나 다른 버전의 ID는 사용할 수 없습니다."))
    return errors


# --- writing ----------------------------------------------------------------------------------


def same_content(db: Session, version: ManualVersion, body: dict) -> bool:
    """True when `body` shows exactly what the draft shows (missing-information order aside)."""
    current = content_body(db, version.id, owner=True)
    key = lambda items: sorted(items, key=lambda m: m["id"])
    return (
        {k: v for k, v in current.items() if k != "missingInformation"}
        == {k: v for k, v in body.items() if k != "missingInformation"}
        and key(current["missingInformation"]) == key(body["missingInformation"])
    )


def replace_content(db: Session, version: ManualVersion, prepared: PreparedContent,
                    *, now: datetime | None = None) -> bool:
    """Write `prepared` as the draft's content. Returns False (nothing written) when it equals
    the current content; otherwise revision and content_revision go up by one."""
    if same_content(db, version, prepared.body):
        return False
    now = now or utcnow()
    body = prepared.body
    previous_photos = set()
    for media_id, video_id in db.execute(select(ManualPhotoAttachment.media_id, ManualPhotoAttachment.video_media_id)
                                         .where(ManualPhotoAttachment.version_id == version.id)):
        previous_photos.update(filter(None, (media_id, video_id)))
    # Rows are rewritten under the same IDs: drop the loaded copies first so the identity map
    # does not confuse a new row with the deleted one.
    for row in list(db.identity_map.values()):
        if isinstance(row, ManualShift | ManualSection | ManualStep | ManualPhotoAttachment):
            db.expunge(row)
    # Old rows by primary key (plain reads, then point deletes): range deletes on version_id and
    # section_id would take gap locks that other manuals' INSERTs wait on, and the rows come back
    # under the same keys right below.
    section_ids = list(db.scalars(select(ManualSection.id).where(ManualSection.version_id == version.id)))
    for model, keys in (
        (ManualPhotoAttachment, select(ManualPhotoAttachment.id).where(ManualPhotoAttachment.version_id == version.id)),
        (ManualStep, select(ManualStep.id).where(ManualStep.section_id.in_(section_ids))),
        (ManualSection, None),
        (ManualShift, select(ManualShift.id).where(ManualShift.version_id == version.id)),
    ):
        delete_by_key(db, model, section_ids if keys is None else list(db.scalars(keys)))
    _insert_rows(db, version.id, body)
    _sync_issues(db, version.id, body["missingInformation"], now)
    # Invariant: a content write changes the version in the same transaction (tests/draft_revision.py).
    version.revision += 1
    version.content_revision += 1
    version.updated_at = now
    db.flush()
    release_unreferenced(db, previous_photos - set(prepared.photo_ids), now=now)
    return True


def write_initial_content(db: Session, version: ManualVersion, prepared: PreparedContent, *,
                          issue_intents: Mapping[str, str] | None = None,
                          now: datetime | None = None) -> None:
    """Write the first content of a generated draft (DRAFT_GENERATION apply, #120).

    Same rows and gap issues as `replace_content`, but `revision`/`content_revision` keep their
    initial values (the spec shows a freshly generated draft at revision 1) and only
    `updated_at` moves. `issue_intents` maps a missingInformation ID to the interview intent it
    came from (default: none, like an edit). The draft must have no content rows yet; the
    caller sets `generation_status` itself. Call it under the manual lock with photos locked by
    `prepare_content`.
    """
    has_rows = db.scalar(select(ManualShift.id).where(ManualShift.version_id == version.id).limit(1)) or db.scalar(
        select(ManualSection.id).where(ManualSection.version_id == version.id).limit(1))
    if version.status != "DRAFT" or has_rows:
        raise ValueError("initial content goes to an empty draft")
    now = now or utcnow()
    _insert_rows(db, version.id, prepared.body)
    _sync_issues(db, version.id, prepared.body["missingInformation"], now, intents=issue_intents or {})
    version.updated_at = now
    db.flush()


def _insert_rows(db: Session, version_id: str, body: dict) -> None:
    for order, shift in enumerate(body["shifts"]):
        db.add(ManualShift(
            id=shift["id"], version_id=version_id, sort_order=order, name=shift["name"],
            start_time=_clock(shift["startTime"]), end_time=_clock(shift["endTime"]),
            ends_next_day=shift["endsNextDay"]))
    db.flush()
    for order, section in enumerate(body["sections"]):
        db.add(ManualSection(
            id=section["id"], version_id=version_id, shift_id=section["shiftId"], sort_order=order,
            category=section["category"], title=section["title"]))
    db.flush()
    for section in body["sections"]:
        for order, step in enumerate(section["steps"]):
            db.add(ManualStep(id=step["id"], section_id=section["id"], sort_order=order,
                              instruction=step["instruction"], checklist_item=step["checklistItem"]))
        _add_photos(db, version_id, section["id"], section["photos"])
    _add_photos(db, version_id, None, body["structurePhotos"])
    db.flush()


def _add_photos(db: Session, version_id: str, section_id: str | None, photos: list[dict]) -> None:
    """A video item is stored as its poster's attachment remembering the video (app.manual_attachments)."""
    for order, photo in enumerate(photos):
        video = photo.get("kind") == "VIDEO"
        db.add(ManualPhotoAttachment(
            version_id=version_id, section_id=section_id,
            media_id=photo["posterMediaId"] if video else photo["mediaId"],
            video_media_id=photo["mediaId"] if video else None, sort_order=order,
            title=photo["title"], caption=photo["caption"]))


def _sync_issues(db: Session, version_id: str, missing: list[dict], now: datetime, *,
                 intents: Mapping[str, str] | None = None) -> None:
    """Open exactly the given gaps: resolve filled ones, reopen/update kept ones, add new ones
    (intent_id NULL: born from an edit). Resolved rows stay for the acknowledgement history."""
    wanted = {item["id"]: item for item in missing}
    rows = {issue.id: issue for issue in db.scalars(select(ManualReviewIssue).where(
        ManualReviewIssue.version_id == version_id, ManualReviewIssue.target_kind.is_not(None)))}
    for issue in rows.values():
        if issue.id not in wanted and issue.resolved_at is None:
            issue.resolved_at = now
    for item_id, item in wanted.items():
        issue = rows.get(item_id)
        if issue is None:
            db.add(ManualReviewIssue(
                id=item_id, version_id=version_id, intent_id=(intents or {}).get(item_id),
                description=item["description"],
                target_kind=item["target"], target_id=item["targetId"], field_name=item["field"],
                public_description=item["description"], created_at=now))
        else:
            if issue.public_description != item["description"]:
                issue.description = item["description"]
                issue.public_description = item["description"]
            issue.resolved_at = None
    db.flush()


# --- issues and acknowledgements --------------------------------------------------------------


def open_issues(db: Session, version: ManualVersion) -> list[ManualReviewIssue]:
    """Unresolved issues of the version: gaps in content order, then the other review items."""
    rows = load_rows(db, version.id)
    plain = db.scalars(select(ManualReviewIssue).where(
        ManualReviewIssue.version_id == version.id, ManualReviewIssue.resolved_at.is_(None),
        ManualReviewIssue.target_kind.is_(None),
    ).order_by(ManualReviewIssue.created_at, ManualReviewIssue.id)).all()
    return [*order_missing(rows.missing, rows.shifts, rows.sections), *plain]


def current_acknowledgements(db: Session, version: ManualVersion) -> dict[str, ManualIssueAcknowledgement]:
    """Latest acknowledgement per issue that still applies (made for the current content)."""
    latest: dict[str, ManualIssueAcknowledgement] = {}
    for ack in db.scalars(
        select(ManualIssueAcknowledgement)
        .join(ManualReviewIssue, ManualReviewIssue.id == ManualIssueAcknowledgement.issue_id)
        .where(ManualReviewIssue.version_id == version.id,
               ManualIssueAcknowledgement.content_revision == version.content_revision)
        .order_by(ManualIssueAcknowledgement.version_revision)
    ):
        latest[ack.issue_id] = ack
    return latest


def issue_bodies(db: Session, version: ManualVersion) -> list[dict]:
    """API `ManualReviewIssue` list: ACKNOWLEDGED with the note/time of the applying
    acknowledgement, else OPEN."""
    acks = current_acknowledgements(db, version)
    bodies = []
    for issue in open_issues(db, version):
        ack = acks.get(issue.id)
        bodies.append({
            "id": issue.id, "intentId": issue.intent_id, "description": issue.description,
            "status": "ACKNOWLEDGED" if ack else "OPEN",
            "ownerNote": ack.owner_note if ack else None,
            "acknowledgedAt": iso(ack.acknowledged_at) if ack else None,
        })
    return bodies


def acknowledge(db: Session, version: ManualVersion, issues: list[ManualReviewIssue], owner_id: str,
                note: str | None, *, now: datetime | None = None) -> bool:
    """Record the owner's acknowledgement of `issues` (all open issues of `version`).

    Issues already acknowledged for the current content with the same note keep their first
    record; if every issue is such, nothing changes and False is returned. Otherwise one
    acknowledgement row per changed issue is added at the new revision (revision + 1, the
    content revision is unchanged) and True is returned. Old rows are never deleted.
    """
    acks = current_acknowledgements(db, version)
    changed = [issue for issue in issues if issue.id not in acks or acks[issue.id].owner_note != note]
    if not changed:
        return False
    now = now or utcnow()
    version.revision += 1
    version.updated_at = now
    for issue in changed:
        db.add(ManualIssueAcknowledgement(
            issue_id=issue.id, version_revision=version.revision, content_revision=version.content_revision,
            acknowledged_snapshot={
                "issueId": issue.id, "description": issue.description, "target": issue.target_kind,
                "targetId": issue.target_id, "field": issue.field_name,
                "contentRevision": version.content_revision,
            },
            owner_id=owner_id, owner_note=note, acknowledged_at=now))
    db.flush()
    return True


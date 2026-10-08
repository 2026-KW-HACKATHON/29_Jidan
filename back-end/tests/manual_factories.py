"""Manual drafts and published versions written straight to the tables (#118 tests).

Content is given in the API `ManualContent` shape so a test states what the API should show:

    version = make_ready_draft(db, store, sample_content(photo=media.id))
    publish_version(db, version)

The rows are written here independently of app.manual_content so reads are checked against an
independent writer.
"""

import uuid
from datetime import time, timedelta

from sqlalchemy import select

from app.db.models import (
    ManualMedia,
    ManualPhotoAttachment,
    ManualReviewIssue,
    ManualSection,
    ManualShift,
    ManualStep,
    Store,
    StoreManual,
)
from tests.factories import NOW, make_manual_draft


def new_id() -> str:
    return str(uuid.uuid4())


def _time(value: str | None) -> time | None:
    return None if value is None else time.fromisoformat(value)


def sample_content(*, photo: str | None = None, structure_photo: str | None = None) -> dict:
    """Two shifts (the night one without an end time), a shift task, a common task without steps,
    a rule; the gaps are listed in missingInformation as the contract requires."""
    day, night = new_id(), new_id()
    shift_task, common, rule = new_id(), new_id(), new_id()
    return {
        "shifts": [
            {"id": day, "name": "오전조", "startTime": "09:00", "endTime": "15:00", "endsNextDay": False},
            {"id": night, "name": "야간조", "startTime": "22:00", "endTime": None, "endsNextDay": True},
        ],
        "sections": [
            {"id": shift_task, "category": "SHIFT_TASK", "shiftId": day, "title": "오픈 준비",
             "steps": [
                 {"id": new_id(), "instruction": "포스기를 켜세요.", "checklistItem": True},
                 {"id": new_id(), "instruction": "냉장고 온도를 확인하세요.", "checklistItem": False},
             ],
             "photos": [] if photo is None else [{"mediaId": photo, "title": "사진 1", "caption": None}]},
            {"id": common, "category": "COMMON_TASK", "shiftId": None, "title": "재고 정리",
             "steps": [], "photos": []},
            {"id": rule, "category": "RULE", "shiftId": None, "title": "복장 규정",
             "steps": [{"id": new_id(), "instruction": "앞치마를 착용하세요.", "checklistItem": False}],
             "photos": []},
        ],
        "structurePhotos": [] if structure_photo is None else [
            {"mediaId": structure_photo, "title": "근무표", "caption": "이번 달 근무표"}],
        "missingInformation": [
            {"id": new_id(), "target": "SHIFT", "targetId": night, "field": "endTime",
             "description": "야간조 종료 시각이 정해지지 않았어요."},
            {"id": new_id(), "target": "SECTION", "targetId": common, "field": "steps",
             "description": "재고 정리 절차가 아직 없어요."},
        ],
    }


def seed_content(db, version, content: dict, *, intent_id: str | None = None) -> None:
    for order, shift in enumerate(content["shifts"]):
        db.add(ManualShift(
            id=shift["id"], version_id=version.id, sort_order=order, name=shift["name"],
            start_time=_time(shift["startTime"]), end_time=_time(shift["endTime"]),
            ends_next_day=shift["endsNextDay"],
        ))
    db.flush()
    for order, section in enumerate(content["sections"]):
        db.add(ManualSection(
            id=section["id"], version_id=version.id, shift_id=section["shiftId"], sort_order=order,
            category=section["category"], title=section["title"],
        ))
    db.flush()
    for section in content["sections"]:
        for order, step in enumerate(section["steps"]):
            db.add(ManualStep(id=step["id"], section_id=section["id"], sort_order=order,
                              instruction=step["instruction"], checklist_item=step["checklistItem"]))
        for order, photo in enumerate(section["photos"]):
            db.add(ManualPhotoAttachment(
                version_id=version.id, section_id=section["id"], media_id=photo["mediaId"],
                sort_order=order, title=photo["title"], caption=photo["caption"]))
    for order, photo in enumerate(content.get("structurePhotos", [])):
        db.add(ManualPhotoAttachment(
            version_id=version.id, section_id=None, media_id=photo["mediaId"], sort_order=order,
            title=photo["title"], caption=photo["caption"]))
    for item in content.get("missingInformation", []):
        db.add(ManualReviewIssue(
            id=item["id"], version_id=version.id, intent_id=intent_id, description=item["description"],
            target_kind=item["target"], target_id=item["targetId"], field_name=item["field"],
            public_description=item["description"]))
    db.flush()


def make_ready_draft(db, store, content: dict | None = None, **overrides):
    """A generated (READY) draft holding `content` (default `sample_content()`)."""
    version = make_manual_draft(db, store, generation_status="READY", **overrides)
    seed_content(db, version, sample_content() if content is None else content)
    return version


def publish_version(db, version, *, at=NOW):
    """Publish `version` as the owner would (pointer swap); the previous version stays."""
    manual = db.get(StoreManual, version.manual_id)
    owner_id = db.get(Store, manual.store_id).owner_id
    version.status = "PUBLISHED"
    version.published_at = at
    version.published_by_owner_id = owner_id
    db.flush()
    manual.current_published_version_id = version.id
    db.flush()
    return version


def make_published(db, store, content: dict | None = None, *, at=NOW):
    return publish_version(db, make_ready_draft(db, store, content), at=at)


def make_photo(db, store, *, kind: str = "IMAGE", **overrides) -> ManualMedia:
    """A stored photo row of `store` (the bytes are not needed by the content APIs)."""
    media_id = new_id()
    media = ManualMedia(
        id=media_id, store_id=store.id, uploaded_by_owner_id=store.owner_id, kind=kind,
        object_key=f"manual/{store.id}/{media_id}",
        mime_type="image/jpeg" if kind == "IMAGE" else "audio/mpeg", byte_size=100,
        duration_ms=None if kind == "IMAGE" else 1000, created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    for key, value in overrides.items():
        setattr(media, key, value)
    db.add(media)
    db.flush()
    return media


def manual_of(db, store) -> StoreManual | None:
    return db.scalars(select(StoreManual).where(StoreManual.store_id == store.id)).first()

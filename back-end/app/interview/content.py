"""Review content: the API `ManualInterviewReview` JSON kept in `ready_content`, and its
conversion to and from the AI's photo-free `StructureSnapshot`.

The model never sees photos. Whenever a structure comes back from the AI the photos are joined
again by section ID (and the work-structure photos are kept as they are), so a summary or a
correction can neither invent, move nor drop a photo except by deleting its section.
"""

from typing import Any

from app.ai.contracts import MissingItem, SectionItem, ShiftItem, StepItem, StructureSnapshot


def snapshot_from_content(content: dict[str, Any]) -> StructureSnapshot:
    return StructureSnapshot(
        shifts=tuple(
            ShiftItem(id=s["id"], name=s["name"], start_time=s["startTime"], end_time=s["endTime"],
                      ends_next_day=s["endsNextDay"])
            for s in content.get("shifts", [])
        ),
        sections=tuple(
            SectionItem(
                id=s["id"], category=s["category"], shift_id=s["shiftId"], title=s["title"],
                steps=tuple(StepItem(id=t["id"], instruction=t["instruction"],
                                     checklist_item=t["checklistItem"]) for t in s["steps"]),
            )
            for s in content.get("sections", [])
        ),
        missing_information=tuple(
            MissingItem(id=m["id"], target=m["target"], target_id=m["targetId"], field=m["field"],
                        description=m["description"])
            for m in content.get("missingInformation", [])
        ),
    )


def shifts_json(structure: StructureSnapshot) -> list[dict[str, Any]]:
    return [{"id": s.id, "name": s.name, "startTime": s.start_time, "endTime": s.end_time,
             "endsNextDay": s.ends_next_day} for s in structure.shifts]


def content_from_structure(intent_id: str, summary: str, structure: StructureSnapshot, *,
                           needs_detail: bool, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """New review content; photos of `previous` stay on the sections that still exist."""
    section_photos = {s["id"]: s.get("photos", []) for s in (previous or {}).get("sections", [])}
    return {
        "intentId": intent_id,
        "summary": summary,
        "shifts": shifts_json(structure),
        "sections": [
            {
                "id": s.id, "category": s.category, "shiftId": s.shift_id, "title": s.title,
                "steps": [{"id": t.id, "instruction": t.instruction, "checklistItem": t.checklist_item}
                          for t in s.steps],
                "photos": list(section_photos.get(s.id, [])),
            }
            for s in structure.sections
        ],
        "needsDetail": needs_detail,
        "structurePhotos": list((previous or {}).get("structurePhotos", [])),
        "missingInformation": [
            {"id": m.id, "target": m.target, "targetId": m.target_id, "field": m.field,
             "description": m.description}
            for m in structure.missing_information
        ],
    }


def photo_ids(content: dict[str, Any] | None) -> list[str]:
    """Every photo the content shows, in display order, without duplicates."""
    if not content:
        return []
    ids = [p["mediaId"] for p in content.get("structurePhotos", [])]
    for section in content.get("sections", []):
        ids.extend(p["mediaId"] for p in section.get("photos", []))
    return list(dict.fromkeys(ids))


def shift_ids(content: dict[str, Any] | None) -> set[str]:
    return {s["id"] for s in (content or {}).get("shifts", [])}


def referenced_shift_ids(content: dict[str, Any] | None) -> set[str]:
    return {s["shiftId"] for s in (content or {}).get("sections", []) if s.get("shiftId")}

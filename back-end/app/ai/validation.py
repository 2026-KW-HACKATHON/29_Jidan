"""Server-side re-validation of model output against the request it answered.

Structured Outputs guarantees a shape, not the truth. Everything here checks what the schema
cannot: that referenced IDs were really present in the input, that new items use placeholders,
that references resolve, the ManualContent rules for unknown values, and that a targeted
correction did not touch unrelated content. A violation raises `AiError(INVALID_OUTPUT)`
(retryable) with a fixed `detail` naming the rule, never the offending text.
"""

import re
import uuid
from collections.abc import Iterable

from app.ai.contracts import (
    Citation,
    MissingItem,
    SectionItem,
    ShiftItem,
    StepItem,
    StructureSnapshot,
    clean_text,
)
from app.ai.errors import AiError, AiErrorCode
from app.ai.schemas import RawCitation, RawMissing, RawStructure

NEW_REF = re.compile(r"^new-[1-9][0-9]{0,3}$")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SHIFT_FIELDS = {"startTime": "start_time", "endTime": "end_time", "endsNextDay": "ends_next_day"}
MAX_EXCERPT = 1000


def invalid(detail: str) -> AiError:
    return AiError(AiErrorCode.INVALID_OUTPUT, detail=detail)


def _text(value: str, limit: int, detail: str) -> str:
    cleaned = clean_text(value)
    if not cleaned or len(cleaned) > limit:
        raise invalid(detail)
    return cleaned


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def shift_span_is_valid(start: str, end: str, ends_next_day: bool) -> bool:
    """0 < duration <= 24h: same day needs end > start; overnight needs end <= start."""
    if ends_next_day:
        return _minutes(end) <= _minutes(start)
    return _minutes(end) > _minutes(start)


class _Refs:
    """Maps output references to IDs: known input IDs are kept, `new-<n>` gets a fresh UUID."""

    def __init__(self, known: dict[str, set[str]]):
        self.known = known  # kind -> IDs present in the input
        self.mapping: dict[str, str] = {}

    def claim(self, ref: str, kind: str) -> str:
        if ref in self.mapping:
            raise invalid(f"duplicate_ref:{kind}")
        if NEW_REF.match(ref):
            value = str(uuid.uuid4())
        elif ref in self.known.get(kind, set()):
            value = ref
        else:
            raise invalid(f"unknown_ref:{kind}")
        self.mapping[ref] = value
        return value


def materialize_structure(
    raw: RawStructure,
    *,
    known_shift_ids: Iterable[str] = (),
    known_section_ids: Iterable[str] = (),
    known_step_ids: Iterable[str] = (),
    external_shifts: Iterable[ShiftItem] = (),
    previous_missing: Iterable[MissingItem] = (),
    require_manual_level: bool = False,
) -> StructureSnapshot:
    """Validate a raw structure and give every item a real, unique UUID.

    * Output refs must be IDs from the input (of the same kind) or `new-<n>` placeholders.
    * `external_shifts` may be referenced by SHIFT_TASK sections but not redefined.
    * Unknown values (null times, empty steps; with `require_manual_level` also empty
      shift/section lists) must have a missing-information entry. Entries that point at a
      complete value are dropped; duplicates are merged. Entry IDs are reused from
      `previous_missing` for the same (target, targetId, field) so acknowledgements keep
      matching the same issue.
    """
    external = {shift.id: shift for shift in external_shifts}
    refs = _Refs({
        "shift": set(known_shift_ids) - set(external),
        "section": set(known_section_ids),
        "step": set(known_step_ids),
    })

    shifts: list[ShiftItem] = []
    for raw_shift in raw.shifts:
        shift_id = refs.claim(raw_shift.ref, "shift")
        times = (raw_shift.start_time, raw_shift.end_time)
        if any(value is not None and not TIME.match(value) for value in times):
            raise invalid("shift_time_format")
        if None not in (*times, raw_shift.ends_next_day) and not shift_span_is_valid(
            raw_shift.start_time, raw_shift.end_time, raw_shift.ends_next_day,
        ):
            raise invalid("shift_span")
        shifts.append(ShiftItem(
            id=shift_id, name=_text(raw_shift.name, 50, "shift_name"),
            start_time=raw_shift.start_time, end_time=raw_shift.end_time,
            ends_next_day=raw_shift.ends_next_day,
        ))
    shift_ids = {shift.id for shift in shifts} | set(external)

    sections: list[SectionItem] = []
    for raw_section in raw.sections:
        section_id = refs.claim(raw_section.ref, "section")
        if raw_section.category == "SHIFT_TASK":
            if raw_section.shift_ref is None:
                raise invalid("shift_task_without_shift")
            shift_id = refs.mapping.get(raw_section.shift_ref, raw_section.shift_ref)
            if shift_id not in shift_ids:
                raise invalid("unresolved_shift_ref")
        elif raw_section.shift_ref is not None:
            raise invalid("shift_ref_on_shared_section")
        else:
            shift_id = None
        steps = tuple(
            StepItem(
                id=refs.claim(raw_step.ref, "step"),
                instruction=_text(raw_step.instruction, 3000, "step_instruction"),
                checklist_item=raw_step.checklist_item,
            )
            for raw_step in raw_section.steps
        )
        sections.append(SectionItem(
            id=section_id, category=raw_section.category, shift_id=shift_id,
            title=_text(raw_section.title, 100, "section_title"), steps=steps,
        ))

    missing = _materialize_missing(
        raw.missing_information, refs, shifts, sections, previous_missing, require_manual_level,
    )
    return StructureSnapshot(shifts=tuple(shifts), sections=tuple(sections), missing_information=missing)


def _materialize_missing(
    raw_items: list[RawMissing],
    refs: _Refs,
    shifts: list[ShiftItem],
    sections: list[SectionItem],
    previous_missing: Iterable[MissingItem],
    require_manual_level: bool,
) -> tuple[MissingItem, ...]:
    by_shift = {shift.id: shift for shift in shifts}
    by_section = {section.id: section for section in sections}
    previous = {(item.target, item.target_id, item.field): item.id for item in previous_missing}
    entries: dict[tuple[str, str | None, str], str] = {}
    for item in raw_items:
        target_id = None if item.target_ref is None else refs.mapping.get(item.target_ref, item.target_ref)
        if item.target == "MANUAL":
            if item.target_ref is not None or item.field not in ("shifts", "sections"):
                raise invalid("missing_manual_shape")
            unknown = (not shifts) if item.field == "shifts" else (not sections)
        elif item.target == "SHIFT":
            if target_id not in by_shift or item.field not in SHIFT_FIELDS:
                raise invalid("missing_shift_target")
            unknown = getattr(by_shift[target_id], SHIFT_FIELDS[item.field]) is None
        else:
            if target_id not in by_section or item.field != "steps":
                raise invalid("missing_section_target")
            unknown = not by_section[target_id].steps
        if not unknown:
            continue  # a note about a value that is in fact complete: not a missing value
        key = (item.target, target_id, item.field)
        entries.setdefault(key, _text(item.description, 300, "missing_description"))

    required: list[tuple[str, str | None, str]] = []
    for shift in shifts:
        for field, attribute in SHIFT_FIELDS.items():
            if getattr(shift, attribute) is None:
                required.append(("SHIFT", shift.id, field))
    for section in sections:
        if not section.steps:
            required.append(("SECTION", section.id, "steps"))
    if require_manual_level:
        if not shifts:
            required.append(("MANUAL", None, "shifts"))
        if not sections:
            required.append(("MANUAL", None, "sections"))
    if any(key not in entries for key in required):
        raise invalid("unknown_value_without_missing_information")
    return tuple(
        MissingItem(
            id=previous.get(key) or str(uuid.uuid4()), target=key[0], target_id=key[1],
            field=key[2], description=description,
        )
        for key, description in entries.items()
    )


def known_ids(snapshot: StructureSnapshot) -> dict[str, set[str]]:
    return {
        "shift": {shift.id for shift in snapshot.shifts},
        "section": {section.id for section in snapshot.sections},
        "step": {step.id for section in snapshot.sections for step in section.steps},
    }


def _without_missing(snapshot: StructureSnapshot) -> StructureSnapshot:
    return snapshot.model_copy(update={"missing_information": ()})


def same_content(left: StructureSnapshot, right: StructureSnapshot) -> bool:
    """Equal shifts, sections and missing entries (missing IDs and order ignored)."""
    def missing(snapshot: StructureSnapshot):
        return sorted(
            (item.target, item.target_id or "", item.field, item.description)
            for item in snapshot.missing_information
        )
    return _without_missing(left) == _without_missing(right) and missing(left) == missing(right)


def check_revision_scope(current: StructureSnapshot, revised: StructureSnapshot, kind: str,
                         target_id: str | None) -> None:
    """For SHIFT/SECTION targets, every other existing item must come back unchanged.

    New items may be added and the target itself may change or be deleted. MANUAL targets may
    change anything.
    """
    if kind == "MANUAL":
        return
    revised_shifts = {shift.id: shift for shift in revised.shifts}
    revised_sections = {section.id: section for section in revised.sections}
    for shift in current.shifts:
        if kind == "SHIFT" and shift.id == target_id:
            continue
        if revised_shifts.get(shift.id) != shift:
            raise invalid("revision_outside_target")
    for section in current.sections:
        if kind == "SECTION" and section.id == target_id:
            continue
        if revised_sections.get(section.id) != section:
            raise invalid("revision_outside_target")

    # Missing descriptions are content too, including outside a correction target.
    def outside_missing(snapshot):
        return {(item.target, item.target_id, item.field): item.description
                for item in snapshot.missing_information
                if (item.target, item.target_id) != (kind, target_id)}
    revised_missing = outside_missing(revised)
    if any(revised_missing.get(key) != value for key, value in outside_missing(current).items()):
        raise invalid("revision_outside_target")



def dangling_shift_references(snapshot: StructureSnapshot, external_shift_ids: Iterable[str] = ()) -> bool:
    shift_ids = {shift.id for shift in snapshot.shifts} | set(external_shift_ids)
    return any(
        section.shift_id is not None and section.shift_id not in shift_ids
        for section in snapshot.sections
    )


def build_citations(raw: list[RawCitation], manual: StructureSnapshot) -> tuple[Citation, ...]:
    """Resolve cited sections/steps against the manual and quote the steps verbatim.

    The excerpt is assembled by the server from the cited steps (or the section's first steps),
    so it is always real text of the fixed manual version and never model-written.
    """
    sections = {section.id: section for section in manual.sections}
    citations: list[Citation] = []
    seen: set[str] = set()
    for item in raw:
        section = sections.get(item.section_id)
        if section is None:
            raise invalid("citation_unknown_section")
        if section.id in seen:
            continue
        seen.add(section.id)
        steps = {step.id: step for step in section.steps}
        if any(step_id not in steps for step_id in item.step_ids):
            raise invalid("citation_unknown_step")
        cited = [steps[step_id] for step_id in dict.fromkeys(item.step_ids)] or list(section.steps)
        excerpt = "\n".join(step.instruction for step in cited) or section.title
        if len(excerpt) > MAX_EXCERPT:
            excerpt = excerpt[: MAX_EXCERPT - 1].rstrip() + "…"
        citations.append(Citation(
            section_id=section.id, section_title=section.title,
            step_ids=tuple(step.id for step in cited), excerpt=excerpt,
        ))
    return tuple(citations)

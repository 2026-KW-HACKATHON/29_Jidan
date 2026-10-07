"""Server-side re-validation of model output against the request it answered.

Structured Outputs guarantees a shape, not the truth. Everything here checks what the schema
cannot: that referenced IDs were really present in the input, that new items use placeholders,
that references resolve, the ManualContent rules for unknown values, and that a targeted
correction did not touch unrelated content, and (`ground_structure`) that written steps cite
the owner's words given as evidence. A violation raises `AiError(INVALID_OUTPUT)`
(retryable) with a fixed `detail` naming the rule, never the offending text.
"""

import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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
from app.ai.schemas import RawCitation, RawMissing, RawShift, RawStructure

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


UNGROUNDED_STEPS = "점주 답변에서 근거를 찾지 못한 단계라 비워 두었어요. 점주 확인이 필요해요."
UNGROUNDED_TIME = "점주 답변에서 근거를 찾지 못한 시간이라 비워 두었어요. 점주 확인이 필요해요."
ShiftTimes = tuple[str | None, str | None, bool | None]


@dataclass(frozen=True)
class Grounding:
    """What `ground_structure` removed (counts only, safe to log)."""

    dropped_steps: int = 0
    cleared_shifts: int = 0
    # Of dropped_steps, those removed from a section that kept other steps. Nothing in the
    # content records them: a missing-information entry is only valid for an unknown value
    # (`_materialize_missing`), and a section with steps is not unknown. Logged only.
    dropped_in_kept_sections: int = 0


def _times(shift: RawShift | ShiftItem) -> ShiftTimes:
    return (shift.start_time, shift.end_time, shift.ends_next_day)


def ground_structure(
    raw: RawStructure,
    evidence_ids: Iterable[str],
    *,
    grounded_steps: Mapping[str, str | None] | None = None,
    grounded_shifts: Mapping[str, ShiftTimes | None] | None = None,
) -> tuple[RawStructure, Grounding]:
    """Enforce citations against the request's evidence before `materialize_structure`.

    With no evidence (requests built before grounding) nothing is checked
    and citations are ignored, so those callers behave exactly as before.

    With evidence:
    * Every cited ID must be one of the evidence chunk IDs, else INVALID_OUTPUT
      (`unknown_evidence_id`, retryable): a made-up citation means the output cannot be trusted.
    * A step without any citation is *removed*, not rejected. Rejecting would turn a single
      unsupported sentence into a failed review after retries; keeping it would put a fact
      the owner never said into the manual. Removing it keeps the policy "only the owner's own
      words are facts" and the existing unknown-value rule: a section whose steps all went
      becomes "steps unknown" and gets a SECTION/steps missing-information entry (an entry is
      only valid for an unknown value, so a section that keeps other steps gets none).
    * Shift times (start/end/endsNextDay) without a citation are cleared to null, each with a
      SHIFT missing-information entry, for the same reason. Shift names and section titles are
      labels, not facts, and need no citation.
    * Content that already passed validation is exempt: `grounded_steps` maps existing step IDs
      to their instruction (None = may be reworded, e.g. a draft polishing reviewed steps) and
      `grounded_shifts` maps existing shift IDs to their times (None = any). An existing item
      returned unchanged needs no new citation; a changed one does.
    Model-written missing entries come first, so their wording wins over the fixed text here.
    """
    allowed = set(evidence_ids)
    if not allowed:
        return raw, Grounding()
    grounded_steps = grounded_steps or {}
    grounded_shifts = grounded_shifts or {}

    def cited(ids: list[str]) -> bool:
        if any(value not in allowed for value in ids):
            raise invalid("unknown_evidence_id")
        return bool(ids)

    missing = list(raw.missing_information)
    shifts: list[RawShift] = []
    cleared = 0
    for shift in raw.shifts:
        times = _times(shift)
        has_citation = cited(shift.evidence_ids)
        exempt = shift.ref in grounded_shifts and grounded_shifts[shift.ref] in (None, times)
        if times == (None, None, None) or has_citation or exempt:
            shifts.append(shift)
            continue
        cleared += 1
        shifts.append(shift.model_copy(update={"start_time": None, "end_time": None, "ends_next_day": None}))
        missing.extend(
            RawMissing(target="SHIFT", target_ref=shift.ref, field=field, description=UNGROUNDED_TIME)
            for field in SHIFT_FIELDS
        )

    sections = []
    dropped = partial = 0
    for section in raw.sections:
        kept = []
        for step in section.steps:
            has_citation = cited(step.evidence_ids)
            exempt = step.ref in grounded_steps and (
                grounded_steps[step.ref] is None
                or clean_text(grounded_steps[step.ref]) == clean_text(step.instruction))
            if has_citation or exempt:
                kept.append(step)
            else:
                dropped += 1
        if section.steps and not kept:
            missing.append(RawMissing(target="SECTION", target_ref=section.ref, field="steps",
                                      description=UNGROUNDED_STEPS))
        elif kept:
            partial += len(section.steps) - len(kept)
        sections.append(section.model_copy(update={"steps": kept}) if len(kept) != len(section.steps)
                        else section)

    if not cleared and not dropped:
        return raw, Grounding()
    grounded = raw.model_copy(update={"shifts": shifts, "sections": sections, "missing_information": missing})
    return grounded, Grounding(dropped_steps=dropped, cleared_shifts=cleared, dropped_in_kept_sections=partial)


# --- content-free steps (backstop for the writing prompts) --------------------------------------
#
# The prompts say that a step is a concrete action and that "do it as the situation requires"
# or "there are no rules" is not one. These patterns are only a backstop for the clearest cases
# the model still writes (seen in live runs); they are deliberately narrow: a vague phrase must
# be followed directly by a handling verb, and the step must be a single short clause, so a step
# with any concrete action ("먼저 사과하고, 나머지는 상황에 맞게 처리해요") is kept.

_VAGUE_HANDLING = re.compile(
    r"(상황에\s*맞게|상황을?\s*봐\s*서|상황에\s*따라|그때그때|알아서|적당히|눈치껏|상식적으로|"
    r"융통성\s*있게|유연하게|센스\s*있게|자연스럽게)\s*(잘\s*)?"
    r"(처리|대응|판단|행동|해요|해\s*주세요|하세요|하면\s*돼요|하면\s*됩니다)")
_NO_RULE = re.compile(
    r"(정해\s*(둔|놓은|진)|정한|따로\s*있는)\s*[^.!?]{0,30}?(없어요|없습니다|없음|없다)|해당\s*(사항\s*)?없")
_CLAUSE_JOIN = re.compile(r"[,;·]|고\s|한\s*(뒤|후|다음)|하면서")
MAX_CONTENTLESS_CHARS = 80
VAGUE_STEPS = "구체적인 처리 방법을 아직 정하지 않았어요. 점주 확인이 필요해요."
NO_RULE_STEPS = "점주가 따로 정한 내용이 없다고 했어요."


def contentless_kind(instruction: str) -> str | None:
    """"vague" ("상황에 맞게 처리해요"), "no_rule" ("따로 정해 둔 규칙은 없어요") or None."""
    text = clean_text(instruction)
    if len(text) > MAX_CONTENTLESS_CHARS or _CLAUSE_JOIN.search(text):
        return None
    if _NO_RULE.search(text):
        return "no_rule"
    if _VAGUE_HANDLING.search(text):
        return "vague"
    return None


@dataclass(frozen=True)
class Contentless:
    """What `drop_contentless_steps` removed (counts only, safe to log)."""

    vague_steps: int = 0
    no_rule_steps: int = 0
    removed_sections: int = 0


def drop_contentless_steps(
    raw: RawStructure,
    *,
    kept_section_refs: Iterable[str] = (),
    existing_steps: Mapping[str, str] | None = None,
) -> tuple[RawStructure, Contentless]:
    """Remove steps that tell a worker nothing to do.

    A vague step is what the owner did not settle: a section left without steps gets a
    SECTION/steps missing entry (the existing unknown-value rule). A "no rule" step states that
    nothing exists, which is a fact, not an unknown: a new section made only of such steps is
    removed, while an existing section (`kept_section_refs`: reviewed sections a draft or a
    correction must keep) stays empty with a missing entry so the owner sees and settles it.
    `existing_steps` (step ID -> instruction) are left alone when returned unchanged, so a
    correction never touches content outside its target.
    """
    kept_refs = set(kept_section_refs)
    existing = existing_steps or {}

    def kind_of(step) -> str | None:
        if step.ref in existing and clean_text(existing[step.ref]) == clean_text(step.instruction):
            return None
        return contentless_kind(step.instruction)

    missing = list(raw.missing_information)
    sections = []
    vague = no_rule = removed = 0
    for section in raw.sections:
        kinds = [kind_of(step) for step in section.steps]
        if not any(kinds):
            sections.append(section)
            continue
        vague += kinds.count("vague")
        no_rule += kinds.count("no_rule")
        kept = [step for step, kind in zip(section.steps, kinds, strict=True) if kind is None]
        if kept:
            sections.append(section.model_copy(update={"steps": kept}))
            continue
        if section.ref not in kept_refs and "vague" not in kinds:
            removed += 1
            missing = [m for m in missing if m.target_ref != section.ref]
            continue
        sections.append(section.model_copy(update={"steps": []}))
        missing.append(RawMissing(target="SECTION", target_ref=section.ref, field="steps",
                                  description=VAGUE_STEPS if "vague" in kinds else NO_RULE_STEPS))
    if not (vague or no_rule):
        return raw, Contentless()
    cleaned = raw.model_copy(update={"sections": sections, "missing_information": missing})
    return cleaned, Contentless(vague_steps=vague, no_rule_steps=no_rule, removed_sections=removed)


def empty_structure() -> RawStructure:
    return RawStructure(shifts=[], sections=[], missing_information=[])


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

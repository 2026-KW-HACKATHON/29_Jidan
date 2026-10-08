"""Question guidance (OpenAPI 0.11.0, #158): the server rules for help text and guidance cards.

Guidance is written once with its question (app.interview.flow.write_question), in the same
transaction that stores the question and bumps the session revision, and is never recomputed on
read: one session revision always shows one question with one set of cards.

ManualGuidanceCard (JSON Schema) fixes each card's shape. What it cannot express is checked here
before anything is stored (docs/manual-interview-design.md "질문 안내 카드"):

    * at most 5 cards; card IDs unique in the array, item IDs unique in their card
    * at most one PROGRESS_CHECKLIST per question and at most one CURRENT item in it
    * a PHOTO_SUGGESTIONS attachmentTarget names a READY review of the same session, and a
      SECTION target a section of that review's READY content

Guidance is decoration, so it never stops a question: a card that breaks its own rules is left
out alone (with the reason logged), the rest is kept, and a photo target that does not name a
READY review/section of this session becomes null (recommendations only, no attachment). A target
that a later correction removes is not rewritten: the client re-reads the review before attaching
and the photos API checks the reference again (a card never grants an upload).

The deterministic builder uses only this session's intent progress and READY review targets.
It does not turn AI examples into discovered tasks or infer completion from an answer. Broad
questions point to their single intent with CURRENT on BASE and PROBE alike. A caller can omit
CURRENT only when it explicitly knows that the question spans multiple checklist intents.

The question generator's examples become one LIST card (`example_card`). Its IDs are UUIDv5 values
of the session, the intent and the label, so an example that means the same thing keeps its ID
across the questions of an intent.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import InterviewIntent, InterviewIntentReview, InterviewSession
from app.interview.common import session_intents

MAX_CARDS = 5
MAX_ITEMS = 50
MAX_GUIDANCE = 2000
MAX_TITLE = 200
MAX_LABEL = 200
MAX_TEXT = 1000
EXAMPLE_TITLE = "답변 예시"
INTENT_LABELS = {
    "WORK_STRUCTURE": "근무 구조", "COMMON_TASKS": "공통 업무", "SHIFT_TASKS": "근무조별 업무",
    "RULES": "매장 규칙", "EQUIPMENT": "설비 사용", "EXCEPTIONS": "예외 상황",
}
NAMESPACE = uuid.UUID("a3c1e0b4-6f0d-4d7e-9a43-1c58e2b7d901")

PROGRESS_STATUSES = ("PENDING", "CURRENT", "COMPLETED", "NEEDS_DETAIL")
PHOTO_TARGETS = ("WORK_STRUCTURE", "SECTION")


class InvalidGuidance(ValueError):
    """Guidance that breaks the contract; the question is stored without it."""


def _text(value: Any, limit: int, what: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise InvalidGuidance(f"{what}: not a string")
    text = value.strip()
    if not text or len(text) > limit:
        raise InvalidGuidance(f"{what}: blank or longer than {limit}")
    return text


def _uuid(value: Any, what: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise InvalidGuidance(f"{what}: not a UUID") from None


def _fields(value: Any, allowed: set[str], required: set[str], what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidGuidance(f"{what}: not an object")
    if unknown := set(value) - allowed:
        raise InvalidGuidance(f"{what}: unknown fields {sorted(unknown)}")
    if missing := required - set(value):
        raise InvalidGuidance(f"{what}: missing fields {sorted(missing)}")
    return value


def check_guidance(value: Any) -> str | None:
    """ManualInterviewQuestion.guidance: null, or 1..2000 characters that are not all blank."""
    return _text(value, MAX_GUIDANCE, "guidance", nullable=True)


def _item(value: Any, progress: bool, what: str) -> dict[str, Any]:
    keys = {"id", "label", "description"} | ({"status"} if progress else set())
    raw = _fields(value, keys, keys - {"description"}, what)
    item = {
        "id": _uuid(raw["id"], f"{what}.id"),
        "label": _text(raw["label"], MAX_LABEL, f"{what}.label"),
        "description": _text(raw.get("description"), MAX_TEXT, f"{what}.description", nullable=True),
    }
    if progress:
        if raw["status"] not in PROGRESS_STATUSES:
            raise InvalidGuidance(f"{what}.status: unknown")
        item["status"] = raw["status"]
    return item


def _target(db: Session, session_id: str, value: Any, what: str) -> dict[str, Any]:
    raw = _fields(value, {"intentId", "target", "sectionId"}, {"intentId", "target", "sectionId"}, what)
    intent_id = _uuid(raw["intentId"], f"{what}.intentId")
    if raw["target"] not in PHOTO_TARGETS:
        raise InvalidGuidance(f"{what}.target: unknown")
    review = db.scalars(select(InterviewIntentReview).where(
        InterviewIntentReview.session_id == session_id, InterviewIntentReview.intent_id == intent_id,
    )).first()
    if review is None or review.status != "READY" or not review.ready_content:
        raise InvalidGuidance(f"{what}.intentId: no READY review of this session")
    if raw["target"] == "WORK_STRUCTURE":
        intent = db.get(InterviewIntent, intent_id)
        if intent is None or intent.stage != "WORK_STRUCTURE":
            raise InvalidGuidance(f"{what}.target: not a WORK_STRUCTURE stage review")
        if raw["sectionId"] is not None:
            raise InvalidGuidance(f"{what}.sectionId: must be null for WORK_STRUCTURE")
        return {"intentId": intent_id, "target": "WORK_STRUCTURE", "sectionId": None}
    section_id = _uuid(raw["sectionId"], f"{what}.sectionId")
    if section_id not in {section["id"] for section in review.ready_content.get("sections", [])}:
        raise InvalidGuidance(f"{what}.sectionId: not a section of the READY review")
    return {"intentId": intent_id, "target": "SECTION", "sectionId": section_id}


def check_card(db: Session, session_id: str, value: Any, what: str = "card",
               notes: list[str] | None = None) -> dict[str, Any]:
    """One card in the stored (= response) form, or InvalidGuidance. A photo target that cannot
    be used becomes null; the reason goes to `notes`."""
    kind = value.get("type") if isinstance(value, dict) else None
    keys = {"id", "type", "title", "items", "footer"}
    if kind == "PHOTO_SUGGESTIONS":
        raw = _fields(value, keys | {"attachmentTarget"}, keys - {"footer"} | {"attachmentTarget"}, what)
    elif kind in ("LIST", "PROGRESS_CHECKLIST"):
        raw = _fields(value, keys, keys - {"footer"}, what)
    else:
        raise InvalidGuidance(f"{what}.type: unknown")
    items = raw["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise InvalidGuidance(f"{what}.items: 1..{MAX_ITEMS} items")
    progress = kind == "PROGRESS_CHECKLIST"
    checked = [_item(item, progress, f"{what}.items[{i}]") for i, item in enumerate(items)]
    if len({item["id"] for item in checked}) != len(checked):
        raise InvalidGuidance(f"{what}.items: duplicate item IDs")
    if progress and sum(item["status"] == "CURRENT" for item in checked) > 1:
        raise InvalidGuidance(f"{what}.items: more than one CURRENT")
    card = {
        "id": _uuid(raw["id"], f"{what}.id"), "type": kind,
        "title": _text(raw["title"], MAX_TITLE, f"{what}.title"), "items": checked,
        "footer": _text(raw.get("footer"), MAX_TEXT, f"{what}.footer", nullable=True),
    }
    if kind == "PHOTO_SUGGESTIONS":
        card["attachmentTarget"] = None
        if raw["attachmentTarget"] is not None:
            try:
                card["attachmentTarget"] = _target(db, session_id, raw["attachmentTarget"],
                                                   f"{what}.attachmentTarget")
            except InvalidGuidance as error:
                if notes is not None:
                    notes.append(f"{error} (target set to null)")
    return card


def clean_cards(db: Session, session_id: str, cards: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """ManualInterviewQuestion.guidanceCards to store, in order, and why anything was left out or
    changed. Later cards lose: a repeated card ID, a second PROGRESS_CHECKLIST, a sixth card."""
    if not isinstance(cards, list | tuple):
        return [], ["guidanceCards: not a list"]
    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    for index, value in enumerate(cards):
        what = f"guidanceCards[{index}]"
        try:
            card = check_card(db, session_id, value, what, notes)
        except InvalidGuidance as error:
            notes.append(f"{error} (card left out)")
            continue
        if any(other["id"] == card["id"] for other in kept):
            notes.append(f"{what}: duplicate card ID (card left out)")
        elif card["type"] == "PROGRESS_CHECKLIST" and any(o["type"] == card["type"] for o in kept):
            notes.append(f"{what}: more than one PROGRESS_CHECKLIST (card left out)")
        elif len(kept) == MAX_CARDS:
            notes.append(f"{what}: more than {MAX_CARDS} cards (card left out)")
        else:
            kept.append(card)
    return kept, notes


def state_cards(db: Session, session: InterviewSession, intent_id: str, *, focused: bool = True,
                max_cards: int = MAX_CARDS) -> list[dict[str, Any]]:
    """Snapshot observed interview progress and available photo targets at question creation.

    Checklist units are the actual question-set intents, not guessed tasks/procedures. IDs
    depend on the session and semantic target, never question/depth/attempt or status. A question
    marks CURRENT on its own still-PENDING intent. focused=False is reserved for questions that
    span multiple checklist intents. Coverage comes exclusively from the stored Jev result.
    No review/target is fabricated from examples.

    Different READY sections get separate cards. Current review targets come first, then previous
    intents from nearest to oldest, then later related intents in interview order. Sections keep
    their review order. The caller reserves slots for LIST examples through max_cards; the
    builder never creates cards that will be
    silently cut off. If no READY target exists, the recommendation has a null target.
    """
    progress_rows = session_intents(db, session.id)
    limit = max(0, min(MAX_CARDS, max_cards))
    if not progress_rows or not limit:
        return []
    progress_id = uuid.uuid5(NAMESPACE, f"{session.id}:progress")
    items = []
    for progress, intent in progress_rows[:MAX_ITEMS]:
        status = {"COVERED": "COMPLETED", "NEEDS_DETAIL": "NEEDS_DETAIL"}.get(
            progress.coverage_status, "PENDING")
        if (status == "PENDING" and focused and intent.id == intent_id
                and session.current_intent_id == intent_id):
            status = "CURRENT"
        items.append({"id": str(uuid.uuid5(progress_id, intent.id)),
                      "label": INTENT_LABELS.get(intent.intent_key, intent.intent_key), "status": status})
    cards = [{"id": str(progress_id), "type": "PROGRESS_CHECKLIST", "title": "인터뷰 진행",
              "items": items, "footer": None}]
    reviews = {r.intent_id: r for r in db.scalars(select(InterviewIntentReview).where(
        InterviewIntentReview.session_id == session.id, InterviewIntentReview.status == "READY",
    ))}
    current_index = next((index for index, (_p, intent) in enumerate(progress_rows)
                          if intent.id == intent_id), len(progress_rows))
    prioritized = (progress_rows[current_index:current_index + 1]
                   + list(reversed(progress_rows[:current_index]))
                   + progress_rows[current_index + 1:])
    for _progress, intent in prioritized:
        if len(cards) >= limit:
            break
        review = reviews.get(intent.id)
        if review is None or not review.ready_content:
            continue
        if intent.stage == "WORK_STRUCTURE" and review.ready_content.get("shifts"):
            cards.append(_photo_card(session.id, intent.id, "WORK_STRUCTURE", "근무표",
                                     {"intentId": intent.id, "target": "WORK_STRUCTURE", "sectionId": None}))
        for section in review.ready_content.get("sections", []):
            if len(cards) >= limit:
                break
            cards.append(_photo_card(session.id, intent.id, section["id"], section["title"],
                                     {"intentId": intent.id, "target": "SECTION", "sectionId": section["id"]}))
    if len(cards) == 1 and len(cards) < limit:
        intent = next((i for _p, i in progress_rows if i.id == intent_id), None)
        if intent is not None:
            label = "근무표" if intent.stage == "WORK_STRUCTURE" else INTENT_LABELS.get(
                intent.intent_key, intent.intent_key)
            cards.append(_photo_card(session.id, intent.id,
                                     "WORK_STRUCTURE" if intent.stage == "WORK_STRUCTURE" else "recommendation",
                                     label, None))
    return cards


def _photo_card(session_id: str, intent_id: str, semantic_target: str, label: str,
                target: dict[str, Any] | None) -> dict[str, Any]:
    card_id = uuid.uuid5(NAMESPACE, f"{session_id}:{intent_id}:photos:{semantic_target}")
    return {"id": str(card_id), "type": "PHOTO_SUGGESTIONS", "title": "첨부 추천 사진",
            "items": [{"id": str(uuid.uuid5(card_id, "photo")), "label": label,
                       "description": "위치나 배치를 확인할 수 있는 사진이 있으면 첨부해 주세요."}],
            "attachmentTarget": target, "footer": "사진 없이 계속할 수 있어요."}


def example_card(session_id: str, intent_id: str, examples: Any) -> dict[str, Any] | None:
    """The LIST card of the generator's examples ((label, description) pairs), or None.

    Repeated labels are kept once (first wins); an example that breaks the item rules makes the
    whole card invalid rather than silently showing part of it."""
    if not examples:
        return None
    card_id = uuid.uuid5(NAMESPACE, f"{session_id}:examples:{intent_id}")
    items, seen = [], set()
    for label, description in examples:
        text = _text(label, MAX_LABEL, "example.label")
        if text in seen:
            continue
        seen.add(text)
        items.append({"id": str(uuid.uuid5(card_id, text)), "label": text,
                      "description": _text(description, MAX_TEXT, "example.description", nullable=True)})
    return {"id": str(card_id), "type": "LIST", "title": EXAMPLE_TITLE, "items": items[:MAX_ITEMS],
            "footer": None}

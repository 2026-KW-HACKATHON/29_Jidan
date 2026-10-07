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

Not checked here (the card builder's job): that CURRENT is the item the question asks about, and
that an item keeps its ID across the questions of a session.

The question generator's examples become one LIST card (`example_card`). Its IDs are UUIDv5 values
of the session, the intent and the label, so an example that means the same thing keeps its ID
across the questions of an intent.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import InterviewIntentReview

MAX_CARDS = 5
MAX_ITEMS = 50
MAX_GUIDANCE = 2000
MAX_TITLE = 200
MAX_LABEL = 200
MAX_TEXT = 1000
EXAMPLE_TITLE = "답변 예시"
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

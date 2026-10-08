"""Pure question-card validation and stable server identifier assignment.

Only the same-intent history supplied to this generation is an ID authority. Text and
position never identify a card or an item. Call once while persisting a question.
"""
from uuid import UUID, uuid4

from app.ai.contracts import clean_text
from app.ai.schemas import RawQuestionCard


def normalize_question_cards(raw_cards, previous_cards=()) -> list[dict]:
    history = {}
    owners = {}
    for card in previous_cards:
        try:
            UUID(card["id"])
            if card["type"] not in ("LIST", "PROGRESS_CHECKLIST"):
                continue
            for item in card["items"]:
                UUID(item["id"])
                # question_context supplies newest cards first. An item may have
                # belonged to older cards before a merge; those obsolete owners
                # must not make the current card ambiguous on every later turn.
                owners.setdefault(item["id"], {(card["type"], card["id"])})
            history[card["id"]] = card
        except (KeyError, TypeError, ValueError):
            continue
    result, used_items, used_cards = [], set(), set()
    progress = False
    if not isinstance(raw_cards, (list, tuple)):
        return result
    for value in raw_cards[:5]:
        try:
            card = RawQuestionCard.model_validate(value)
            if card.type == "PROGRESS_CHECKLIST" and progress:
                continue
            title, footer = clean_text(card.title), card.footer
            if not title or footer is not None and not clean_text(footer):
                continue
            ids = [item.id for item in card.items if item.id is not None]
            if len(ids) != len(set(ids)) or used_items.intersection(ids):
                continue
            if any(not owners.get(item_id) or any(t != card.type for t, _ in owners[item_id]) for item_id in ids):
                continue
            if card.type == "LIST" and any(item.status is not None for item in card.items):
                continue
            if card.type == "PROGRESS_CHECKLIST" and (
                any(item.status is None for item in card.items)
                or sum(item.status == "CURRENT" for item in card.items) > 1
            ):
                continue
            if any(not clean_text(i.label) or i.description is not None and not clean_text(i.description) for i in card.items):
                continue
            candidates = {owner for item_id in ids for typ, owner in owners[item_id] if typ == card.type}
            card_id = next(iter(candidates)) if len(candidates) == 1 else str(uuid4())
            if card_id in used_cards:
                continue
            items = []
            for item in card.items:
                saved = {"id": item.id or str(uuid4()), "label": clean_text(item.label),
                         "description": clean_text(item.description) if item.description is not None else None}
                if card.type == "PROGRESS_CHECKLIST":
                    saved["status"] = item.status
                items.append(saved)
            result.append({"id": card_id, "type": card.type, "title": title, "items": items,
                           "footer": clean_text(footer) if footer is not None else None})
            used_cards.add(card_id)
            used_items.update(i["id"] for i in items)
            progress |= card.type == "PROGRESS_CHECKLIST"
        except (ValueError, TypeError):
            continue
    return result

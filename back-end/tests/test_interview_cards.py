from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from app.interview.cards import normalize_question_cards


def card(kind="LIST", status=None, count=1):
    return {"type": kind, "title": "업무", "footer": None, "items": [
        {"id": None, "label": f"업무 {i}", "description": None, "status": status}
        for i in range(count)
    ]}


def test_list_has_server_ids_and_omits_status():
    saved = normalize_question_cards([card()])[0]
    UUID(saved["id"])
    UUID(saved["items"][0]["id"])
    assert "status" not in saved["items"][0]


@pytest.mark.parametrize("count,accepted", [(0, False), (1, True), (50, True), (51, False)])
def test_item_limits(count, accepted):
    assert bool(normalize_question_cards([card(count=count)])) == accepted


@pytest.mark.parametrize("kind,status,accepted", [
    ("LIST", "CURRENT", False), ("PROGRESS_CHECKLIST", None, False),
    ("PROGRESS_CHECKLIST", "PENDING", True), ("PROGRESS_CHECKLIST", "NEEDS_DETAIL", True),
    ("PHOTO_SUGGESTIONS", None, False),
])
def test_card_status_rules(kind, status, accepted):
    assert bool(normalize_question_cards([card(kind, status)])) == accepted


def test_progress_and_current_limits_preserve_valid_card():
    good = card("PROGRESS_CHECKLIST", "PENDING")
    bad = card("PROGRESS_CHECKLIST", "CURRENT", 2)
    assert len(normalize_question_cards([bad, good, good, card()])) == 2
    assert len(normalize_question_cards([card()] * 6)) == 5


def test_stable_ids_survive_reorder_rename_addition_and_empty_turn():
    old = normalize_question_cards([card("PROGRESS_CHECKLIST", "PENDING", 2)])[0]
    new = card("PROGRESS_CHECKLIST", "PENDING", 3)
    for item, prior in zip(new["items"], reversed(old["items"])):
        item["id"] = prior["id"]
        item["label"] = "수정된 표현"
    assert normalize_question_cards([], [old]) == []
    saved = normalize_question_cards([new], [old])[0]
    assert saved["id"] == old["id"]
    assert [i["id"] for i in saved["items"][:2]] == [i["id"] for i in reversed(old["items"])]
    assert saved["items"][2]["id"] not in {i["id"] for i in old["items"]}


def test_unknown_duplicate_or_wrong_type_ids_drop_only_bad_card():
    old = normalize_question_cards([card()])[0]
    candidate = card()
    for item_id, history in [(str(uuid4()), [old]), (old["items"][0]["id"], [])]:
        candidate["items"][0]["id"] = item_id
        assert len(normalize_question_cards([candidate, card()], history)) == 1
    candidate["items"] *= 2
    assert normalize_question_cards([candidate], [old]) == []
    candidate = card("PROGRESS_CHECKLIST", "PENDING")
    candidate["items"][0]["id"] = old["items"][0]["id"]
    assert normalize_question_cards([candidate], [old]) == []


def test_latest_card_owns_item_when_older_history_has_another_card():
    first = normalize_question_cards([card()])[0]
    second = deepcopy(first)
    second["id"] = str(uuid4())
    candidate = card()
    candidate["items"][0]["id"] = first["items"][0]["id"]
    saved = normalize_question_cards([candidate], [first, second])[0]
    assert saved["id"] == first["id"]
    assert normalize_question_cards([candidate], [second, first])[0]["id"] == second["id"]


def test_merged_list_keeps_its_id_on_subsequent_identical_turns():
    first, second = normalize_question_cards([card(), card()])
    merged = card(count=2)
    for item, prior in zip(merged["items"], [first, second]):
        item["id"] = prior["items"][0]["id"]
    saved = normalize_question_cards([merged], [first, second])[0]
    assert saved["id"] not in {first["id"], second["id"]}
    history = [saved, first, second]
    for _ in range(2):
        repeated = normalize_question_cards([merged], history)[0]
        assert repeated == saved
        history = [repeated, *history]


def test_split_list_keeps_all_items_and_stabilizes_new_card_ids():
    original = normalize_question_cards([card(count=2)])[0]
    split = [card(), card()]
    for candidate, item in zip(split, original["items"]):
        candidate["items"][0]["id"] = item["id"]
    saved = normalize_question_cards(split, [original])
    assert len(saved) == 2
    assert saved[0]["id"] == original["id"]
    assert saved[1]["id"] != original["id"]
    assert [c["items"][0]["id"] for c in saved] == [i["id"] for i in original["items"]]
    assert normalize_question_cards(split, [*saved, original]) == saved


def test_duplicate_item_cannot_be_reused_by_another_card():
    original = normalize_question_cards([card()])[0]
    candidate = card()
    candidate["items"][0]["id"] = original["items"][0]["id"]
    saved = normalize_question_cards([candidate, deepcopy(candidate)], [original])
    assert len(saved) == 1
    assert saved[0]["id"] == original["id"]


def test_progress_item_reappears_with_same_id_from_allowed_history():
    original = normalize_question_cards([card("PROGRESS_CHECKLIST", "PENDING", 2)])[0]
    current = card("PROGRESS_CHECKLIST", "CURRENT")
    current["items"][0]["id"] = original["items"][0]["id"]
    latest = normalize_question_cards([current], [original])[0]
    returning = card("PROGRESS_CHECKLIST", "PENDING", 2)
    for candidate, item in zip(returning["items"], reversed(original["items"])):
        candidate["id"] = item["id"]
    saved = normalize_question_cards([returning], [latest, original])[0]
    assert saved["id"] == original["id"]
    assert [i["id"] for i in saved["items"]] == [i["id"] for i in reversed(original["items"])]


def test_example_is_not_promoted_using_its_list_identity():
    # A scripted output checks the deterministic provenance boundary only; it
    # does not demonstrate that a model understood the owner's actual answer.
    example = card()
    example["items"][0]["label"] = "청소"
    previous = normalize_question_cards([example])[0]
    invented = card("PROGRESS_CHECKLIST", "COMPLETED")
    invented["items"][0].update(id=previous["items"][0]["id"], label="청소")
    assert normalize_question_cards([invented, example], [previous])[0]["type"] == "LIST"
    assert len(normalize_question_cards([invented, example], [previous])) == 1


def test_grounded_checklist_preserves_distinct_supplied_statuses():
    # Supplied example: only inventory details are complete; this question asks
    # about the till; an unfinished reviewed item remains NEEDS_DETAIL.
    raw = card("PROGRESS_CHECKLIST", "PENDING", 4)
    statuses = ["COMPLETED", "CURRENT", "NEEDS_DETAIL", "PENDING"]
    labels = ["재고 정리", "시재 점검", "마감 정산", "물품 입고"]
    for item, label, status in zip(raw["items"], labels, statuses):
        item.update(label=label, status=status)
    saved = normalize_question_cards([raw])[0]
    assert [i["status"] for i in saved["items"]] == statuses
    assert [i["label"] for i in saved["items"]] == labels


@pytest.mark.parametrize("field,value", [("title", " "), ("footer", "\x00"), ("title", "가" * 201)])
def test_invalid_card_text(field, value):
    bad = card()
    bad[field] = value
    assert normalize_question_cards([bad]) == []

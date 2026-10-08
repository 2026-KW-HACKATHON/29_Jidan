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


def test_ambiguous_card_history_gets_new_id_without_guessing():
    first = normalize_question_cards([card()])[0]
    second = deepcopy(first)
    second["id"] = str(uuid4())
    candidate = card()
    candidate["items"][0]["id"] = first["items"][0]["id"]
    saved = normalize_question_cards([candidate], [first, second])[0]
    assert saved["id"] not in {first["id"], second["id"]}


@pytest.mark.parametrize("field,value", [("title", " "), ("footer", "\x00"), ("title", "가" * 201)])
def test_invalid_card_text(field, value):
    bad = card()
    bad[field] = value
    assert normalize_question_cards([bad]) == []

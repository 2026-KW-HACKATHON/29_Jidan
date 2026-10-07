"""#158 question guidance (OpenAPI 0.11.0): guidance, guidanceCards and lastAnsweredQuestion.

Every response is checked against openapi.yaml by the `api` client; every test runs on SQLite
and MySQL. The question generator's guidance and examples come from the scripted
FakeAiProvider, so they go through the real parser, app.interview.cards and the task handler.
"""

import logging
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.ai.fake import FakeOutcome
from app.db.models import BackgroundTask, InterviewIntentReview, InterviewSession, InterviewTurn
from app.interview import cards as guidance_cards
from app.interview.cards import (
    EXAMPLE_TITLE,
    InvalidGuidance,
    check_card,
    check_guidance,
    clean_cards,
    example_card,
)
from app.interview.common import GUIDANCE_RESPONSES_ENV
from app.interview.flow import write_question
from app.interview.question_set import INTENTS_V1
from app.tasks import drain
from tests.test_interview_api import INSUFFICIENT, SUFFICIENT, build_ctx, code, count, rows

GUIDANCE = "처음 일하는 근무자도 따라 할 수 있게 알려주세요."


@pytest.fixture
def ctx(api, db_engine, media_root, monkeypatch):
    monkeypatch.setenv(GUIDANCE_RESPONSES_ENV, "true")
    return build_ctx(api, db_engine)


@pytest.fixture
def media_root(tmp_path):
    from app.media.storage import LocalMediaStorage, set_media_storage

    storage = LocalMediaStorage(tmp_path / "media")
    set_media_storage(storage)
    yield storage
    set_media_storage(None)


def question(text="근무조와 시간을 알려주세요.", guidance=GUIDANCE, examples=(("오전조", "시작 시간과 종료 시간"),)):
    return FakeOutcome.ok({"question": text, "guidance": guidance,
                           "examples": [{"label": label, "description": description}
                                        for label, description in examples]})


def ctx_base_question(index: int) -> str:
    return INTENTS_V1[index].base_question


def turn(ctx, question_id) -> InterviewTurn:
    with Session(ctx.engine) as db:
        return db.get(InterviewTurn, question_id)


# --- the question generator's guidance in responses and rows ------------------------------------


def test_generated_guidance_and_examples_are_stored_and_shown(ctx, fake_ai):
    fake_ai.script("generate_question", question(examples=(("오전조", "시작 시간과 종료 시간"), ("오후조", None))))
    sid = ctx.started()
    state = ctx.get(sid)
    [shown] = state["questions"]
    assert shown["guidance"] == GUIDANCE
    [card] = shown["guidanceCards"]
    assert (card["type"], card["title"], card["footer"]) == ("LIST", EXAMPLE_TITLE, None)
    assert [(item["label"], item["description"]) for item in card["items"]] == [
        ("오전조", "시작 시간과 종료 시간"), ("오후조", None)]
    assert state["lastAnsweredQuestion"] is None  # COLLECTING: nothing is being evaluated
    stored = turn(ctx, shown["id"])  # committed with the question, read on a separate connection
    assert stored.guidance == GUIDANCE and stored.guidance_cards == shown["guidanceCards"]
    assert ctx.get(sid) == state  # a re-read of the same revision shows the same cards


def test_example_ids_are_stable_across_the_questions_of_an_intent(ctx, fake_ai):
    fake_ai.script("generate_question", question(examples=(("오전조", None), ("마감", None))))
    sid = ctx.started()
    base = ctx.get(sid)["questions"][0]["guidanceCards"][0]
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    fake_ai.script("generate_question", question("마감 순서는요?", examples=(("마감", "다른 설명"), ("청소", None))))
    probe = ctx.answer_and_run(sid)["questions"][0]["guidanceCards"][0]
    assert probe["id"] == base["id"]
    ids = {item["label"]: item["id"] for item in base["items"]}
    assert {item["label"]: item["id"] for item in probe["items"]}["마감"] == ids["마감"]
    assert len({item["id"] for item in probe["items"]}) == 2
    # Another session has other IDs for the same labels.
    other = example_card(str(uuid.uuid4()), ctx.intents[0], [("마감", None)])
    assert other["id"] != base["id"] and other["items"][0]["id"] != ids["마감"]


def test_no_guidance_is_null_and_no_examples_is_an_empty_list(ctx, fake_ai):
    fake_ai.script("generate_question", question(guidance=None, examples=()))
    [shown] = ctx.get(ctx.started())["questions"]
    assert (shown["guidance"], shown["guidanceCards"]) == (None, [])


def test_blank_guidance_and_repeated_or_blank_examples_are_cleaned(ctx, fake_ai):
    fake_ai.script("generate_question", question(guidance=" \u0007 ", examples=(
        ("오전조", "  "), (" 오전조 ", "중복"), ("\u0000 ", "빈 이름"), ("오후조", None))))
    [shown] = ctx.get(ctx.started())["questions"]
    assert shown["guidance"] is None
    assert [(i["label"], i["description"]) for i in shown["guidanceCards"][0]["items"]] == [
        ("오전조", None), ("오후조", None)]


def test_oversized_guidance_is_left_out_but_the_generated_question_is_kept(ctx, fake_ai):
    examples = [("가" * 201, None), ("긴 설명", "가" * 1001), ("", None)] + [(f"항목 {n}", None) for n in range(9)]
    fake_ai.script("generate_question", question("생성된 질문이에요?", guidance="가" * 2001, examples=examples))
    [shown] = ctx.get(ctx.started())["questions"]
    assert shown["text"] == "생성된 질문이에요?"  # not the fallback: guidance never fails a question
    assert shown["guidance"] is None
    labels = [(item["label"], item["description"]) for item in shown["guidanceCards"][0]["items"]]
    assert labels == [("긴 설명", None)] + [(f"항목 {n}", None) for n in range(7)]  # first 8 kept
    stored = turn(ctx, shown["id"])
    assert (stored.guidance, stored.guidance_cards) == (None, shown["guidanceCards"])


def test_guidance_at_its_limits_is_kept(ctx, fake_ai):
    fake_ai.script("generate_question", question(guidance="가" * 2000, examples=[("나" * 200, "다" * 1000)]))
    [shown] = ctx.get(ctx.started())["questions"]
    assert shown["guidance"] == "가" * 2000
    assert shown["guidanceCards"][0]["items"][0] | {"id": None} == {"id": None, "label": "나" * 200,
                                                                     "description": "다" * 1000}


def test_a_broken_generator_output_still_falls_back(ctx, fake_ai):
    fake_ai.script("generate_question", *[FakeOutcome.ok({"question": "질문?", "guidance": None,
                                                          "examples": [{"label": "x", "description": None}] * 51})] * 3)
    [shown] = ctx.get(ctx.started())["questions"]
    assert shown["text"] == ctx_base_question(0)
    assert (shown["guidance"], shown["guidanceCards"]) == (None, [])


def test_probe_fallback_has_no_guidance(ctx, fake_ai):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(INSUFFICIENT))
    fake_ai.script("generate_question", FakeOutcome.fail("refused"))
    [probe] = ctx.answer_and_run(sid)["questions"]
    assert (probe["kind"], probe["guidance"], probe["guidanceCards"]) == ("PROBE", None, [])
    stored = turn(ctx, probe["id"])
    assert (stored.guidance, stored.guidance_cards) == (None, [])


def test_questions_without_guidance_columns_read_as_null_and_empty(ctx, fake_ai):
    sid = ctx.started()
    question_id = ctx.get(sid)["questions"][0]["id"]
    with Session(ctx.engine) as db:  # a question written before migration 0042: SQL NULL
        db.execute(text("UPDATE interview_turns SET guidance = NULL, guidance_cards = NULL WHERE id = :id"),
                   {"id": question_id})
        db.commit()
        assert db.execute(text("SELECT guidance_cards IS NULL FROM interview_turns WHERE id = :id"),
                          {"id": question_id}).scalar() == 1
    [shown] = ctx.get(sid)["questions"]
    assert (shown["guidance"], shown["guidanceCards"]) == (None, [])


def test_fields_are_left_out_while_the_switch_is_off(ctx, fake_ai, monkeypatch):
    fake_ai.script("generate_question", question())
    sid = ctx.started()
    monkeypatch.setenv(GUIDANCE_RESPONSES_ENV, "false")
    state = ctx.get(sid)
    assert "lastAnsweredQuestion" not in state
    assert not {"guidance", "guidanceCards"} & set(state["questions"][0])
    answered = ctx.answer(sid)
    assert answered.status_code == 202 and "lastAnsweredQuestion" not in answered.json()
    # The guidance is stored either way and shows once the switch is on.
    monkeypatch.setenv(GUIDANCE_RESPONSES_ENV, "true")
    assert ctx.get(sid)["lastAnsweredQuestion"]["guidance"] == GUIDANCE


# --- lastAnsweredQuestion -------------------------------------------------------------------------


def test_answer_response_and_reads_restore_the_question_under_evaluation(ctx, fake_ai):
    fake_ai.script("generate_question", question())
    sid = ctx.started()
    asked = ctx.get(sid)["questions"][0]
    response = ctx.answer(sid)
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert (accepted["phase"], accepted["processing"]["kind"], accepted["questions"]) == (
        "PROCESSING", "EVALUATION", [])
    assert accepted["lastAnsweredQuestion"] == {**asked, "answered": True}
    assert ctx.get(sid)["lastAnsweredQuestion"] == accepted["lastAnsweredQuestion"]


def test_snapshot_survives_a_failed_evaluation_and_its_retry_then_disappears(ctx, fake_ai):
    fake_ai.script("generate_question", question())
    sid = ctx.started()
    asked = ctx.get(sid)["questions"][0]
    fake_ai.script("judge_sufficiency", FakeOutcome.fail("refused"))
    ctx.answer(sid)
    ctx.run(rounds=1)
    failed = ctx.get(sid)
    assert (failed["phase"], failed["processing"]["kind"]) == ("ERROR", "EVALUATION")
    assert failed["lastAnsweredQuestion"] == {**asked, "answered": True}
    fake_ai.script("judge_sufficiency", FakeOutcome.fail("refused"))  # the retry fails as well
    retried = ctx.post(ctx.url(sid, "retries"), {"expectedRevision": failed["revision"]})
    assert retried.status_code == 202, retried.text
    assert retried.json()["lastAnsweredQuestion"] == failed["lastAnsweredQuestion"]
    ctx.run(rounds=1)
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(SUFFICIENT))
    failed_again = ctx.get(sid)
    assert failed_again["phase"] == "ERROR" and failed_again["processing"]["attempt"] == 2
    assert failed_again["lastAnsweredQuestion"] == failed["lastAnsweredQuestion"]
    again = ctx.post(ctx.url(sid, "retries"), {"expectedRevision": failed_again["revision"]})
    assert again.status_code == 202, again.text
    assert again.json()["lastAnsweredQuestion"]["id"] == asked["id"]
    ctx.run()
    moved = ctx.get(sid)  # sufficient: the next intent's question replaced the evaluation
    assert moved["currentIntentId"] == ctx.intents[1] and moved["lastAnsweredQuestion"] is None


@pytest.mark.parametrize("judgement, next_kind", [(INSUFFICIENT, "FOLLOWUP_GENERATION"),
                                                   (SUFFICIENT, "INITIAL_QUESTION")])
def test_snapshot_is_gone_while_the_next_question_or_intent_is_generated(ctx, fake_ai, judgement, next_kind):
    sid = ctx.started()
    fake_ai.script("judge_sufficiency", FakeOutcome.ok(judgement))
    ctx.answer(sid)
    drain(kinds=("EVALUATION",))  # only the evaluation: the next question is still queued
    state = ctx.get(sid)
    assert (state["phase"], state["processing"]["kind"]) == ("PROCESSING", next_kind)
    assert (state["questions"], state["lastAnsweredQuestion"]) == ([], None)


def test_snapshot_stays_while_a_failed_evaluation_waits_for_its_retry(ctx, fake_ai):
    sid = ctx.started()
    asked = ctx.get(sid)["questions"][0]
    fake_ai.script("judge_sufficiency", FakeOutcome.fail("timeout"))
    ctx.answer(sid)
    [run] = drain(kinds=("EVALUATION",))  # first try fails; the task waits for its backoff
    assert run.outcome != "succeeded"
    state = ctx.get(sid)
    assert (state["phase"], state["processing"]["kind"]) == ("PROCESSING", "EVALUATION")
    assert state["lastAnsweredQuestion"]["id"] == asked["id"]


def test_snapshot_keeps_the_cards_as_they_were_when_the_answer_was_accepted(ctx, fake_ai):
    fake_ai.script("generate_question", question())
    sid = ctx.started()
    asked = ctx.get(sid)["questions"][0]
    ctx.answer(sid)
    with Session(ctx.engine) as db:  # questions are never rewritten; prove the read uses the row
        stored = db.get(InterviewTurn, asked["id"])
        assert stored.guidance_cards == asked["guidanceCards"]
    assert ctx.get(sid)["lastAnsweredQuestion"]["guidanceCards"] == asked["guidanceCards"]


def test_no_snapshot_while_generating_the_draft_after_its_failure_or_when_completed(ctx, fake_ai):
    sid = ctx.started()
    state = ctx.finish_all(sid)
    assert (state["phase"], state["lastAnsweredQuestion"]) == ("READY_TO_GENERATE", None)
    ctx.run()  # the summaries
    fake_ai.script("compose_draft", FakeOutcome.fail("refused"))
    generating = ctx.complete(sid)
    assert generating.status_code == 202, generating.text
    assert (generating.json()["phase"], generating.json()["lastAnsweredQuestion"]) == ("GENERATING", None)
    ctx.run()
    failed = ctx.get(sid)
    assert (failed["phase"], failed["processing"]["kind"], failed["lastAnsweredQuestion"]) == (
        "ERROR", "DRAFT_GENERATION", None)
    retried = ctx.post(ctx.url(sid, "retries"), {"expectedRevision": failed["revision"]})
    assert retried.status_code == 202, retried.text
    ctx.run()
    done = ctx.get(sid)
    assert (done["phase"], done["lastAnsweredQuestion"]) == ("COMPLETED", None)


def test_replayed_answer_returns_the_stored_response(ctx, fake_ai, monkeypatch):
    fake_ai.script("generate_question", question())
    sid = ctx.started()
    state = ctx.get(sid)
    key = str(uuid.uuid4())
    first = ctx.answer(sid, question=state["questions"][0]["id"], revision=state["revision"], key=key)
    monkeypatch.setenv(GUIDANCE_RESPONSES_ENV, "false")  # stored responses are not rewritten
    again = ctx.answer(sid, question=state["questions"][0]["id"], revision=state["revision"], key=key)
    assert (again.status_code, again.json()) == (202, first.json())
    assert again.json()["lastAnsweredQuestion"]["id"] == state["questions"][0]["id"]


def test_answering_the_snapshot_again_is_refused_and_changes_nothing(ctx, fake_ai):
    sid = ctx.started()
    accepted = ctx.answer(sid).json()
    tasks = count(ctx, BackgroundTask)
    again = ctx.answer(sid, question=accepted["lastAnsweredQuestion"]["id"], revision=accepted["revision"])
    assert (again.status_code, code(again)) == (409, "QUESTION_ALREADY_ANSWERED")
    assert ctx.get(sid) == accepted  # same revision, same snapshot
    assert count(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER") == 1
    assert count(ctx, BackgroundTask) == tasks


# --- the server rules (app.interview.cards) ---------------------------------------------------------


def _item(label="항목", **extra):
    return {"id": str(uuid.uuid4()), "label": label, **extra}


def _card(kind="LIST", items=None, **extra):
    return {"id": str(uuid.uuid4()), "type": kind, "title": "제목", "items": items or [_item()], **extra}


def _checklist(*statuses):
    return _card("PROGRESS_CHECKLIST", [_item(f"업무 {n}", status=s) for n, s in enumerate(statuses)])


def _photos(target):
    return _card("PHOTO_SUGGESTIONS", attachmentTarget=target)


@pytest.fixture
def ready(ctx, fake_ai):
    """A session whose first intent has a READY review with one section (fake summary)."""
    sid = ctx.started()
    ctx.answer_and_run(sid)
    review = ctx.review(sid, ctx.intents[0]).json()
    assert review["status"] == "READY", review
    return sid, ctx.intents[0], review["content"]["sections"][0]["id"]


def clean(ctx, sid, cards):
    with Session(ctx.engine) as db:
        return clean_cards(db, sid, cards)


def test_valid_cards_of_every_kind_are_kept_in_order_and_match_the_contract(ctx, ready):
    sid, intent, section = ready
    cards = [_card(footer="끝"), _checklist("COMPLETED", "CURRENT", "PENDING", "NEEDS_DETAIL"),
             _photos({"intentId": intent, "target": "WORK_STRUCTURE", "sectionId": None}),
             _photos({"intentId": intent, "target": "SECTION", "sectionId": section}), _photos(None)]
    kept, notes = clean(ctx, sid, cards)
    assert notes == [] and [c["id"] for c in kept] == [c["id"] for c in cards]
    assert kept[0]["items"][0]["description"] is None and kept[1]["footer"] is None
    # Shown through the API (whose responses are checked against ManualGuidanceCard's oneOf).
    question_id = ctx.get(sid)["questions"][0]["id"]
    with Session(ctx.engine) as db:
        db.get(InterviewTurn, question_id).guidance_cards = kept
        db.commit()
    assert ctx.get(sid)["questions"][0]["guidanceCards"] == kept


@pytest.mark.parametrize("card, reason", [
    ({**_card(), "type": "CHOICE"}, "type"),
    ({**_card(), "extra": 1}, "unknown fields"),
    ({k: v for k, v in _card().items() if k != "title"}, "missing fields"),
    ({**_card(), "items": []}, "1..50"),
    (_card(items=[_item() for _ in range(51)]), "1..50"),
    (_card(items=[_item(status="CURRENT")]), "unknown fields"),  # LIST items have no status
    (_card("PROGRESS_CHECKLIST", [_item()]), "missing fields"),  # progress items need one
    (_checklist("DONE"), "status"),
    (_checklist("CURRENT", "CURRENT"), "more than one CURRENT"),
    ({**_card(), "id": "not-a-uuid"}, "not a UUID"),
    ({**_card(), "title": "   "}, "blank"),
    ({**_card(), "title": "가" * 201}, "longer than 200"),
    (_card(items=[{**_item(), "label": 3}]), "not a string"),
    (_card(items=[_item(description="\t\n")]), "blank"),
    (_card(footer="가" * 1001), "longer than 1000"),
    ({**_card(), "type": "PHOTO_SUGGESTIONS"}, "missing fields"),  # attachmentTarget is required
    ("not a card", "type"),
])
def test_a_card_breaking_its_rules_is_left_out_alone(ctx, card, reason):
    good = _card()
    kept, [note] = clean(ctx, str(uuid.uuid4()), [card, good])
    assert kept == check_cards_of([good]) and reason in note and "left out" in note
    with Session(ctx.engine) as db, pytest.raises(InvalidGuidance, match=reason):
        check_card(db, str(uuid.uuid4()), card)


def check_cards_of(cards):
    return [{**card, "footer": card.get("footer"),
             "items": [{**item, "description": item.get("description")} for item in card["items"]]}
            for card in cards]


def test_later_cards_lose_on_duplicate_ids_a_second_checklist_and_a_sixth_card(ctx):
    sid = str(uuid.uuid4())
    first, checklist = _card(), _checklist("CURRENT")
    cards = [first, {**_card(), "id": first["id"]}, checklist, _checklist("PENDING"),
             _card(), _card(), _card(), _card()]
    kept, notes = clean(ctx, sid, cards)
    assert [c["id"] for c in kept] == [first["id"], checklist["id"]] + [c["id"] for c in cards[4:7]]
    assert [n.split(": ", 1)[1] for n in notes] == [
        "duplicate card ID (card left out)", "more than one PROGRESS_CHECKLIST (card left out)",
        "more than 5 cards (card left out)"]
    assert clean(ctx, sid, "not a list") == ([], ["guidanceCards: not a list"])
    assert clean(ctx, sid, []) == ([], [])


def test_the_same_item_id_may_appear_in_two_cards(ctx):
    item = _item()
    kept, notes = clean(ctx, str(uuid.uuid4()), [_card(items=[item]), _card(items=[item])])
    assert len(kept) == 2 and notes == []
    with pytest.raises(InvalidGuidance, match="duplicate item IDs"), Session(ctx.engine) as db:
        check_card(db, "s", _card(items=[item, {**_item("다른"), "id": item["id"]}]))


def test_a_photo_target_that_is_not_a_ready_review_of_this_session_becomes_null(ctx, ready, fake_ai):
    sid, intent, section = ready
    def nulled(session_id, target, reason):
        card = _photos(target)
        kept, [note] = clean(ctx, session_id, [card])
        assert kept[0]["attachmentTarget"] is None and kept[0]["id"] == card["id"]
        assert reason in note and "target set to null" in note
    nulled(sid, {"intentId": ctx.intents[1], "target": "WORK_STRUCTURE", "sectionId": None}, "no READY review")
    nulled(sid, {"intentId": intent, "target": "WORK_STRUCTURE", "sectionId": section}, "must be null")
    nulled(sid, {"intentId": intent, "target": "SECTION", "sectionId": str(uuid.uuid4())}, "not a section")
    nulled(sid, {"intentId": intent, "target": "SECTION", "sectionId": None}, "not a UUID")
    nulled(sid, {"intentId": intent, "target": "SHIFT", "sectionId": None}, "target")
    nulled(sid, {"intentId": intent, "target": "SECTION"}, "missing fields")
    nulled(sid, {"intentId": "x", "target": "WORK_STRUCTURE", "sectionId": None}, "not a UUID")
    # Another session (another store) cannot be named, even with a READY review of the same intent.
    nulled(str(uuid.uuid4()), {"intentId": intent, "target": "WORK_STRUCTURE", "sectionId": None},
           "no READY review")


def test_a_review_that_is_not_ready_is_not_a_target(ctx, ready, fake_ai):
    sid, intent, _section = ready
    with Session(ctx.engine) as db:
        review = db.scalars(select(InterviewIntentReview).where(
            InterviewIntentReview.session_id == sid, InterviewIntentReview.intent_id == intent)).one()
        review.status = "ERROR"
        review.processing_kind, review.processing_task_id, review.processing_attempt = (
            "CORRECTION", str(uuid.uuid4()), 1)
        review.error_code = "AI_PROCESSING_FAILED"
        db.commit()
    kept, [note] = clean(ctx, sid, [_photos({"intentId": intent, "target": "WORK_STRUCTURE", "sectionId": None})])
    assert kept[0]["attachmentTarget"] is None and "no READY review" in note


@pytest.mark.parametrize("value, expected", [(None, None), ("  설명  ", "설명"), ("가" * 2000, "가" * 2000)])
def test_guidance_text(value, expected):
    assert check_guidance(value) == expected


@pytest.mark.parametrize("value", ["", "  \n", "가" * 2001, 3])
def test_guidance_text_refused(value):
    with pytest.raises(InvalidGuidance):
        check_guidance(value)


def test_bad_guidance_parts_are_left_out_and_the_rest_is_written(ctx, fake_ai, caplog):
    sid = ctx.started()
    ctx.answer(sid)
    good = _card()
    with Session(ctx.engine) as db:  # write a further question directly, as a task would
        session = db.execute(select(InterviewSession).where(InterviewSession.id == sid).with_for_update()).scalar_one()
        payload = {"intentId": ctx.intents[1], "batchId": None, "depth": 0}
        before = session.revision
        with caplog.at_level(logging.WARNING, logger="app.interview.flow"):
            written = write_question(db, session, payload, "질문?", "test", guidance="  ",
                                     cards=[_checklist("CURRENT", "CURRENT"), good])
            kept = write_question(db, session, {**payload, "intentId": ctx.intents[2]}, "질문 2?", "test",
                                  guidance=GUIDANCE, cards=[good])
        assert (written.guidance, written.guidance_cards, session.revision) == (
            None, check_cards_of([good]), before + 2)
        assert (kept.guidance, kept.guidance_cards) == (GUIDANCE, check_cards_of([good]))
        db.rollback()  # only what was written matters here, not two more open questions
    assert "more than one CURRENT" in caplog.text and "guidance left out" in caplog.text
    assert "질문?" not in caplog.text and "업무 0" not in caplog.text  # no content in the logs


def test_example_card_limits(ctx):
    assert example_card("s", "i", []) is None
    many = example_card("s", "i", [(f"항목 {n}", None) for n in range(60)])
    assert len(many["items"]) == guidance_cards.MAX_ITEMS
    with pytest.raises(InvalidGuidance):
        example_card("s", "i", [("가" * 201, None)])


@pytest.mark.parametrize("column, value", [("guidance", GUIDANCE), ("guidance_cards", [])])
def test_database_keeps_guidance_on_questions_only(ctx, fake_ai, column, value):
    sid = ctx.started()
    ctx.answer(sid)
    [answer] = rows(ctx, InterviewTurn, InterviewTurn.turn_kind == "ANSWER")
    with Session(ctx.engine) as db:
        setattr(db.get(InterviewTurn, answer.id), column, value)
        # SQLite: IntegrityError; MySQL: OperationalError 3819 (CHECK violated).
        with pytest.raises((IntegrityError, OperationalError)):
            db.commit()
        db.rollback()
        correction = InterviewTurn(session_id=sid, turn_no=99, speaker="OWNER", turn_kind="CORRECTION",
                                   intent_id=ctx.intents[0], input_method="TEXT", content="정정이에요.",
                                   **{column: value})
        db.add(correction)
        with pytest.raises((IntegrityError, OperationalError)):
            db.commit()
    assert rows(ctx, InterviewTurn, InterviewTurn.id == answer.id)[0].guidance is None

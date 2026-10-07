"""Evidence retrieval (app.ai.retrieval): stable chunks, Korean-friendly BM25, budget, ties."""

import pytest

from app.ai.contracts import EvidenceChunk
from app.ai.retrieval import (
    Utterance,
    bm25_scores,
    chunk_utterances,
    retrieve,
    split_sentences,
    terms,
)


def chunk(cid, text, intent="COMMON_TASKS", question=None):
    return EvidenceChunk(id=cid, intent_key=intent, text=text, question=question)


def test_sentences_split_on_endings_and_newlines_and_merge_tiny_fragments():
    assert split_sentences("포스기를 켜요. 금고를 열어요!\n그다음 청소해요") == [
        "포스기를 켜요.", "금고를 열어요!", "그다음 청소해요"]
    assert split_sentences("네. 오픈은 9시예요.") == ["네. 오픈은 9시예요."]
    assert split_sentences("오픈은 9시예요. 네.") == ["오픈은 9시예요. 네."]
    assert split_sentences("  \n\t ") == [] and split_sentences("") == []


def test_chunk_ids_are_turn_id_and_sentence_number_and_stable():
    utterances = [Utterance("t1", "WORK_STRUCTURE", "오픈조가 있어요. 마감조도 있어요.", "근무조가 있나요?"),
                  Utterance("t2", "COMMON_TASKS", "손님께 인사해요.")]
    first, again = chunk_utterances(utterances), chunk_utterances(utterances)
    assert first == again
    assert [(c.id, c.intent_key, c.question, c.text) for c in first] == [
        ("t1#1", "WORK_STRUCTURE", "근무조가 있나요?", "오픈조가 있어요."),
        ("t1#2", "WORK_STRUCTURE", "근무조가 있나요?", "마감조도 있어요."),
        ("t2#1", "COMMON_TASKS", None, "손님께 인사해요."),
    ]
    assert chunk_utterances([]) == [] and chunk_utterances([Utterance("t3", "X", " \n ")]) == []


def test_korean_particle_variants_still_match():
    # 포스기를 / 포스기는 / 포스기에서: the stem's bigrams match whatever particle follows.
    docs = ["포스기는 마감 때 정산해요.", "화분에 물을 줘요.", "포스기에서 영수증을 뽑아요."]
    scores = bm25_scores(docs, "포스기를 어떻게 다루나요")
    assert scores[0] > 0 and scores[2] > 0 and scores[1] == 0
    assert "포스" in terms("포스기를") and terms("A") == ["a"]


def test_empty_inputs():
    assert retrieve([], "아무거나") == ()
    assert bm25_scores([], "쿼리") == []
    chunks = [chunk("a#1", "인사해요."), chunk("b#1", "청소해요.", intent="RULES")]
    assert bm25_scores(["인사해요."], "") == [0.0]
    # Empty query: nothing scores, so only the required intent's chunks come back.
    assert [c.id for c in retrieve(chunks, "", required_intent="COMMON_TASKS")] == ["a#1"]
    assert retrieve(chunks, "", required_intent=None) == ()


def test_required_intent_is_always_included_and_others_are_ranked_top_k():
    chunks = [
        chunk("r#1", "전혀 무관한 내용이에요.", intent="TARGET"),
        chunk("o#1", "커피 머신 청소는 매일 해요.", intent="OTHER"),
        chunk("o#2", "커피 머신은 마감 때 끄고 청소해요.", intent="OTHER"),
        chunk("o#3", "화분에 물을 줘요.", intent="OTHER"),
    ]
    result = retrieve(chunks, "커피 머신 청소", required_intent="TARGET", top_k=1)
    ids = [c.id for c in result]
    assert "r#1" in ids and "o#3" not in ids and len(ids) == 2
    # Output keeps the original (chronological) order.
    assert ids == sorted(ids, key=[c.id for c in chunks].index)


def test_ties_keep_the_original_order():
    chunks = [chunk(f"t{i}#1", "커피를 내려요.", intent="OTHER") for i in range(5)]
    assert [c.id for c in retrieve(chunks, "커피", top_k=3)] == ["t0#1", "t1#1", "t2#1"]
    reordered = list(reversed(chunks))
    assert [c.id for c in retrieve(reordered, "커피", top_k=3)] == ["t4#1", "t3#1", "t2#1"]


def test_budget_is_a_hard_cap_even_for_the_required_intent():
    chunks = [chunk(f"r{i}#1", "가" * 40, intent="TARGET") for i in range(5)]
    chunks.append(chunk("o#1", "가" * 10, intent="OTHER"))
    result = retrieve(chunks, "가가", required_intent="TARGET", budget_chars=100)
    assert sum(len(c.text) for c in result) <= 100
    assert [c.id for c in result] == ["r0#1", "r1#1", "o#1"]  # a smaller lower-ranked chunk still fits
    assert retrieve(chunks, "가가", required_intent="TARGET", budget_chars=0) == ()
    with pytest.raises(ValueError):
        retrieve(chunks, "가", budget_chars=-1)


def test_question_is_kept_on_the_first_selected_chunk_of_a_turn_only():
    chunks = chunk_utterances([Utterance("t1", "TARGET", "오픈은 9시예요. 마감은 22시예요.", "근무 시간이 어떻게 되나요?")])
    result = retrieve(chunks, "근무", required_intent="TARGET")
    assert [(c.id, c.question) for c in result] == [("t1#1", "근무 시간이 어떻게 되나요?"), ("t1#2", None)]


def test_retrieval_is_deterministic():
    utterances = [Utterance(f"t{i}", "A" if i % 2 else "B", f"{i}번 업무는 커피와 청소예요. 마무리해요.") for i in range(30)]
    chunks = chunk_utterances(utterances)
    runs = {retrieve(chunks, "커피 청소", required_intent="A", top_k=5, budget_chars=500) for _ in range(5)}
    assert len(runs) == 1

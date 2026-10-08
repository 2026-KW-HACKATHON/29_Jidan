"""Evidence retrieval for grounded manual writing (RAG without a vector store).

Manual text may only state what the owner actually said (docs/ai-foundation.md, prompts
policy). The writing operations therefore receive the owner's own words as *evidence chunks*
and must cite them per step (`evidence_ids`); `app.ai.validation.ground_structure` checks the
citations.

    utterances (owner turns of ONE session)  ->  chunk_utterances  ->  EvidenceChunk[]
    EvidenceChunk[] + query + required intent ->  retrieve          ->  bounded, ordered subset

Design (MVP, MySQL only, no embeddings):
* Only one session's turns are ever passed in, so another store's data cannot be retrieved.
* Chunks are sentences of an owner turn. IDs are `<turnId>#<n>` (n from 1), so the same stored
  turns always give the same IDs, and a retry rebuilds exactly the same evidence.
* Ranking is BM25 over character bigrams of each word. Korean attaches particles to words
  (포스기를 / 포스기는 / 포스기에서); bigrams of the stem still match, without a morphological
  analyser. Pure Python and deterministic: ties keep the original (chronological) order.
* Chunks of the required intent (the one being written) are always included first; other
  chunks are added by score (score > 0 only) up to `top_k`. Everything is bounded by
  `budget_chars` (characters of text + question, a conservative token proxy for Korean): if the
  required chunks alone exceed it, the best-ranked of them are kept and the rest dropped.
* The result is in original order, with each turn's question kept on its first chunk only.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.ai.contracts import MAX_EVIDENCE, EvidenceChunk, clean_text

DEFAULT_TOP_K = 20
DEFAULT_BUDGET_CHARS = 12000
BM25_K1 = 1.2
BM25_B = 0.75
MIN_SENTENCE_CHARS = 4  # shorter fragments ("네.", "음") are merged into the previous sentence

_SENTENCE_END = re.compile(r"(?<=[.!?。！？…])\s+|\n+")
_WORD = re.compile(r"[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ]+")


@dataclass(frozen=True)
class Utterance:
    """One owner turn (an answer or a correction) of the session, in turn order."""

    turn_id: str
    intent_key: str
    text: str
    question: str | None = None


def split_sentences(text: str) -> list[str]:
    """Sentences of `text`; very short fragments join the sentence before them."""
    sentences: list[str] = []
    for part in _SENTENCE_END.split(clean_text(text)):
        part = part.strip()
        if not part:
            continue
        if sentences and len(part) < MIN_SENTENCE_CHARS or sentences and len(sentences[-1]) < MIN_SENTENCE_CHARS:
            sentences[-1] = f"{sentences[-1]} {part}"
        else:
            sentences.append(part)
    return sentences


def chunk_utterances(utterances: Iterable[Utterance]) -> list[EvidenceChunk]:
    chunks: list[EvidenceChunk] = []
    for utterance in utterances:
        question = clean_text(utterance.question or "")[:2000] or None
        for index, sentence in enumerate(split_sentences(utterance.text), 1):
            chunks.append(EvidenceChunk(
                id=f"{utterance.turn_id}#{index}", intent_key=utterance.intent_key,
                question=question, text=sentence[:10000],
            ))
    return chunks


def terms(text: str) -> list[str]:
    """Character bigrams of every word (a one-character word is its own term), lowercased."""
    result: list[str] = []
    for word in _WORD.findall(text.lower()):
        if len(word) == 1:
            result.append(word)
        else:
            result.extend(word[i:i + 2] for i in range(len(word) - 1))
    return result


def bm25_scores(documents: Sequence[str], query: str) -> list[float]:
    """Okapi BM25 of every document for `query` (0.0 when nothing matches)."""
    docs = [Counter(terms(doc)) for doc in documents]
    query_terms = Counter(terms(query))
    if not docs or not query_terms:
        return [0.0] * len(docs)
    lengths = [sum(doc.values()) for doc in docs]
    average = (sum(lengths) / len(docs)) or 1.0
    frequency = Counter(term for doc in docs for term in doc)
    total = len(docs)
    scores = []
    for doc, length in zip(docs, lengths, strict=True):
        score = 0.0
        for term, weight in query_terms.items():
            tf = doc.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (total - frequency[term] + 0.5) / (frequency[term] + 0.5))
            score += weight * idf * tf * (BM25_K1 + 1) / (
                tf + BM25_K1 * (1 - BM25_B + BM25_B * length / average))
        scores.append(score)
    return scores


def _cost(chunks: Sequence[EvidenceChunk], chosen: Sequence[int]) -> int:
    # The final output keeps only the earliest selected chunk's question per turn. Ranking
    # may select a later chunk first, so recompute from chronological order on each addition.
    seen: set[str] = set()
    total = 0
    for index in sorted(chosen):
        chunk = chunks[index]
        turn = chunk.id.rsplit("#", 1)[0]
        total += len(chunk.text)
        if turn not in seen:
            total += len(chunk.question or "")
        seen.add(turn)
    return total


def retrieve(chunks: Sequence[EvidenceChunk], query: str, *, required_intent: str | None = None,
             top_k: int = DEFAULT_TOP_K, budget_chars: int = DEFAULT_BUDGET_CHARS,
             ) -> tuple[EvidenceChunk, ...]:
    """The evidence for one writing request. See the module docstring for the rules."""
    if top_k < 0 or budget_chars < 0:
        raise ValueError("top_k and budget_chars must not be negative")
    scores = bm25_scores([f"{c.question or ''} {c.text}" for c in chunks], query)

    def rank(index: int) -> tuple[float, int]:
        return (-scores[index], index)

    required = sorted((i for i, c in enumerate(chunks) if c.intent_key == required_intent), key=rank)
    others = sorted((i for i, c in enumerate(chunks)
                     if c.intent_key != required_intent and scores[i] > 0), key=rank)[:top_k]
    chosen: list[int] = []
    for index in [*required, *others]:
        if len(chosen) >= MAX_EVIDENCE:
            break
        if _cost(chunks, [*chosen, index]) > budget_chars:
            continue  # does not fit; a smaller, lower-ranked chunk still may
        chosen.append(index)

    result: list[EvidenceChunk] = []
    seen_turns: set[str] = set()
    for index in sorted(chosen):
        chunk = chunks[index]
        turn = chunk.id.rsplit("#", 1)[0]
        if turn in seen_turns and chunk.question is not None:
            chunk = chunk.model_copy(update={"question": None})
        seen_turns.add(turn)
        result.append(chunk)
    return tuple(result)

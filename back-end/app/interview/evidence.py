"""Evidence (the owner's own words) for the interview's grounded writing tasks.

The pool is always the owner turns (ANSWER, CORRECTION) of ONE interview session, i.e. one
store: another store's data is never read. `app.ai.retrieval` splits them into stable chunks
(`<turnId>#<n>`) and picks the relevant ones; the model must cite them per step
(`app.ai.validation.ground_structure`).

Retries see the same evidence:
* summary (REVIEW_UNDERSTANDING): retrieved when the task is enqueued and stored in its payload;
* draft (DRAFT_GENERATION): retrieved at completion and frozen in the generation snapshot;
* review correction (REVIEW_CORRECTION): rebuilt in `execute` like the rest of that request,
  from turns up to the correction turn (`turn_no` bound) and the review content, which do not
  change while the correction is PROCESSING — so every attempt rebuilds identical evidence.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import EvidenceChunk, StructureSnapshot
from app.ai.retrieval import Utterance, chunk_utterances, retrieve
from app.db.models import InterviewIntent, InterviewTurn
from app.interview.common import session_intents

SUMMARY_TOP_K, SUMMARY_BUDGET = 12, 12000
CORRECTION_TOP_K, CORRECTION_BUDGET = 12, 12000
DRAFT_TOP_K, DRAFT_BUDGET = 60, 16000


def session_utterances(db: Session, session_id: str, *, through_turn_no: int | None = None) -> list[Utterance]:
    """Owner answers (with the question they answered) and corrections, in turn order."""
    keys = {intent.id: intent.intent_key for _progress, intent in session_intents(db, session_id)}
    query = select(InterviewTurn).where(InterviewTurn.session_id == session_id).order_by(InterviewTurn.turn_no)
    if through_turn_no is not None:
        query = query.where(InterviewTurn.turn_no <= through_turn_no)
    turns = list(db.scalars(query))
    questions = {turn.id: turn.content for turn in turns if turn.turn_kind == "QUESTION"}
    return [
        Utterance(turn_id=turn.id, intent_key=keys.get(turn.intent_id, "UNKNOWN"), text=turn.content,
                  question=questions.get(turn.reply_to_question_turn_id) if turn.turn_kind == "ANSWER" else None)
        for turn in turns
        if turn.speaker == "OWNER" and turn.turn_kind in ("ANSWER", "CORRECTION")
    ]


def _text(parts: Iterable[str | None]) -> str:
    return "\n".join(part for part in parts if part)


def summary_evidence(db: Session, session_id: str, intent: InterviewIntent) -> tuple[EvidenceChunk, ...]:
    """The intent's own answers (always) plus related answers of the other intents."""
    query = _text((intent.intent_key, intent.base_question, intent.coverage_criteria))
    return retrieve(chunk_utterances(session_utterances(db, session_id)), query,
                    required_intent=intent.intent_key, top_k=SUMMARY_TOP_K, budget_chars=SUMMARY_BUDGET)


def correction_evidence(db: Session, session_id: str, intent_key: str, correction: InterviewTurn,
                        current: StructureSnapshot) -> tuple[EvidenceChunk, ...]:
    """The intent's answers and corrections through this correction, plus related answers."""
    query = _text((correction.content, *(section.title for section in current.sections),
                   *(shift.name for shift in current.shifts)))
    pool = chunk_utterances(session_utterances(db, session_id, through_turn_no=correction.turn_no))
    return retrieve(pool, query, required_intent=intent_key, top_k=CORRECTION_TOP_K,
                    budget_chars=CORRECTION_BUDGET)


def draft_evidence(db: Session, session_id: str, reviews: Iterable[dict]) -> tuple[EvidenceChunk, ...]:
    """Answers related to the reviewed content (for anything the draft adds beyond the reviews)."""
    parts: list[str | None] = []
    for content in reviews:
        parts.append(content.get("summary"))
        parts.extend(section.get("title") for section in content.get("sections", []))
        parts.extend(item.get("description") for item in content.get("missingInformation", []))
    return retrieve(chunk_utterances(session_utterances(db, session_id)), _text(parts),
                    top_k=DRAFT_TOP_K, budget_chars=DRAFT_BUDGET)

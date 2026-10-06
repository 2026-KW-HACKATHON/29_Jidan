"""Interview completion: freeze every review into the draft's generation snapshot and compose
the first draft (DRAFT_GENERATION). Later draft edits and corrections belong to #118.

Completion (request; store_manuals, session and version locked in that order): READY_TO_GENERATE, session revision and every
review's (intentId, revision) as listed by GET reviews, every review READY, cross-review shift
references intact. Then, atomically: `generation_input_snapshot`, snapshot photo references,
draft RUNNING, session GENERATING and the task. Review changes after that are refused because
the session is GENERATING (first accepted wins).

Apply (task): the composed structure, with the reviews' photos re-attached by section ID
(structure photos at version level), is validated and written by #118's
`prepare_content`/`write_initial_content`: the same shift and section IDs, an OPEN issue with the
same ID per missing-information entry (intent of origin when known), revision kept at 1. Every
NEEDS_DETAIL intent adds an issue without a target. Then the draft is
READY and the session COMPLETED. It is never published automatically.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import get_ai_provider
from app.ai.contracts import DraftComposition, DraftRequest, ReviewForDraft, StoreContext
from app.db import new_uuid, session_scope, utcnow
from app.db.models import (
    InterviewIntent,
    InterviewSession,
    InterviewSessionIntent,
    ManualReviewIssue,
    ManualVersion,
    Store,
    StoreManual,
)
from app.errors import ApiError, ErrorCode
from app.interview.common import (
    REVIEW_NOT_READY_MESSAGE,
    lock_review,
    lock_session_row,
    lock_version,
    not_found,
    phase_of,
    revision_conflict,
    session_intents,
    state_conflict,
)
from app.interview.content import (
    content_from_structure,
    photo_ids,
    referenced_shift_ids,
    shift_ids,
    snapshot_from_content,
)
from app.interview.flow import intent_label, store_context
from app.manual_editing import ContentIn, prepare_content, write_initial_content
from app.media.references import add_snapshot_refs
from app.tasks import TaskContext, enqueue

REFERENCE_CONFLICT_MESSAGE = "다른 요약이 참조하는 근무조가 없어요. 관련 요약을 정정한 뒤 다시 시도해 주세요."
INCOMPLETE_MESSAGE = "아직 진행하지 않은 질문이 있어요."
NEEDS_DETAIL_DESCRIPTION = "'{label}' 항목은 인터뷰에서 충분히 확인하지 못했어요."


def start_generation(db: Session, store: Store, manual: StoreManual, session: InterviewSession,
                     version: ManualVersion, expected_revision: int,
                     review_revisions: list[tuple[str, int]]) -> None:
    """Validate and accept a completion; the caller holds the manual, session and version locks."""
    phase = phase_of(session)
    if phase in ("COLLECTING", "PROCESSING"):
        raise ApiError(409, ErrorCode.INTERVIEW_INCOMPLETE, INCOMPLETE_MESSAGE)
    if phase != "READY_TO_GENERATE" or version.status != "DRAFT" or version.generation_status != "NOT_STARTED":
        raise state_conflict()
    progress = session_intents(db, session.id)
    known = {intent.id for _p, intent in progress}
    if any(intent_id not in known for intent_id, _r in review_revisions):
        raise not_found()
    # One unique (session_id, intent_id) lock per intent rather than FOR UPDATE on session_id:
    # a range lock's scope follows MySQL's plan and its next-key lock reaches the next
    # session's reviews (app.jobs.state.lock_each). The session lock serializes review writers.
    locked_reviews = (lock_review(db, session.id, intent_id) for intent_id in sorted(known))
    reviews = {review.intent_id: review for review in locked_reviews if review is not None}
    if {intent_id for intent_id, _r in review_revisions} != set(reviews):
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[{
            "field": "reviewRevisions", "code": "INVALID_FORMAT",
            "message": "완료한 모든 인텐트의 검토를 한 번씩 포함해 주세요.",
        }])
    if session.revision != expected_revision or any(
        reviews[intent_id].revision != revision for intent_id, revision in review_revisions
    ):
        raise revision_conflict()
    if any(review.status != "READY" for review in reviews.values()):
        raise ApiError(409, ErrorCode.REVIEW_NOT_READY, REVIEW_NOT_READY_MESSAGE)

    ordered = [(p, intent, reviews[intent.id]) for p, intent in progress]
    _check_references([review.ready_content for _p, _i, review in ordered])
    snapshot = {
        "sessionRevision": session.revision,
        "reviews": [{"intentId": intent.id, "intentKey": intent.intent_key, "stage": intent.stage,
                     "revision": review.revision, "coverage": p.coverage_status,
                     "content": review.ready_content} for p, intent, review in ordered],
    }
    snapshot["store"] = store_context(store).model_dump(mode="json")
    draft_request(snapshot)  # the frozen input must make a valid request (same check as execute)
    version.generation_input_snapshot = snapshot
    version.generation_status = "RUNNING"
    add_snapshot_refs(db, "DRAFT_GENERATION", version.id,
                      [m for _p, _i, review in ordered for m in photo_ids(review.ready_content)])
    session.revision += 1
    task_id = enqueue(db, "DRAFT_GENERATION", session.id,
                      {"manualId": manual.id, "versionId": version.id},
                      input_revision=session.revision, attempt=1)
    session.processing_kind, session.processing_task_id, session.processing_attempt = (
        "DRAFT_GENERATION", task_id, 1)
    db.flush()


def _check_references(contents: list[dict[str, Any]]) -> None:
    """Shift references across reviews resolve, and no ID is defined twice."""
    defined: list[str] = []
    for content in contents:
        defined.extend(s["id"] for s in content.get("shifts", []))
        defined.extend(s["id"] for s in content.get("sections", []))
    all_shifts = set().union(*(shift_ids(c) for c in contents))
    dangling = set().union(*(referenced_shift_ids(c) for c in contents)) - all_shifts
    if dangling or len(defined) != len(set(defined)):
        raise ApiError(409, ErrorCode.MANUAL_REFERENCE_CONFLICT, REFERENCE_CONFLICT_MESSAGE)


# --- DRAFT_GENERATION task -------------------------------------------------------------------


def draft_request(snapshot: dict[str, Any]) -> DraftRequest:
    """The composition request built from the frozen generation snapshot. The snapshot lives on
    the version row (it can exceed the 1 MB task payload limit); retries rebuild the same request."""
    return DraftRequest(
        reviews=tuple(
            ReviewForDraft(intent_key=review["intentKey"], stage=review["stage"],
                           summary=review["content"]["summary"], needs_detail=review["coverage"] == "NEEDS_DETAIL",
                           structure=snapshot_from_content(review["content"]))
            for review in snapshot["reviews"]
        ),
        store=StoreContext.model_validate(snapshot["store"]) if snapshot.get("store") else None,
    )


def execute(ctx: TaskContext) -> DraftComposition:
    with session_scope() as db:  # a short read; the snapshot is immutable while generation runs
        snapshot = db.get(ManualVersion, ctx.payload["versionId"]).generation_input_snapshot
    return get_ai_provider().compose_draft(draft_request(snapshot))


def _waiting(db: Session, ctx: TaskContext, *, attempt: bool):
    """Lock store_manuals first (the global order, shared with #118), then session and version."""
    manual = db.execute(
        select(StoreManual).where(StoreManual.id == ctx.payload["manualId"])
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    ctx.ensure(manual is not None)
    session = lock_session_row(db, ctx.subject_id)
    ctx.ensure(session is not None and session.processing_task_id == ctx.task_id
               and session.processing_kind == "DRAFT_GENERATION" and session.status == "IN_PROGRESS"
               and session.revision == ctx.input_revision
               and (not attempt or session.processing_attempt == ctx.attempt))
    version = lock_version(db, ctx.payload["versionId"])
    ctx.ensure(version.id == session.manual_version_id and version.manual_id == manual.id
               and version.status == "DRAFT" and version.generation_status == "RUNNING")
    return manual, session, version


def draft_content(structure, snapshot: dict) -> tuple[dict, dict[str, str]]:
    """API ManualContent of the composed structure with the reviews' photos re-joined by section
    ID, and the intent each missing-information entry came from (issue_intents)."""
    origin: dict[str, str] = {}
    section_photos: dict[str, list[dict]] = {}
    structure_photos: dict[str, dict] = {}
    for review in snapshot["reviews"]:
        content = review["content"]
        for key in ("shifts", "sections", "missingInformation"):
            origin.update({item["id"]: review["intentId"] for item in content.get(key, [])})
        for section in content.get("sections", []):
            section_photos[section["id"]] = section.get("photos", [])
        for photo in content.get("structurePhotos", []):
            structure_photos.setdefault(photo["mediaId"], photo)
    body = content_from_structure("", "-", structure, needs_detail=False)
    for section in body["sections"]:
        section["photos"] = list(section_photos.get(section["id"], []))
    content = {"shifts": body["shifts"], "sections": body["sections"],
               "structurePhotos": list(structure_photos.values()),
               "missingInformation": body["missingInformation"]}
    intents = {}
    for item in structure.missing_information:
        source = origin.get(item.id) or (origin.get(item.target_id) if item.target_id else None)
        if source:
            intents[item.id] = source
    return content, intents


def apply(db: Session, ctx: TaskContext, composition: DraftComposition) -> None:
    manual, session, version = _waiting(db, ctx, attempt=True)
    snapshot = version.generation_input_snapshot
    content, issue_intents = draft_content(composition.structure, snapshot)
    # The draft module's own validation and writer (#118): same rows and gap issues an edit makes.
    # ContentInvalid means the composition broke the ManualContent rules; raising rolls the apply
    # back and the runner retries the call, then fails the session like any AI failure.
    prepared = prepare_content(db, version, manual.store_id, ContentIn.model_validate(content))
    now = utcnow()
    write_initial_content(db, version, prepared, issue_intents=issue_intents, now=now)
    intents = {intent.id: intent for _p, intent in session_intents(db, session.id)}
    for review in snapshot["reviews"]:
        if review["coverage"] == "NEEDS_DETAIL":  # a review item without a target (검수중)
            progress = db.get(InterviewSessionIntent, (session.id, review["intentId"]))
            db.add(ManualReviewIssue(
                id=new_uuid(), version_id=version.id, intent_id=review["intentId"], created_at=now,
                description=_needs_detail_text(intents[review["intentId"]], progress),
            ))
    # Invariant: the first content and READY land in one transaction (tests/draft_revision.py).
    version.generation_status = "READY"  # revision stays 1: a fresh draft (write_initial_content)
    session.status, session.completed_at, session.current_intent_id = "COMPLETED", now, None
    session.processing_kind = session.processing_task_id = session.processing_attempt = None
    session.revision += 1
    db.flush()


def _needs_detail_text(intent: InterviewIntent, progress: InterviewSessionIntent) -> str:
    text = NEEDS_DETAIL_DESCRIPTION.format(label=intent_label(intent))
    if progress.coverage_note:
        aspects = ", ".join(line for line in progress.coverage_note.splitlines() if line)
        text += f" 더 확인할 내용: {aspects}"
    return text[:1000]


def fail(db: Session, ctx: TaskContext, _error: Exception) -> None:
    _manual, session, version = _waiting(db, ctx, attempt=False)
    version.generation_status = "ERROR"
    session.status, session.error_code = "ERROR", "AI_PROCESSING_FAILED"
    session.revision += 1

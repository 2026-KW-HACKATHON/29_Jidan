"""Voice/text corrections of a generated draft (#118, OpenAPI 0.10.0).

    POST /api/stores/{storeId}/manual/draft/corrections                     accept (202)
    GET  /api/stores/{storeId}/manual/draft/corrections/{correctionId}      status
    POST /api/stores/{storeId}/manual/draft/corrections/{correctionId}/retries  retry a failure (202)

Acceptance and retry take the same manual lock and draft checks as editing
(`app.manual_drafts.lock_current_draft`), store the instruction text (a VOICE input copies the
READY transcription, so a retry does not need the recording) and enqueue a DRAFT_CORRECTION
task. The AI call (`revise_structure`) runs outside any transaction; `_apply` re-locks the
manual and the correction and applies the result only if the correction still waits for this
task and attempt and the draft is still the same version at the base revision.

| Correction | Event | Result |
| --- | --- | --- |
| (new) RUNNING | accepted (attempt 1) | draft unchanged, later edits/acks/publication 409 |
| RUNNING | APPLIED with a real change | SUCCEEDED, resultRevision = base + 1, content/issues synced, acks void |
| RUNNING | NO_CHANGE or identical content | SUCCEEDED, resultRevision = base |
| RUNNING | CLARIFICATION_REQUIRED / REFERENCE_CONFLICT | ERROR (retryable=false), draft unchanged |
| RUNNING | AI failure after automatic retries, invalid output | ERROR AI_PROCESSING_FAILED (retryable) |
| RUNNING | draft replaced / revision moved meanwhile | ERROR MANUAL_VERSION_CONFLICT / REVISION_CONFLICT |
| ERROR (retryable, latest) | retry | RUNNING, attempt + 1, new task; the old task's result is discarded |
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import get_ai_provider
from app.ai.contracts import (
    RevisionTarget,
    StructureRevision,
    StructureRevisionRequest,
    StructureSnapshot,
)
from app.auth import CurrentOwner, DbSession
from app.csrf import CsrfOwner
from app.db import new_uuid, session_scope, utcnow
from app.db.models import ManualDraftCorrection, ManualVersion, MediaTranscription, StoreManual
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.jobs.state import begin_transition
from app.manual_content import (
    active_draft,
    load_rows,
    manual_not_found,
    rows_content_body,
    store_manual,
    structure_snapshot,
)
from app.manual_drafts import conflict, correction_body, latest_correction, lock_current_draft
from app.manual_editing import ContentIn, ContentInvalid, prepare_content, replace_content
from app.store_access import UUID_PATTERN, StoreIdPath, load_owned_store, normalize_uuid
from app.tasks import StaleTask, TaskContext, TaskHandler, enqueue, register_handler

router = APIRouter()

KIND = "DRAFT_CORRECTION"
POLL_SECONDS = "2"
CorrectionIdPath = Annotated[str, Path(alias="correctionId", pattern=UUID_PATTERN)]


class TargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["MANUAL", "SHIFT", "SECTION"]
    target_id: str | None = Field(alias="targetId", pattern=UUID_PATTERN)


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["TEXT"]
    text: str = Field(min_length=1, max_length=10000, pattern=r"\S")


class VoiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["VOICE"]
    transcription_id: str = Field(alias="transcriptionId", pattern=UUID_PATTERN)


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version_id: str = Field(alias="expectedVersionId", pattern=UUID_PATTERN)
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class CorrectionIn(_Command):
    target: TargetIn
    input: Annotated[TextInput | VoiceInput, Field(discriminator="method")]


class RetryIn(_Command):
    pass


def _target_error() -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[{
        "field": "target.targetId", "code": "INVALID_FORMAT",
        "message": "전체 정정은 targetId 없이, 근무조·업무 정정은 해당 ID와 함께 보내 주세요."}])


def _accepted(response: Response) -> Response:
    response.headers["Retry-After"] = POLL_SECONDS
    return response


def _enqueue(db: Session, correction_id: str, base_revision: int, attempt: int) -> str:
    return enqueue(db, KIND, correction_id, {"correctionId": correction_id}, input_revision=base_revision,
                   attempt=attempt)


@router.post("/api/stores/{storeId}/manual/draft/corrections", status_code=202)
def create_manual_draft_correction(
    store_id: StoreIdPath, body: CorrectionIn, owner: CsrfOwner, db: DbSession, key: IdempotencyKey,
) -> Response:
    target = body.target
    if (target.kind == "MANUAL") != (target.target_id is None):
        raise _target_error()
    target_id = None if target.target_id is None else normalize_uuid(target.target_id)

    def work() -> IdempotentResult:
        store, _manual, draft = lock_current_draft(
            db, owner.user_id, store_id, body.expected_version_id, body.expected_revision)
        if target.kind != "MANUAL":
            rows = load_rows(db, draft.id)
            items = rows.shifts if target.kind == "SHIFT" else rows.sections
            if target_id not in {item.id for item in items}:
                raise manual_not_found()
        transcription_id = None
        if isinstance(body.input, TextInput):
            text = body.input.text
        else:
            transcription = db.scalars(select(MediaTranscription).where(
                MediaTranscription.id == normalize_uuid(body.input.transcription_id),
                MediaTranscription.store_id == store.id, MediaTranscription.manual_media_id.is_not(None),
            )).first()
            if transcription is None:
                raise manual_not_found()
            if transcription.status != "READY":
                raise ApiError(409, ErrorCode.TRANSCRIPTION_NOT_READY, "음성 변환이 끝난 뒤 다시 시도해 주세요.")
            text, transcription_id = transcription.text, transcription.id
        now = utcnow()
        correction_id = new_uuid()
        row = ManualDraftCorrection(
            id=correction_id, task_id=_enqueue(db, correction_id, draft.revision, 1), version_id=draft.id, base_revision=draft.revision, target_kind=target.kind, target_id=target_id,
            input_method=body.input.method, input_text=text, transcription_id=transcription_id,
            status="RUNNING", attempt=1, requested_by_owner_id=owner.user_id, created_at=now, updated_at=now,
        )
        db.add(row)
        db.flush()
        return IdempotentResult(202, correction_body(row))

    key_body = body.model_dump(by_alias=True, mode="json")
    key_body["expectedVersionId"] = normalize_uuid(body.expected_version_id)
    key_body["target"]["targetId"] = target_id
    if isinstance(body.input, VoiceInput):
        key_body["input"]["transcriptionId"] = normalize_uuid(body.input.transcription_id)
    return _accepted(run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path=f"/api/stores/{normalize_uuid(store_id)}/manual/draft/corrections",
        body=key_body, handler=work, revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    ))


def _current_draft_correction(db: Session, store_id: str, correction_id: str, *,
                              lock: bool = False) -> tuple[ManualVersion | None, ManualDraftCorrection | None]:
    manual = store_manual(db, store_id, lock=lock)
    draft = None if manual is None else active_draft(db, manual.id, refresh=lock)
    if draft is None:
        return None, None
    statement = select(ManualDraftCorrection).where(
        ManualDraftCorrection.id == normalize_uuid(correction_id), ManualDraftCorrection.version_id == draft.id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return draft, db.scalars(statement).first()


@router.get("/api/stores/{storeId}/manual/draft/corrections/{correctionId}")
def get_manual_draft_correction(
    store_id: StoreIdPath, correction_id: CorrectionIdPath, owner: CurrentOwner, db: DbSession,
    response: Response,
) -> dict:
    store = load_owned_store(db, owner.user_id, store_id)
    _draft, row = _current_draft_correction(db, store.id, correction_id)
    if row is None:
        raise manual_not_found()
    if row.status == "RUNNING":
        response.headers["Retry-After"] = POLL_SECONDS
    return correction_body(row)


@router.post("/api/stores/{storeId}/manual/draft/corrections/{correctionId}/retries", status_code=202)
def retry_manual_draft_correction(
    store_id: StoreIdPath, correction_id: CorrectionIdPath, body: RetryIn, owner: CsrfOwner, db: DbSession,
    key: IdempotencyKey,
) -> Response:
    def work() -> IdempotentResult:
        begin_transition(db)
        store = load_owned_store(db, owner.user_id, store_id)
        draft, row = _current_draft_correction(db, store.id, correction_id, lock=True)
        if draft is None or draft.id != normalize_uuid(body.expected_version_id):
            raise conflict(ErrorCode.MANUAL_VERSION_CONFLICT)
        if row is None:
            raise manual_not_found()
        if draft.revision != body.expected_revision:
            raise conflict(ErrorCode.REVISION_CONFLICT)
        latest = latest_correction(db, draft.id)
        if (row.status != "ERROR" or row.error_code != "AI_PROCESSING_FAILED"
                or latest is None or latest.id != row.id):
            raise ApiError(409, ErrorCode.MANUAL_CORRECTION_NOT_RETRYABLE,
                           "이 정정은 다시 시도할 수 없습니다. 최신 초안을 확인하고 새로 말해 주세요.")
        if row.base_revision != draft.revision:  # the draft was edited after the failure
            raise conflict(ErrorCode.REVISION_CONFLICT)
        now = utcnow()
        attempt = row.attempt + 1
        task_id = _enqueue(db, row.id, row.base_revision, attempt)
        row.status, row.attempt, row.task_id, row.error_code = "RUNNING", attempt, task_id, None
        row.result_revision, row.completed_at, row.updated_at = None, None, now
        db.flush()
        return IdempotentResult(202, correction_body(row))

    key_body = {"expectedVersionId": normalize_uuid(body.expected_version_id),
                "expectedRevision": body.expected_revision}
    return _accepted(run_idempotent(
        db=db, principal=owner, key=key, method="POST",
        path=f"/api/stores/{normalize_uuid(store_id)}/manual/draft/corrections/{normalize_uuid(correction_id)}/retries",
        body=key_body, handler=work, revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    ))


# --- DRAFT_CORRECTION task ----------------------------------------------------------------------


def _execute(ctx: TaskContext) -> StructureRevision | None:
    """Read the instruction and the draft at the base revision, then call the model (no
    transaction is open during the call). None when the correction no longer waits for this
    task or the draft moved: `_apply` records what happened."""
    with session_scope() as db:
        row = db.get(ManualDraftCorrection, ctx.subject_id)
        if row is None or row.status != "RUNNING" or row.task_id != ctx.task_id:
            return None
        version = db.get(ManualVersion, row.version_id)
        if version.status != "DRAFT" or version.revision != row.base_revision:
            return None
        request = StructureRevisionRequest(
            current=structure_snapshot(db, version.id), summary=None,
            target=RevisionTarget(kind=row.target_kind, target_id=row.target_id),
            instruction=row.input_text, require_manual_level=True,
        )
    return get_ai_provider().revise_structure(request)


def _locked(db: Session, ctx: TaskContext) -> tuple[ManualDraftCorrection, ManualVersion]:
    """Manual lock first (the order every request uses), then the correction; stale tasks stop."""
    version_id = db.scalar(select(ManualDraftCorrection.version_id).where(ManualDraftCorrection.id == ctx.subject_id))
    ctx.ensure(version_id is not None)
    manual_id = db.scalar(select(ManualVersion.manual_id).where(ManualVersion.id == version_id))
    db.scalars(select(StoreManual).where(StoreManual.id == manual_id).with_for_update()).first()
    row = db.scalars(select(ManualDraftCorrection).where(ManualDraftCorrection.id == ctx.subject_id)
                     .with_for_update().execution_options(populate_existing=True)).first()
    ctx.ensure(row.status == "RUNNING" and row.task_id == ctx.task_id and row.attempt == ctx.attempt)
    version = db.scalars(select(ManualVersion).where(ManualVersion.id == version_id)
                         .with_for_update().execution_options(populate_existing=True)).one()
    return row, version


def _finish(row: ManualDraftCorrection, now: datetime, *, error: str | None = None,
            result_revision: int | None = None) -> None:
    row.status = "ERROR" if error else "SUCCEEDED"
    row.error_code, row.result_revision = error, result_revision
    row.completed_at = row.updated_at = now


def correction_content(db: Session, version_id: str, structure: StructureSnapshot) -> ContentIn:
    """The corrected structure as API content: photos stay on their (kept) section IDs and the
    structure photos are untouched; photos of removed sections are unlinked."""
    current = rows_content_body(load_rows(db, version_id))
    photos = {section["id"]: section["photos"] for section in current["sections"]}
    return ContentIn.model_validate({
        "shifts": [
            {"id": s.id, "name": s.name, "startTime": s.start_time, "endTime": s.end_time,
             "endsNextDay": s.ends_next_day}
            for s in structure.shifts
        ],
        "sections": [
            {"id": s.id, "category": s.category, "shiftId": s.shift_id, "title": s.title,
             "steps": [{"id": t.id, "instruction": t.instruction, "checklistItem": t.checklist_item}
                       for t in s.steps],
             "photos": photos.get(s.id, [])}
            for s in structure.sections
        ],
        "structurePhotos": current["structurePhotos"],
        "missingInformation": [
            {"id": m.id, "target": m.target, "targetId": m.target_id, "field": m.field,
             "description": m.description}
            for m in structure.missing_information
        ],
    })


def _apply(db: Session, ctx: TaskContext, result: StructureRevision | None) -> None:
    row, version = _locked(db, ctx)
    now = utcnow()
    if version.status != "DRAFT":
        return _finish(row, now, error="MANUAL_VERSION_CONFLICT")
    if version.revision != row.base_revision:
        return _finish(row, now, error="REVISION_CONFLICT")
    if result is None:  # execute saw an older state; nothing to apply
        raise StaleTask(ctx.task_id)
    if result.outcome == "CLARIFICATION_REQUIRED":
        return _finish(row, now, error="CORRECTION_CLARIFICATION_REQUIRED")
    if result.outcome == "REFERENCE_CONFLICT":
        return _finish(row, now, error="MANUAL_REFERENCE_CONFLICT")
    if result.outcome == "NO_CHANGE":
        return _finish(row, now, result_revision=row.base_revision)
    store_id = db.scalar(select(StoreManual.store_id).where(StoreManual.id == version.manual_id))
    try:
        prepared = prepare_content(db, version, store_id, correction_content(db, version.id, result.structure))
    except (ContentInvalid, ValidationError):
        # The model's structure passed app.ai validation but not the manual rules: a failed
        # processing the owner may retry, never a partial write.
        return _finish(row, now, error="AI_PROCESSING_FAILED")
    changed = replace_content(db, version, prepared, now=now)
    _finish(row, now, result_revision=version.revision if changed else row.base_revision)


def _fail(db: Session, ctx: TaskContext, _error: Exception) -> None:
    row, _version = _locked(db, ctx)
    _finish(row, utcnow(), error="AI_PROCESSING_FAILED")


HANDLER = TaskHandler(kind=KIND, execute=_execute, apply=_apply, fail=_fail, max_tries=3,
                      lease_seconds=300, backoff_seconds=(2.0, 10.0))
register_handler(HANDLER)

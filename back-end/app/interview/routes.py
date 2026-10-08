"""Owner AI interview API (#120; openapi tags 매뉴얼 인터뷰·인텐트 검토), M = /api/stores/{storeId}/manual:

    POST M/interviews                                   start (new draft + session)      201
    GET  M/interviews/{sessionId}                       progress, current question
    GET  M/interviews/{sessionId}/turns                 conversation history (paged)
    POST M/interviews/{sessionId}/answers               answer the current question      202
    POST M/interviews/{sessionId}/completion            freeze reviews, compose draft    202
    POST M/interviews/{sessionId}/retries               resume a failed Jev/draft task   202
    GET  M/interviews/{sessionId}/reviews               every finished intent's review
    GET  .../intents/{intentId}/review                  one review
    POST .../intents/{intentId}/review/confirmations    confirm (never a progress gate)  200
    POST .../intents/{intentId}/review/corrections      correct the summary              202
    POST .../intents/{intentId}/review/retries          resume a failed summary task     202
    PUT  .../intents/{intentId}/review/photos           replace one photo list           200

Every request re-checks the ACTIVE OWNER session, store ownership and APPROVED status. Writes
need CSRF/Origin and an Idempotency-Key (`run_idempotent`: the handler starts on a fresh
transaction, takes the session row lock first and commits with the stored response).
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentOwner, DbSession
from app.csrf import CsrfOwner
from app.db import utcnow
from app.db.models import (
    BackgroundTask,
    InterviewIntent,
    InterviewIntentReview,
    InterviewQuestionSet,
    InterviewReviewConfirmation,
    InterviewSession,
    InterviewSessionIntent,
    InterviewTurn,
    InterviewTurnPhoto,
    ManualVersion,
    MediaTranscription,
    StoreManual,
)
from app.errors import ApiError, ErrorCode
from app.idempotency import IdempotencyKey, IdempotentResult, run_idempotent
from app.interview import drafting
from app.interview import tasks as _tasks  # noqa: F401 - registers the task handlers
from app.interview.common import (
    REVIEW_NOT_READY_MESSAGE,
    REVIEW_PROCESSING_MESSAGE,
    current_question,
    is_answered,
    iso,
    load_session,
    lock_manual_and_session,
    lock_review,
    lock_session,
    not_found,
    question_body,
    review_body,
    reviews_in_order,
    revision_conflict,
    session_body,
    session_intent,
    state_conflict,
    turn_body,
)
from app.interview.content import photo_ids
from app.interview.flow import (
    enqueue_base_question,
    enqueue_evaluation,
    next_turn_no,
)
from app.interview.question_set import CURRENT_QUESTION_SET_ID
from app.jobs.state import begin_transition
from app.manual_content import active_draft, store_manual
from app.manual_drafts import ensure_no_running_correction
from app.media.references import (
    MediaLinkError,
    add_snapshot_refs,
    lock_photos_for_link,
    replace_snapshot_refs,
)
from app.pagination import Pagination, page_response
from app.store_access import UUID_PATTERN, StoreIdPath, load_owned_store, normalize_uuid
from app.tasks import enqueue

router = APIRouter()

M = "/api/stores/{storeId}/manual"
S = M + "/interviews/{sessionId}"
R = S + "/intents/{intentId}/review"

SessionIdPath = Annotated[str, Path(alias="sessionId", pattern=UUID_PATTERN)]
IntentIdPath = Annotated[str, Path(alias="intentId", pattern=UUID_PATTERN)]
Uuid = Annotated[str, Field(pattern=UUID_PATTERN, strict=True)]
Revision = Annotated[int, Field(ge=1, strict=True)]

ALREADY_EXISTS_MESSAGE = "이미 작성 중인 매뉴얼이 있어요. 이어서 작성해 주세요."
ALREADY_ANSWERED_MESSAGE = "이미 답변한 질문이에요. 최신 질문을 다시 확인해 주세요."
TRANSCRIPTION_NOT_READY_MESSAGE = "음성 인식이 아직 끝나지 않았어요."


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextInput(_Body):
    method: Literal["TEXT"]
    text: Annotated[str, Field(min_length=1, max_length=10000, pattern=r"\S", strict=True)]


class VoiceInput(_Body):
    method: Literal["VOICE"]
    transcriptionId: Uuid


InterviewInput = Annotated[TextInput | VoiceInput, Field(discriminator="method")]


def _unique(values: list[str], field: str) -> None:
    if len({value.lower() for value in values}) != len(values):
        raise ValueError(f"duplicate {field}")


class AnswerBody(_Body):
    expectedRevision: Revision
    questionId: Uuid
    input: InterviewInput
    photoIds: Annotated[list[Uuid], Field(max_length=20)] = []

    @model_validator(mode="after")
    def _distinct(self):
        _unique(self.photoIds, "photoIds")
        return self


class ReviewRevision(_Body):
    intentId: Uuid
    revision: Revision


class CompletionBody(_Body):
    expectedRevision: Revision
    reviewRevisions: Annotated[list[ReviewRevision], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def _distinct(self):
        _unique([item.intentId for item in self.reviewRevisions], "intentId")
        return self


class RevisionCommand(_Body):
    expectedRevision: Revision


class ConfirmBody(_Body):
    expectedRevision: Revision
    confirmed: Literal[True]


class CorrectionBody(_Body):
    expectedRevision: Revision
    input: InterviewInput


class PhotoAttachment(_Body):
    mediaId: Uuid
    caption: Annotated[str | None, Field(max_length=300, strict=True)]
    title: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S", strict=True)]


class PhotoUpdate(_Body):
    expectedRevision: Revision
    target: Literal["WORK_STRUCTURE", "SECTION"]
    sectionId: Uuid | None
    photos: Annotated[list[PhotoAttachment], Field(max_length=20)]

    @model_validator(mode="after")
    def _shape(self):
        if (self.target == "WORK_STRUCTURE") != (self.sectionId is None):
            raise ValueError("sectionId must be null for WORK_STRUCTURE and set for SECTION")
        _unique([photo.mediaId for photo in self.photos], "mediaId")
        return self


def _validation(field: str, message: str) -> ApiError:
    return ApiError(422, ErrorCode.VALIDATION_ERROR, field_errors=[
        {"field": field, "code": "INVALID_FORMAT", "message": message}])


def _path(store_id: str, *parts: str) -> str:
    return "/".join([f"/api/stores/{normalize_uuid(store_id)}/manual", *parts])


def _idempotent(db: Session, owner, key: str, store_id: str, path: str, body: Any, work,
                method: str = "POST") -> Response:
    return run_idempotent(
        db=db, principal=owner, key=key, method=method,
        path=path, body=body, handler=work, revalidate=lambda: load_owned_store(db, owner.user_id, store_id),
    )


def _input_text(db: Session, store_id: str, data: TextInput | VoiceInput) -> tuple[str, str, str | None]:
    """(method, text, transcription ID): VOICE copies the READY transcript of this store."""
    if isinstance(data, TextInput):
        return "TEXT", data.text, None
    row = db.scalars(select(MediaTranscription).where(
        MediaTranscription.id == normalize_uuid(data.transcriptionId), MediaTranscription.store_id == store_id,
        MediaTranscription.manual_media_id.is_not(None),
    ).with_for_update(read=True)).first()
    if row is None:
        raise not_found()
    if row.status != "READY":
        raise ApiError(409, ErrorCode.TRANSCRIPTION_NOT_READY, TRANSCRIPTION_NOT_READY_MESSAGE)
    return "VOICE", row.text, row.id


def _input_body(data: TextInput | VoiceInput) -> dict:
    if isinstance(data, TextInput):
        return {"method": "TEXT", "text": data.text}
    return {"method": "VOICE", "transcriptionId": normalize_uuid(data.transcriptionId)}


def _lock_photos(db: Session, store_id: str, media_ids: list[str], field: str):
    try:
        return lock_photos_for_link(db, store_id, [normalize_uuid(m) for m in media_ids])
    except MediaLinkError as error:
        if error.reason == "not_image":
            raise _validation(field, "사진 파일만 연결할 수 있습니다.") from None
        raise not_found() from None


# --- start ------------------------------------------------------------------------------------


@router.post("/api/stores/{storeId}/manual/interviews", status_code=201)
def start_manual_interview(store_id: StoreIdPath, body: dict, owner: CsrfOwner, db: DbSession,
                           key: IdempotencyKey) -> Response:
    if body:  # ManualInterviewStart is an empty object (additionalProperties: false)
        raise _validation(next(iter(body)), "허용되지 않는 항목입니다.")

    def work() -> IdempotentResult:
        begin_transition(db)  # READ COMMITTED: the draft checks after the locks read current rows
        # The store row serializes the first start of a store (no store_manuals row to lock yet).
        store = load_owned_store(db, owner.user_id, store_id, lock=True)
        manual = store_manual(db, store.id, lock=True)
        if manual is None:
            manual = StoreManual(store_id=store.id)
            db.add(manual)
            db.flush()
        draft = active_draft(db, manual.id, refresh=True)
        if draft is not None:  # one unpublished draft per store; a running correction says so first
            ensure_no_running_correction(db, draft.id)
            raise ApiError(409, ErrorCode.INTERVIEW_ALREADY_EXISTS, ALREADY_EXISTS_MESSAGE)
        question_set = db.get(InterviewQuestionSet, CURRENT_QUESTION_SET_ID)
        if question_set is None:
            raise RuntimeError("interview question set is not seeded (migration 0040)")
        number = db.scalar(select(func.coalesce(func.max(ManualVersion.revision_no), 0))
                           .where(ManualVersion.manual_id == manual.id))
        version = ManualVersion(manual_id=manual.id, revision_no=number + 1, created_by_owner_id=owner.user_id)
        db.add(version)
        db.flush()
        intents = _question_set_intents(db, question_set.id)
        session = InterviewSession(manual_version_id=version.id, owner_id=owner.user_id,
                                   question_set_id=question_set.id, current_intent_id=intents[0].id, revision=1)
        db.add(session)
        db.flush()
        db.add_all([InterviewSessionIntent(session_id=session.id, intent_id=intent.id) for intent in intents])
        db.flush()
        enqueue_base_question(db, session, intents[0], store)
        db.flush()
        return IdempotentResult(201, session_body(db, session))

    return _idempotent(db, owner, key, store_id, _path(store_id, "interviews"), {}, work)


def _question_set_intents(db: Session, question_set_id: str) -> list[InterviewIntent]:
    return list(db.scalars(select(InterviewIntent).where(InterviewIntent.question_set_id == question_set_id)
                           .order_by(InterviewIntent.sort_order)))


# --- session reads ------------------------------------------------------------------------------


@router.get(S)
def get_manual_interview(store_id: StoreIdPath, session_id: SessionIdPath, owner: CurrentOwner,
                         db: DbSession) -> dict:
    _store, session = load_session(db, owner.user_id, store_id, session_id)
    return session_body(db, session)


@router.get(S + "/turns")
def get_manual_interview_turns(store_id: StoreIdPath, session_id: SessionIdPath, owner: CurrentOwner,
                               db: DbSession, page: Pagination) -> dict:
    _store, session = load_session(db, owner.user_id, store_id, session_id)
    as_of = utcnow()
    total = db.scalar(select(func.count()).select_from(InterviewTurn).where(InterviewTurn.session_id == session.id))
    turns = list(db.scalars(select(InterviewTurn).where(InterviewTurn.session_id == session.id)
                            .order_by(InterviewTurn.turn_no).offset(page.offset).limit(page.limit)))
    photos: dict[str, list[str]] = {}
    if turns:
        for row in db.scalars(select(InterviewTurnPhoto).where(
                InterviewTurnPhoto.turn_id.in_([t.id for t in turns])).order_by(InterviewTurnPhoto.sort_order)):
            photos.setdefault(row.turn_id, []).append(row.media_id)
    body = page_response([turn_body(t, photos.get(t.id, [])) for t in turns], total, page)
    body["asOf"] = iso(as_of)
    return body


# --- answers ----------------------------------------------------------------------------------


@router.post(S + "/answers", status_code=202)
def answer_manual_interview_question(store_id: StoreIdPath, session_id: SessionIdPath, body: AnswerBody,
                                     owner: CsrfOwner, db: DbSession, key: IdempotencyKey) -> Response:
    def work() -> IdempotentResult:
        store, session = lock_session(db, owner.user_id, store_id, session_id)
        question = db.get(InterviewTurn, normalize_uuid(body.questionId))
        if question is None or question.session_id != session.id or question.turn_kind != "QUESTION":
            raise state_conflict()
        if is_answered(db, question.id):
            raise ApiError(409, ErrorCode.QUESTION_ALREADY_ANSWERED, ALREADY_ANSWERED_MESSAGE)
        current = current_question(db, session)
        if current is None or current.id != question.id:
            raise state_conflict()
        if session.revision != body.expectedRevision:
            raise revision_conflict()
        method, text, transcription_id = _input_text(db, store.id, body.input)
        photos = _lock_photos(db, store.id, body.photoIds, "photoIds")
        session.revision += 1
        snapshot = question_body(question, answered=True)
        answer = InterviewTurn(
            session_id=session.id, turn_no=next_turn_no(db, session.id), speaker="OWNER", turn_kind="ANSWER",
            intent_id=question.intent_id, depth=question.depth, probe_batch_id=question.probe_batch_id,
            reply_to_question_turn_id=question.id, input_method=method, content=text,
            transcription_id=transcription_id,
            guidance=snapshot.get("guidance"), guidance_cards=snapshot.get("guidanceCards"),
        )
        db.add(answer)
        db.flush()
        db.add_all([InterviewTurnPhoto(turn_id=answer.id, media_id=p.id, sort_order=i)
                    for i, p in enumerate(photos)])
        progress, intent = session_intent(db, session.id, question.intent_id)
        enqueue_evaluation(db, session, progress, intent, store, question, answer)
        db.flush()
        return IdempotentResult(202, session_body(db, session))

    request = {"expectedRevision": body.expectedRevision, "questionId": normalize_uuid(body.questionId),
               "input": _input_body(body.input), "photoIds": [normalize_uuid(p) for p in body.photoIds]}
    return _idempotent(db, owner, key, store_id, _path(store_id, "interviews", normalize_uuid(session_id),
                                                       "answers"), request, work)


# --- completion and session retries ----------------------------------------------------------------


@router.post(S + "/completion", status_code=202)
def generate_manual_draft(store_id: StoreIdPath, session_id: SessionIdPath, body: CompletionBody,
                          owner: CsrfOwner, db: DbSession, key: IdempotencyKey) -> Response:
    items = [(normalize_uuid(item.intentId), item.revision) for item in body.reviewRevisions]

    def work() -> IdempotentResult:
        store, manual, session, version = lock_manual_and_session(db, owner.user_id, store_id, session_id)
        drafting.start_generation(db, store, manual, session, version, body.expectedRevision, items)
        return IdempotentResult(202, session_body(db, session))

    request = {"expectedRevision": body.expectedRevision,
               "reviewRevisions": [{"intentId": i, "revision": r} for i, r in items]}
    return _idempotent(db, owner, key, store_id, _path(store_id, "interviews", normalize_uuid(session_id),
                                                       "completion"), request, work)


@router.post(S + "/retries", status_code=202)
def retry_manual_interview_processing(store_id: StoreIdPath, session_id: SessionIdPath, body: RevisionCommand,
                                      owner: CsrfOwner, db: DbSession, key: IdempotencyKey) -> Response:
    def work() -> IdempotentResult:
        # Manual first: a draft-generation retry changes the draft (global lock order).
        _store, _manual, session, version = lock_manual_and_session(db, owner.user_id, store_id, session_id)
        if session.status != "ERROR":
            raise state_conflict()
        if session.revision != body.expectedRevision:
            raise revision_conflict()
        failed = db.get(BackgroundTask, session.processing_task_id)
        if session.processing_kind == "DRAFT_GENERATION":
            if version.status != "DRAFT" or version.generation_status != "ERROR":
                raise state_conflict()
            version.generation_status = "RUNNING"
        attempt = session.processing_attempt + 1
        session.status, session.error_code = "IN_PROGRESS", None
        session.revision += 1
        session.processing_task_id = enqueue(db, session.processing_kind, session.id, failed.payload,
                                             input_revision=session.revision, attempt=attempt)
        session.processing_attempt = attempt
        db.flush()
        return IdempotentResult(202, session_body(db, session))

    return _idempotent(db, owner, key, store_id, _path(store_id, "interviews", normalize_uuid(session_id),
                                                       "retries"), {"expectedRevision": body.expectedRevision},
                       work)


# --- reviews: reads -------------------------------------------------------------------------------


@router.get(S + "/reviews")
def list_manual_intent_reviews(store_id: StoreIdPath, session_id: SessionIdPath, owner: CurrentOwner,
                               db: DbSession) -> dict:
    _store, session = load_session(db, owner.user_id, store_id, session_id)
    return {"sessionId": session.id, "sessionRevision": session.revision,
            "items": [review_body(review) for review in reviews_in_order(db, session.id)]}


def _review_or_409(db: Session, session: InterviewSession, intent_id: str, *, lock: bool):
    found = session_intent(db, session.id, normalize_uuid(intent_id))
    if found is None:
        raise not_found()
    review = (lock_review(db, session.id, found[1].id) if lock
              else db.get(InterviewIntentReview, (session.id, found[1].id)))
    if review is None:  # the intent is still PENDING
        raise ApiError(409, ErrorCode.REVIEW_NOT_READY, REVIEW_NOT_READY_MESSAGE)
    return found[0], found[1], review


@router.get(R)
def get_manual_intent_review(store_id: StoreIdPath, session_id: SessionIdPath, intent_id: IntentIdPath,
                             owner: CurrentOwner, db: DbSession) -> dict:
    _store, session = load_session(db, owner.user_id, store_id, session_id)
    _progress, _intent, review = _review_or_409(db, session, intent_id, lock=False)
    return review_body(review)


# --- reviews: changes ------------------------------------------------------------------------------


def _reviews_frozen(session: InterviewSession) -> bool:
    """GENERATING/COMPLETED, or a failed draft generation whose retry reuses the frozen snapshot."""
    return session.status == "COMPLETED" or session.processing_kind == "DRAFT_GENERATION"


def _changeable_review(db: Session, session: InterviewSession, intent_id: str, expected: int):
    progress, intent, review = _review_or_409(db, session, intent_id, lock=True)
    if _reviews_frozen(session):
        raise state_conflict()
    if review.status == "PROCESSING":
        raise ApiError(409, ErrorCode.REVIEW_PROCESSING, REVIEW_PROCESSING_MESSAGE)
    if review.status != "READY":  # ERROR: only review retries are accepted
        raise ApiError(409, ErrorCode.REVIEW_NOT_READY, REVIEW_NOT_READY_MESSAGE)
    if review.revision != expected:
        raise revision_conflict()
    return progress, intent, review


@router.post(R + "/confirmations")
def confirm_manual_interview_understanding(store_id: StoreIdPath, session_id: SessionIdPath,
                                           intent_id: IntentIdPath, body: ConfirmBody, owner: CsrfOwner,
                                           db: DbSession, key: IdempotencyKey) -> Response:
    def work() -> IdempotentResult:
        _store, session = lock_session(db, owner.user_id, store_id, session_id)
        _progress, _intent, review = _changeable_review(db, session, intent_id, body.expectedRevision)
        if review.confirmed_at is None:  # confirming the confirmed revision again changes nothing
            confirmation = InterviewReviewConfirmation(
                session_id=session.id, intent_id=review.intent_id, reviewed_revision=review.revision,
                confirmed_revision=review.revision + 1, confirmed_content=review.ready_content,
                owner_id=owner.user_id, confirmed_at=utcnow(),
            )
            db.add(confirmation)
            db.flush()
            add_snapshot_refs(db, "REVIEW_CONFIRMATION", confirmation.id, photo_ids(review.ready_content))
            review.confirmed_at, review.confirmed_by_owner_id = confirmation.confirmed_at, owner.user_id
            review.revision += 1
            db.flush()
        return IdempotentResult(200, review_body(review))

    return _idempotent(db, owner, key, store_id, _review_path(store_id, session_id, intent_id, "confirmations"),
                       {"expectedRevision": body.expectedRevision, "confirmed": True}, work)


def _review_path(store_id: str, session_id: str, intent_id: str, action: str) -> str:
    return _path(store_id, "interviews", normalize_uuid(session_id), "intents", normalize_uuid(intent_id),
                 "review", action)


@router.post(R + "/corrections", status_code=202)
def correct_manual_interview_understanding(store_id: StoreIdPath, session_id: SessionIdPath,
                                           intent_id: IntentIdPath, body: CorrectionBody, owner: CsrfOwner,
                                           db: DbSession, key: IdempotencyKey) -> Response:
    def work() -> IdempotentResult:
        store, session = lock_session(db, owner.user_id, store_id, session_id)
        _progress, intent, review = _changeable_review(db, session, intent_id, body.expectedRevision)
        method, text, transcription_id = _input_text(db, store.id, body.input)
        turn = InterviewTurn(
            session_id=session.id, turn_no=next_turn_no(db, session.id), speaker="OWNER", turn_kind="CORRECTION",
            intent_id=intent.id, depth=0, input_method=method, content=text, transcription_id=transcription_id,
        )
        db.add(turn)
        db.flush()
        previous = None
        if review.confirmed_at is not None:
            previous = {"at": review.confirmed_at.isoformat(), "by": review.confirmed_by_owner_id}
        # The request is rebuilt in execute from the review (frozen while PROCESSING) and this turn,
        # so a large review never exceeds the task payload limit. Enqueue first: the row may only
        # become PROCESSING together with its task (CHECK).
        task_id = enqueue(
            db, "REVIEW_CORRECTION", session.id,
            {"intentId": intent.id, "correctionTurnId": turn.id, "previousConfirmation": previous},
            input_revision=review.revision + 1, attempt=1,
        )
        review.confirmed_at = review.confirmed_by_owner_id = None
        review.status, review.error_code = "PROCESSING", None
        review.revision += 1
        review.processing_kind, review.processing_task_id, review.processing_attempt = "CORRECTION", task_id, 1
        db.flush()
        return IdempotentResult(202, review_body(review))

    return _idempotent(db, owner, key, store_id, _review_path(store_id, session_id, intent_id, "corrections"),
                       {"expectedRevision": body.expectedRevision, "input": _input_body(body.input)}, work)


@router.post(R + "/retries", status_code=202)
def retry_manual_intent_review(store_id: StoreIdPath, session_id: SessionIdPath, intent_id: IntentIdPath,
                               body: RevisionCommand, owner: CsrfOwner, db: DbSession,
                               key: IdempotencyKey) -> Response:
    def work() -> IdempotentResult:
        _store, session = lock_session(db, owner.user_id, store_id, session_id)
        _progress, _intent, review = _review_or_409(db, session, intent_id, lock=True)
        if _reviews_frozen(session) or review.status != "ERROR":
            raise state_conflict()
        if review.revision != body.expectedRevision:
            raise revision_conflict()
        failed = db.get(BackgroundTask, review.processing_task_id)
        attempt = review.processing_attempt + 1
        task_id = enqueue(db, "REVIEW_" + review.processing_kind, session.id, failed.payload,
                          input_revision=review.revision + 1, attempt=attempt)
        review.status, review.error_code = "PROCESSING", None
        review.revision += 1
        review.processing_task_id, review.processing_attempt = task_id, attempt
        db.flush()
        return IdempotentResult(202, review_body(review))

    return _idempotent(db, owner, key, store_id, _review_path(store_id, session_id, intent_id, "retries"),
                       {"expectedRevision": body.expectedRevision}, work)


@router.put(R + "/photos")
def replace_manual_interview_review_photos(store_id: StoreIdPath, session_id: SessionIdPath,
                                           intent_id: IntentIdPath, body: PhotoUpdate, owner: CsrfOwner,
                                           db: DbSession, key: IdempotencyKey) -> Response:
    section_id = normalize_uuid(body.sectionId) if body.sectionId else None
    photos = [{"mediaId": normalize_uuid(p.mediaId), "caption": p.caption, "title": p.title} for p in body.photos]

    def work() -> IdempotentResult:
        store, session = lock_session(db, owner.user_id, store_id, session_id)
        _progress, intent, review = _changeable_review(db, session, intent_id, body.expectedRevision)
        content = dict(review.ready_content)
        if body.target == "WORK_STRUCTURE":
            if intent.stage != "WORK_STRUCTURE":
                raise _validation("target", "근무 구조 단계의 요약에만 근무 구조 사진을 연결할 수 있습니다.")
            current = content.get("structurePhotos", [])
        else:
            matches = [s for s in content["sections"] if s["id"] == section_id]
            if not matches:
                raise _validation("sectionId", "이 요약에 있는 업무를 선택해 주세요.")
            current = matches[0].get("photos", [])
        _lock_photos(db, store.id, [p["mediaId"] for p in photos], "photos")
        if current == photos:  # same names, captions and order: nothing changes
            return IdempotentResult(200, review_body(review))
        if body.target == "WORK_STRUCTURE":
            content["structurePhotos"] = photos
        else:
            content["sections"] = [{**s, "photos": photos} if s["id"] == section_id else s
                                   for s in content["sections"]]
        review.ready_content = content
        replace_snapshot_refs(db, "INTENT_REVIEW", session.id, photo_ids(content), intent_id=review.intent_id)
        review.confirmed_at = review.confirmed_by_owner_id = None
        review.revision += 1
        db.flush()
        return IdempotentResult(200, review_body(review))

    request = {"expectedRevision": body.expectedRevision, "target": body.target, "sectionId": section_id,
               "photos": photos}
    return _idempotent(db, owner, key, store_id, _review_path(store_id, session_id, intent_id, "photos"),
                       request, work, method="PUT")

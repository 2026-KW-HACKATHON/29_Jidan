"""`Idempotency-Key` handling for create-style endpoints.

Contract (openapi.yaml): a UUID key plus the member, endpoint and normalized body are kept for
24 hours. Retrying the same request replays the first response, the same key with another body
or endpoint is `409 IDEMPOTENCY_KEY_REUSED`, and concurrent requests with the same key run the
work once.

How it works. `run_idempotent` first *reserves* the key in its own short transaction (a unique
`(principal, key)` row in state PROCESSING with a 60 second lease), so competing requests see it
at once. The handler then does its business writes on the request's session, and the same
transaction flips the row to COMPLETED with the response. Business data and the stored response
therefore commit together; a crash leaves nothing behind but an expiring lease, and a failed
handler rolls back and releases the key so the client can retry.

Only successful results are stored. Errors raised by the handler are not replayed.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, Header
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db import new_uuid, session_scope, utcnow
from app.db.models import IdempotencyRecord
from app.errors import ApiError, ErrorCode

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"
RECORD_TTL = timedelta(hours=24)
# How long a request may hold a key before another request may take it over (crash recovery).
PROCESSING_LEASE = timedelta(seconds=60)
# How long a duplicate waits for the in-flight original before answering 409.
WAIT_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.1
MAX_RESERVE_ATTEMPTS = 5

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class IdempotentResult:
    """What a handler produced: a status code and a JSON-compatible body (None for no body)."""

    status_code: int
    body: Any = None


def idempotency_key(
    value: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> str:
    """Dependency: the required `Idempotency-Key` header as a canonical lowercase UUID."""
    if value is None or not value.strip():
        code, message = "REQUIRED", "Idempotency-Key 헤더가 필요합니다."
    elif not _UUID.match(value):
        code, message = "INVALID_FORMAT", "Idempotency-Key는 UUID 형식이어야 합니다."
    else:
        return value.lower()
    raise ApiError(
        422, ErrorCode.VALIDATION_ERROR,
        field_errors=[{"field": IDEMPOTENCY_HEADER, "code": code, "message": message}],
    )


IdempotencyKey = Annotated[str, Depends(idempotency_key)]


def canonical_body(body: Any) -> str:
    """Stable text for a request body: key order and whitespace do not matter, list order does."""
    if isinstance(body, BaseModel):
        body = body.model_dump(mode="json", by_alias=True)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def body_hash(body: Any) -> str:
    return hashlib.sha256(canonical_body(body).encode("utf-8")).hexdigest()


def _endpoint(method: str, path: str) -> str:
    value = f"{method.upper()} {path}"
    return value if len(value) <= 255 else f"{method.upper()} sha256:{hashlib.sha256(path.encode()).hexdigest()}"


@dataclass(frozen=True)
class _Owned:
    record_id: str
    lock_token: str


@dataclass(frozen=True)
class _Completed:
    status_code: int
    body: Any


class _Busy:
    pass


def _reuse_error() -> ApiError:
    return ApiError(409, ErrorCode.IDEMPOTENCY_KEY_REUSED)


def _reserve(principal_id: str, key: str, endpoint: str, request_hash: str):
    """Claim the key (_Owned), find its stored result (_Completed) or see it in use (_Busy)."""
    for _ in range(MAX_RESERVE_ATTEMPTS):
        now = utcnow()
        lock_token = new_uuid()
        try:
            with session_scope() as session:
                record = IdempotencyRecord(
                    principal_id=principal_id, idempotency_key=key, endpoint=endpoint,
                    request_hash=request_hash, state="PROCESSING", lock_token=lock_token,
                    locked_until=now + PROCESSING_LEASE, created_at=now,
                    expires_at=now + RECORD_TTL,
                )
                session.add(record)
                session.flush()
                record_id = record.id
            return _Owned(record_id, lock_token)
        except (IntegrityError, OperationalError):
            pass  # the key exists (or a lock deadlock): look at what is stored

        with session_scope() as session:
            record = session.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.principal_id == principal_id,
                    IdempotencyRecord.idempotency_key == key,
                )
            ).scalar_one_or_none()
            if record is None:
                continue  # released between our insert and this read
            if record.expires_at <= now:
                session.execute(delete(IdempotencyRecord).where(
                    IdempotencyRecord.id == record.id, IdempotencyRecord.expires_at <= now,
                ))
                continue
            if record.endpoint != endpoint or record.request_hash != request_hash:
                raise _reuse_error()
            if record.state == "COMPLETED":
                return _Completed(record.response_status, record.response_body)
            if record.locked_until is not None and record.locked_until <= now:
                taken = session.execute(
                    update(IdempotencyRecord)
                    .where(
                        IdempotencyRecord.id == record.id,
                        IdempotencyRecord.state == "PROCESSING",
                        IdempotencyRecord.lock_token == record.lock_token,
                    )
                    .values(lock_token=lock_token, locked_until=now + PROCESSING_LEASE)
                )
                if taken.rowcount == 1:
                    return _Owned(record.id, lock_token)
                continue
            return _Busy()
    return _Busy()


def _complete(db: Session, owned: _Owned, result: IdempotentResult) -> None:
    updated = db.execute(
        update(IdempotencyRecord)
        .where(
            IdempotencyRecord.id == owned.record_id,
            IdempotencyRecord.state == "PROCESSING",
            IdempotencyRecord.lock_token == owned.lock_token,
        )
        .values(
            state="COMPLETED", response_status=result.status_code, response_body=result.body,
            completed_at=utcnow(), lock_token=None, locked_until=None,
        )
    )
    if updated.rowcount != 1:
        # The lease expired and another request took the key over; our work must not commit.
        raise ApiError(409, ErrorCode.STATE_CONFLICT)


def _release(owned: _Owned) -> None:
    with session_scope() as session:
        session.execute(delete(IdempotencyRecord).where(
            IdempotencyRecord.id == owned.record_id,
            IdempotencyRecord.lock_token == owned.lock_token,
            IdempotencyRecord.state == "PROCESSING",
        ))


def _response(status_code: int, body: Any, *, replayed: bool) -> Response:
    headers = {REPLAY_HEADER: "true"} if replayed else None
    if body is None:
        return Response(status_code=status_code, headers=headers)
    return JSONResponse(body, status_code=status_code, headers=headers)


def run_idempotent(
    *,
    db: Session,
    principal_id: str,
    key: str,
    method: str,
    path: str,
    body: Any,
    handler: Callable[[], IdempotentResult],
    revalidate: Callable[[], None] | None = None,
) -> Response:
    """Run `handler` at most once for this principal and key, replaying its result afterwards.

    * `db` is the request's session; `handler` does its writes on it and returns an
      `IdempotentResult`. This function commits `db` together with the stored response, so
      the handler must not commit itself.
    * `principal_id` is the member id (or the Google sub for registration).
    * `body` is the request body (pydantic model or JSON-compatible value).
    * `revalidate` runs before a stored response is replayed. Raise `ApiError` there when the
      principal has lost the right to see the original result (e.g. store ownership changed);
      authentication itself is already re-checked by the session dependency on every request.

    Raises 409 IDEMPOTENCY_KEY_REUSED for the same key with another body/endpoint and 409
    STATE_CONFLICT (with Retry-After) if the original is still running after the wait timeout.
    """
    endpoint = _endpoint(method, path)
    request_hash = body_hash(body)
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while True:
        outcome = _reserve(principal_id, key, endpoint, request_hash)
        if isinstance(outcome, _Owned):
            break
        if isinstance(outcome, _Completed):
            if revalidate is not None:
                revalidate()
            return _response(outcome.status_code, outcome.body, replayed=True)
        if time.monotonic() >= deadline:
            raise ApiError(
                409, ErrorCode.STATE_CONFLICT, "이전 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.",
                headers={"Retry-After": "1"},
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    try:
        result = handler()
        _complete(db, outcome, result)
        db.commit()
    except BaseException:
        db.rollback()
        _release(outcome)
        raise
    return _response(result.status_code, result.body, replayed=False)


def purge_expired(*, db: Session | None = None) -> int:
    """Delete records past their 24 hours; safe to run from a periodic job."""
    statement = delete(IdempotencyRecord).where(IdempotencyRecord.expires_at <= utcnow())
    if db is not None:
        return db.execute(statement).rowcount
    with session_scope() as session:
        return session.execute(statement).rowcount

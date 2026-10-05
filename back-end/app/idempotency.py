"""`Idempotency-Key` handling for create-style endpoints.

Contract (openapi.yaml): a UUID key plus the subject, endpoint and normalized body are kept for
24 hours. Retrying the same request replays the first response, the same key with another body
or endpoint is `409 IDEMPOTENCY_KEY_REUSED`, and concurrent requests with the same key run the
work once.

How it works. `run_idempotent` first *reserves* the key in its own short transaction (a unique
`(subject, key)` row in state PROCESSING with a 60 second lease), so competing requests see it
at once. The handler then does its business writes on the request's session, and the same
transaction flips the row to COMPLETED with the response. Business data and the stored response
therefore commit together; a crash leaves nothing behind but an expiring lease, and a failed
handler rolls back and releases the key so the client can retry.

Only successful results are stored. Errors raised by the handler are not replayed.

Headers. A result may carry the allow-listed headers in `REPLAY_HEADERS` (`Location`, ...); they
are stored and sent again on a replay. `Set-Cookie` and every other header are never stored: a
replay does not create a new session, so a cookie must not be (and cannot be) replayed from the
database. The caller adds cookies to the response of the request that actually ran the handler;
see the "#105 합의 사항" section of the README.

Subject. Records are keyed by the person's Google `sub` (hashed), not by the session kind: a
registration session and the member session created from it map to the same subject, so the
registration request can be retried after registering (docs/auth-design.md). See
`subject_id_for`.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, Header
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.auth import MemberPrincipal, RegistrationPrincipal
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


def subject_id_for(principal: MemberPrincipal | RegistrationPrincipal) -> str:
    """The idempotency subject of a caller: SHA-256 hex of its Google `sub`.

    Member session -> `users.google_sub`; registration session -> the verified Google `sub`.
    Both are the same string for the same person, so a record written during registration is
    found again by the member session created at the end of it, with no linking step. Hashing
    keeps the 255 character `sub` within the 64 character column and out of the table.
    """
    google_sub = principal.google_sub
    if not isinstance(google_sub, str) or not google_sub:
        raise ValueError("principal has no Google sub")
    return hashlib.sha256(google_sub.encode("utf-8")).hexdigest()


# Response headers that are stored with the result and sent again on a replay. Everything else
# is dropped on purpose; in particular `Set-Cookie` and anything carrying a session or token
# secret must never reach the database. A header outside this list is rejected loudly
# (ValueError) rather than silently lost, so a handler cannot assume it will be replayed.
REPLAY_HEADERS = frozenset({"location", "content-location", "etag"})
MAX_REPLAY_HEADER_LENGTH = 2048


@dataclass(frozen=True)
class IdempotentResult:
    """What a handler produced: a status code, a JSON-compatible body (None for no body) and
    optional response `headers` limited to `REPLAY_HEADERS` (e.g. `Location` of a created
    resource). Cookies are not part of a result: see `run_idempotent`."""

    status_code: int
    body: Any = None
    headers: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.headers:
            return
        clean: dict[str, str] = {}
        for name, value in self.headers.items():
            lowered = str(name).lower()
            if lowered not in REPLAY_HEADERS:
                raise ValueError(
                    f"header {name!r} cannot be stored for replay (allowed: {sorted(REPLAY_HEADERS)})"
                )
            if (
                not isinstance(value, str) or not value or len(value) > MAX_REPLAY_HEADER_LENGTH
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
            ):
                raise ValueError(f"invalid value for header {name!r}")
            clean[lowered] = value
        object.__setattr__(self, "headers", clean)


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
    headers: Mapping[str, str] | None = None


class _Busy:
    pass


def _reuse_error() -> ApiError:
    return ApiError(409, ErrorCode.IDEMPOTENCY_KEY_REUSED)


# MySQL errors that mean "another transaction holds the row": lock wait timeout, deadlock.
_MYSQL_LOCK_ERRNOS = frozenset({1205, 1213})
_SQLITE_LOCK_MESSAGES = ("database is locked", "database table is locked", "database is busy")


def _is_lock_contention(error: OperationalError) -> bool:
    """True only for lock waits and deadlocks; connection errors and the rest must propagate."""
    original = error.orig
    args = getattr(original, "args", ())
    if args and isinstance(args[0], int):
        return args[0] in _MYSQL_LOCK_ERRNOS
    text = str(original).lower()
    return any(message in text for message in _SQLITE_LOCK_MESSAGES)


def _reserve(subject_id: str, key: str, endpoint: str, request_hash: str):
    """Claim the key (_Owned), find its stored result (_Completed) or see it in use (_Busy)."""
    for _ in range(MAX_RESERVE_ATTEMPTS):
        now = utcnow()
        lock_token = new_uuid()
        try:
            with session_scope() as session:
                record = IdempotencyRecord(
                    subject_id=subject_id, idempotency_key=key, endpoint=endpoint,
                    request_hash=request_hash, state="PROCESSING", lock_token=lock_token,
                    locked_until=now + PROCESSING_LEASE, created_at=now,
                    expires_at=now + RECORD_TTL,
                )
                session.add(record)
                session.flush()
                record_id = record.id
            return _Owned(record_id, lock_token)
        except IntegrityError:
            pass  # the key exists: look at what is stored
        except OperationalError as error:
            if not _is_lock_contention(error):
                raise  # connection loss and the like: a 500 now, not a wait and a false 409
            # lock wait timeout or deadlock on the unique index: look at what is stored

        with session_scope() as session:
            record = session.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.subject_id == subject_id,
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
                return _Completed(
                    record.response_status, record.response_body, _replayable(record.response_headers),
                )
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


def _replayable(stored: Any) -> dict[str, str] | None:
    """Stored headers filtered through the allow list again, whatever the row contains."""
    if not isinstance(stored, dict):
        return None
    headers = {
        name.lower(): value for name, value in stored.items()
        if isinstance(name, str) and name.lower() in REPLAY_HEADERS and isinstance(value, str)
    }
    return headers or None


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
            response_headers=dict(result.headers) if result.headers else None,
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


def _response(
    status_code: int, body: Any, headers: Mapping[str, str] | None = None, *, replayed: bool,
) -> Response:
    headers = dict(headers or {})
    if replayed:
        headers[REPLAY_HEADER] = "true"
    headers = headers or None
    if body is None:
        return Response(status_code=status_code, headers=headers)
    return JSONResponse(body, status_code=status_code, headers=headers)


def run_idempotent(
    *,
    db: Session,
    principal: MemberPrincipal | RegistrationPrincipal,
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
    * `principal` is the caller, a `MemberPrincipal` or a `RegistrationPrincipal`; the stored
      subject comes from `subject_id_for`.
    * `body` is the request body (pydantic model or JSON-compatible value).
    * `revalidate` runs before a stored response is replayed. Raise `ApiError` there when the
      principal has lost the right to see the original result (e.g. store ownership changed);
      authentication itself is already re-checked by the session dependency on every request.

    Raises 409 IDEMPOTENCY_KEY_REUSED for the same key with another body/endpoint and 409
    STATE_CONFLICT (with Retry-After) if the original is still running after the wait timeout.
    """
    subject_id = subject_id_for(principal)
    endpoint = _endpoint(method, path)
    request_hash = body_hash(body)
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while True:
        outcome = _reserve(subject_id, key, endpoint, request_hash)
        if isinstance(outcome, _Owned):
            break
        if isinstance(outcome, _Completed):
            # Authorization may have changed while this request waited. Both the database
            # snapshot and retained ORM objects must be fresh before the replay is authorized.
            db.commit()
            db.expire_all()
            if revalidate is not None:
                revalidate()
            return _response(outcome.status_code, outcome.body, outcome.headers, replayed=True)
        if time.monotonic() >= deadline:
            raise ApiError(
                409, ErrorCode.STATE_CONFLICT, "이전 요청을 처리 중입니다. 잠시 후 다시 시도해 주세요.",
                headers={"Retry-After": "1"},
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    try:
        # The request's transaction was opened by authentication, possibly seconds ago (we may
        # have waited above). Under MySQL REPEATABLE READ the handler would read that old
        # snapshot and miss what the earlier request committed meanwhile. End it explicitly so
        # the handler's first read opens a fresh one. Anything written before this call is
        # committed (never silently dropped); the handler's own writes commit below.
        db.commit()
        db.expire_all()  # expire_on_commit=False otherwise keeps previously loaded ORM values
        result = handler()
        _complete(db, outcome, result)
        db.commit()
    except BaseException:
        db.rollback()
        _release(outcome)
        raise
    return _response(result.status_code, result.body, result.headers, replayed=False)


def purge_expired(*, db: Session | None = None) -> int:
    """Delete records past their 24 hours; safe to run from a periodic job."""
    statement = delete(IdempotencyRecord).where(IdempotencyRecord.expires_at <= utcnow())
    if db is not None:
        return db.execute(statement).rowcount
    with session_scope() as session:
        return session.execute(statement).rowcount

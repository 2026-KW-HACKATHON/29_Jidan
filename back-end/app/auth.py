"""Opaque cookie sessions and the authorization dependencies built on them.

Browsers hold only a random token in an HttpOnly cookie. The server stores the SHA-256 of the
token, so a database leak does not yield usable cookies. Authorization is re-evaluated from the
database on every request: role, account status and expiry are never cached in the cookie.

Two kinds of session exist (docs/auth-design.md):

* member session  - cookie `jidan_session`, Path=/, idle 24h / absolute 7 days.
* registration session - cookie `jidan_registration`, Path=/api/auth, fixed 10 minutes. It only
  proves a Google identity that has not registered yet and never opens member APIs.
"""

import hashlib
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_session, session_scope, utcnow
from app.db.models import AuthSession, RegistrationSession, User
from app.errors import ApiError, ErrorCode

SESSION_COOKIE_NAME = "jidan_session"
SESSION_COOKIE_PATH = "/"
REGISTRATION_COOKIE_NAME = "jidan_registration"
REGISTRATION_COOKIE_PATH = "/api/auth"
COOKIE_SAMESITE = "lax"  # Domain is never set: host-only cookies.

SESSION_IDLE_TIMEOUT = timedelta(hours=24)
SESSION_ABSOLUTE_LIFETIME = timedelta(days=7)
REGISTRATION_LIFETIME = timedelta(minutes=10)
# last_seen_at is refreshed at most this often so read-only requests do not each write a row.
LAST_SEEN_REFRESH_INTERVAL = timedelta(minutes=1)

MAX_TOKEN_LENGTH = 256

ROLE_OWNER = "OWNER"
ROLE_WORKER = "WORKER"
STATUS_ACTIVE = "ACTIVE"
STATUS_SUSPENDED = "SUSPENDED"


@dataclass(frozen=True)
class IssuedSession:
    """A freshly created session. `token` is the only copy of the secret; never log it."""

    token: str = field(repr=False)
    expires_at: datetime

    @property
    def max_age(self) -> int:
        return max(0, int((self.expires_at - utcnow()).total_seconds()))


@dataclass(frozen=True)
class MemberPrincipal:
    user_id: str
    role: str
    session_id: str


@dataclass(frozen=True)
class RegistrationPrincipal:
    registration_id: str
    google_sub: str
    google_email: str
    email_verified: bool


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def cookie_secure() -> bool:
    """Secure cookies everywhere except local HTTP; COOKIE_SECURE=true|false overrides."""
    override = os.getenv("COOKIE_SECURE", "").strip().lower()
    if override in {"true", "false"}:
        return override == "true"
    return os.getenv("APP_ENV", "local") != "local"


@contextmanager
def _transaction(db: Session | None) -> Iterator[Session]:
    """Use the caller's session (the caller commits) or run in a transaction of our own."""
    if db is not None:
        yield db
    else:
        with session_scope() as own:
            yield own


# --- issuing and revoking -------------------------------------------------------------------

def create_session(user_id: str, *, db: Session | None = None) -> IssuedSession:
    """Start a member session. Set the cookie from the result with `set_session_cookie`."""
    token = generate_token()
    now = utcnow()
    expires_at = now + SESSION_ABSOLUTE_LIFETIME
    with _transaction(db) as session:
        session.add(AuthSession(
            token_hash=hash_token(token), user_id=user_id, created_at=now, last_seen_at=now,
            expires_at=expires_at,
        ))
        session.flush()
    return IssuedSession(token, expires_at)


def revoke_session(token: str, *, db: Session | None = None) -> bool:
    """Revoke the session for a cookie token. Unknown or already revoked tokens return False."""
    with _transaction(db) as session:
        result = session.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == hash_token(token), AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return result.rowcount > 0


def revoke_user_sessions(user_id: str, *, db: Session | None = None) -> int:
    """Revoke every live session of a user, e.g. when the account is suspended."""
    with _transaction(db) as session:
        result = session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        return result.rowcount


def create_registration_session(
    google_sub: str, google_email: str, *, email_verified: bool = True, db: Session | None = None,
) -> IssuedSession:
    """Start a 10 minute registration session for a verified Google identity."""
    token = generate_token()
    now = utcnow()
    expires_at = now + REGISTRATION_LIFETIME
    with _transaction(db) as session:
        session.add(RegistrationSession(
            token_hash=hash_token(token), google_sub=google_sub, google_email=google_email,
            email_verified=email_verified, created_at=now, expires_at=expires_at,
        ))
        session.flush()
    return IssuedSession(token, expires_at)


def revoke_registration_session(token: str, *, db: Session | None = None) -> bool:
    """Make a registration session unusable (logout); True if one was live."""
    with _transaction(db) as session:
        result = session.execute(
            update(RegistrationSession)
            .where(
                RegistrationSession.token_hash == hash_token(token),
                RegistrationSession.consumed_at.is_(None),
            )
            .values(consumed_at=utcnow())
        )
        return result.rowcount > 0


def consume_registration_session(registration_id: str, *, db: Session) -> bool:
    """Atomically spend a registration session when registration succeeds.

    Returns False if it was already consumed or has expired, so two concurrent final
    submissions cannot both register the same Google identity.
    """
    now = utcnow()
    result = db.execute(
        update(RegistrationSession)
        .where(
            RegistrationSession.id == registration_id,
            RegistrationSession.consumed_at.is_(None),
            RegistrationSession.expires_at > now,
        )
        .values(consumed_at=now)
    )
    return result.rowcount == 1


# --- cookies --------------------------------------------------------------------------------

def set_session_cookie(response: Response, issued: IssuedSession) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, issued.token, max_age=issued.max_age, path=SESSION_COOKIE_PATH,
        httponly=True, secure=cookie_secure(), samesite=COOKIE_SAMESITE,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME, path=SESSION_COOKIE_PATH, httponly=True, secure=cookie_secure(),
        samesite=COOKIE_SAMESITE,
    )


def set_registration_cookie(response: Response, issued: IssuedSession) -> None:
    response.set_cookie(
        REGISTRATION_COOKIE_NAME, issued.token, max_age=issued.max_age,
        path=REGISTRATION_COOKIE_PATH, httponly=True, secure=cookie_secure(),
        samesite=COOKIE_SAMESITE,
    )


def clear_registration_cookie(response: Response) -> None:
    response.delete_cookie(
        REGISTRATION_COOKIE_NAME, path=REGISTRATION_COOKIE_PATH, httponly=True,
        secure=cookie_secure(), samesite=COOKIE_SAMESITE,
    )


def _cookie_token(request: Request, name: str) -> str | None:
    token = request.cookies.get(name)
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    return token


# --- resolving the current session ----------------------------------------------------------

def _find_registration(db: Session, token: str | None) -> RegistrationPrincipal | None:
    if token is None:
        return None
    row = db.execute(
        select(RegistrationSession).where(RegistrationSession.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if row is None or row.consumed_at is not None or row.expires_at <= utcnow():
        return None
    return RegistrationPrincipal(row.id, row.google_sub, row.google_email, row.email_verified)


def _session_missing(db: Session, request: Request) -> ApiError:
    """401 for a request without a usable member session."""
    if _find_registration(db, _cookie_token(request, REGISTRATION_COOKIE_NAME)) is not None:
        return ApiError(401, ErrorCode.REGISTRATION_REQUIRED)
    return ApiError(401, ErrorCode.SESSION_EXPIRED)


def _resolve_member(request: Request, db: Session) -> MemberPrincipal | None:
    """The member behind the cookie, None when there is no live member session.

    Raises 403 ACCOUNT_SUSPENDED (and revokes the session) for suspended accounts.
    """
    token = _cookie_token(request, SESSION_COOKIE_NAME)
    if token is None:
        return None
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_token(token))
    ).first()
    if row is None:
        return None
    auth_session, user = row
    now = utcnow()
    if (
        auth_session.revoked_at is not None
        or auth_session.expires_at <= now
        or auth_session.last_seen_at + SESSION_IDLE_TIMEOUT <= now
    ):
        return None
    if user.status != STATUS_ACTIVE:
        auth_session.revoked_at = now
        db.commit()  # persist the revocation even though this request fails
        raise ApiError(403, ErrorCode.ACCOUNT_SUSPENDED)
    if now - auth_session.last_seen_at >= LAST_SEEN_REFRESH_INTERVAL:
        auth_session.last_seen_at = now
    return MemberPrincipal(user.id, user.role, auth_session.id)


def optional_member(
    request: Request, db: Annotated[Session, Depends(get_session)],
) -> MemberPrincipal | None:
    """Like `require_member` but anonymous (or registration-only) callers get None."""
    return _resolve_member(request, db)


def require_member(
    request: Request, db: Annotated[Session, Depends(get_session)],
) -> MemberPrincipal:
    """Any ACTIVE member. 401 SESSION_EXPIRED / REGISTRATION_REQUIRED, 403 ACCOUNT_SUSPENDED."""
    member = _resolve_member(request, db)
    if member is None:
        raise _session_missing(db, request)
    return member


def require_owner(
    member: Annotated[MemberPrincipal, Depends(require_member)],
) -> MemberPrincipal:
    """OWNER role only. Store ownership and approval are checked per resource by the endpoint."""
    if member.role != ROLE_OWNER:
        raise ApiError(403, ErrorCode.FORBIDDEN)
    return member


def require_worker(
    member: Annotated[MemberPrincipal, Depends(require_member)],
) -> MemberPrincipal:
    """WORKER role only."""
    if member.role != ROLE_WORKER:
        raise ApiError(403, ErrorCode.FORBIDDEN)
    return member


def optional_registration_session(
    request: Request, db: Annotated[Session, Depends(get_session)],
) -> RegistrationPrincipal | None:
    return _find_registration(db, _cookie_token(request, REGISTRATION_COOKIE_NAME))


def require_registration_session(
    registration: Annotated[
        RegistrationPrincipal | None, Depends(optional_registration_session),
    ],
) -> RegistrationPrincipal:
    """A live (unexpired, unconsumed) registration session; otherwise 401 SESSION_EXPIRED."""
    if registration is None:
        raise ApiError(401, ErrorCode.SESSION_EXPIRED)
    return registration


# Annotated aliases for endpoint signatures: `def handler(member: CurrentMember, db: DbSession)`.
DbSession = Annotated[Session, Depends(get_session)]
CurrentMember = Annotated[MemberPrincipal, Depends(require_member)]
CurrentOwner = Annotated[MemberPrincipal, Depends(require_owner)]
CurrentWorker = Annotated[MemberPrincipal, Depends(require_worker)]
CurrentRegistration = Annotated[RegistrationPrincipal, Depends(require_registration_session)]

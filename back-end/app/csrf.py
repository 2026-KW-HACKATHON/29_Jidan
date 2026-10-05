"""CSRF defence for state-changing requests: exact Origin match plus a synchronizer token.

SameSite=Lax cookies are not enough on their own, so every non-safe request must

1. carry an `Origin` header whose scheme, host and port equal one of the allowed origins, and
2. send the session's token in `X-CSRF-Token` (see `app.auth.csrf_token_for`).

Either failure is `403 CSRF_INVALID`. Allowed origins come from `ALLOWED_ORIGINS`, a comma
separated list such as `https://jidan.example.com,http://localhost:5173`. With the variable
unset or empty every unsafe request is rejected (fail closed).
"""

import hmac
import logging
import os
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, Request

from app.auth import (
    MemberPrincipal,
    RegistrationPrincipal,
    require_member,
    require_member_or_registration,
    require_owner,
    require_registration_session,
    require_worker,
)
from app.errors import ApiError, ErrorCode

logger = logging.getLogger("jidan.csrf")

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(value: str) -> str | None:
    """Canonical `scheme://host:port`, or None if `value` is not a bare origin.

    Scheme and host are case-insensitive and default ports are made explicit. Paths (including
    a trailing slash), queries, fragments, credentials and `null` are not origins and give None,
    so `https://app.example.com.evil.com` or `https://evil.com/https://app.example.com` can never
    be confused with an allowed origin by prefix or substring matching.
    """
    if value != value.strip() or not value:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    scheme, host = parts.scheme.lower(), parts.hostname
    if scheme not in DEFAULT_PORTS or not host or parts.netloc.endswith(":"):
        return None
    if parts.path or parts.query or parts.fragment or "@" in parts.netloc:
        return None
    if "\\" in value:
        return None
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    return f"{scheme}://{host}:{port or DEFAULT_PORTS[scheme]}"


def allowed_origins() -> frozenset[str]:
    """Origins from ALLOWED_ORIGINS, normalized. Invalid entries are skipped and logged."""
    result = set()
    for entry in os.getenv("ALLOWED_ORIGINS", "").split(","):
        entry = entry.strip().removesuffix("/")  # forgive a trailing slash in configuration
        if not entry:
            continue
        origin = normalize_origin(entry)
        if origin is None:
            logger.warning("Ignoring invalid ALLOWED_ORIGINS entry")
            continue
        result.add(origin)
    return frozenset(result)


def _csrf_error() -> ApiError:
    return ApiError(403, ErrorCode.CSRF_INVALID)


def require_allowed_origin(request: Request) -> None:
    """Dependency for unsafe methods: the Origin header must exactly match an allowed origin.

    Use it alone on endpoints without a session (login, admin password check) and implicitly
    through `csrf_protected` everywhere else.
    """
    if request.method.upper() in SAFE_METHODS:
        return
    values = request.headers.getlist("origin")
    if len(values) != 1:  # missing, or ambiguous
        raise _csrf_error()
    origin = normalize_origin(values[0])
    if origin is None or origin not in allowed_origins():
        raise _csrf_error()


def verify_csrf_token(request: Request, expected: str) -> None:
    """Check `X-CSRF-Token` against the session's token (constant time) for unsafe methods."""
    if request.method.upper() in SAFE_METHODS:
        return
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise _csrf_error()


def csrf_protected(
    authenticate: Callable[..., Any],
) -> Callable[..., Any]:
    """Wrap an authentication dependency so unsafe methods also pass Origin and token checks.

    Order: Origin, then authentication (401/403 for the session), then the token. Safe methods
    pass straight through to `authenticate`.
    """

    def dependency(
        request: Request,
        _origin: Annotated[None, Depends(require_allowed_origin)],
        principal: Annotated[Any, Depends(authenticate)],
    ) -> Any:
        verify_csrf_token(request, principal.csrf_token)
        return principal

    return dependency


require_member_csrf = csrf_protected(require_member)
require_owner_csrf = csrf_protected(require_owner)
require_worker_csrf = csrf_protected(require_worker)
require_registration_csrf = csrf_protected(require_registration_session)
require_member_or_registration_csrf = csrf_protected(require_member_or_registration)

# Annotated aliases for write endpoints: `def handler(member: CsrfMember)`.
CsrfMember = Annotated[MemberPrincipal, Depends(require_member_csrf)]
CsrfOwner = Annotated[MemberPrincipal, Depends(require_owner_csrf)]
CsrfWorker = Annotated[MemberPrincipal, Depends(require_worker_csrf)]
CsrfRegistration = Annotated[RegistrationPrincipal, Depends(require_registration_csrf)]
CsrfMemberOrRegistration = Annotated[
    MemberPrincipal | RegistrationPrincipal, Depends(require_member_or_registration_csrf),
]

"""Google OIDC login: one-use DB transaction and opaque browser binding."""
import hmac
import os
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.id_token import verify_oauth2_token
from sqlalchemy import select, update

from app import auth
from app.auth_views import identity
from app.db import SessionDep, utcnow
from app.db.models import AuthSession, OAuthTransaction, RegistrationSession, User
from app.errors import ApiError, ErrorCode, error_response
from app.ratelimit import enforce_login_rate_limit

router = APIRouter(prefix="/api/auth")
OAUTH_COOKIE = "jidan_oauth"
# The specified /api/auth/google cookie cannot reach /api/auth/logout. This companion binding
# permits server-side invalidation at logout without widening the specified cookie's Path.
OAUTH_LOGOUT_COOKIE = "jidan_oauth_logout"
OAUTH_PATH = "/api/auth/google"
OAUTH_LIFETIME = timedelta(minutes=5)


def settings() -> tuple[str, str, str, str]:
    values = tuple(os.getenv(key, "").strip() for key in (
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI", "FRONTEND_ORIGIN",
    ))
    if not all(values):
        raise ApiError(500, ErrorCode.INTERNAL_ERROR)
    for value in values[2:]:
        parsed = urlsplit(value)
        # Redirect targets go into Location headers and must equal the browser's ASCII Origin;
        # an IDN or other non-ASCII value is a misconfiguration, not something to re-encode.
        if (not value.isascii() or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme != "https" and os.getenv("APP_ENV", "local") != "local")):
            raise ApiError(500, ErrorCode.INTERNAL_ERROR)
    if urlsplit(values[3]).path not in {"", "/"}:
        raise ApiError(500, ErrorCode.INTERNAL_ERROR)
    return values[0], values[1], values[2], values[3].rstrip("/")


def clear_oauth_cookies(response: Response) -> None:
    for name, path in ((OAUTH_COOKIE, OAUTH_PATH), (OAUTH_LOGOUT_COOKIE, "/api/auth")):
        response.delete_cookie(name, path=path, httponly=True, secure=auth.cookie_secure(), samesite="lax")


def revoke_oauth(db, token: str | None) -> None:
    if not token or len(token) > auth.MAX_TOKEN_LENGTH:
        return
    row = db.scalar(select(OAuthTransaction).where(
        OAuthTransaction.token_hash == auth.hash_token(token),
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        return
    row.cancelled_at = utcnow()
    # A callback may have committed while its Set-Cookie response is still in flight.
    # Revoke by the recorded IDs, even when the browser has not received those cookies.
    if row.issued_session_id:
        db.execute(update(AuthSession).where(AuthSession.id == row.issued_session_id,
                                            AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    if row.issued_registration_id:
        db.execute(update(RegistrationSession).where(RegistrationSession.id == row.issued_registration_id,
                   RegistrationSession.consumed_at.is_(None)).values(consumed_at=utcnow()))


@router.get("/google")
def start_google(request: Request, db: SessionDep) -> Response:
    enforce_login_rate_limit(request)
    client_id, _, redirect_uri, _ = settings()
    state, nonce, token = (auth.generate_token() for _ in range(3))
    revoke_oauth(db, request.cookies.get(OAUTH_COOKIE))
    db.add(OAuthTransaction(
        token_hash=auth.hash_token(token), state_hash=auth.hash_token(state),
        nonce_hash=auth.hash_token(nonce), expires_at=utcnow() + OAUTH_LIFETIME,
    ))
    db.commit()
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce,
    }), status_code=302)
    for name, path in ((OAUTH_COOKIE, OAUTH_PATH), (OAUTH_LOGOUT_COOKIE, "/api/auth")):
        response.set_cookie(name, token, max_age=300, path=path, httponly=True,
                            secure=auth.cookie_secure(), samesite="lax")
    return response


class BoundedGoogleRequest(GoogleRequest):
    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = 5
        return super().__call__(*args, **kwargs)


def google_identity(code: str, nonce_hash: str) -> dict:
    client_id, secret, redirect_uri, _ = settings()
    try:
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            result = client.post("https://oauth2.googleapis.com/token", data={
                "code": code, "client_id": client_id, "client_secret": secret,
                "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            })
        if result.status_code == 400:
            raise ApiError(400, ErrorCode.OAUTH_CODE_INVALID)
        if result.status_code != 200:
            raise ApiError(502, ErrorCode.GOOGLE_UNAVAILABLE)
        payload = result.json()
        token = payload.get("id_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise ApiError(401, ErrorCode.GOOGLE_IDENTITY_INVALID)
        transport = BoundedGoogleRequest()
        try:
            claims = verify_oauth2_token(token, transport, audience=client_id)
        finally:
            transport.session.close()
    except (httpx.HTTPError, TransportError):
        raise ApiError(502, ErrorCode.GOOGLE_UNAVAILABLE) from None
    except (ValueError, GoogleAuthError, TypeError, KeyError):
        raise ApiError(401, ErrorCode.GOOGLE_IDENTITY_INVALID) from None
    nonce, sub, email = (claims.get(key) for key in ("nonce", "sub", "email"))
    if (not isinstance(nonce, str) or not hmac.compare_digest(auth.hash_token(nonce), nonce_hash)
            or not isinstance(sub, str) or not 1 <= len(sub) <= 255
            or not isinstance(email, str) or not 3 <= len(email) <= 320 or "@" not in email
            or claims.get("email_verified") is not True
            or ("azp" in claims and claims["azp"] != client_id)):
        raise ApiError(401, ErrorCode.GOOGLE_IDENTITY_INVALID)
    return {"sub": sub, "email": email.strip().lower()}


@router.get("/google/callback")
def callback(request: Request, db: SessionDep) -> Response:
    token = request.cookies.get(OAUTH_COOKIE)
    claimed = False
    try:
        enforce_login_rate_limit(request)
        state = request.query_params.get("state", "")
        row = db.scalar(select(OAuthTransaction).where(
            OAuthTransaction.token_hash == auth.hash_token(token or ""),
        )) if token and len(token) <= auth.MAX_TOKEN_LENGTH else None
        valid = (row is not None and row.consumed_at is None and row.cancelled_at is None and row.expires_at > utcnow()
                 and len(request.query_params.getlist("state")) == 1 and 32 <= len(state) <= 512
                 and hmac.compare_digest(row.state_hash, auth.hash_token(state)))
        nonce_hash = row.nonce_hash if valid else ""
        # Even a malformed/error callback spends the browser-bound login attempt.
        if row is not None:
            spent = db.execute(update(OAuthTransaction).where(
                OAuthTransaction.id == row.id, OAuthTransaction.consumed_at.is_(None),
                OAuthTransaction.cancelled_at.is_(None),
            ).values(consumed_at=utcnow())).rowcount
            db.commit()
            valid = valid and spent == 1
            claimed = valid
        if not valid:
            raise ApiError(400, ErrorCode.OAUTH_STATE_INVALID)
        code, error = request.query_params.get("code"), request.query_params.get("error")
        if (any(len(request.query_params.getlist(k)) > 1 for k in request.query_params)
                or (code is None) == (error is None)
                or (code is not None and not 1 <= len(code) <= 4096)
                or (error is not None and not 1 <= len(error) <= 200)):
            raise ApiError(400, ErrorCode.INVALID_REQUEST)
        origin = settings()[3]
        if error:
            if error != "access_denied":
                raise ApiError(400, ErrorCode.OAUTH_CODE_INVALID)
            response = RedirectResponse(origin + "/login?error=GOOGLE_ACCESS_DENIED", status_code=302)
        else:
            verified = google_identity(code, nonce_hash)
            # Lock the same row as logout and re-read after provider I/O. This check and
            # the new session/link commit form one transaction under MySQL as well.
            row = db.scalar(select(OAuthTransaction).where(
                OAuthTransaction.id == row.id,
            ).with_for_update().execution_options(populate_existing=True))
            if row is None or row.cancelled_at is not None or row.expires_at <= utcnow():
                raise ApiError(400, ErrorCode.OAUTH_STATE_INVALID)
            user = db.scalar(select(User).where(User.google_sub == verified["sub"]))
            if user is not None and user.status != "ACTIVE":
                auth.revoke_user_sessions(user.id, db=db)
                db.commit()
                raise ApiError(403, ErrorCode.ACCOUNT_SUSPENDED)
            auth.revoke_session(request.cookies.get(auth.SESSION_COOKIE_NAME, ""), db=db)
            auth.revoke_registration_session(request.cookies.get(auth.REGISTRATION_COOKIE_NAME, ""), db=db)
            if user is None:
                issued = auth.create_registration_session(verified["sub"], verified["email"], db=db)
                row.issued_registration_id = db.scalar(select(RegistrationSession.id).where(
                    RegistrationSession.token_hash == auth.hash_token(issued.token),
                ))
                db.commit()
                response = RedirectResponse(origin + "/__auth/signup", status_code=302)
                auth.set_registration_cookie(response, issued)
                auth.clear_session_cookie(response)
            else:
                user.google_email, user.email_verified = verified["email"], True
                issued = auth.create_session(user.id, db=db)
                row.issued_session_id = db.scalar(select(AuthSession.id).where(
                    AuthSession.token_hash == auth.hash_token(issued.token),
                ))
                db.commit()
                response = RedirectResponse(origin + "/__auth/session", status_code=302)
                auth.set_session_cookie(response, issued)
                auth.clear_registration_cookie(response)
    except Exception as exc:  # noqa: BLE001 - callback must clear browser bindings on all failures
        db.rollback()
        if isinstance(exc, ApiError):
            response = error_response(exc.status_code, exc.code, exc.message, headers=exc.headers)
        else:
            response = error_response(500, ErrorCode.INTERNAL_ERROR, "처리에 실패했습니다.")
        try:
            if claimed:
                revoke_oauth(db, token)
                db.commit()
        except Exception:  # noqa: BLE001 - DB outage must still return cleared cookies
            db.rollback()
            response = error_response(500, ErrorCode.INTERNAL_ERROR, "처리에 실패했습니다.")
    clear_oauth_cookies(response)
    return response


@router.get("/registration")
def registration(principal: auth.CurrentMemberOrRegistration, db: SessionDep) -> dict:
    if isinstance(principal, auth.MemberPrincipal) or db.scalar(select(User.id).where(User.google_sub == principal.google_sub)) is not None:
        raise ApiError(403, ErrorCode.ALREADY_REGISTERED)
    if not principal.email_verified:
        raise ApiError(401, ErrorCode.GOOGLE_IDENTITY_INVALID)
    return {"identity": identity(principal.google_email), "expiresAt": principal.expires_at.isoformat(),
            "allowedRoles": ["WORKER", "OWNER"]}

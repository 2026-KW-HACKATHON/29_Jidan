"""Logout is repeatable and revokes this browser's sessions before deleting cookies."""
from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app import auth
from app.csrf import require_allowed_origin, verify_csrf_token
from app.db import SessionDep, utcnow
from app.db.models import AuthSession, RegistrationSession
from app.oauth import OAUTH_LOGOUT_COOKIE, clear_oauth_cookies, revoke_oauth

router = APIRouter(prefix="/api/auth")


@router.post("/logout", status_code=204)
def logout(request: Request, db: SessionDep) -> Response:
    require_allowed_origin(request)
    now = utcnow()
    csrf = None
    member_token = request.cookies.get(auth.SESSION_COOKIE_NAME, "")
    registration_token = request.cookies.get(auth.REGISTRATION_COOKIE_NAME, "")
    if member_token and len(member_token) <= auth.MAX_TOKEN_LENGTH:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(member_token)))
        if (row and row.revoked_at is None and row.expires_at > now
                and row.last_seen_at + auth.SESSION_IDLE_TIMEOUT > now):
            csrf = auth.csrf_token_for(member_token)
    if csrf is None and registration_token and len(registration_token) <= auth.MAX_TOKEN_LENGTH:
        row = db.scalar(select(RegistrationSession).where(
            RegistrationSession.token_hash == auth.hash_token(registration_token),
        ))
        if row and row.consumed_at is None and row.expires_at > now:
            csrf = auth.csrf_token_for(registration_token)
    if csrf is not None:
        verify_csrf_token(request, csrf)
    auth.revoke_session(member_token, db=db)
    auth.revoke_registration_session(registration_token, db=db)
    revoke_oauth(db, request.cookies.get(OAUTH_LOGOUT_COOKIE))
    db.commit()
    response = Response(status_code=204)
    auth.clear_session_cookie(response)
    auth.clear_registration_cookie(response)
    clear_oauth_cookies(response)
    return response

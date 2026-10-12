"""Login-free, host-bound visitors for the isolated public demo deployment.

The existing session, ownership and manual/QA authorization paths remain authoritative.
No real Google identity can authenticate as a visitor. Reset rotates the entire workspace;
the retired workspace is unreachable and is removed by the demo retention command.
"""
import os
import re
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app import auth
from app.csrf import CsrfMember, require_allowed_origin
from app.db import SessionDep, utcnow
from app.db.models import AuthSession, Store, StoreAccessGrant, StoreInvitation, User
from app.errors import ApiError, ErrorCode
from app.request_id import current_request_id

PREFIX = "demo-visitor:"
LIFETIME = timedelta(hours=2)
SAMPLE_OWNER = str(uuid.uuid5(uuid.NAMESPACE_URL, "jidan-public-demo:owner"))
SAMPLE_STORE = str(uuid.uuid5(uuid.NAMESPACE_URL, "jidan-public-demo:store"))
HOSTS = {"OWNER": "jidan-jumju-demo.leehyowon14.dev",
         "WORKER": "jidan-ilban-demo.leehyowon14.dev"}
# Separate deployment-only contract in docs/public-demo.md; never part of the member API.
router = APIRouter(prefix="/api/demo", include_in_schema=False)


@dataclass(frozen=True)
class DemoConfig:
    role: str
    host: str


def configuration() -> DemoConfig | None:
    role = os.getenv("DEMO_ROLE", "").strip()
    if not role:
        return None
    if role not in HOSTS:
        raise ValueError("DEMO_ROLE must be OWNER or WORKER")
    if os.getenv("APP_ENV", "local") not in {"local", "dev"}:
        raise ValueError("demo requires APP_ENV=local or dev")
    if not os.getenv("DB_NAME", "").endswith(("_demo", "_test")):
        raise ValueError("demo requires a separate *_demo or *_test database")
    if not os.getenv("MEDIA_ROOT", "").strip():
        raise ValueError("demo requires a dedicated MEDIA_ROOT")
    host = os.getenv("DEMO_HOST", HOSTS[role]).strip()
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("DEMO_HOST must be a hostname")
    return DemoConfig(role, host)


def require_demo() -> DemoConfig:
    config = configuration()
    if config is None:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return config


def visitor_store(db: Session, user_id: str, role: str) -> str:
    if role == "WORKER":
        return SAMPLE_STORE
    store = db.scalar(select(Store.id).where(Store.owner_id == user_id))
    if store is None:
        raise ApiError(404, ErrorCode.RESOURCE_NOT_FOUND)
    return store


def new_store(db: Session, owner_id: str, *, store_id: str | None = None) -> Store:
    store = Store(id=store_id or str(uuid.uuid4()), owner_id=owner_id,
                  name="지단 카페 체험 매장", industry="카페", postal_code="01897",
                  address="서울특별시 노원구 광운로 20", phone_number="0200000000",
                  business_registration_number=f"{secrets.randbelow(10**10):010d}",
                  approval_status="APPROVED", approved_at=utcnow())
    db.add(store)
    db.flush()
    return store


def new_visitor(db: Session, response: Response, config: DemoConfig) -> dict:
    now = utcnow()
    identifier = str(uuid.uuid4())
    user = User(id=identifier, google_sub=f"{PREFIX}{identifier}:",
                google_email=f"{identifier}@demo.invalid", email_verified=True,
                role=config.role, name="데모 점주" if config.role == "OWNER" else "데모 근무자",
                phone_number="01000000000")
    db.add(user)
    db.flush()
    if config.role == "OWNER":
        store_id = new_store(db, user.id).id
    else:
        store = db.get(Store, SAMPLE_STORE)
        if store is None:
            raise ApiError(503, ErrorCode.INTERNAL_ERROR, "체험 매장을 준비하고 있어요.")
        store_id = store.id
        invitation = StoreInvitation(store_id=store.id, inviter_owner_id=store.owner_id,
                                     invited_email=user.google_email,
                                     token_hash=auth.hash_token(auth.generate_token()),
                                     expires_at=now + LIFETIME, access_expires_at=now + LIFETIME,
                                     accepted_by_worker_id=user.id, accepted_at=now)
        db.add(invitation)
        db.flush()
        db.add(StoreAccessGrant(store_id=store.id, worker_id=user.id,
                               invitation_id=invitation.id, granted_at=now,
                               valid_until=now + LIFETIME))
    issued = auth.create_session(user.id, db=db)
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == auth.hash_token(issued.token)))
    row.expires_at = now + LIFETIME
    issued = auth.IssuedSession(issued.token, row.expires_at)
    auth.commit_then_set_session_cookie(db, response, issued)
    return {"role": config.role, "storeId": store_id, "expiresAt": issued.expires_at.isoformat(),
            "csrfToken": issued.csrf_token}


@router.post("/session", dependencies=[Depends(require_allowed_origin)])
def start_session(request: Request, response: Response, db: SessionDep) -> dict:
    config = require_demo()
    member = auth.optional_member(request, db)
    if member is not None:
        return {"role": member.role, "storeId": visitor_store(db, member.user_id, member.role),
                "expiresAt": member.expires_at.isoformat(), "csrfToken": member.csrf_token}
    return new_visitor(db, response, config)


@router.post("/reset")
def reset_session(response: Response, member: CsrfMember, db: SessionDep) -> dict:
    config = require_demo()
    db.rollback()
    row = db.scalar(select(AuthSession).where(AuthSession.id == member.session_id)
                    .with_for_update().execution_options(populate_existing=True))
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        raise ApiError(401, ErrorCode.SESSION_EXPIRED)
    row.revoked_at = utcnow()
    return new_visitor(db, response, config)


def allowed_path(path: str, method: str, role: str) -> bool:
    if (path, method) in {("/api/health", "GET"), ("/api/auth/csrf", "GET"),
                          ("/api/demo/session", "POST"), ("/api/demo/reset", "POST")}:
        return True
    match = re.fullmatch(r"/api/stores/[0-9a-fA-F-]{36}/manual(?:/(.*))?", path)
    if not match:
        return False
    rest = match.group(1) or ""
    if role == "WORKER":
        return (method == "GET" and (rest == "published" or rest.startswith("published/sections/"))) or rest.startswith("qa/")
    return not (rest == "qa" or rest.startswith("qa/"))


class DemoBoundary:
    """Fail closed outside the demo paths/host, with bounded per-process public quotas.

    One uvicorn worker per role is required. Existing DB task runner concurrency is also
    bounded. The hourly global write cap cannot be evaded by rotating cookies/IP headers.
    Quotas are conservative: failed/replayed writes count too; no request bodies are consumed.
    """
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.buckets = OrderedDict()

    def consume(self, key: str, limit: int, now: int) -> bool:
        with self.lock:
            bucket = self.buckets.get(key)
            count = bucket[1] if bucket and bucket[0] == now else 0
            if count >= limit:
                return False
            self.buckets[key] = (now, count + 1)
            self.buckets.move_to_end(key)
            while len(self.buckets) > 4096:
                self.buckets.popitem(last=False)
            return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (config := configuration()) is None:
            return await self.app(scope, receive, send)
        request = Request(scope)
        path, method = request.url.path, request.method
        status = None
        code = ErrorCode.FORBIDDEN
        message = "이 체험에서는 사용할 수 없는 기능이에요."
        if path != "/api/health" and request.url.hostname != config.host or not allowed_path(path, method, config.role):
            status = 403
        elif method not in {"GET", "HEAD", "OPTIONS"}:
            hour = int(time.monotonic() // 3600)
            token = request.cookies.get(auth.SESSION_COOKIE_NAME, "")
            key = auth.hash_token(token) if token else (request.client.host if request.client else "anonymous")
            if not self.consume("global", 1000, hour) or not self.consume("visitor:" + key, 150, hour):
                status, code, message = 429, ErrorCode.RATE_LIMITED, "체험 요청이 많아요. 잠시 후 다시 시도해 주세요."
        if status is not None:
            response = JSONResponse({"code": code, "message": message, "requestId": current_request_id(),
                                     "fieldErrors": []}, status_code=status,
                                    headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        await self.app(scope, receive, send)

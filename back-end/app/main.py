import os

import pymysql
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.admin_password_config import HASH_ENV as ADMIN_PASSWORD_HASH_ENV
from app.admin_password_config import parse_password_hash
from app.auth import validate_cookie_settings
from app.auth_views import router as auth_views_router
from app.database import database_status
from app.design_docs import install_design_docs
from app.errors import UnstructuredHTTPException, install_error_handlers
from app.favorite_stores import router as favorite_stores_router
from app.home import router as home_router
from app.interview.routes import router as interview_router
from app.invitation_inbox import router as invitation_inbox_router
from app.invitation_responses import router as invitation_responses_router
from app.invitations import router as invitations_router
from app.jobs import router as jobs_router
from app.lifespan import lifespan
from app.logout import router as logout_router
from app.manual_corrections import router as manual_corrections_router
from app.manual_drafts import router as manual_drafts_router
from app.manual_media import router as manual_media_router
from app.manual_published import router as manual_published_router
from app.middleware import install_middleware
from app.notification_views import router as notification_views_router
from app.oauth import router as oauth_router
from app.qa import router as qa_router
from app.registration import router as registration_router
from app.store_approvals import router as store_approvals_router
from app.store_workers import router as store_workers_router
from app.stores import router as stores_router
from app.worker_profile import router as worker_profile_router
from app.worker_stores import router as worker_stores_router

validate_cookie_settings()  # a bad COOKIE_SECURE stops the process before it serves anything
app = FastAPI(title="Jidan API", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
install_middleware(app)
app.include_router(auth_views_router)
app.include_router(oauth_router)
app.include_router(registration_router)
app.include_router(logout_router)
app.include_router(stores_router)
app.include_router(store_approvals_router)
app.include_router(worker_profile_router)
app.include_router(notification_views_router)
app.include_router(favorite_stores_router)
app.include_router(invitations_router)
app.include_router(invitation_responses_router)
app.include_router(jobs_router)
app.include_router(store_workers_router)
app.include_router(worker_stores_router)
app.include_router(invitation_inbox_router)
app.include_router(home_router)
app.include_router(manual_media_router)
app.include_router(interview_router)
app.include_router(qa_router)
app.include_router(manual_published_router)
app.include_router(manual_drafts_router)
app.include_router(manual_corrections_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    environment = os.getenv("APP_ENV", "local")
    try:
        if environment not in {"local", "dev", "production"}:
            raise ValueError("Invalid environment")
        if environment in {"dev", "production"}:
            # Admin approval cannot work without a valid hash; fail the deploy health gate instead.
            parse_password_hash(os.getenv(ADMIN_PASSWORD_HASH_ENV, ""))
        database = database_status(environment)
    except (pymysql.MySQLError, SQLAlchemyError, OSError, ValueError):
        # Keep connection details and credentials out of public health responses.
        # /api/health keeps its {"detail": ...} body; deploy checks depend on it.
        raise UnstructuredHTTPException(status_code=503, detail="Service unavailable") from None
    return {"status": "ok", "environment": environment, "database": database}


install_design_docs(app)

import os

import pymysql
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.auth import validate_cookie_settings
from app.auth_views import router as auth_views_router
from app.database import database_status
from app.design_docs import install_design_docs
from app.errors import UnstructuredHTTPException, install_error_handlers
from app.logout import router as logout_router
from app.middleware import install_middleware
from app.oauth import router as oauth_router
from app.oauth_cleanup import oauth_cleanup_lifespan
from app.registration import router as registration_router
from app.worker_profile import router as worker_profile_router

validate_cookie_settings()  # a bad COOKIE_SECURE stops the process before it serves anything
app = FastAPI(title="Jidan API", version="0.1.0", lifespan=oauth_cleanup_lifespan)
install_error_handlers(app)
install_middleware(app)
app.include_router(auth_views_router)
app.include_router(oauth_router)
app.include_router(registration_router)
app.include_router(logout_router)
app.include_router(worker_profile_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    environment = os.getenv("APP_ENV", "local")
    try:
        if environment not in {"local", "dev", "production"}:
            raise ValueError("Invalid environment")
        database = database_status(environment)
    except (pymysql.MySQLError, SQLAlchemyError, OSError, ValueError):
        # Keep connection details and credentials out of public health responses.
        # /api/health keeps its {"detail": ...} body; deploy checks depend on it.
        raise UnstructuredHTTPException(status_code=503, detail="Service unavailable") from None
    return {"status": "ok", "environment": environment, "database": database}


install_design_docs(app)

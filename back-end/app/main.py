import os

import pymysql
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.database import database_status
from app.design_docs import install_design_docs
from app.errors import UnstructuredHTTPException, install_error_handlers
from app.middleware import install_middleware

app = FastAPI(title="Jidan API", version="0.1.0")
install_error_handlers(app)
install_middleware(app)


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

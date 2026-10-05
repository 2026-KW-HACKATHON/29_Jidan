import os

import pymysql
from fastapi import FastAPI, HTTPException

from app.database import database_status
from app.design_docs import install_design_docs

app = FastAPI(title="Jidan API", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    environment = os.getenv("APP_ENV", "local")
    try:
        if environment not in {"local", "dev", "production"}:
            raise ValueError("Invalid environment")
        database = database_status(environment)
    except (pymysql.MySQLError, OSError, ValueError):
        # Keep connection details and credentials out of public health responses.
        raise HTTPException(status_code=503, detail="Service unavailable") from None
    return {"status": "ok", "environment": environment, "database": database}


install_design_docs(app)

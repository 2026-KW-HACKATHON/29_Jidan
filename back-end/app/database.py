import os

from sqlalchemy import text

from app.db import get_health_engine


def database_status(environment: str) -> str:
    if environment == "local" and not any(
        os.getenv(f"DB_{key}") for key in ("HOST", "NAME", "USER", "PASSWORD")
    ):
        return "not_configured"
    with get_health_engine().connect() as connection:
        if connection.execute(text("SELECT 1")).scalar() != 1:
            raise ValueError("Unexpected database response")
    return "ok"

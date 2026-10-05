import os
from threading import Lock

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

CONNECT_TIMEOUT_SECONDS = 3


def database_url() -> URL:
    """Build the MySQL URL from DB_* variables; credentials never leave the URL object."""
    values = {key: os.getenv(f"DB_{key.upper()}") for key in ("host", "name", "user", "password")}
    if not all(values.values()):
        raise ValueError("Incomplete database configuration")
    port = int(os.getenv("DB_PORT", "3306"))
    if not 1 <= port <= 65535:
        raise ValueError("Invalid database port")
    return URL.create(
        "mysql+pymysql", username=values["user"], password=values["password"],
        host=values["host"], port=port, database=values["name"], query={"charset": "utf8mb4"},
    )


_engine: Engine | None = None
_engine_lock = Lock()


def get_engine() -> Engine:
    """Return the process-wide engine, created lazily so imports need no DB settings."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = create_engine(
                database_url(), pool_pre_ping=True, pool_recycle=1800,
                connect_args={
                    "connect_timeout": CONNECT_TIMEOUT_SECONDS,
                    "read_timeout": CONNECT_TIMEOUT_SECONDS,
                    "write_timeout": CONNECT_TIMEOUT_SECONDS,
                    # Timestamps are stored in UTC regardless of the server's zone.
                    "init_command": "SET time_zone = '+00:00'",
                },
            )
        return _engine


def reset_engine() -> None:
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)

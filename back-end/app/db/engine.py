import os
from threading import Lock

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

CONNECT_TIMEOUT_SECONDS = 3
# A row lock wait ends as MySQL 1205 after this many seconds (session innodb_lock_wait_timeout),
# which the lock contention handling (`is_lock_contention`, idempotency reservation) expects.
# The client read/write timeout must stay longer: a socket timeout first would surface the wait
# as 2013 "Lost connection" (a 500 nothing retries) and leave the server still waiting.
LOCK_WAIT_ENV = "DB_LOCK_WAIT_TIMEOUT_SECONDS"
READ_TIMEOUT_ENV = "DB_READ_TIMEOUT_SECONDS"
DEFAULT_LOCK_WAIT_SECONDS = 5
DEFAULT_READ_TIMEOUT_SECONDS = 15
MAX_READ_TIMEOUT_SECONDS = 55  # below the reverse proxy's 60 second proxy_read_timeout


def _seconds(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    if not raw.isascii() or not raw.isdigit():
        raise ValueError(f"Invalid {name}")
    return int(raw)


def db_timeouts() -> tuple[int, int]:
    """(lock wait, read/write timeout) in seconds; invalid settings raise ValueError."""
    lock_wait = _seconds(LOCK_WAIT_ENV, DEFAULT_LOCK_WAIT_SECONDS)
    read_timeout = _seconds(READ_TIMEOUT_ENV, DEFAULT_READ_TIMEOUT_SECONDS)
    if not 1 <= lock_wait < read_timeout <= MAX_READ_TIMEOUT_SECONDS:
        raise ValueError(f"Invalid {LOCK_WAIT_ENV} / {READ_TIMEOUT_ENV}")
    return lock_wait, read_timeout


def validate_database_settings() -> None:
    """Fail at startup on malformed timeouts rather than on the first query."""
    db_timeouts()


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
_health_engine: Engine | None = None
_engine_lock = Lock()


def _create(read_timeout: int, lock_wait: int, **pool) -> Engine:
    return create_engine(
        database_url(), pool_pre_ping=True, pool_recycle=1800, hide_parameters=True,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "read_timeout": read_timeout,
            "write_timeout": read_timeout,
            # Timestamps are stored in UTC regardless of the server's zone.
            "init_command": f"SET time_zone = '+00:00', innodb_lock_wait_timeout = {lock_wait}",
        },
        **pool,
    )


def get_engine() -> Engine:
    """Return the process-wide engine, created lazily so imports need no DB settings."""
    global _engine
    with _engine_lock:
        if _engine is None:
            lock_wait, read_timeout = db_timeouts()
            _engine = _create(read_timeout, lock_wait)
        return _engine


def get_health_engine() -> Engine:
    """A one-connection engine for `/api/health`: a stalled database must fail the check within
    the container healthcheck's few seconds, not after the request timeout."""
    global _health_engine
    with _engine_lock:
        if _health_engine is None:
            # SELECT 1 takes no row locks; the lock wait only has to stay below the read timeout.
            _health_engine = _create(CONNECT_TIMEOUT_SECONDS, 1, pool_size=1, max_overflow=0,
                                     pool_timeout=CONNECT_TIMEOUT_SECONDS)
        return _health_engine


def reset_engine() -> None:
    global _engine, _health_engine
    with _engine_lock:
        for engine in (_engine, _health_engine):
            if engine is not None:
                engine.dispose()
        _engine = _health_engine = None


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)

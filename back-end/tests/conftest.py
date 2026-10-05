import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

BACK_END = Path(__file__).resolve().parent.parent
DB_VARIABLES = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")


def alembic_config(connection=None) -> Config:
    config = Config(str(BACK_END / "alembic.ini"))
    config.set_main_option("script_location", str(BACK_END / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def migrated_sqlite_engine() -> Engine:
    """Fresh in-memory SQLite built by the real migrations, with foreign keys enforced."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys = ON")
        dbapi_connection.isolation_level = None  # let SQLAlchemy own BEGIN so SAVEPOINT works

    @event.listens_for(engine, "begin")
    def _begin(connection):
        connection.exec_driver_sql("BEGIN")

    with engine.connect() as connection:
        command.upgrade(alembic_config(connection), "head")
        connection.commit()
    return engine


@pytest.fixture
def engine():
    engine = migrated_sqlite_engine()
    yield engine
    engine.dispose()


@pytest.fixture
def sqlite_session(engine):
    """Each test gets its own database, so tests cannot affect one another."""
    with Session(engine) as session:
        yield session


@pytest.fixture(params=["sqlite", pytest.param("mysql", marks=pytest.mark.mysql)])
def session(request):
    """Runs a test on SQLite always and on the real MySQL test database when configured."""
    yield request.getfixturevalue(f"{request.param}_session")


def mysql_unavailable_reason() -> str | None:
    if not all(os.getenv(key) for key in DB_VARIABLES):
        return "DB_* variables are not set"
    # Schema tests drop and recreate tables, so only a dedicated *_test database may be used.
    if not os.environ["DB_NAME"].endswith("_test"):
        return "DB_NAME must end with _test"
    return None


def mysql_required() -> bool:
    return os.getenv("JIDAN_REQUIRE_MYSQL") == "1"


def pytest_collection_modifyitems(config, items):
    reason = mysql_unavailable_reason()
    if reason is None:
        return
    if mysql_required():
        # A skipped mysql test must never be mistaken for a verified one.
        pytest.exit(f"JIDAN_REQUIRE_MYSQL=1 but mysql tests cannot run: {reason}", returncode=3)
    skip = pytest.mark.skip(reason=f"mysql test skipped: {reason}")
    for item in items:
        if "mysql" in item.keywords:
            item.add_marker(skip)


def pytest_terminal_summary(terminalreporter):
    """Report mysql-marked results separately so a SQLite pass is not read as MySQL coverage."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for outcome in counts:
        for report in terminalreporter.stats.get(outcome, []):
            if "mysql" in getattr(report, "keywords", {}) and (
                report.when == "call" or outcome in ("skipped", "error")
            ):
                counts[outcome] += 1
    ran = counts["passed"] + counts["failed"] + counts["error"]
    terminalreporter.write_sep("-", "mysql-marked tests")
    terminalreporter.write_line(
        f"mysql: ran={ran} passed={counts['passed']} failed={counts['failed']} "
        f"error={counts['error']} skipped={counts['skipped']}"
    )
    if counts["skipped"]:
        terminalreporter.write_line(
            "WARNING: mysql tests were SKIPPED - MySQL is NOT verified "
            f"({mysql_unavailable_reason() or 'see -rs'}). Set JIDAN_REQUIRE_MYSQL=1 to fail instead.",
            yellow=True, bold=True,
        )


def reset_mysql_schema() -> None:
    """Empty the dedicated test database and rebuild it at head through the real migrations."""
    from sqlalchemy import MetaData

    from app.db import get_engine

    engine = get_engine()
    assert engine.url.database and engine.url.database.endswith("_test"), "refusing non-test DB"
    with engine.begin() as connection:
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
        metadata = MetaData()
        metadata.reflect(connection)
        metadata.drop_all(connection)
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")
    command.upgrade(alembic_config(), "head")


@pytest.fixture(scope="session")
def mysql_schema():
    """Known state for every mysql test: a *_test database freshly migrated to head, no rows."""
    from app.db import reset_engine

    reason = mysql_unavailable_reason()
    if reason:
        pytest.skip(f"mysql test skipped: {reason}")
    reset_engine()
    reset_mysql_schema()
    yield
    reset_engine()


@pytest.fixture
def mysql_engine(mysql_schema):
    from app.db import get_engine, reset_engine

    reset_engine()
    engine = get_engine()
    yield engine
    reset_engine()


@pytest.fixture
def mysql_session(mysql_engine):
    """Rolls back at the end, leaving the migrated test schema empty."""
    with mysql_engine.connect() as connection, connection.begin() as transaction:
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()

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


@pytest.fixture(params=["sqlite", pytest.param("mysql", marks=pytest.mark.mysql)])
def db_engine(request, tmp_path, monkeypatch):
    """A migrated database that real request handling can use, on SQLite and on MySQL.

    Unlike `session`, data really commits and several connections coexist, which is what
    authentication, idempotency and concurrency tests need. `app.db.session_scope` and the
    `get_session` dependency are pointed at it. Data is wiped after each test.
    """
    from sqlalchemy.orm import sessionmaker

    from app.db import get_engine, reset_engine
    from app.db.models import Base

    if request.param == "sqlite":
        engine = create_engine(
            f"sqlite:///{tmp_path / 'api.sqlite'}", connect_args={"timeout": 15},
        )

        @event.listens_for(engine, "connect")
        def _foreign_keys(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys = ON")

        with engine.connect() as connection:
            command.upgrade(alembic_config(connection), "head")
            connection.commit()
        monkeypatch.setattr(
            "app.db.session.get_session_factory",
            lambda: sessionmaker(engine, expire_on_commit=False),
        )
        yield engine
        engine.dispose()
        return

    request.getfixturevalue("mysql_schema")
    reset_engine()
    engine = get_engine()
    yield engine
    with engine.begin() as connection:
        # Self references (interview_turns) and the store_manuals <-> manual_versions cycle have
        # no deletion order that satisfies every row-by-row FK check; empty all tables at once.
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")
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


@pytest.fixture
def api(db_engine, monkeypatch):
    """The real application on `db_engine`, with every /api response checked against OpenAPI.

    Writes need `tests.api_contract.login(api, user_id).headers(...)` for Origin and CSRF.
    """
    from tests.api_contract import ORIGIN, ApiContractClient

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", ORIGIN)
    from app.main import app

    with ApiContractClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(autouse=True)
def _no_background_jobs(monkeypatch):
    """Periodic jobs never start in tests: they would sweep the fixed test dates on the real clock.

    Tests of the jobs call them directly with an injected clock.
    """
    monkeypatch.setenv("BACKGROUND_JOBS", "off")


@pytest.fixture(autouse=True)
def fake_ai(request, monkeypatch):
    """Every test talks to a deterministic FakeAiProvider and can never reach the network.

    Tests marked `openai` keep the real environment; they run only when OPENAI_API_KEY and
    JIDAN_RUN_OPENAI=1 are both set, so an exported key alone never makes a default run call
    the API.
    """
    from app.ai import set_ai_provider
    from app.ai.fake import FakeAiProvider

    if "openai" in request.keywords:
        if not os.getenv("OPENAI_API_KEY") or os.getenv("JIDAN_RUN_OPENAI") != "1":
            pytest.skip("real OpenAI test: set OPENAI_API_KEY and JIDAN_RUN_OPENAI=1")
        set_ai_provider(None)
        yield None
        set_ai_provider(None)
        return
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "fake")
    provider = FakeAiProvider()
    set_ai_provider(provider)
    yield provider
    set_ai_provider(None)


@pytest.fixture(autouse=True)
def manual_task_runner(monkeypatch):
    """Background task threads never start in tests; tests run due tasks with `app.tasks.drain`."""
    monkeypatch.setenv("TASK_RUNNER_MODE", "manual")


def pytest_configure(config):
    """`JIDAN_SPEC_COVERAGE=<file>` records which OpenAPI operations/statuses the run exercised
    (tests/spec_coverage.py). Without it nothing is registered and the run is unchanged."""
    from tests.spec_coverage import plugin_from_env

    plugin = plugin_from_env()
    if plugin is not None:
        config.pluginmanager.register(plugin, "jidan-spec-coverage")


from tests.draft_revision import (
    draft_revision_guard,  # noqa: F401 - autouse: content writes bump revision
)
from tests.lock_scope import lock_scope_guard  # noqa: F401 - autouse: MySQL locks only by key

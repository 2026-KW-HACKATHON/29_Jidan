"""Alembic environment. The URL is built from DB_* variables; credentials are never logged.

Tests pass an existing connection through `config.attributes["connection"]`.
"""

from alembic import context
from sqlalchemy import create_engine

from app.db.engine import database_url
from app.db.models import Base

target_metadata = Base.metadata
config = context.config


def run_migrations_offline() -> None:
    context.configure(
        url=database_url().render_as_string(hide_password=False).replace("+pymysql", ""),
        target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        engine = create_engine(
            database_url(), connect_args={"init_command": "SET time_zone = '+00:00'"},
        )
        with engine.connect() as connection:
            _migrate(connection)
        engine.dispose()
    else:
        _migrate(connection)


def _migrate(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

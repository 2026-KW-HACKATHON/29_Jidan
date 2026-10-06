"""Require the whole processing triple, including a non-NULL attempt (B07).

Revision ID: 0041
Revises: 0040

The 0034 CHECK `... AND processing_attempt >= 1` passed rows with kind and task set and attempt
NULL (`NULL >= 1` is UNKNOWN). Existing rows in that state are not guessed at: the upgrade
counts them on both tables first and refuses before any DDL (MySQL DDL is not transactional),
naming the table to repair.
"""
import sqlalchemy as sa
from alembic import context, op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

TABLES = ("interview_sessions", "interview_intent_reviews")
OLD = (
    "(processing_kind IS NULL AND processing_task_id IS NULL AND processing_attempt IS NULL)"
    " OR (processing_kind IS NOT NULL AND processing_task_id IS NOT NULL AND processing_attempt >= 1)"
)
NEW = (
    "(processing_kind IS NULL AND processing_task_id IS NULL AND processing_attempt IS NULL)"
    " OR (processing_kind IS NOT NULL AND processing_task_id IS NOT NULL"
    " AND processing_attempt IS NOT NULL AND processing_attempt >= 1)"
)


def _preflight() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    for table in TABLES:
        incomplete = bind.execute(sa.text(
            f"SELECT COUNT(*) FROM {table} WHERE processing_kind IS NOT NULL"
            " AND processing_task_id IS NOT NULL AND processing_attempt IS NULL"
        )).scalar()
        if incomplete:
            raise RuntimeError(
                f"{table}: {incomplete} row(s) with a processing kind and task but no attempt;"
                " repair them before upgrading to 0041"
            )


def _replace(expression: str) -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(f"ck_{table}_processing"), type_="check")
            batch.create_check_constraint(op.f(f"ck_{table}_processing"), expression)


def upgrade() -> None:
    _preflight()
    _replace(NEW)


def downgrade() -> None:
    _replace(OLD)

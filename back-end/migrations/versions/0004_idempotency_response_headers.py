"""Replayable response headers on idempotency records.

Revision ID: 0004
Revises: 0003

Adds a nullable JSON column holding the allow-listed response headers (app.idempotency
REPLAY_HEADERS, e.g. Location). Rows written before this revision have NULL: no headers.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("idempotency_records", sa.Column("response_headers", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("idempotency_records", "response_headers")

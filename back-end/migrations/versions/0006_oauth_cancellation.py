"""Separate OAuth cancellation and link sessions issued by callbacks.

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    op.add_column("oauth_transactions", sa.Column("cancelled_at", timestamp, nullable=True))
    op.add_column("oauth_transactions", sa.Column("issued_session_id", sa.CHAR(36), nullable=True))
    op.add_column("oauth_transactions", sa.Column("issued_registration_id", sa.CHAR(36), nullable=True))


def downgrade() -> None:
    for column in ("issued_registration_id", "issued_session_id", "cancelled_at"):
        op.drop_column("oauth_transactions", column)

"""Persist single-use OAuth browser transactions.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    case_sensitive = sa.String(64).with_variant(
        sa.String(64, collation="utf8mb4_0900_as_cs"), "mysql",
    )
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    op.create_table(
        "oauth_transactions",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("token_hash", case_sensitive, nullable=False),
        sa.Column("state_hash", case_sensitive, nullable=False),
        sa.Column("nonce_hash", case_sensitive, nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("consumed_at", timestamp, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_transactions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_oauth_transactions_token_hash")),
        sa.UniqueConstraint("state_hash", name=op.f("uq_oauth_transactions_state_hash")),
    )
    op.create_index("ix_oauth_transactions_expires_at", "oauth_transactions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("oauth_transactions")

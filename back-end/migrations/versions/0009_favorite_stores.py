"""Workers' saved (관심) stores.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    op.create_table(
        "favorite_stores",
        sa.Column("worker_id", sa.CHAR(36), nullable=False),
        sa.Column("store_id", sa.CHAR(36), nullable=False),
        sa.Column("saved_at", timestamp, nullable=False),
        sa.ForeignKeyConstraint(["worker_id"], ["users.id"], name=op.f("fk_favorite_stores_worker_id_users")),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name=op.f("fk_favorite_stores_store_id_stores")),
        sa.PrimaryKeyConstraint("worker_id", "store_id", name=op.f("pk_favorite_stores")),
    )
    op.create_index("ix_favorite_stores_worker_id_saved_at", "favorite_stores", ["worker_id", "saved_at"])
    op.create_index("ix_favorite_stores_store_id", "favorite_stores", ["store_id"])


def downgrade() -> None:
    op.drop_table("favorite_stores")

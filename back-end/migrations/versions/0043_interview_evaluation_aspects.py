"""Keep successful evaluation aspects; NULL on old/failed rows means unknown.

Revision ID: 0043
Revises: 0042
"""
import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_evaluations", sa.Column("missing_aspects", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_evaluations", "missing_aspects")

"""Persist optional question guidance without regenerating historical turns.

Revision ID: 0042
Revises: 0041

NULL on legacy QUESTION/ANSWER/CORRECTION rows means no guidance. Response
projection remains disabled until separately configured; this migration adds storage only.
"""
import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interview_turns", sa.Column("guidance", sa.Text(), nullable=True))
    op.add_column("interview_turns", sa.Column("guidance_cards", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_turns", "guidance_cards")
    op.drop_column("interview_turns", "guidance")

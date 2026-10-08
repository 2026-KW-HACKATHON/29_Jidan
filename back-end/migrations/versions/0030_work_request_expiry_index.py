"""Index for finding work requests by deadline (the no-response sweep).

Revision ID: 0030
Revises: 0020
"""
from alembic import op

revision = "0030"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_work_requests_status_expires_at", "work_requests", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_work_requests_status_expires_at", table_name="work_requests")

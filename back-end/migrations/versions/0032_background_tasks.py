"""AI/STT background tasks run outside request transactions (app.jobs).

Revision ID: 0032
Revises: 0030

Frozen copy of the matching model at this point. Never edit it after release; add a new
revision. Timestamps are naive UTC DATETIME(6).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def cs_string(length):
    """Case-sensitive on MySQL (the default utf8mb4_0900_ai_ci folds case)."""
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_0900_as_cs"), "mysql")


UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
TASK_KINDS = (
    "'TRANSCRIPTION', 'INITIAL_QUESTION', 'EVALUATION', 'FOLLOWUP_GENERATION', 'DRAFT_GENERATION', "
    "'REVIEW_UNDERSTANDING', 'REVIEW_CORRECTION', 'DRAFT_CORRECTION', 'QA_ANSWER'"
)

revision = "0032"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_tasks",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("kind", cs_string(32), nullable=False),
        sa.Column("subject_id", sa.CHAR(36), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", cs_string(16), nullable=False),
        sa.Column("tries", sa.Integer(), nullable=False),
        sa.Column("max_tries", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("available_at", UTC_DATETIME, nullable=False),
        sa.Column("lease_token", sa.CHAR(36), nullable=True),
        sa.Column("lease_expires_at", UTC_DATETIME, nullable=True),
        sa.Column("last_error_code", cs_string(32), nullable=True),
        sa.Column("created_at", UTC_DATETIME, nullable=False),
        sa.Column("started_at", UTC_DATETIME, nullable=True),
        sa.Column("finished_at", UTC_DATETIME, nullable=True),
        sa.CheckConstraint(f"kind IN ({TASK_KINDS})", name=op.f("ck_background_tasks_kind")),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name=op.f("ck_background_tasks_status"),
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_background_tasks_attempt")),
        sa.CheckConstraint(
            "tries >= 0 AND max_tries >= 1 AND tries <= max_tries", name=op.f("ck_background_tasks_tries"),
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name=op.f("ck_background_tasks_lease_consistency"),
        ),
        sa.CheckConstraint(
            "(status IN ('SUCCEEDED', 'FAILED', 'CANCELLED')) = (finished_at IS NOT NULL)",
            name=op.f("ck_background_tasks_finished_consistency"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_tasks")),
    )
    op.create_index(
        "ix_background_tasks_status_available_at", "background_tasks", ["status", "available_at"],
    )
    op.create_index("ix_background_tasks_kind_subject_id", "background_tasks", ["kind", "subject_id"])


def downgrade() -> None:
    op.drop_table("background_tasks")

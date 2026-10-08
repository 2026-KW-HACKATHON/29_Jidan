"""24 hour Idempotency-Key records.

Revision ID: 0003
Revises: 0002

Frozen copy of the matching model at this point. Never edit it after release; add a new
revision. Timestamps are naive UTC DATETIME(6).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def cs_string(length):
    """Case-sensitive on MySQL (the default utf8mb4_0900_ai_ci folds case)."""
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_0900_as_cs"), "mysql")


def cs_char(length):
    return sa.CHAR(length).with_variant(sa.CHAR(length, collation="utf8mb4_0900_as_cs"), "mysql")


UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('idempotency_records',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('subject_id', cs_string(64), nullable=False),
    sa.Column('idempotency_key', cs_char(36), nullable=False),
    sa.Column('endpoint', cs_string(255), nullable=False),
    sa.Column('request_hash', cs_string(64), nullable=False),
    sa.Column('state', cs_string(16), nullable=False),
    sa.Column('lock_token', sa.CHAR(length=36), nullable=True),
    sa.Column('locked_until', UTC_DATETIME, nullable=True),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_body', sa.JSON(), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('expires_at', UTC_DATETIME, nullable=False),
    sa.Column('completed_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(state = 'PROCESSING' AND response_status IS NULL) OR (state = 'COMPLETED' AND response_status IS NOT NULL)", name=op.f('ck_idempotency_records_state_consistency')),
    sa.CheckConstraint("state IN ('PROCESSING', 'COMPLETED')", name=op.f('ck_idempotency_records_state')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_idempotency_records')),
    sa.UniqueConstraint('subject_id', 'idempotency_key', name=op.f('uq_idempotency_records_subject_id_idempotency_key'))
    )
    op.create_index('ix_idempotency_records_expires_at', 'idempotency_records', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_table('idempotency_records')

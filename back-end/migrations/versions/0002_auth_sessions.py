"""Server-side member sessions and 10 minute registration sessions.

Revision ID: 0002
Revises: 0001

Frozen copy of the matching models at this point. Never edit it after release; add a new
revision. Only token hashes are stored. Timestamps are naive UTC DATETIME(6).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def cs_string(length):
    """Case-sensitive on MySQL (the default utf8mb4_0900_ai_ci folds case)."""
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_0900_as_cs"), "mysql")


UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('auth_sessions',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('token_hash', cs_string(64), nullable=False),
    sa.Column('user_id', sa.CHAR(length=36), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('last_seen_at', UTC_DATETIME, nullable=False),
    sa.Column('expires_at', UTC_DATETIME, nullable=False),
    sa.Column('revoked_at', UTC_DATETIME, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_auth_sessions_token_hash'))
    )
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'], unique=False)
    op.create_table('registration_sessions',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('token_hash', cs_string(64), nullable=False),
    sa.Column('google_sub', cs_string(255), nullable=False),
    sa.Column('google_email', sa.String(length=320), nullable=False),
    sa.Column('email_verified', sa.Boolean(), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('expires_at', UTC_DATETIME, nullable=False),
    sa.Column('consumed_at', UTC_DATETIME, nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_registration_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_registration_sessions_token_hash'))
    )
    op.create_index('ix_registration_sessions_expires_at', 'registration_sessions', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_table('registration_sessions')
    op.drop_table('auth_sessions')

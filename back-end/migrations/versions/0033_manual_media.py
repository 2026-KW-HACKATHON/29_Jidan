"""Owner/worker manual media and their transcriptions (docs/erd/manual.md, qa.md).

Revision ID: 0033
Revises: 0032

Frozen copy of the matching models at this point. Never edit it after release; add a new
revision. Timestamps are naive UTC DATETIME(6).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


def cs_string(length):
    """Case-sensitive on MySQL (the default utf8mb4_0900_ai_ci folds case)."""
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_0900_as_cs"), "mysql")


# Frozen copy of app.db.checks.not_blank (Unicode White_Space aware on both databases).
WHITESPACE = "char(9,10,11,12,13,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)"


def not_blank(name, column, nullable=False):
    prefix = f"{column} IS NULL OR " if nullable else ""
    is_mysql = op.get_context().dialect.name in ("mysql", "mariadb")
    sql = (f"{prefix}REGEXP_LIKE({column}, '[^[:space:]]')" if is_mysql
           else f"{prefix}TRIM({column}, {WHITESPACE}) <> ''")
    return sa.CheckConstraint(sql, name=op.f(name))


UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('manual_media',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('uploaded_by_owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('kind', cs_string(8), nullable=False),
    sa.Column('object_key', cs_string(200), nullable=False),
    sa.Column('mime_type', cs_string(32), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('expires_at', UTC_DATETIME, nullable=False),
    sa.Column('deleted_at', UTC_DATETIME, nullable=True),
    sa.Column('content_deleted_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(kind = 'IMAGE' AND mime_type IN ('image/jpeg', 'image/png', 'image/webp') AND byte_size <= 10485760 AND duration_ms IS NULL) OR (kind = 'AUDIO' AND mime_type IN ('audio/mpeg', 'audio/mp4', 'audio/webm', 'audio/wav') AND byte_size <= 20971520 AND duration_ms IS NOT NULL AND duration_ms >= 1 AND duration_ms <= 120000)", name=op.f('ck_manual_media_media_shape')),
    sa.CheckConstraint("kind IN ('IMAGE', 'AUDIO')", name=op.f('ck_manual_media_kind')),
    sa.CheckConstraint('byte_size >= 1', name=op.f('ck_manual_media_byte_size')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_manual_media_expiry')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_manual_media_store_id_stores')),
    sa.ForeignKeyConstraint(['uploaded_by_owner_id'], ['users.id'], name=op.f('fk_manual_media_uploaded_by_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_media')),
    sa.UniqueConstraint('object_key', name=op.f('uq_manual_media_object_key'))
    )
    op.create_index('ix_manual_media_content_deleted_at_expires_at', 'manual_media', ['content_deleted_at', 'expires_at'], unique=False)
    op.create_index('ix_manual_media_store_id', 'manual_media', ['store_id'], unique=False)
    op.create_table('qa_media',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('worker_id', sa.CHAR(length=36), nullable=False),
    sa.Column('kind', cs_string(8), nullable=False),
    sa.Column('object_key', cs_string(200), nullable=False),
    sa.Column('mime_type', cs_string(32), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('expires_at', UTC_DATETIME, nullable=False),
    sa.Column('deleted_at', UTC_DATETIME, nullable=True),
    sa.Column('content_deleted_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(kind = 'IMAGE' AND mime_type IN ('image/jpeg', 'image/png', 'image/webp') AND byte_size <= 10485760 AND duration_ms IS NULL) OR (kind = 'AUDIO' AND mime_type IN ('audio/mpeg', 'audio/mp4', 'audio/webm', 'audio/wav') AND byte_size <= 20971520 AND duration_ms IS NOT NULL AND duration_ms >= 1 AND duration_ms <= 120000)", name=op.f('ck_qa_media_media_shape')),
    sa.CheckConstraint("kind IN ('IMAGE', 'AUDIO')", name=op.f('ck_qa_media_kind')),
    sa.CheckConstraint('byte_size >= 1', name=op.f('ck_qa_media_byte_size')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_qa_media_expiry')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_qa_media_store_id_stores')),
    sa.ForeignKeyConstraint(['worker_id'], ['users.id'], name=op.f('fk_qa_media_worker_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_qa_media')),
    sa.UniqueConstraint('object_key', name=op.f('uq_qa_media_object_key'))
    )
    op.create_index('ix_qa_media_content_deleted_at_expires_at', 'qa_media', ['content_deleted_at', 'expires_at'], unique=False)
    op.create_index('ix_qa_media_store_id', 'qa_media', ['store_id'], unique=False)
    op.create_table('media_transcriptions',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('manual_media_id', sa.CHAR(length=36), nullable=True),
    sa.Column('qa_media_id', sa.CHAR(length=36), nullable=True),
    sa.Column('status', cs_string(16), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('error_code', cs_string(32), nullable=True),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.CHAR(length=36), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.Column('completed_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(status = 'RUNNING' AND text IS NULL AND error_code IS NULL AND completed_at IS NULL AND task_id IS NOT NULL) OR (status = 'READY' AND text IS NOT NULL AND error_code IS NULL AND completed_at IS NOT NULL) OR (status = 'ERROR' AND text IS NULL AND error_code IS NOT NULL AND completed_at IS NOT NULL)", name=op.f('ck_media_transcriptions_status_consistency')),
    sa.CheckConstraint("error_code = 'TRANSCRIPTION_FAILED'", name=op.f('ck_media_transcriptions_error_code')),
    sa.CheckConstraint("status IN ('RUNNING', 'READY', 'ERROR')", name=op.f('ck_media_transcriptions_status')),
    not_blank('ck_media_transcriptions_text_not_blank', 'text', nullable=True),
    sa.CheckConstraint('(manual_media_id IS NULL) <> (qa_media_id IS NULL)', name=op.f('ck_media_transcriptions_media_xor')),
    sa.CheckConstraint('attempt >= 1', name=op.f('ck_media_transcriptions_attempt')),
    sa.ForeignKeyConstraint(['manual_media_id'], ['manual_media.id'], name=op.f('fk_media_transcriptions_manual_media_id_manual_media')),
    sa.ForeignKeyConstraint(['qa_media_id'], ['qa_media.id'], name=op.f('fk_media_transcriptions_qa_media_id_qa_media')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_media_transcriptions_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_media_transcriptions')),
    sa.UniqueConstraint('manual_media_id', name=op.f('uq_media_transcriptions_manual_media_id')),
    sa.UniqueConstraint('qa_media_id', name=op.f('uq_media_transcriptions_qa_media_id'))
    )
    op.create_index('ix_media_transcriptions_store_id', 'media_transcriptions', ['store_id'], unique=False)


def downgrade() -> None:
    op.drop_table('media_transcriptions')
    op.drop_table('qa_media')
    op.drop_table('manual_media')

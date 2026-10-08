"""Worker AI Q&A conversations, questions, citations and question photos (docs/erd/qa.md).

Revision ID: 0035
Revises: 0034

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

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('manual_qa_conversations',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('worker_id', sa.CHAR(length=36), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_manual_qa_conversations_store_id_stores')),
    sa.ForeignKeyConstraint(['worker_id'], ['users.id'], name=op.f('fk_manual_qa_conversations_worker_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_qa_conversations'))
    )
    op.create_index('ix_manual_qa_conversations_store_worker_updated', 'manual_qa_conversations', ['store_id', 'worker_id', 'updated_at'], unique=False)
    op.create_table('manual_qa',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('conversation_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('published_version_id', sa.CHAR(length=36), nullable=False),
    sa.Column('input_method', cs_string(8), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('transcription_id', sa.CHAR(length=36), nullable=True),
    sa.Column('status', cs_string(16), nullable=False),
    sa.Column('outcome', cs_string(16), nullable=True),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('public_error_code', cs_string(32), nullable=True),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.CHAR(length=36), nullable=True),
    sa.Column('asked_at', UTC_DATETIME, nullable=False),
    sa.Column('completed_at', UTC_DATETIME, nullable=True),
    sa.Column('running_conversation_id', sa.CHAR(length=36), sa.Computed("CASE WHEN status = 'RUNNING' THEN conversation_id END", persisted=True), nullable=True),
    sa.CheckConstraint("(input_method = 'VOICE') = (transcription_id IS NOT NULL)", name=op.f('ck_manual_qa_voice_source')),
    sa.CheckConstraint("(status = 'RUNNING' AND outcome IS NULL AND answer IS NULL AND public_error_code IS NULL AND completed_at IS NULL AND task_id IS NOT NULL) OR (status = 'READY' AND outcome IS NOT NULL AND answer IS NOT NULL AND public_error_code IS NULL AND completed_at IS NOT NULL) OR (status = 'ERROR' AND outcome IS NULL AND answer IS NULL AND public_error_code IS NOT NULL AND completed_at IS NOT NULL)", name=op.f('ck_manual_qa_status_consistency')),
    not_blank('ck_manual_qa_question_not_blank', 'question'),
    not_blank('ck_manual_qa_answer_not_blank', 'answer', nullable=True),
    sa.CheckConstraint("input_method IN ('TEXT', 'VOICE')", name=op.f('ck_manual_qa_input_method')),
    sa.CheckConstraint("outcome IN ('ANSWERED', 'NEEDS_OWNER')", name=op.f('ck_manual_qa_outcome')),
    sa.CheckConstraint("public_error_code IN ('AI_PROCESSING_FAILED', 'TRANSCRIPTION_FAILED')", name=op.f('ck_manual_qa_public_error_code')),
    sa.CheckConstraint("status IN ('RUNNING', 'READY', 'ERROR')", name=op.f('ck_manual_qa_status')),
    sa.CheckConstraint('sequence >= 1 AND attempt >= 1', name=op.f('ck_manual_qa_sequence_attempt')),
    sa.ForeignKeyConstraint(['conversation_id'], ['manual_qa_conversations.id'], name=op.f('fk_manual_qa_conversation_id_manual_qa_conversations')),
    sa.ForeignKeyConstraint(['published_version_id'], ['manual_versions.id'], name=op.f('fk_manual_qa_published_version_id_manual_versions')),
    sa.ForeignKeyConstraint(['transcription_id'], ['media_transcriptions.id'], name=op.f('fk_manual_qa_transcription_id_media_transcriptions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_qa')),
    sa.UniqueConstraint('conversation_id', 'sequence', name=op.f('uq_manual_qa_conversation_id_sequence')),
    sa.UniqueConstraint('running_conversation_id', name=op.f('uq_manual_qa_running_conversation_id'))
    )
    op.create_table('manual_qa_photos',
    sa.Column('qa_id', sa.CHAR(length=36), nullable=False),
    sa.Column('media_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.CheckConstraint('sort_order >= 0 AND sort_order < 3', name=op.f('ck_manual_qa_photos_sort_order')),
    sa.ForeignKeyConstraint(['media_id'], ['qa_media.id'], name=op.f('fk_manual_qa_photos_media_id_qa_media')),
    sa.ForeignKeyConstraint(['qa_id'], ['manual_qa.id'], name=op.f('fk_manual_qa_photos_qa_id_manual_qa')),
    sa.PrimaryKeyConstraint('qa_id', 'media_id', name=op.f('pk_manual_qa_photos')),
    sa.UniqueConstraint('qa_id', 'sort_order', name=op.f('uq_manual_qa_photos_qa_id_sort_order'))
    )
    op.create_index('ix_manual_qa_photos_media_id', 'manual_qa_photos', ['media_id'], unique=False)
    op.create_table('manual_qa_citations',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('qa_id', sa.CHAR(length=36), nullable=False),
    sa.Column('section_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('excerpt', sa.Text(), nullable=False),
    not_blank('ck_manual_qa_citations_excerpt_not_blank', 'excerpt'),
    sa.CheckConstraint('sort_order >= 0 AND sort_order < 10', name=op.f('ck_manual_qa_citations_sort_order')),
    sa.ForeignKeyConstraint(['qa_id'], ['manual_qa.id'], name=op.f('fk_manual_qa_citations_qa_id_manual_qa')),
    sa.ForeignKeyConstraint(['section_id'], ['manual_sections.id'], name=op.f('fk_manual_qa_citations_section_id_manual_sections')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_qa_citations')),
    sa.UniqueConstraint('qa_id', 'section_id', name=op.f('uq_manual_qa_citations_qa_id_section_id')),
    sa.UniqueConstraint('qa_id', 'sort_order', name=op.f('uq_manual_qa_citations_qa_id_sort_order'))
    )
    op.create_index('ix_manual_qa_citations_section_id', 'manual_qa_citations', ['section_id'], unique=False)


def downgrade() -> None:
    op.drop_table('manual_qa_citations')
    op.drop_table('manual_qa_photos')
    op.drop_table('manual_qa')
    op.drop_table('manual_qa_conversations')

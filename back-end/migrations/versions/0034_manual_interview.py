"""Manuals, versions and content, AI interview, reviews, issues and draft corrections.

Revision ID: 0034
Revises: 0033

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

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('interview_question_sets',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('revision_no', sa.Integer(), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.CheckConstraint('revision_no >= 1', name=op.f('ck_interview_question_sets_revision_no')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_question_sets')),
    sa.UniqueConstraint('revision_no', name=op.f('uq_interview_question_sets_revision_no'))
    )
    op.create_table('interview_intents',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('question_set_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('intent_key', cs_string(100), nullable=False),
    sa.Column('stage', cs_string(16), nullable=False),
    sa.Column('base_question', sa.Text(), nullable=False),
    sa.Column('coverage_criteria', sa.Text(), nullable=False),
    not_blank('ck_interview_intents_base_question_not_blank', 'base_question'),
    not_blank('ck_interview_intents_coverage_criteria_not_blank', 'coverage_criteria'),
    sa.CheckConstraint("stage IN ('WORK_STRUCTURE', 'COMMON_TASKS', 'SHIFT_TASKS', 'COMPLEMENTS')", name=op.f('ck_interview_intents_stage')),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_interview_intents_sort_order')),
    sa.ForeignKeyConstraint(['question_set_id'], ['interview_question_sets.id'], name=op.f('fk_interview_intents_question_set_id_interview_question_sets')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_intents')),
    sa.UniqueConstraint('question_set_id', 'intent_key', name=op.f('uq_interview_intents_question_set_id_intent_key')),
    sa.UniqueConstraint('question_set_id', 'sort_order', name=op.f('uq_interview_intents_question_set_id_sort_order'))
    )
    op.create_table('store_manuals',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('current_published_version_id', sa.CHAR(length=36), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.ForeignKeyConstraint(['current_published_version_id'], ['manual_versions.id'], name='fk_store_manuals_current_published_version_id', use_alter=True),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_store_manuals_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_store_manuals')),
    sa.UniqueConstraint('store_id', name=op.f('uq_store_manuals_store_id'))
    )
    op.create_table('manual_media_snapshot_refs',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('media_id', sa.CHAR(length=36), nullable=False),
    sa.Column('holder_kind', cs_string(32), nullable=False),
    sa.Column('holder_id', sa.CHAR(length=36), nullable=False),
    sa.Column('holder_intent_id', sa.CHAR(length=36), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.CheckConstraint("holder_kind IN ('INTENT_REVIEW', 'REVIEW_CONFIRMATION', 'DRAFT_GENERATION', 'DRAFT_CORRECTION')", name=op.f('ck_manual_media_snapshot_refs_holder_kind')),
    sa.ForeignKeyConstraint(['media_id'], ['manual_media.id'], name=op.f('fk_manual_media_snapshot_refs_media_id_manual_media')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_media_snapshot_refs'))
    )
    op.create_index('ix_manual_media_snapshot_refs_holder', 'manual_media_snapshot_refs', ['holder_kind', 'holder_id', 'holder_intent_id'], unique=False)
    op.create_index('ix_manual_media_snapshot_refs_media_id', 'manual_media_snapshot_refs', ['media_id'], unique=False)
    op.create_table('manual_versions',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('manual_id', sa.CHAR(length=36), nullable=False),
    sa.Column('revision_no', sa.Integer(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('content_revision', sa.Integer(), nullable=False),
    sa.Column('status', cs_string(16), nullable=False),
    sa.Column('generation_status', cs_string(16), nullable=False),
    sa.Column('generation_input_snapshot', sa.JSON(), nullable=True),
    sa.Column('created_by_owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.Column('published_at', UTC_DATETIME, nullable=True),
    sa.Column('published_by_owner_id', sa.CHAR(length=36), nullable=True),
    sa.Column('active_draft_manual_id', sa.CHAR(length=36), sa.Computed("CASE WHEN status = 'DRAFT' THEN manual_id END", persisted=True), nullable=True),
    sa.CheckConstraint("(status = 'DRAFT' AND published_at IS NULL AND published_by_owner_id IS NULL) OR (status = 'PUBLISHED' AND published_at IS NOT NULL AND published_by_owner_id IS NOT NULL AND generation_status = 'READY')", name=op.f('ck_manual_versions_publication')),
    sa.CheckConstraint("generation_status IN ('NOT_STARTED', 'RUNNING', 'READY', 'ERROR')", name=op.f('ck_manual_versions_generation_status')),
    sa.CheckConstraint("status IN ('DRAFT', 'PUBLISHED')", name=op.f('ck_manual_versions_status')),
    sa.CheckConstraint('revision_no >= 1 AND revision >= 1 AND content_revision >= 1', name=op.f('ck_manual_versions_revisions')),
    sa.ForeignKeyConstraint(['created_by_owner_id'], ['users.id'], name=op.f('fk_manual_versions_created_by_owner_id_users')),
    sa.ForeignKeyConstraint(['manual_id'], ['store_manuals.id'], name=op.f('fk_manual_versions_manual_id_store_manuals')),
    sa.ForeignKeyConstraint(['published_by_owner_id'], ['users.id'], name=op.f('fk_manual_versions_published_by_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_versions')),
    sa.UniqueConstraint('active_draft_manual_id', name=op.f('uq_manual_versions_active_draft_manual_id')),
    sa.UniqueConstraint('manual_id', 'revision_no', name=op.f('uq_manual_versions_manual_id_revision_no'))
    )
    op.create_table('interview_sessions',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('manual_version_id', sa.CHAR(length=36), nullable=False),
    sa.Column('owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('question_set_id', sa.CHAR(length=36), nullable=False),
    sa.Column('status', cs_string(16), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('current_intent_id', sa.CHAR(length=36), nullable=True),
    sa.Column('processing_kind', cs_string(32), nullable=True),
    sa.Column('processing_task_id', sa.CHAR(length=36), nullable=True),
    sa.Column('processing_attempt', sa.Integer(), nullable=True),
    sa.Column('error_code', cs_string(32), nullable=True),
    sa.Column('started_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.Column('completed_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(status = 'COMPLETED') = (completed_at IS NOT NULL)", name=op.f('ck_interview_sessions_completed')),
    sa.CheckConstraint("(status = 'ERROR') = (error_code IS NOT NULL) AND (status <> 'ERROR' OR processing_task_id IS NOT NULL) AND (status <> 'COMPLETED' OR (processing_task_id IS NULL AND current_intent_id IS NULL))", name=op.f('ck_interview_sessions_status_consistency')),
    sa.CheckConstraint("error_code IN ('AI_PROCESSING_FAILED', 'TRANSCRIPTION_FAILED')", name=op.f('ck_interview_sessions_error_code')),
    sa.CheckConstraint("processing_kind IN ('INITIAL_QUESTION', 'EVALUATION', 'FOLLOWUP_GENERATION', 'DRAFT_GENERATION')", name=op.f('ck_interview_sessions_processing_kind')),
    sa.CheckConstraint("status IN ('IN_PROGRESS', 'ERROR', 'COMPLETED')", name=op.f('ck_interview_sessions_status')),
    sa.CheckConstraint('(processing_kind IS NULL AND processing_task_id IS NULL AND processing_attempt IS NULL) OR (processing_kind IS NOT NULL AND processing_task_id IS NOT NULL AND processing_attempt >= 1)', name=op.f('ck_interview_sessions_processing')),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_interview_sessions_revision')),
    sa.ForeignKeyConstraint(['current_intent_id'], ['interview_intents.id'], name=op.f('fk_interview_sessions_current_intent_id_interview_intents')),
    sa.ForeignKeyConstraint(['manual_version_id'], ['manual_versions.id'], name=op.f('fk_interview_sessions_manual_version_id_manual_versions')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_interview_sessions_owner_id_users')),
    sa.ForeignKeyConstraint(['question_set_id'], ['interview_question_sets.id'], name=op.f('fk_interview_sessions_question_set_id_interview_question_sets')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_sessions')),
    sa.UniqueConstraint('manual_version_id', name=op.f('uq_interview_sessions_manual_version_id'))
    )
    op.create_index('ix_interview_sessions_owner_id', 'interview_sessions', ['owner_id'], unique=False)
    op.create_table('manual_draft_corrections',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('version_id', sa.CHAR(length=36), nullable=False),
    sa.Column('base_revision', sa.Integer(), nullable=False),
    sa.Column('target_kind', cs_string(8), nullable=False),
    sa.Column('target_id', sa.CHAR(length=36), nullable=True),
    sa.Column('input_method', cs_string(8), nullable=False),
    sa.Column('input_text', sa.Text(), nullable=False),
    sa.Column('transcription_id', sa.CHAR(length=36), nullable=True),
    sa.Column('status', cs_string(16), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.CHAR(length=36), nullable=True),
    sa.Column('result_revision', sa.Integer(), nullable=True),
    sa.Column('error_code', cs_string(40), nullable=True),
    sa.Column('requested_by_owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.Column('completed_at', UTC_DATETIME, nullable=True),
    sa.Column('running_version_id', sa.CHAR(length=36), sa.Computed("CASE WHEN status = 'RUNNING' THEN version_id END", persisted=True), nullable=True),
    sa.CheckConstraint("(input_method = 'VOICE') = (transcription_id IS NOT NULL)", name=op.f('ck_manual_draft_corrections_voice_source')),
    sa.CheckConstraint("(status = 'RUNNING' AND result_revision IS NULL AND error_code IS NULL AND completed_at IS NULL AND task_id IS NOT NULL) OR (status = 'SUCCEEDED' AND result_revision IS NOT NULL AND error_code IS NULL AND completed_at IS NOT NULL) OR (status = 'ERROR' AND result_revision IS NULL AND error_code IS NOT NULL AND completed_at IS NOT NULL)", name=op.f('ck_manual_draft_corrections_status_consistency')),
    sa.CheckConstraint("(target_kind = 'MANUAL') = (target_id IS NULL)", name=op.f('ck_manual_draft_corrections_target')),
    not_blank('ck_manual_draft_corrections_input_text_not_blank', 'input_text'),
    sa.CheckConstraint("error_code IN ('AI_PROCESSING_FAILED', 'CORRECTION_CLARIFICATION_REQUIRED', 'MANUAL_REFERENCE_CONFLICT', 'MANUAL_VERSION_CONFLICT', 'REVISION_CONFLICT')", name=op.f('ck_manual_draft_corrections_error_code')),
    sa.CheckConstraint("input_method IN ('TEXT', 'VOICE')", name=op.f('ck_manual_draft_corrections_input_method')),
    sa.CheckConstraint("status IN ('RUNNING', 'SUCCEEDED', 'ERROR')", name=op.f('ck_manual_draft_corrections_status')),
    sa.CheckConstraint("target_kind IN ('MANUAL', 'SHIFT', 'SECTION')", name=op.f('ck_manual_draft_corrections_target_kind')),
    sa.CheckConstraint('base_revision >= 1 AND attempt >= 1', name=op.f('ck_manual_draft_corrections_revisions')),
    sa.ForeignKeyConstraint(['requested_by_owner_id'], ['users.id'], name=op.f('fk_manual_draft_corrections_requested_by_owner_id_users')),
    sa.ForeignKeyConstraint(['transcription_id'], ['media_transcriptions.id'], name='fk_manual_draft_corrections_transcription_id'),
    sa.ForeignKeyConstraint(['version_id'], ['manual_versions.id'], name=op.f('fk_manual_draft_corrections_version_id_manual_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_draft_corrections')),
    sa.UniqueConstraint('running_version_id', name=op.f('uq_manual_draft_corrections_running_version_id'))
    )
    op.create_index('ix_manual_draft_corrections_version_id_created_at', 'manual_draft_corrections', ['version_id', 'created_at'], unique=False)
    op.create_table('manual_review_issues',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('version_id', sa.CHAR(length=36), nullable=False),
    sa.Column('intent_id', sa.CHAR(length=36), nullable=True),
    sa.Column('description', sa.String(length=1000), nullable=False),
    sa.Column('target_kind', cs_string(8), nullable=True),
    sa.Column('target_id', sa.CHAR(length=36), nullable=True),
    sa.Column('field_name', cs_string(16), nullable=True),
    sa.Column('public_description', sa.String(length=300), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('resolved_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(target_kind IS NULL AND target_id IS NULL AND field_name IS NULL AND public_description IS NULL) OR (target_kind = 'MANUAL' AND target_id IS NULL AND field_name IN ('shifts', 'sections') AND public_description IS NOT NULL) OR (target_kind = 'SHIFT' AND target_id IS NOT NULL AND field_name IN ('startTime', 'endTime', 'endsNextDay') AND public_description IS NOT NULL) OR (target_kind = 'SECTION' AND target_id IS NOT NULL AND field_name = 'steps' AND public_description IS NOT NULL)", name=op.f('ck_manual_review_issues_target_shape')),
    not_blank('ck_manual_review_issues_description_not_blank', 'description'),
    not_blank('ck_manual_review_issues_public_description_not_blank', 'public_description', nullable=True),
    sa.CheckConstraint("target_kind IN ('MANUAL', 'SHIFT', 'SECTION')", name=op.f('ck_manual_review_issues_target_kind')),
    sa.ForeignKeyConstraint(['intent_id'], ['interview_intents.id'], name=op.f('fk_manual_review_issues_intent_id_interview_intents')),
    sa.ForeignKeyConstraint(['version_id'], ['manual_versions.id'], name=op.f('fk_manual_review_issues_version_id_manual_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_review_issues'))
    )
    op.create_index('ix_manual_review_issues_version_id', 'manual_review_issues', ['version_id'], unique=False)
    op.create_table('manual_shifts',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('version_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=True),
    sa.Column('end_time', sa.Time(), nullable=True),
    sa.Column('ends_next_day', sa.Boolean(), nullable=True),
    not_blank('ck_manual_shifts_name_not_blank', 'name'),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_manual_shifts_sort_order')),
    sa.CheckConstraint('start_time IS NULL OR end_time IS NULL OR ends_next_day IS NULL OR ((ends_next_day = 0 AND end_time > start_time) OR (ends_next_day = 1 AND end_time <= start_time))', name=op.f('ck_manual_shifts_time_span')),
    sa.ForeignKeyConstraint(['version_id'], ['manual_versions.id'], name=op.f('fk_manual_shifts_version_id_manual_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_shifts')),
    sa.UniqueConstraint('id', 'version_id', name=op.f('uq_manual_shifts_id_version_id')),
    sa.UniqueConstraint('version_id', 'sort_order', name=op.f('uq_manual_shifts_version_id_sort_order'))
    )
    op.create_table('interview_session_intents',
    sa.Column('session_id', sa.CHAR(length=36), nullable=False),
    sa.Column('intent_id', sa.CHAR(length=36), nullable=False),
    sa.Column('coverage_status', cs_string(16), nullable=False),
    sa.Column('depth', sa.Integer(), nullable=False),
    sa.Column('coverage_note', sa.Text(), nullable=True),
    sa.Column('covered_at', UTC_DATETIME, nullable=True),
    sa.Column('finished_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(coverage_status = 'PENDING' AND finished_at IS NULL AND covered_at IS NULL) OR (coverage_status = 'COVERED' AND finished_at IS NOT NULL AND covered_at IS NOT NULL) OR (coverage_status = 'NEEDS_DETAIL' AND depth = 5 AND finished_at IS NOT NULL AND covered_at IS NULL)", name=op.f('ck_interview_session_intents_coverage_consistency')),
    sa.CheckConstraint("coverage_status IN ('PENDING', 'NEEDS_DETAIL', 'COVERED')", name=op.f('ck_interview_session_intents_coverage_status')),
    sa.CheckConstraint('depth >= 0 AND depth <= 5', name=op.f('ck_interview_session_intents_depth')),
    sa.ForeignKeyConstraint(['intent_id'], ['interview_intents.id'], name=op.f('fk_interview_session_intents_intent_id_interview_intents')),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_interview_session_intents_session_id_interview_sessions')),
    sa.PrimaryKeyConstraint('session_id', 'intent_id', name=op.f('pk_interview_session_intents'))
    )
    op.create_table('manual_issue_acknowledgements',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('issue_id', sa.CHAR(length=36), nullable=False),
    sa.Column('version_revision', sa.Integer(), nullable=False),
    sa.Column('content_revision', sa.Integer(), nullable=False),
    sa.Column('acknowledged_snapshot', sa.JSON(), nullable=False),
    sa.Column('owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('owner_note', sa.String(length=2000), nullable=True),
    sa.Column('acknowledged_at', UTC_DATETIME, nullable=False),
    not_blank('ck_manual_issue_acknowledgements_owner_note_not_blank', 'owner_note', nullable=True),
    sa.CheckConstraint('version_revision >= 1 AND content_revision >= 1', name=op.f('ck_manual_issue_acknowledgements_revisions')),
    sa.ForeignKeyConstraint(['issue_id'], ['manual_review_issues.id'], name=op.f('fk_manual_issue_acknowledgements_issue_id_manual_review_issues')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_manual_issue_acknowledgements_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_issue_acknowledgements')),
    sa.UniqueConstraint('issue_id', 'version_revision', name=op.f('uq_manual_issue_acknowledgements_issue_id_version_revision'))
    )
    op.create_table('manual_sections',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('version_id', sa.CHAR(length=36), nullable=False),
    sa.Column('shift_id', sa.CHAR(length=36), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('category', cs_string(16), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.CheckConstraint("(category = 'SHIFT_TASK') = (shift_id IS NOT NULL)", name=op.f('ck_manual_sections_shift_scope')),
    not_blank('ck_manual_sections_title_not_blank', 'title'),
    sa.CheckConstraint("category IN ('COMMON_TASK', 'SHIFT_TASK', 'RULE', 'EQUIPMENT')", name=op.f('ck_manual_sections_category')),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_manual_sections_sort_order')),
    sa.ForeignKeyConstraint(['shift_id', 'version_id'], ['manual_shifts.id', 'manual_shifts.version_id'], name='fk_manual_sections_shift_same_version'),
    sa.ForeignKeyConstraint(['version_id'], ['manual_versions.id'], name=op.f('fk_manual_sections_version_id_manual_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_sections')),
    sa.UniqueConstraint('id', 'version_id', name=op.f('uq_manual_sections_id_version_id')),
    sa.UniqueConstraint('version_id', 'sort_order', name=op.f('uq_manual_sections_version_id_sort_order'))
    )
    op.create_table('interview_intent_reviews',
    sa.Column('session_id', sa.CHAR(length=36), nullable=False),
    sa.Column('intent_id', sa.CHAR(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('status', cs_string(16), nullable=False),
    sa.Column('ready_content', sa.JSON(), nullable=True),
    sa.Column('confirmed_by_owner_id', sa.CHAR(length=36), nullable=True),
    sa.Column('confirmed_at', UTC_DATETIME, nullable=True),
    sa.Column('processing_kind', cs_string(16), nullable=True),
    sa.Column('processing_task_id', sa.CHAR(length=36), nullable=True),
    sa.Column('processing_attempt', sa.Integer(), nullable=True),
    sa.Column('error_code', cs_string(32), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.CheckConstraint("(status = 'ERROR') = (error_code IS NOT NULL) AND (status <> 'PROCESSING' OR (processing_task_id IS NOT NULL AND confirmed_at IS NULL)) AND (status <> 'READY' OR (ready_content IS NOT NULL AND processing_task_id IS NULL))", name=op.f('ck_interview_intent_reviews_status_consistency')),
    sa.CheckConstraint("error_code IN ('AI_PROCESSING_FAILED', 'TRANSCRIPTION_FAILED')", name=op.f('ck_interview_intent_reviews_error_code')),
    sa.CheckConstraint("processing_kind IN ('UNDERSTANDING', 'CORRECTION')", name=op.f('ck_interview_intent_reviews_processing_kind')),
    sa.CheckConstraint("status IN ('PROCESSING', 'READY', 'ERROR')", name=op.f('ck_interview_intent_reviews_status')),
    sa.CheckConstraint('(confirmed_at IS NULL) = (confirmed_by_owner_id IS NULL)', name=op.f('ck_interview_intent_reviews_confirmation')),
    sa.CheckConstraint('(processing_kind IS NULL AND processing_task_id IS NULL AND processing_attempt IS NULL) OR (processing_kind IS NOT NULL AND processing_task_id IS NOT NULL AND processing_attempt >= 1)', name=op.f('ck_interview_intent_reviews_processing')),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_interview_intent_reviews_revision')),
    sa.ForeignKeyConstraint(['confirmed_by_owner_id'], ['users.id'], name=op.f('fk_interview_intent_reviews_confirmed_by_owner_id_users')),
    sa.ForeignKeyConstraint(['session_id', 'intent_id'], ['interview_session_intents.session_id', 'interview_session_intents.intent_id'], name='fk_interview_intent_reviews_session_intent'),
    sa.PrimaryKeyConstraint('session_id', 'intent_id', name=op.f('pk_interview_intent_reviews'))
    )
    op.create_table('interview_probe_batches',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('session_id', sa.CHAR(length=36), nullable=False),
    sa.Column('intent_id', sa.CHAR(length=36), nullable=False),
    sa.Column('depth', sa.Integer(), nullable=False),
    sa.Column('status', cs_string(16), nullable=False),
    sa.Column('generator_source', sa.String(length=200), nullable=True),
    sa.Column('error_code', cs_string(32), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.CheckConstraint("(status = 'ERROR') = (error_code IS NOT NULL)", name=op.f('ck_interview_probe_batches_error_consistency')),
    sa.CheckConstraint("error_code IN ('TIMEOUT', 'RATE_LIMITED', 'UNAVAILABLE', 'INVALID_OUTPUT', 'REFUSED', 'INPUT_REJECTED', 'NOT_CONFIGURED', 'EMPTY_TRANSCRIPT', 'INTERNAL', 'LEASE_EXPIRED')", name=op.f('ck_interview_probe_batches_error_code')),
    sa.CheckConstraint("status IN ('GENERATING', 'READY', 'ERROR')", name=op.f('ck_interview_probe_batches_status')),
    sa.CheckConstraint('depth >= 1 AND depth <= 5', name=op.f('ck_interview_probe_batches_depth')),
    sa.ForeignKeyConstraint(['session_id', 'intent_id'], ['interview_session_intents.session_id', 'interview_session_intents.intent_id'], name='fk_interview_probe_batches_session_intent'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_probe_batches')),
    sa.UniqueConstraint('id', 'session_id', 'intent_id', name=op.f('uq_interview_probe_batches_id_session_id_intent_id')),
    sa.UniqueConstraint('session_id', 'intent_id', 'depth', name=op.f('uq_interview_probe_batches_session_id_intent_id_depth'))
    )
    op.create_table('manual_photo_attachments',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('version_id', sa.CHAR(length=36), nullable=False),
    sa.Column('section_id', sa.CHAR(length=36), nullable=True),
    sa.Column('media_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('caption', sa.String(length=300), nullable=True),
    sa.Column('scope_id', sa.CHAR(length=36), sa.Computed('COALESCE(section_id, version_id)', persisted=True), nullable=False),
    not_blank('ck_manual_photo_attachments_title_not_blank', 'title'),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_manual_photo_attachments_sort_order')),
    sa.ForeignKeyConstraint(['media_id'], ['manual_media.id'], name=op.f('fk_manual_photo_attachments_media_id_manual_media')),
    sa.ForeignKeyConstraint(['section_id', 'version_id'], ['manual_sections.id', 'manual_sections.version_id'], name='fk_manual_photo_attachments_section_same_version'),
    sa.ForeignKeyConstraint(['version_id'], ['manual_versions.id'], name=op.f('fk_manual_photo_attachments_version_id_manual_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_photo_attachments')),
    sa.UniqueConstraint('scope_id', 'media_id', name=op.f('uq_manual_photo_attachments_scope_id_media_id')),
    sa.UniqueConstraint('scope_id', 'sort_order', name=op.f('uq_manual_photo_attachments_scope_id_sort_order'))
    )
    op.create_index('ix_manual_photo_attachments_media_id', 'manual_photo_attachments', ['media_id'], unique=False)
    op.create_table('manual_steps',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('section_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('instruction', sa.Text(), nullable=False),
    sa.Column('checklist_item', sa.Boolean(), nullable=False),
    not_blank('ck_manual_steps_instruction_not_blank', 'instruction'),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_manual_steps_sort_order')),
    sa.ForeignKeyConstraint(['section_id'], ['manual_sections.id'], name=op.f('fk_manual_steps_section_id_manual_sections')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_steps')),
    sa.UniqueConstraint('section_id', 'sort_order', name=op.f('uq_manual_steps_section_id_sort_order'))
    )
    op.create_table('interview_review_confirmations',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('session_id', sa.CHAR(length=36), nullable=False),
    sa.Column('intent_id', sa.CHAR(length=36), nullable=False),
    sa.Column('reviewed_revision', sa.Integer(), nullable=False),
    sa.Column('confirmed_revision', sa.Integer(), nullable=False),
    sa.Column('confirmed_content', sa.JSON(), nullable=False),
    sa.Column('owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('confirmed_at', UTC_DATETIME, nullable=False),
    sa.CheckConstraint('reviewed_revision >= 1 AND confirmed_revision > reviewed_revision', name=op.f('ck_interview_review_confirmations_revisions')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_interview_review_confirmations_owner_id_users')),
    sa.ForeignKeyConstraint(['session_id', 'intent_id'], ['interview_intent_reviews.session_id', 'interview_intent_reviews.intent_id'], name='fk_interview_review_confirmations_review'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_review_confirmations')),
    sa.UniqueConstraint('session_id', 'intent_id', 'confirmed_revision', name='uq_interview_review_confirmations_revision')
    )
    op.create_table('interview_turns',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('session_id', sa.CHAR(length=36), nullable=False),
    sa.Column('turn_no', sa.Integer(), nullable=False),
    sa.Column('speaker', cs_string(8), nullable=False),
    sa.Column('turn_kind', cs_string(16), nullable=False),
    sa.Column('question_kind', cs_string(8), nullable=True),
    sa.Column('intent_id', sa.CHAR(length=36), nullable=False),
    sa.Column('depth', sa.Integer(), nullable=False),
    sa.Column('probe_batch_id', sa.CHAR(length=36), nullable=True),
    sa.Column('reply_to_question_turn_id', sa.CHAR(length=36), nullable=True),
    sa.Column('input_method', cs_string(8), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('transcription_id', sa.CHAR(length=36), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('base_question_intent_id', sa.CHAR(length=36), sa.Computed("CASE WHEN question_kind = 'BASE' THEN intent_id END", persisted=True), nullable=True),
    sa.Column('probe_question_batch_id', sa.CHAR(length=36), sa.Computed("CASE WHEN turn_kind = 'QUESTION' THEN probe_batch_id END", persisted=True), nullable=True),
    sa.CheckConstraint("(input_method = 'VOICE') = (transcription_id IS NOT NULL)", name=op.f('ck_interview_turns_voice_source')),
    sa.CheckConstraint("(turn_kind = 'QUESTION' AND speaker = 'AI' AND question_kind IS NOT NULL AND reply_to_question_turn_id IS NULL AND input_method IS NULL) OR (turn_kind = 'ANSWER' AND speaker = 'OWNER' AND question_kind IS NULL AND reply_to_question_turn_id IS NOT NULL AND input_method IS NOT NULL) OR (turn_kind = 'CORRECTION' AND speaker = 'OWNER' AND question_kind IS NULL AND reply_to_question_turn_id IS NULL AND input_method IS NOT NULL)", name=op.f('ck_interview_turns_kind_consistency')),
    not_blank('ck_interview_turns_content_not_blank', 'content'),
    sa.CheckConstraint("input_method IN ('TEXT', 'VOICE')", name=op.f('ck_interview_turns_input_method')),
    sa.CheckConstraint("question_kind IN ('BASE', 'PROBE')", name=op.f('ck_interview_turns_question_kind')),
    sa.CheckConstraint("question_kind IS NULL OR (question_kind = 'BASE' AND depth = 0 AND probe_batch_id IS NULL) OR (question_kind = 'PROBE' AND depth >= 1 AND probe_batch_id IS NOT NULL)", name=op.f('ck_interview_turns_question_depth')),
    sa.CheckConstraint("speaker IN ('AI', 'OWNER')", name=op.f('ck_interview_turns_speaker')),
    sa.CheckConstraint("turn_kind IN ('QUESTION', 'ANSWER', 'CORRECTION')", name=op.f('ck_interview_turns_turn_kind')),
    sa.CheckConstraint('depth >= 0 AND depth <= 5', name=op.f('ck_interview_turns_depth')),
    sa.CheckConstraint('turn_no >= 1', name=op.f('ck_interview_turns_turn_no')),
    sa.ForeignKeyConstraint(['probe_batch_id', 'session_id', 'intent_id'], ['interview_probe_batches.id', 'interview_probe_batches.session_id', 'interview_probe_batches.intent_id'], name='fk_interview_turns_probe_batch'),
    sa.ForeignKeyConstraint(['reply_to_question_turn_id'], ['interview_turns.id'], name=op.f('fk_interview_turns_reply_to_question_turn_id_interview_turns')),
    sa.ForeignKeyConstraint(['session_id', 'intent_id'], ['interview_session_intents.session_id', 'interview_session_intents.intent_id'], name='fk_interview_turns_session_intent'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_interview_turns_session_id_interview_sessions')),
    sa.ForeignKeyConstraint(['transcription_id'], ['media_transcriptions.id'], name=op.f('fk_interview_turns_transcription_id_media_transcriptions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_turns')),
    sa.UniqueConstraint('probe_question_batch_id', name=op.f('uq_interview_turns_probe_question_batch_id')),
    sa.UniqueConstraint('reply_to_question_turn_id', name=op.f('uq_interview_turns_reply_to_question_turn_id')),
    sa.UniqueConstraint('session_id', 'base_question_intent_id', name='uq_interview_turns_session_id_base_question'),
    sa.UniqueConstraint('session_id', 'turn_no', name=op.f('uq_interview_turns_session_id_turn_no'))
    )
    op.create_table('interview_evaluations',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('session_id', sa.CHAR(length=36), nullable=False),
    sa.Column('intent_id', sa.CHAR(length=36), nullable=False),
    sa.Column('probe_batch_id', sa.CHAR(length=36), nullable=True),
    sa.Column('depth', sa.Integer(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('evaluated_through_turn_id', sa.CHAR(length=36), nullable=False),
    sa.Column('input_snapshot', sa.JSON(), nullable=False),
    sa.Column('evaluation_config_version', sa.String(length=200), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('status', cs_string(16), nullable=False),
    sa.Column('needs_follow_up', sa.Boolean(), nullable=True),
    sa.Column('probability', sa.Float(), nullable=True),
    sa.Column('error_code', cs_string(32), nullable=True),
    sa.Column('task_id', sa.CHAR(length=36), nullable=True),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('applied_at', UTC_DATETIME, nullable=True),
    sa.Column('applied_depth', sa.Integer(), sa.Computed('CASE WHEN applied_at IS NOT NULL THEN depth END', persisted=True), nullable=True),
    sa.CheckConstraint("(status = 'SUCCEEDED' AND needs_follow_up IS NOT NULL AND probability IS NOT NULL AND error_code IS NULL) OR (status = 'FAILED' AND needs_follow_up IS NULL AND error_code IS NOT NULL AND applied_at IS NULL)", name=op.f('ck_interview_evaluations_status_consistency')),
    sa.CheckConstraint("error_code IN ('TIMEOUT', 'RATE_LIMITED', 'UNAVAILABLE', 'INVALID_OUTPUT', 'REFUSED', 'INPUT_REJECTED', 'NOT_CONFIGURED', 'EMPTY_TRANSCRIPT', 'INTERNAL', 'LEASE_EXPIRED')", name=op.f('ck_interview_evaluations_error_code')),
    sa.CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name=op.f('ck_interview_evaluations_status')),
    sa.CheckConstraint('(depth = 0) = (probe_batch_id IS NULL)', name=op.f('ck_interview_evaluations_batch_depth')),
    sa.CheckConstraint('depth >= 0 AND depth <= 5 AND attempt_no >= 1', name=op.f('ck_interview_evaluations_depth_attempt')),
    sa.CheckConstraint('probability IS NULL OR (probability >= 0 AND probability <= 1)', name=op.f('ck_interview_evaluations_probability')),
    sa.ForeignKeyConstraint(['evaluated_through_turn_id'], ['interview_turns.id'], name='fk_interview_evaluations_evaluated_through_turn'),
    sa.ForeignKeyConstraint(['probe_batch_id', 'session_id', 'intent_id'], ['interview_probe_batches.id', 'interview_probe_batches.session_id', 'interview_probe_batches.intent_id'], name='fk_interview_evaluations_probe_batch'),
    sa.ForeignKeyConstraint(['session_id', 'intent_id'], ['interview_session_intents.session_id', 'interview_session_intents.intent_id'], name='fk_interview_evaluations_session_intent'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_evaluations')),
    sa.UniqueConstraint('session_id', 'intent_id', 'applied_depth', name='uq_interview_evaluations_applied'),
    sa.UniqueConstraint('session_id', 'intent_id', 'depth', 'attempt_no', name='uq_interview_evaluations_attempt')
    )
    op.create_table('interview_turn_photos',
    sa.Column('turn_id', sa.CHAR(length=36), nullable=False),
    sa.Column('media_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_interview_turn_photos_sort_order')),
    sa.ForeignKeyConstraint(['media_id'], ['manual_media.id'], name=op.f('fk_interview_turn_photos_media_id_manual_media')),
    sa.ForeignKeyConstraint(['turn_id'], ['interview_turns.id'], name=op.f('fk_interview_turn_photos_turn_id_interview_turns')),
    sa.PrimaryKeyConstraint('turn_id', 'media_id', name=op.f('pk_interview_turn_photos')),
    sa.UniqueConstraint('turn_id', 'sort_order', name=op.f('uq_interview_turn_photos_turn_id_sort_order'))
    )
    op.create_index('ix_interview_turn_photos_media_id', 'interview_turn_photos', ['media_id'], unique=False)
    if op.get_context().dialect.supports_alter:
        # store_manuals <-> manual_versions is a cycle: MySQL gets this FK once both tables
        # exist (SQLite, which cannot ALTER constraints, created it inline above).
        op.create_foreign_key(
            'fk_store_manuals_current_published_version_id', 'store_manuals', 'manual_versions',
            ['current_published_version_id'], ['id'],
        )


def downgrade() -> None:
    if op.get_context().dialect.supports_alter:
        op.drop_constraint('fk_store_manuals_current_published_version_id', 'store_manuals', type_='foreignkey')
    op.drop_table('interview_turn_photos')
    op.drop_table('interview_evaluations')
    op.drop_table('interview_turns')
    op.drop_table('interview_review_confirmations')
    op.drop_table('manual_steps')
    op.drop_table('manual_photo_attachments')
    op.drop_table('interview_probe_batches')
    op.drop_table('interview_intent_reviews')
    op.drop_table('manual_sections')
    op.drop_table('manual_issue_acknowledgements')
    op.drop_table('interview_session_intents')
    op.drop_table('manual_shifts')
    op.drop_table('manual_review_issues')
    op.drop_table('manual_draft_corrections')
    op.drop_table('interview_sessions')
    op.drop_table('manual_versions')
    op.drop_table('manual_media_snapshot_refs')
    op.drop_table('store_manuals')
    op.drop_table('interview_intents')
    op.drop_table('interview_question_sets')

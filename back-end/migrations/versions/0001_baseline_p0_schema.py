"""Baseline P0 schema: users, worker profile, store/approval, invitation/access, jobs.

Revision ID: 0001
Revises: None

Frozen copy of app.db.models at this point. Never edit it after release; add a new revision.
Timestamps are naive UTC DATETIME(6).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

UTC_DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('google_sub', sa.String(length=255), nullable=False),
    sa.Column('google_email', sa.String(length=320), nullable=False),
    sa.Column('email_verified', sa.Boolean(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('phone_number', sa.String(length=20), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('updated_at', UTC_DATETIME, nullable=False),
    sa.CheckConstraint("role IN ('WORKER', 'OWNER')", name=op.f('ck_users_role')),
    sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED')", name=op.f('ck_users_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('google_sub', name=op.f('uq_users_google_sub'))
    )
    op.create_table('stores',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('industry', sa.String(length=50), nullable=False),
    sa.Column('postal_code', sa.String(length=10), nullable=False),
    sa.Column('address', sa.String(length=255), nullable=False),
    sa.Column('detail_address', sa.String(length=255), nullable=True),
    sa.Column('business_registration_number', sa.String(length=10), nullable=False),
    sa.Column('phone_number', sa.String(length=20), nullable=False),
    sa.Column('approval_status', sa.String(length=16), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('approved_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(approval_status = 'PENDING' AND approved_at IS NULL) OR (approval_status = 'APPROVED' AND approved_at IS NOT NULL)", name=op.f('ck_stores_approval_consistency')),
    sa.CheckConstraint("approval_status IN ('PENDING', 'APPROVED')", name=op.f('ck_stores_approval_status')),
    sa.CheckConstraint('LENGTH(business_registration_number) = 10', name=op.f('ck_stores_brn_digits')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_stores_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stores')),
    sa.UniqueConstraint('business_registration_number', name=op.f('uq_stores_business_registration_number'))
    )
    op.create_index('ix_stores_owner_id', 'stores', ['owner_id'], unique=False)
    op.create_table('worker_profiles',
    sa.Column('user_id', sa.CHAR(length=36), nullable=False),
    sa.Column('birth_date', sa.Date(), nullable=False),
    sa.Column('gender', sa.String(length=8), nullable=False),
    sa.Column('experience_level', sa.String(length=16), nullable=False),
    sa.CheckConstraint("experience_level IN ('NEW', 'EXPERIENCED')", name=op.f('ck_worker_profiles_experience_level')),
    sa.CheckConstraint("gender IN ('MALE', 'FEMALE')", name=op.f('ck_worker_profiles_gender')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_worker_profiles_user_id_users')),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_worker_profiles'))
    )
    op.create_table('availability_rules',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('worker_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('ends_next_day', sa.Boolean(), nullable=False),
    sa.CheckConstraint('(ends_next_day = 0 AND end_time > start_time) OR (ends_next_day = 1 AND end_time <= start_time)', name=op.f('ck_availability_rules_time_span')),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_availability_rules_sort_order')),
    sa.ForeignKeyConstraint(['worker_id'], ['worker_profiles.user_id'], name=op.f('fk_availability_rules_worker_id_worker_profiles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_availability_rules')),
    sa.UniqueConstraint('worker_id', 'sort_order', name=op.f('uq_availability_rules_worker_id_sort_order'))
    )
    op.create_table('job_postings',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('created_by_owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('duty_description', sa.Text(), nullable=False),
    sa.Column('work_part', sa.String(length=16), nullable=False),
    sa.Column('work_date', sa.Date(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('ends_next_day', sa.Boolean(), nullable=False),
    sa.Column('headcount', sa.Integer(), nullable=False),
    sa.Column('min_experience_months', sa.Integer(), nullable=False),
    sa.Column('extra_requirements', sa.Text(), nullable=True),
    sa.Column('hourly_wage_krw', sa.Integer(), nullable=False),
    sa.Column('payment_timing', sa.String(length=16), nullable=False),
    sa.Column('pay_note', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('closed_at', UTC_DATETIME, nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.CheckConstraint("(status = 'RECRUITING' AND closed_at IS NULL) OR (status = 'CLOSED' AND closed_at IS NOT NULL)", name=op.f('ck_job_postings_closed_consistency')),
    sa.CheckConstraint("payment_timing IN ('WORK_DAY', 'NEXT_DAY', 'NEGOTIABLE')", name=op.f('ck_job_postings_payment_timing')),
    sa.CheckConstraint("status IN ('RECRUITING', 'CLOSED')", name=op.f('ck_job_postings_status')),
    sa.CheckConstraint("work_part IN ('WEEKDAY_OPEN', 'WEEKDAY_CLOSE', 'WEEKEND_OPEN', 'WEEKEND_CLOSE', 'OTHER')", name=op.f('ck_job_postings_work_part')),
    sa.CheckConstraint('(ends_next_day = 0 AND end_time > start_time) OR (ends_next_day = 1 AND end_time <= start_time)', name=op.f('ck_job_postings_time_span')),
    sa.CheckConstraint('headcount = 1', name=op.f('ck_job_postings_headcount')),
    sa.CheckConstraint('hourly_wage_krw > 0', name=op.f('ck_job_postings_hourly_wage')),
    sa.CheckConstraint('min_experience_months IN (0, 3, 6, 12)', name=op.f('ck_job_postings_min_experience')),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_job_postings_revision')),
    sa.ForeignKeyConstraint(['created_by_owner_id'], ['users.id'], name=op.f('fk_job_postings_created_by_owner_id_users')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_job_postings_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_postings'))
    )
    op.create_index('ix_job_postings_status_work_date', 'job_postings', ['status', 'work_date'], unique=False)
    op.create_index('ix_job_postings_store_id_status', 'job_postings', ['store_id', 'status'], unique=False)
    op.create_table('store_approval_requests',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('submitted_at', UTC_DATETIME, nullable=False),
    sa.Column('approved_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("(status = 'PENDING' AND approved_at IS NULL) OR (status = 'APPROVED' AND approved_at IS NOT NULL)", name=op.f('ck_store_approval_requests_approval_consistency')),
    sa.CheckConstraint("status IN ('PENDING', 'APPROVED')", name=op.f('ck_store_approval_requests_status')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_store_approval_requests_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_store_approval_requests')),
    sa.UniqueConstraint('store_id', name=op.f('uq_store_approval_requests_store_id'))
    )
    op.create_table('store_invitations',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('inviter_owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('invited_email', sa.String(length=320), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', UTC_DATETIME, nullable=False),
    sa.Column('last_sent_at', UTC_DATETIME, nullable=False),
    sa.Column('expires_at', UTC_DATETIME, nullable=False),
    sa.Column('access_expires_at', UTC_DATETIME, nullable=True),
    sa.Column('accepted_by_worker_id', sa.CHAR(length=36), nullable=True),
    sa.Column('accepted_at', UTC_DATETIME, nullable=True),
    sa.Column('declined_by_worker_id', sa.CHAR(length=36), nullable=True),
    sa.Column('declined_at', UTC_DATETIME, nullable=True),
    sa.Column('canceled_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint('(accepted_at IS NOT NULL) + (declined_at IS NOT NULL) + (canceled_at IS NOT NULL) <= 1', name=op.f('ck_store_invitations_single_outcome')),
    sa.CheckConstraint('(accepted_at IS NULL) = (accepted_by_worker_id IS NULL)', name=op.f('ck_store_invitations_accepted_actor')),
    sa.CheckConstraint('(declined_at IS NULL) = (declined_by_worker_id IS NULL)', name=op.f('ck_store_invitations_declined_actor')),
    sa.CheckConstraint('access_expires_at IS NULL OR access_expires_at > created_at', name=op.f('ck_store_invitations_access_expiry')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_store_invitations_link_expiry')),
    sa.ForeignKeyConstraint(['accepted_by_worker_id'], ['users.id'], name=op.f('fk_store_invitations_accepted_by_worker_id_users')),
    sa.ForeignKeyConstraint(['declined_by_worker_id'], ['users.id'], name=op.f('fk_store_invitations_declined_by_worker_id_users')),
    sa.ForeignKeyConstraint(['inviter_owner_id'], ['users.id'], name=op.f('fk_store_invitations_inviter_owner_id_users')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_store_invitations_store_id_stores')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_store_invitations')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_store_invitations_token_hash'))
    )
    op.create_index('ix_store_invitations_store_id_invited_email', 'store_invitations', ['store_id', 'invited_email'], unique=False)
    op.create_table('worker_careers',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('worker_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('industry', sa.String(length=50), nullable=False),
    sa.Column('duties', sa.String(length=500), nullable=False),
    sa.Column('store_name', sa.String(length=100), nullable=True),
    sa.Column('start_month', sa.String(length=7), nullable=False),
    sa.Column('end_month', sa.String(length=7), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.CheckConstraint('(is_current = 1 AND end_month IS NULL) OR (is_current = 0 AND end_month IS NOT NULL)', name=op.f('ck_worker_careers_current_end_month')),
    sa.CheckConstraint('end_month IS NULL OR start_month <= end_month', name=op.f('ck_worker_careers_month_order')),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_worker_careers_sort_order')),
    sa.CheckConstraint("store_name IS NULL OR TRIM(store_name) <> ''", name=op.f('ck_worker_careers_store_name_not_blank')),
    sa.ForeignKeyConstraint(['worker_id'], ['worker_profiles.user_id'], name=op.f('fk_worker_careers_worker_id_worker_profiles')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_worker_careers')),
    sa.UniqueConstraint('worker_id', 'sort_order', name=op.f('uq_worker_careers_worker_id_sort_order'))
    )
    op.create_table('availability_days',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('rule_id', sa.CHAR(length=36), nullable=False),
    sa.Column('weekday', sa.String(length=3), nullable=False),
    sa.CheckConstraint("weekday IN ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')", name=op.f('ck_availability_days_weekday')),
    sa.ForeignKeyConstraint(['rule_id'], ['availability_rules.id'], name=op.f('fk_availability_days_rule_id_availability_rules')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_availability_days')),
    sa.UniqueConstraint('rule_id', 'weekday', name=op.f('uq_availability_days_rule_id_weekday'))
    )
    op.create_table('job_applications',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('job_id', sa.CHAR(length=36), nullable=False),
    sa.Column('worker_id', sa.CHAR(length=36), nullable=False),
    sa.Column('introduction', sa.String(length=500), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('applied_at', UTC_DATETIME, nullable=False),
    sa.Column('withdrawn_at', UTC_DATETIME, nullable=True),
    sa.Column('applicant_name', sa.String(length=100), nullable=False),
    sa.Column('age_at_submission', sa.Integer(), nullable=False),
    sa.Column('experience_level', sa.String(length=16), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('active_worker_id', sa.CHAR(length=36), sa.Computed("CASE WHEN status IN ('APPLIED', 'REQUESTED', 'CONFIRMED') THEN worker_id END", persisted=True), nullable=True),
    sa.CheckConstraint("(status = 'WITHDRAWN') = (withdrawn_at IS NOT NULL)", name=op.f('ck_job_applications_withdrawn_consistency')),
    sa.CheckConstraint("TRIM(introduction) <> ''", name=op.f('ck_job_applications_introduction')),
    sa.CheckConstraint("experience_level IN ('NEW', 'EXPERIENCED')", name=op.f('ck_job_applications_experience_level')),
    sa.CheckConstraint("status IN ('APPLIED', 'REQUESTED', 'CONFIRMED', 'WITHDRAWN', 'NOT_SELECTED', 'COMPLETED')", name=op.f('ck_job_applications_status')),
    sa.CheckConstraint('age_at_submission >= 0', name=op.f('ck_job_applications_age')),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_job_applications_revision')),
    sa.ForeignKeyConstraint(['job_id'], ['job_postings.id'], name=op.f('fk_job_applications_job_id_job_postings')),
    sa.ForeignKeyConstraint(['worker_id'], ['users.id'], name=op.f('fk_job_applications_worker_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_applications')),
    sa.UniqueConstraint('job_id', 'active_worker_id', name=op.f('uq_job_applications_job_id_active_worker_id'))
    )
    op.create_index('ix_job_applications_worker_id', 'job_applications', ['worker_id'], unique=False)
    op.create_table('application_careers',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('application_id', sa.CHAR(length=36), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('industry', sa.String(length=50), nullable=False),
    sa.Column('duties', sa.String(length=500), nullable=False),
    sa.Column('store_name', sa.String(length=100), nullable=True),
    sa.Column('start_month', sa.String(length=7), nullable=False),
    sa.Column('end_month', sa.String(length=7), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.CheckConstraint('(is_current = 1 AND end_month IS NULL) OR (is_current = 0 AND end_month IS NOT NULL)', name=op.f('ck_application_careers_current_end_month')),
    sa.CheckConstraint('sort_order >= 0', name=op.f('ck_application_careers_sort_order')),
    sa.CheckConstraint("store_name IS NULL OR TRIM(store_name) <> ''", name=op.f('ck_application_careers_store_name_not_blank')),
    sa.ForeignKeyConstraint(['application_id'], ['job_applications.id'], name=op.f('fk_application_careers_application_id_job_applications')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_application_careers')),
    sa.UniqueConstraint('application_id', 'sort_order', name=op.f('uq_application_careers_application_id_sort_order'))
    )
    op.create_table('work_requests',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('application_id', sa.CHAR(length=36), nullable=False),
    sa.Column('requested_by_owner_id', sa.CHAR(length=36), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('requested_at', UTC_DATETIME, nullable=False),
    sa.Column('expires_at', UTC_DATETIME, nullable=False),
    sa.Column('responded_at', UTC_DATETIME, nullable=True),
    sa.Column('ended_at', UTC_DATETIME, nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('pending_application_id', sa.CHAR(length=36), sa.Computed("CASE WHEN status = 'PENDING' THEN application_id END", persisted=True), nullable=True),
    sa.CheckConstraint("(status = 'PENDING') = (ended_at IS NULL)", name=op.f('ck_work_requests_ended_consistency')),
    sa.CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'DECLINED', 'EXPIRED', 'CANCELLED', 'CONFIRMATION_WITHDRAWN')", name=op.f('ck_work_requests_status')),
    sa.CheckConstraint("status NOT IN ('ACCEPTED', 'DECLINED', 'CONFIRMATION_WITHDRAWN') OR responded_at IS NOT NULL", name=op.f('ck_work_requests_responded_consistency')),
    sa.CheckConstraint('expires_at > requested_at', name=op.f('ck_work_requests_expiry')),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_work_requests_revision')),
    sa.ForeignKeyConstraint(['application_id'], ['job_applications.id'], name=op.f('fk_work_requests_application_id_job_applications')),
    sa.ForeignKeyConstraint(['requested_by_owner_id'], ['users.id'], name=op.f('fk_work_requests_requested_by_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_work_requests')),
    sa.UniqueConstraint('pending_application_id', name=op.f('uq_work_requests_pending_application_id'))
    )
    op.create_index('ix_work_requests_application_id', 'work_requests', ['application_id'], unique=False)
    op.create_table('application_selection_effects',
    sa.Column('request_id', sa.CHAR(length=36), nullable=False),
    sa.Column('application_id', sa.CHAR(length=36), nullable=False),
    sa.Column('previous_status', sa.String(length=16), nullable=False),
    sa.Column('applied_revision', sa.Integer(), nullable=False),
    sa.Column('restored_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint("previous_status IN ('APPLIED', 'REQUESTED', 'CONFIRMED', 'WITHDRAWN', 'NOT_SELECTED', 'COMPLETED')", name=op.f('ck_application_selection_effects_previous_status')),
    sa.CheckConstraint('applied_revision >= 1', name=op.f('ck_application_selection_effects_applied_revision')),
    sa.ForeignKeyConstraint(['application_id'], ['job_applications.id'], name=op.f('fk_application_selection_effects_application_id_job_applications')),
    sa.ForeignKeyConstraint(['request_id'], ['work_requests.id'], name=op.f('fk_application_selection_effects_request_id_work_requests')),
    sa.PrimaryKeyConstraint('request_id', 'application_id', name=op.f('pk_application_selection_effects'))
    )
    op.create_table('shift_assignments',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('job_id', sa.CHAR(length=36), nullable=False),
    sa.Column('work_request_id', sa.CHAR(length=36), nullable=False),
    sa.Column('worker_id', sa.CHAR(length=36), nullable=False),
    sa.Column('confirmed_at', UTC_DATETIME, nullable=False),
    sa.Column('withdrawn_at', UTC_DATETIME, nullable=True),
    sa.Column('active_job_id', sa.CHAR(length=36), sa.Computed('CASE WHEN withdrawn_at IS NULL THEN job_id END', persisted=True), nullable=True),
    sa.CheckConstraint('withdrawn_at IS NULL OR withdrawn_at >= confirmed_at', name=op.f('ck_shift_assignments_withdrawn_after')),
    sa.ForeignKeyConstraint(['job_id'], ['job_postings.id'], name=op.f('fk_shift_assignments_job_id_job_postings')),
    sa.ForeignKeyConstraint(['work_request_id'], ['work_requests.id'], name=op.f('fk_shift_assignments_work_request_id_work_requests')),
    sa.ForeignKeyConstraint(['worker_id'], ['users.id'], name=op.f('fk_shift_assignments_worker_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shift_assignments')),
    sa.UniqueConstraint('active_job_id', name=op.f('uq_shift_assignments_active_job_id')),
    sa.UniqueConstraint('work_request_id', name=op.f('uq_shift_assignments_work_request_id'))
    )
    op.create_index('ix_shift_assignments_job_id', 'shift_assignments', ['job_id'], unique=False)
    op.create_index('ix_shift_assignments_worker_id', 'shift_assignments', ['worker_id'], unique=False)
    op.create_table('store_access_grants',
    sa.Column('id', sa.CHAR(length=36), nullable=False),
    sa.Column('store_id', sa.CHAR(length=36), nullable=False),
    sa.Column('worker_id', sa.CHAR(length=36), nullable=False),
    sa.Column('invitation_id', sa.CHAR(length=36), nullable=True),
    sa.Column('assignment_id', sa.CHAR(length=36), nullable=True),
    sa.Column('duty_label', sa.String(length=100), nullable=True),
    sa.Column('granted_at', UTC_DATETIME, nullable=False),
    sa.Column('valid_until', UTC_DATETIME, nullable=True),
    sa.Column('revoked_at', UTC_DATETIME, nullable=True),
    sa.CheckConstraint('(invitation_id IS NULL) <> (assignment_id IS NULL)', name=op.f('ck_store_access_grants_source_xor')),
    sa.CheckConstraint('revoked_at IS NULL OR revoked_at >= granted_at', name=op.f('ck_store_access_grants_revoked_after_grant')),
    sa.CheckConstraint('valid_until IS NULL OR valid_until > granted_at', name=op.f('ck_store_access_grants_validity')),
    sa.ForeignKeyConstraint(['assignment_id'], ['shift_assignments.id'], name=op.f('fk_store_access_grants_assignment_id_shift_assignments')),
    sa.ForeignKeyConstraint(['invitation_id'], ['store_invitations.id'], name=op.f('fk_store_access_grants_invitation_id_store_invitations')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_store_access_grants_store_id_stores')),
    sa.ForeignKeyConstraint(['worker_id'], ['users.id'], name=op.f('fk_store_access_grants_worker_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_store_access_grants')),
    sa.UniqueConstraint('assignment_id', name=op.f('uq_store_access_grants_assignment_id')),
    sa.UniqueConstraint('invitation_id', name=op.f('uq_store_access_grants_invitation_id'))
    )
    op.create_index('ix_store_access_grants_store_id_worker_id', 'store_access_grants', ['store_id', 'worker_id'], unique=False)


def downgrade() -> None:
    op.drop_table('store_access_grants')
    op.drop_table('shift_assignments')
    op.drop_table('application_selection_effects')
    op.drop_table('work_requests')
    op.drop_table('application_careers')
    op.drop_table('job_applications')
    op.drop_table('availability_days')
    op.drop_table('worker_careers')
    op.drop_table('store_invitations')
    op.drop_table('store_approval_requests')
    op.drop_table('job_postings')
    op.drop_table('availability_rules')
    op.drop_table('worker_profiles')
    op.drop_table('stores')
    op.drop_table('users')

"""Invitation mail delivery: retry backoff and a claim lease on outbox rows.

Revision ID: 0020
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0020"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    with op.batch_alter_table("invitation_mail_outbox") as batch:
        batch.add_column(sa.Column("next_attempt_at", timestamp, nullable=True))
        batch.add_column(sa.Column("claim_token", sa.CHAR(36), nullable=True))
        batch.add_column(sa.Column("claimed_until", timestamp, nullable=True))
        batch.create_check_constraint(
            op.f("ck_invitation_mail_outbox_claim_pair"), "(claim_token IS NULL) = (claimed_until IS NULL)",
        )
        batch.create_check_constraint(
            op.f("ck_invitation_mail_outbox_claim_while_queued"), "status = 'QUEUED' OR claim_token IS NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("invitation_mail_outbox") as batch:
        batch.drop_constraint(op.f("ck_invitation_mail_outbox_claim_while_queued"), type_="check")
        batch.drop_constraint(op.f("ck_invitation_mail_outbox_claim_pair"), type_="check")
        batch.drop_column("claimed_until")
        batch.drop_column("claim_token")
        batch.drop_column("next_attempt_at")

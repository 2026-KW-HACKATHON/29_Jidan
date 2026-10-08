"""Invitation e-mail outbox, written in the invitation's transaction.

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    status = sa.String(16).with_variant(sa.String(16, collation="utf8mb4_0900_as_cs"), "mysql")
    op.create_table(
        "invitation_mail_outbox",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("invitation_id", sa.CHAR(36), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("processed_at", timestamp, nullable=True),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'SENT', 'FAILED', 'DISCARDED')", name=op.f("ck_invitation_mail_outbox_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_invitation_mail_outbox_attempts")),
        sa.CheckConstraint(
            "(status = 'QUEUED') = (payload IS NOT NULL)",
            name=op.f("ck_invitation_mail_outbox_payload_while_queued"),
        ),
        sa.CheckConstraint(
            "(status = 'QUEUED') = (processed_at IS NULL)",
            name=op.f("ck_invitation_mail_outbox_processed_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"], ["store_invitations.id"],
            name=op.f("fk_invitation_mail_outbox_invitation_id_store_invitations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitation_mail_outbox")),
    )
    op.create_index(op.f("ix_invitation_mail_outbox_invitation_id"), "invitation_mail_outbox", ["invitation_id"])
    op.create_index(
        "ix_invitation_mail_outbox_status_created_at", "invitation_mail_outbox", ["status", "created_at"],
    )


def downgrade() -> None:
    # Dropping the table drops its indexes; MySQL refuses to drop the FK-backing index alone.
    op.drop_table("invitation_mail_outbox")

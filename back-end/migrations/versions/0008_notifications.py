"""In-app notifications written as the outbox of domain state changes.

Revision ID: 0008
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TYPES = (
    "NEW_APPLICATION", "INVITATION_ACCEPTED", "STORE_APPROVED", "INVITATION_EXPIRED",
    "WORK_REQUEST_RECEIVED", "WORK_REQUEST_NO_RESPONSE", "WORK_CONFIRMED", "STORE_INVITED",
    "WORK_REMINDER", "MANUAL_PUBLISHED", "WORK_REQUEST_WITHDRAWN", "WORK_CONFIRMATION_WITHDRAWN",
)
TARGET_KINDS = (
    "JOB_APPLICATION", "WORK_REQUEST", "STORE_INVITATION", "STORE", "MANUAL", "WORK_SCHEDULE",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _cs(length: int):
    return sa.String(length).with_variant(sa.String(length, collation="utf8mb4_0900_as_cs"), "mysql")


# Frozen copy of app.db.checks.not_blank (Unicode White_Space, same text per dialect).
WHITESPACE = "char(9,10,11,12,13,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)"


def not_blank(name: str, column: str):
    is_mysql = op.get_context().dialect.name in ("mysql", "mariadb")
    sql = f"REGEXP_LIKE({column}, '[^[:space:]]')" if is_mysql else f"TRIM({column}, {WHITESPACE}) <> ''"
    return sa.CheckConstraint(sql, name=op.f(name))


def upgrade() -> None:
    timestamp = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    op.create_table(
        "notifications",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("recipient_user_id", sa.CHAR(36), nullable=False),
        sa.Column("event_type", _cs(32), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("target_kind", _cs(32), nullable=False),
        sa.Column("target_id", sa.CHAR(36), nullable=False),
        sa.Column("target_context", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", _cs(255), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("read_at", timestamp, nullable=True),
        sa.CheckConstraint(_in("event_type", TYPES), name=op.f("ck_notifications_event_type")),
        sa.CheckConstraint(_in("target_kind", TARGET_KINDS), name=op.f("ck_notifications_target_kind")),
        not_blank("ck_notifications_title_not_blank", "title"),
        not_blank("ck_notifications_body_not_blank", "body"),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["users.id"], name=op.f("fk_notifications_recipient_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint(
            "recipient_user_id", "dedupe_key", name=op.f("uq_notifications_recipient_user_id_dedupe_key"),
        ),
    )
    op.create_index(
        "ix_notifications_recipient_user_id_created_at_id", "notifications",
        ["recipient_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_notifications_recipient_user_id_read_at", "notifications", ["recipient_user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_table("notifications")

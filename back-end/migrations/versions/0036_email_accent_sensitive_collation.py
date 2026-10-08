"""E-mail columns: accent-sensitive but still case-insensitive on MySQL.

utf8mb4_0900_ai_ci folded 'josé@' into 'jose@' and 'straße@' into 'strasse@' in SQL comparisons;
utf8mb4_0900_as_ci keeps them apart while 'JOSE@' still equals 'jose@'. SQLite compares bytes
already, so only MySQL changes. The columns have no UNIQUE or FK, so values that become distinct
cannot violate a constraint; the (store_id, invited_email) index is rebuilt by the table copy.

Revision ID: 0036
Revises: 0035
"""
import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

COLUMNS = (
    ("users", "google_email"),
    ("registration_sessions", "google_email"),
    ("store_invitations", "invited_email"),
)


def _set_collation(old: str, new: str) -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    for table, column in COLUMNS:
        op.alter_column(
            table, column, existing_nullable=False,
            existing_type=sa.String(320, collation=old), type_=sa.String(320, collation=new),
        )


def upgrade() -> None:
    _set_collation("utf8mb4_0900_ai_ci", "utf8mb4_0900_as_ci")


def downgrade() -> None:
    _set_collation("utf8mb4_0900_as_ci", "utf8mb4_0900_ai_ci")

"""Question guidance (OpenAPI 0.11.0, #158): help text and guidance cards on question turns.

Revision ID: 0042
Revises: 0041

Both columns are nullable: questions written before this revision show no help text and no
cards. Only QUESTION turns carry them (answers and corrections never do). The card contents are
checked by app.interview.cards before they are written, not by the database.
"""
import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

GUIDANCE_ON_QUESTIONS = "turn_kind = 'QUESTION' OR (guidance IS NULL AND guidance_cards IS NULL)"


def _is_mysql() -> bool:
    return op.get_context().dialect.name in ("mysql", "mariadb")


def upgrade() -> None:
    if _is_mysql():
        with op.batch_alter_table("interview_turns") as batch:
            batch.add_column(sa.Column("guidance", sa.Text(), nullable=True))
            batch.add_column(sa.Column("guidance_cards", sa.JSON(), nullable=True))
            batch.create_check_constraint(op.f("ck_interview_turns_guidance_on_questions"),
                                          GUIDANCE_ON_QUESTIONS)
        return
    # SQLite: a batch table copy cannot INSERT into the generated columns of interview_turns.
    # ADD COLUMN keeps the table in place, and SQLite lets a named column CHECK refer to other
    # columns, so the rule is the same.
    op.execute("ALTER TABLE interview_turns ADD COLUMN guidance TEXT")
    op.execute("ALTER TABLE interview_turns ADD COLUMN guidance_cards JSON"
               f" CONSTRAINT ck_interview_turns_guidance_on_questions CHECK ({GUIDANCE_ON_QUESTIONS})")


def downgrade() -> None:
    if _is_mysql():
        with op.batch_alter_table("interview_turns") as batch:
            batch.drop_constraint(op.f("ck_interview_turns_guidance_on_questions"), type_="check")
            batch.drop_column("guidance_cards")
            batch.drop_column("guidance")
        return
    # SQLite >= 3.35 drops a column together with its column CHECK (no table copy).
    op.execute("ALTER TABLE interview_turns DROP COLUMN guidance_cards")
    op.execute("ALTER TABLE interview_turns DROP COLUMN guidance")

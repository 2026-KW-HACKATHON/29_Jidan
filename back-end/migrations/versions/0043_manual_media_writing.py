"""Sections written from photos and videos (OpenAPI 0.12.0): owner videos and media-writing tasks.

Revision ID: 0043
Revises: 0042

Photos and videos are AI input only here (user decision 2026-10-08): nothing links a video to a
section, so no new columns, only wider CHECKs.

* manual_media: kind VIDEO (MP4/QuickTime/WebM, <= 100 MiB, 1..60500 ms; app.media.video).
* manual_media_snapshot_refs: holder MEDIA_WRITING, the files a review media-writing task reads
  while it waits or runs (draft media writing holds its files as DRAFT_CORRECTION).
* manual_draft_corrections: input_method MEDIA, which has no input_text (the media IDs are in
  the task payload); TEXT/VOICE keep requiring it.
* background_tasks: kinds REVIEW_MEDIA_WRITING and DRAFT_MEDIA_WRITING;
  interview_intent_reviews: processing_kind MEDIA_WRITING.

SQLite cannot alter a CHECK, and a batch table copy cannot INSERT into the generated column of
manual_draft_corrections (see 0042), so on SQLite each table is rebuilt from its own stored DDL
with exact text edits, copying only the stored columns. The downgrade refuses (before any DDL;
MySQL DDL is not transactional) while rows use a new value.
"""
import sqlalchemy as sa
from alembic import context, op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

# Frozen copy of app.db.checks.not_blank (see 0034).
WHITESPACE = "char(9,10,11,12,13,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)"
OLD_TASK_KINDS = ("TRANSCRIPTION", "INITIAL_QUESTION", "EVALUATION", "FOLLOWUP_GENERATION", "DRAFT_GENERATION",
                  "REVIEW_UNDERSTANDING", "REVIEW_CORRECTION", "DRAFT_CORRECTION", "QA_ANSWER")
NEW_TASK_KINDS = (*OLD_TASK_KINDS, "REVIEW_MEDIA_WRITING", "DRAFT_MEDIA_WRITING")
OLD_REVIEW_KINDS = ("UNDERSTANDING", "CORRECTION")
NEW_REVIEW_KINDS = (*OLD_REVIEW_KINDS, "MEDIA_WRITING")
OLD_METHODS = ("TEXT", "VOICE")
NEW_METHODS = (*OLD_METHODS, "MEDIA")
OLD_HOLDERS = ("INTENT_REVIEW", "REVIEW_CONFIRMATION", "DRAFT_GENERATION", "DRAFT_CORRECTION")
NEW_HOLDERS = (*OLD_HOLDERS, "MEDIA_WRITING")
OLD_MEDIA_KINDS = ("IMAGE", "AUDIO")
NEW_MEDIA_KINDS = (*OLD_MEDIA_KINDS, "VIDEO")
OLD_SHAPE = (
    "(kind = 'IMAGE' AND mime_type IN ('image/jpeg', 'image/png', 'image/webp') AND byte_size <= 10485760"
    " AND duration_ms IS NULL)"
    " OR (kind = 'AUDIO' AND mime_type IN ('audio/mpeg', 'audio/mp4', 'audio/webm', 'audio/wav')"
    " AND byte_size <= 20971520 AND duration_ms IS NOT NULL AND duration_ms >= 1 AND duration_ms <= 120000)"
)
NEW_SHAPE = (
    f"{OLD_SHAPE}"
    " OR (kind = 'VIDEO' AND mime_type IN ('video/mp4', 'video/quicktime', 'video/webm')"
    " AND byte_size <= 104857600 AND duration_ms IS NOT NULL AND duration_ms >= 1 AND duration_ms <= 60500)"
)
MEDIA_INPUT = "(input_method = 'MEDIA') = (input_text IS NULL)"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _is_mysql() -> bool:
    return op.get_context().dialect.name in ("mysql", "mariadb")


def _not_blank(column: str, nullable: bool) -> str:
    prefix = f"{column} IS NULL OR " if nullable else ""
    if _is_mysql():
        return f"{prefix}REGEXP_LIKE({column}, '[^[:space:]]')"
    return f"{prefix}TRIM({column}, {WHITESPACE}) <> ''"


# --- SQLite: rebuild a table from its stored DDL ---------------------------------------------


def _sqlite_rebuild(table: str, edits: list[tuple[str, str]]) -> None:
    """Recreate `table` with `edits` applied to its CREATE TABLE text (each must match once),
    copy the stored (not generated) columns, and restore its indexes."""
    bind = op.get_bind()
    ddl = bind.execute(sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :t"),
                       {"t": table}).scalar_one()
    indexes = bind.execute(sa.text(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = :t AND sql IS NOT NULL"),
        {"t": table}).scalars().all()
    for old, new in edits:
        if ddl.count(old) != 1:
            raise RuntimeError(f"0043: unexpected {table} schema near {old[:60]!r}")
        ddl = ddl.replace(old, new)
    # PRAGMA table_xinfo: hidden 2/3 are generated columns.
    columns = ", ".join(row[1] for row in bind.exec_driver_sql(f"PRAGMA table_xinfo({table})") if row[6] == 0)
    temporary = f"_0043_{table}"
    bind.exec_driver_sql(f"CREATE TABLE {temporary} {ddl[ddl.index('('):]}")
    bind.exec_driver_sql(f"INSERT INTO {temporary} ({columns}) SELECT {columns} FROM {table}")
    bind.exec_driver_sql(f"DROP TABLE {table}")
    bind.exec_driver_sql(f"ALTER TABLE {temporary} RENAME TO {table}")
    for index in indexes:
        bind.exec_driver_sql(index)


def _check(name: str, sql: str) -> str:
    return f"CONSTRAINT {name} CHECK ({sql})"


def _sqlite_edits(upgrade: bool) -> dict[str, list[tuple[str, str]]]:
    def pair(old: str, new: str) -> tuple[str, str]:
        return (old, new) if upgrade else (new, old)

    return {
        "background_tasks": [pair(_check("ck_background_tasks_kind", _in("kind", OLD_TASK_KINDS)),
                                  _check("ck_background_tasks_kind", _in("kind", NEW_TASK_KINDS)))],
        "interview_intent_reviews": [pair(
            _check("ck_interview_intent_reviews_processing_kind", _in("processing_kind", OLD_REVIEW_KINDS)),
            _check("ck_interview_intent_reviews_processing_kind", _in("processing_kind", NEW_REVIEW_KINDS)))],
        "manual_draft_corrections": [
            pair("\tinput_text TEXT NOT NULL, \n", "\tinput_text TEXT, \n"),
            pair(_check("ck_manual_draft_corrections_input_text_not_blank", _not_blank("input_text", False)),
                 _check("ck_manual_draft_corrections_input_text_not_blank", _not_blank("input_text", True))
                 + ", \n\t" + _check("ck_manual_draft_corrections_media_input", MEDIA_INPUT)),
            pair(_check("ck_manual_draft_corrections_input_method", _in("input_method", OLD_METHODS)),
                 _check("ck_manual_draft_corrections_input_method", _in("input_method", NEW_METHODS))),
        ],
        "manual_media": [
            pair(_check("ck_manual_media_media_shape", OLD_SHAPE), _check("ck_manual_media_media_shape", NEW_SHAPE)),
            pair(_check("ck_manual_media_kind", _in("kind", OLD_MEDIA_KINDS)),
                 _check("ck_manual_media_kind", _in("kind", NEW_MEDIA_KINDS))),
        ],
        "manual_media_snapshot_refs": [pair(
            _check("ck_manual_media_snapshot_refs_holder_kind", _in("holder_kind", OLD_HOLDERS)),
            _check("ck_manual_media_snapshot_refs_holder_kind", _in("holder_kind", NEW_HOLDERS)))],
    }


# --- MySQL --------------------------------------------------------------------------------------


def _replace_check(batch, name: str, sql: str) -> None:
    batch.drop_constraint(op.f(name), type_="check")
    batch.create_check_constraint(op.f(name), sql)


def _mysql_checks(upgrade: bool) -> None:
    def pick(old, new):
        return new if upgrade else old

    with op.batch_alter_table("background_tasks") as batch:
        _replace_check(batch, "ck_background_tasks_kind", _in("kind", pick(OLD_TASK_KINDS, NEW_TASK_KINDS)))
    with op.batch_alter_table("interview_intent_reviews") as batch:
        _replace_check(batch, "ck_interview_intent_reviews_processing_kind",
                       _in("processing_kind", pick(OLD_REVIEW_KINDS, NEW_REVIEW_KINDS)))
    with op.batch_alter_table("manual_media") as batch:
        _replace_check(batch, "ck_manual_media_kind", _in("kind", pick(OLD_MEDIA_KINDS, NEW_MEDIA_KINDS)))
        _replace_check(batch, "ck_manual_media_media_shape", pick(OLD_SHAPE, NEW_SHAPE))
    with op.batch_alter_table("manual_media_snapshot_refs") as batch:
        _replace_check(batch, "ck_manual_media_snapshot_refs_holder_kind",
                       _in("holder_kind", pick(OLD_HOLDERS, NEW_HOLDERS)))


def _mysql_upgrade() -> None:
    _mysql_checks(upgrade=True)
    with op.batch_alter_table("manual_draft_corrections") as batch:
        batch.drop_constraint(op.f("ck_manual_draft_corrections_input_text_not_blank"), type_="check")
        batch.drop_constraint(op.f("ck_manual_draft_corrections_input_method"), type_="check")
    with op.batch_alter_table("manual_draft_corrections") as batch:
        batch.alter_column("input_text", existing_type=sa.Text(), nullable=True)
        batch.create_check_constraint(op.f("ck_manual_draft_corrections_input_text_not_blank"),
                                      _not_blank("input_text", True))
        batch.create_check_constraint(op.f("ck_manual_draft_corrections_input_method"),
                                      _in("input_method", NEW_METHODS))
        batch.create_check_constraint(op.f("ck_manual_draft_corrections_media_input"), MEDIA_INPUT)


def _mysql_downgrade() -> None:
    with op.batch_alter_table("manual_draft_corrections") as batch:
        batch.drop_constraint(op.f("ck_manual_draft_corrections_media_input"), type_="check")
        batch.drop_constraint(op.f("ck_manual_draft_corrections_input_method"), type_="check")
        batch.drop_constraint(op.f("ck_manual_draft_corrections_input_text_not_blank"), type_="check")
    with op.batch_alter_table("manual_draft_corrections") as batch:
        batch.alter_column("input_text", existing_type=sa.Text(), nullable=False)
        batch.create_check_constraint(op.f("ck_manual_draft_corrections_input_text_not_blank"),
                                      _not_blank("input_text", False))
        batch.create_check_constraint(op.f("ck_manual_draft_corrections_input_method"),
                                      _in("input_method", OLD_METHODS))
    _mysql_checks(upgrade=False)


# --- entry points --------------------------------------------------------------------------------

DOWNGRADE_BLOCKERS = (
    ("manual_media", "kind = 'VIDEO'"),
    ("manual_media_snapshot_refs", "holder_kind = 'MEDIA_WRITING'"),
    ("manual_draft_corrections", "input_method = 'MEDIA'"),
    ("interview_intent_reviews", "processing_kind = 'MEDIA_WRITING'"),
    ("background_tasks", "kind IN ('REVIEW_MEDIA_WRITING', 'DRAFT_MEDIA_WRITING')"),
)


def _downgrade_preflight() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    for table, condition in DOWNGRADE_BLOCKERS:
        count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE {condition}")).scalar()
        if count:
            raise RuntimeError(f"{table}: {count} row(s) use a 0043 value ({condition});"
                               " remove them before downgrading below 0043")


def upgrade() -> None:
    if _is_mysql():
        _mysql_upgrade()
        return
    for table, edits in _sqlite_edits(upgrade=True).items():
        _sqlite_rebuild(table, edits)


def downgrade() -> None:
    _downgrade_preflight()
    if _is_mysql():
        _mysql_downgrade()
        return
    for table, edits in _sqlite_edits(upgrade=False).items():
        _sqlite_rebuild(table, edits)

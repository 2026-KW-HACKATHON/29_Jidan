"""Sections written from photos and videos (OpenAPI 0.12.0): owner videos, their posters and
the media-writing tasks.

Revision ID: 0043
Revises: 0042

* manual_media: kind VIDEO (MP4/QuickTime/WebM, <= 100 MiB, 1..60000 ms) and
  `poster_media_id`, the IMAGE row the server derived from a video at upload (unique; only a
  VIDEO has one).
* manual_photo_attachments: `video_media_id`, the video a section attachment stands for; its
  `media_id` is then the poster image, so readers keep seeing images only. Section-only.
* manual_draft_corrections: input_method MEDIA (the target section's photos/videos), which has
  no input_text; TEXT/VOICE keep requiring it.
* background_tasks: kinds REVIEW_MEDIA_WRITING and DRAFT_MEDIA_WRITING;
  interview_intent_reviews: processing_kind MEDIA_WRITING.

SQLite cannot alter a CHECK, and a batch table copy cannot INSERT into the generated columns of
manual_photo_attachments / manual_draft_corrections (see 0042), so on SQLite each table is
rebuilt from its own stored DDL with exact text edits, copying only the stored columns. The
downgrade refuses (before any DDL; MySQL DDL is not transactional) while rows use a new value.
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
    " AND byte_size <= 104857600 AND duration_ms IS NOT NULL AND duration_ms >= 1 AND duration_ms <= 60000)"
)
POSTER_FOR_VIDEO = "poster_media_id IS NULL OR kind = 'VIDEO'"
VIDEO_ON_SECTION = "video_media_id IS NULL OR section_id IS NOT NULL"
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
    copy the stored (not generated) columns both versions have, and restore its indexes."""
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

    def stored(name: str) -> list[str]:  # PRAGMA table_xinfo: hidden 2/3 are generated columns
        return [row[1] for row in bind.exec_driver_sql(f"PRAGMA table_xinfo({name})") if row[6] == 0]

    old_columns = stored(table)
    temporary = f"_0043_{table}"
    head = ddl.index("(")
    bind.exec_driver_sql(f"CREATE TABLE {temporary} {ddl[head:]}")
    columns = ", ".join(column for column in stored(temporary) if column in old_columns)
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

    blank_old, blank_new = _not_blank("input_text", False), _not_blank("input_text", True)
    return {
        "background_tasks": [pair(_check("ck_background_tasks_kind", _in("kind", OLD_TASK_KINDS)),
                                  _check("ck_background_tasks_kind", _in("kind", NEW_TASK_KINDS)))],
        "interview_intent_reviews": [pair(
            _check("ck_interview_intent_reviews_processing_kind", _in("processing_kind", OLD_REVIEW_KINDS)),
            _check("ck_interview_intent_reviews_processing_kind", _in("processing_kind", NEW_REVIEW_KINDS)))],
        "manual_draft_corrections": [
            pair("\tinput_text TEXT NOT NULL, \n", "\tinput_text TEXT, \n"),
            pair(_check("ck_manual_draft_corrections_input_text_not_blank", blank_old),
                 _check("ck_manual_draft_corrections_input_text_not_blank", blank_new)
                 + ", \n\t" + _check("ck_manual_draft_corrections_media_input", MEDIA_INPUT)),
            pair(_check("ck_manual_draft_corrections_input_method", _in("input_method", OLD_METHODS)),
                 _check("ck_manual_draft_corrections_input_method", _in("input_method", NEW_METHODS))),
        ],
        "manual_media": [
            pair("\tcontent_deleted_at DATETIME, \n",
                 "\tcontent_deleted_at DATETIME, \n\tposter_media_id CHAR(36), \n"),
            pair(_check("ck_manual_media_media_shape", OLD_SHAPE), _check("ck_manual_media_media_shape", NEW_SHAPE)),
            pair(_check("ck_manual_media_kind", _in("kind", OLD_MEDIA_KINDS)),
                 _check("ck_manual_media_kind", _in("kind", NEW_MEDIA_KINDS))
                 + ", \n\t" + _check("ck_manual_media_poster_for_video", POSTER_FOR_VIDEO)),
            pair("\tCONSTRAINT uq_manual_media_object_key UNIQUE (object_key)\n",
                 "\tCONSTRAINT uq_manual_media_object_key UNIQUE (object_key), \n"
                 "\tCONSTRAINT fk_manual_media_poster_media_id_manual_media FOREIGN KEY(poster_media_id)"
                 " REFERENCES manual_media (id), \n"
                 "\tCONSTRAINT uq_manual_media_poster_media_id UNIQUE (poster_media_id)\n"),
        ],
        "manual_photo_attachments": [
            pair("\tcaption VARCHAR(300), \n", "\tcaption VARCHAR(300), \n\tvideo_media_id CHAR(36), \n"),
            pair(_check("ck_manual_photo_attachments_sort_order", "sort_order >= 0"),
                 _check("ck_manual_photo_attachments_sort_order", "sort_order >= 0")
                 + ", \n\t" + _check("ck_manual_photo_attachments_video_on_section", VIDEO_ON_SECTION)),
            pair("\tCONSTRAINT fk_manual_photo_attachments_media_id_manual_media FOREIGN KEY(media_id)"
                 " REFERENCES manual_media (id), \n",
                 "\tCONSTRAINT fk_manual_photo_attachments_media_id_manual_media FOREIGN KEY(media_id)"
                 " REFERENCES manual_media (id), \n"
                 "\tCONSTRAINT fk_manual_photo_attachments_video_media_id_manual_media FOREIGN KEY(video_media_id)"
                 " REFERENCES manual_media (id), \n"),
        ],
    }


# --- MySQL --------------------------------------------------------------------------------------


def _replace_check(batch, name: str, sql: str) -> None:
    batch.drop_constraint(op.f(name), type_="check")
    batch.create_check_constraint(op.f(name), sql)


def _mysql_upgrade() -> None:
    with op.batch_alter_table("background_tasks") as batch:
        _replace_check(batch, "ck_background_tasks_kind", _in("kind", NEW_TASK_KINDS))
    with op.batch_alter_table("interview_intent_reviews") as batch:
        _replace_check(batch, "ck_interview_intent_reviews_processing_kind",
                       _in("processing_kind", NEW_REVIEW_KINDS))
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
    with op.batch_alter_table("manual_media") as batch:
        batch.add_column(sa.Column("poster_media_id", sa.CHAR(length=36), nullable=True))
        _replace_check(batch, "ck_manual_media_kind", _in("kind", NEW_MEDIA_KINDS))
        _replace_check(batch, "ck_manual_media_media_shape", NEW_SHAPE)
        batch.create_check_constraint(op.f("ck_manual_media_poster_for_video"), POSTER_FOR_VIDEO)
        batch.create_unique_constraint(op.f("uq_manual_media_poster_media_id"), ["poster_media_id"])
        batch.create_foreign_key(op.f("fk_manual_media_poster_media_id_manual_media"), "manual_media",
                                 ["poster_media_id"], ["id"])
    with op.batch_alter_table("manual_photo_attachments") as batch:
        batch.add_column(sa.Column("video_media_id", sa.CHAR(length=36), nullable=True))
        batch.create_check_constraint(op.f("ck_manual_photo_attachments_video_on_section"), VIDEO_ON_SECTION)
        batch.create_foreign_key(op.f("fk_manual_photo_attachments_video_media_id_manual_media"),
                                 "manual_media", ["video_media_id"], ["id"])
        batch.create_index("ix_manual_photo_attachments_video_media_id", ["video_media_id"], unique=False)


def _mysql_downgrade() -> None:
    with op.batch_alter_table("manual_photo_attachments") as batch:
        batch.drop_constraint(op.f("fk_manual_photo_attachments_video_media_id_manual_media"), type_="foreignkey")
        batch.drop_index("ix_manual_photo_attachments_video_media_id")
        batch.drop_constraint(op.f("ck_manual_photo_attachments_video_on_section"), type_="check")
        batch.drop_column("video_media_id")
    with op.batch_alter_table("manual_media") as batch:
        batch.drop_constraint(op.f("fk_manual_media_poster_media_id_manual_media"), type_="foreignkey")
        batch.drop_constraint(op.f("uq_manual_media_poster_media_id"), type_="unique")
        batch.drop_constraint(op.f("ck_manual_media_poster_for_video"), type_="check")
        _replace_check(batch, "ck_manual_media_media_shape", OLD_SHAPE)
        _replace_check(batch, "ck_manual_media_kind", _in("kind", OLD_MEDIA_KINDS))
        batch.drop_column("poster_media_id")
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
    with op.batch_alter_table("interview_intent_reviews") as batch:
        _replace_check(batch, "ck_interview_intent_reviews_processing_kind",
                       _in("processing_kind", OLD_REVIEW_KINDS))
    with op.batch_alter_table("background_tasks") as batch:
        _replace_check(batch, "ck_background_tasks_kind", _in("kind", OLD_TASK_KINDS))


# --- entry points --------------------------------------------------------------------------------

DOWNGRADE_BLOCKERS = (
    ("manual_media", "kind = 'VIDEO' OR poster_media_id IS NOT NULL"),
    ("manual_photo_attachments", "video_media_id IS NOT NULL"),
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
    op.create_index("ix_manual_photo_attachments_video_media_id", "manual_photo_attachments", ["video_media_id"])


def downgrade() -> None:
    _downgrade_preflight()
    if _is_mysql():
        _mysql_downgrade()
        return
    op.drop_index("ix_manual_photo_attachments_video_media_id", "manual_photo_attachments")
    for table, edits in _sqlite_edits(upgrade=False).items():
        _sqlite_rebuild(table, edits)

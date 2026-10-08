"""Every write to a draft's content tables changes the draft version in the same transaction.

Readers and writers of a draft compare `manual_versions.revision` (expectedRevision, the
correction apply's re-check under REPEATABLE READ): a content change that left `revision`
unchanged would be invisible to them and could be overwritten without a REVISION_CONFLICT.
The writers are `app.manual_editing.replace_content` (revision and content_revision + 1) and
`write_initial_content` (a fresh draft: generation_status RUNNING -> READY, revision stays 1).

`draft_revision_guard` (autouse, from conftest) follows each connection's transaction on SQLite
and MySQL, per version: when application code (`app/`, outside an operator CLI run) writes a
row of `CONTENT_TABLES`, the version that row belongs to must also have its `revision` or
`generation_status` updated, or be inserted, before the transaction commits. Content of
version A with a revision change of version B is a violation. A content row's version comes
from the statement's own parameters (INSERT) or, for UPDATE/DELETE by id, from a plain read on
the same DBAPI connection (it sees the transaction's rows and is invisible to the guards). A
write whose version cannot be resolved counts as unmatched, so a new statement shape fails
loudly instead of passing.
"""
import re
import threading

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.lock_scope import _issued_by_application

CONTENT_TABLES = ("manual_shifts", "manual_sections", "manual_steps", "manual_photo_attachments",
                  "manual_review_issues")
_CONTENT = re.compile(rf"^(INSERT INTO|UPDATE|DELETE FROM) ({'|'.join(CONTENT_TABLES)})\b", re.IGNORECASE)
_VERSION_INSERT = re.compile(r"^INSERT INTO manual_versions\b", re.IGNORECASE)
_VERSION_UPDATE = re.compile(r"^UPDATE\s+`?manual_versions`?\s+SET\s+", re.IGNORECASE)
_VERSION_ASSIGNMENT = re.compile(
    r"^(?:`?manual_versions`?\.)?`?(?:revision|generation_status)`?\s*=", re.IGNORECASE,
)
# The version of an existing content row, by the row's id.
_ROW_VERSION = {table: f"SELECT version_id FROM {table} WHERE id = {{p}}"
                for table in CONTENT_TABLES if table != "manual_steps"}
_ROW_VERSION["manual_steps"] = ("SELECT s.version_id FROM manual_steps t JOIN manual_sections s "
                                "ON s.id = t.section_id WHERE t.id = {p}")
_SECTION_VERSION = "SELECT version_id FROM manual_sections WHERE id = {p}"

_found: list[str] = []
_lock = threading.Lock()


def _updates_version_state(statement: str) -> bool:
    """Inspect SET assignment names; WHERE predicates and values cannot count as writes.

    Quotes and expression parentheses keep their commas/WHERE text inside the current value.
    The guard detects an explicit assignment, not whether its value differs from the old row.
    """
    sql = statement.strip()
    update = _VERSION_UPDATE.match(sql)
    if update is None:
        return False
    start = index = update.end()
    quote, depth = None, 0
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            after = index + 5
            where = (sql[index:after].upper() == "WHERE"
                     and (index == 0 or not (sql[index - 1].isalnum() or sql[index - 1] == "_"))
                     and (after == len(sql) or not (sql[after].isalnum() or sql[after] == "_")))
            if char == "," or where:
                if _VERSION_ASSIGNMENT.match(sql[start:index].strip()):
                    return True
                if where:
                    return False
                start = index + 1
        index += 1
    return _VERSION_ASSIGNMENT.match(sql[start:].strip()) is not None


def _read(conn, query: str, value) -> str | None:
    placeholder = "?" if conn.dialect.paramstyle == "qmark" else "%s"
    cursor = conn.connection.dbapi_connection.cursor()
    try:
        cursor.execute(query.format(p=placeholder), (value,))
        row = cursor.fetchone()
    finally:
        cursor.close()
    return row[0] if row else None


def _key(params: dict, table: str):
    """The id a single-row UPDATE/DELETE names: ORM flush `<table>_id`, Core `id_1`."""
    return params.get(f"{table}_id", params.get("id_1"))


def _content_versions(conn, kind: str, table: str, rows: list[dict]) -> set[str]:
    versions = set()
    for params in rows:
        if kind == "INSERT INTO":
            version = params.get("version_id")
            if version is None and table == "manual_steps" and params.get("section_id"):
                version = _read(conn, _SECTION_VERSION, params["section_id"])
        else:
            key = _key(params, table)
            version = _read(conn, _ROW_VERSION[table], key) if key is not None else None
        versions.add(version or f"unresolved {kind} {table}")
    return versions


@event.listens_for(Engine, "before_cursor_execute")
def _track(conn, cursor, statement, parameters, context, executemany):
    sql = " ".join(statement.replace("`", "").split())
    rows = list(getattr(context, "compiled_parameters", None) or [{}]) if context is not None else [{}]
    content = _CONTENT.match(sql)
    if content is not None:
        if _issued_by_application():
            kind, table = content.group(1).upper(), content.group(2).lower()
            conn.info.setdefault("jidan_draft_writes", set()).update(_content_versions(conn, kind, table, rows))
    elif _VERSION_INSERT.match(sql):
        conn.info.setdefault("jidan_draft_changed", set()).update(p.get("id") for p in rows if p.get("id"))
    elif _updates_version_state(sql):
        conn.info.setdefault("jidan_draft_changed", set()).update(
            key for p in rows if (key := _key(p, "manual_versions")) is not None)


def _end(conn, *, committed: bool) -> None:
    if conn.invalidated:
        # A lost connection (the driver reported a disconnect): nothing was committed on it and
        # `conn.info` would raise PendingRollbackError, replacing the application's own error.
        return
    written = conn.info.pop("jidan_draft_writes", set())
    changed = conn.info.pop("jidan_draft_changed", set())
    missing = written - changed
    if committed and missing:
        with _lock:
            _found.append(f"content of version(s) {sorted(missing)} written without that version's revision change")


@event.listens_for(Engine, "commit")
def _commit(conn):
    _end(conn, committed=True)


@event.listens_for(Engine, "rollback")
def _rollback(conn):
    _end(conn, committed=False)


@pytest.fixture(autouse=True)
def draft_revision_guard():
    with _lock:
        _found.clear()
    yield
    with _lock:
        found, _found[:] = list(_found), []
    assert not found, "\n".join(found)

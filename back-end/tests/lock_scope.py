"""Every MySQL locking read, UPDATE and DELETE must name its rows by a primary or unique key.

FOR UPDATE / FOR SHARE locks every row MySQL examines, and MySQL picks the access path from
table statistics that drift as rows come and go. A lock on a non-unique condition or a JOIN
therefore has a plan-dependent scope: driving a posting's PENDING requests from a status index
locked every store's requests and deadlocked unrelated stores (1213). Under REPEATABLE READ a
range lock also takes the next-key gap, which belongs to another store's rows. A key lookup
locks the same single row whatever the plan.

UPDATE and DELETE lock the rows they examine the same way. A primary key `IN (...)` list is not
a set of point locks either: on a freshly filled table MySQL planned
`DELETE ... WHERE id IN (25 keys)` as a clustered index scan that held 190 row locks and
deadlocked two idempotency purges (B05). So an `IN` list passes only in a SKIP LOCKED read.

`lock_scope_guard` (autouse, from conftest) records each locking read a test sends to MySQL,
and each UPDATE/DELETE that application code (`app/`) sends, and fails the test on any that is
neither a key lookup nor a reviewed exception below. Find rows with a plain read and lock,
update or delete them by primary key instead (`app.db.keyed`, `app.jobs.state.lock_each`).
"""
import os
import re
import sys
import threading

import pytest
from sqlalchemy import UniqueConstraint, event
from sqlalchemy.engine import Engine

import app
from app.db.models import Base
from app.operator_cli import operator_cli_active

LOCKING = re.compile(r"\bFOR (?:UPDATE|SHARE)\b|\bLOCK IN SHARE MODE\b", re.IGNORECASE)
WRITE = re.compile(r"^(?:UPDATE|DELETE)\b", re.IGNORECASE)
# One table, named once: `UPDATE t SET ...` / `DELETE FROM t [WHERE ...]`. Multi-table forms
# (`UPDATE a, b`, `UPDATE a JOIN b`, `DELETE a FROM a JOIN b`, `DELETE FROM a USING ...`) lock
# rows of every table they read, and so does a subquery in a write (shared next-key locks).
SINGLE_WRITE = re.compile(r"^(?:UPDATE (\w+) SET |DELETE FROM (\w+)(?: WHERE |$))", re.IGNORECASE)

# Reviewed exceptions: (table, condition column) -> why its lock scope is acceptable.
EXCEPTIONS = {
    # app.media.references._any: FOR SHARE existence checks of links to one media row. The
    # caller holds that media row's X lock, which every linker takes first, so a shared range
    # lock can make an unrelated insert wait but never closes a cycle; it exists to read the
    # latest commit under REPEATABLE READ.
    ("interview_turn_photos", "media_id"): "media reference check (FOR SHARE)",
    ("manual_photo_attachments", "media_id"): "media reference check (FOR SHARE)",
    ("manual_media_snapshot_refs", "media_id"): "media reference check (FOR SHARE)",
    ("manual_qa_photos", "media_id"): "media reference check (FOR SHARE)",
    # app.jobs.state.withdraw_confirmation: the effects of one request, a prefix of the
    # (request_id, application_id) primary key read on the clustered index, inside a READ
    # COMMITTED transition (no gap locks) under the posting lock.
    ("application_selection_effects", "request_id"): "primary key prefix in READ COMMITTED",
}


def _unique_keys() -> dict[str, list[frozenset[str]]]:
    keys: dict[str, list[frozenset[str]]] = {}
    for table in Base.metadata.tables.values():
        found = [frozenset(column.name for column in table.primary_key.columns)]
        found += [frozenset(c.name for c in constraint.columns)
                  for constraint in table.constraints if isinstance(constraint, UniqueConstraint)]
        found += [frozenset(c.name for c in index.columns) for index in table.indexes if index.unique]
        found += [frozenset((column.name,)) for column in table.columns if column.unique]
        keys[table.name] = found
    return keys


UNIQUE_KEYS = _unique_keys()


def violation(statement: str) -> str | None:
    """Why `statement` locks by more than a key, or None when its lock scope is one row per key."""
    # Keywords in any case; identifiers without MySQL backtick quoting (`stores` is stores).
    sql = " ".join(statement.replace("`", "").split())
    write = WRITE.match(sql)
    if write is None and (not LOCKING.search(sql) or "SKIP LOCKED" in sql.upper()):
        # SKIP LOCKED sweeps never wait at the locking read; their batches are bounded.
        return None
    if write:
        single = SINGLE_WRITE.match(sql)
        if single is None:
            return f"multi-table write: {sql[:80]}"
        table = single.group(1) or single.group(2)
        if re.search(r"\bSELECT\b", sql, re.IGNORECASE):
            return f"subquery in a write to {table}"
    else:
        table = re.search(r"\bFROM (\w+)", sql, re.IGNORECASE).group(1)
    if re.search(r"\bJOIN\b", sql, re.IGNORECASE):
        return f"JOIN in a locking read of {table}"
    where = re.search(r"\bWHERE (.*?)(?: ORDER BY | LIMIT | FOR | LOCK IN |$)", sql, re.IGNORECASE)
    if where is None:
        return f"locking read of {table} without WHERE"
    column = rf"(?:\b{table}\.|(?<![\w.]))(\w+)"  # qualified, or bare in hand-written SQL
    equal = set(re.findall(rf"{column} = [%?]", where.group(1)))  # pyformat/format or qmark
    within = re.findall(rf"{column} IN \(", where.group(1), re.IGNORECASE)
    primary = {column.name for column in Base.metadata.tables[table].primary_key.columns}
    if any(key <= equal for key in UNIQUE_KEYS.get(table, [])):
        return None
    if within and set(within) <= primary:
        return f"primary key IN list of {table} (may run as an index scan; use app.db.keyed)"
    first = re.search(rf"{column} (?:=|IN) ", where.group(1), re.IGNORECASE)
    if first is not None and (table, first.group(1)) in EXCEPTIONS:
        return None
    return f"non-unique locking read of {table} on {sorted(equal) or within}"


# The installed `app` package directory, wherever the checkout lives: a path substring such as
# "/back-end/app/" never matched in a container (/app/app/...), which silently disabled the guards.
_APP_DIRS = tuple({os.path.join(os.path.dirname(app.__file__), ""),
                   os.path.join(os.path.realpath(os.path.dirname(app.__file__)), "")})


def _issued_by_application() -> bool:
    """True when application code (`app/`) issued the statement outside an operator CLI run.

    Only the CLI entry points set `operator_cli()`; a request or job that calls the same demo
    helpers is checked like any other application code."""
    if operator_cli_active():
        return False
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename.startswith(_APP_DIRS):
            return True
        frame = frame.f_back
    return False


_recorded: list[tuple[str, str]] = []
_record_lock = threading.Lock()


@event.listens_for(Engine, "before_cursor_execute")
def _record(conn, cursor, statement, parameters, context, executemany):
    if conn.dialect.name != "mysql":
        return
    if WRITE.match(statement.lstrip()):
        if not _issued_by_application():
            return  # test setup and teardown, or an operator CLI
    elif not LOCKING.search(statement):
        return
    problem = violation(statement)
    if problem is not None:
        with _record_lock:
            _recorded.append((problem, " ".join(statement.split())[:300]))


@pytest.fixture(autouse=True)
def lock_scope_guard():
    with _record_lock:
        _recorded.clear()
    yield
    with _record_lock:
        found, _recorded[:] = list(_recorded), []
    assert not found, "\n".join(f"{problem}: {sql}" for problem, sql in found)

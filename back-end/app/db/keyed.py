"""Writes and locking reads that touch rows one primary key at a time.

`WHERE id IN (...)` is not a set of point locks in MySQL: the optimizer may plan it as a scan of
the clustered index (it did on a freshly filled table with stale statistics), and a scan locks
every row it examines. Two idempotency purges deadlocked that way (1213, B05), and the same
plan would let a sweep lock rows that unrelated requests hold. A non-unique condition is worse:
it takes next-key (gap) locks on its index that other rows' INSERTs wait on.

Find the keys with a plain read, then call these helpers: each statement is `WHERE id = ?`
(plus the caller's re-check conditions), issued in ascending key order so two transactions
never take the same rows in opposite orders. `tests/lock_scope.py` enforces the shape.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session


def ordered_keys(keys: Iterable[str]) -> list[str]:
    return sorted(dict.fromkeys(keys))


def update_by_key(db: Session, model, keys: Iterable[str], values: dict[str, Any], *where) -> int:
    """UPDATE each row by primary key (still matching `where`); returns how many changed."""
    changed = 0
    for key in ordered_keys(keys):
        changed += db.execute(
            update(model).where(model.id == key, *where).values(**values)
            .execution_options(synchronize_session=False)
        ).rowcount
    return changed


def delete_by_key(db: Session, model, keys: Iterable[str], *where, synchronize: str | bool = False) -> int:
    """DELETE each row by primary key (still matching `where`); returns how many went.
    `synchronize="auto"` also removes loaded copies from the session."""
    removed = 0
    for key in ordered_keys(keys):
        removed += db.execute(
            delete(model).where(model.id == key, *where).execution_options(synchronize_session=synchronize)
        ).rowcount
    return removed


def lock_by_key(db: Session, model, keys: Iterable[str], *, populate_existing: bool = False) -> dict[str, Any]:
    """SELECT ... FOR UPDATE each row by primary key in ascending order; missing keys are absent."""
    options = {"populate_existing": True} if populate_existing else {}
    rows = {}
    for key in ordered_keys(keys):
        row = db.scalar(select(model).where(model.id == key).with_for_update().execution_options(**options))
        if row is not None:
            rows[key] = row
    return rows

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit when the block succeeds, roll back on any exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


class UncommittedWriteError(RuntimeError):
    """A request handler wrote through its session but never called commit()."""


_ROOT = object()  # a write seen before the outermost transaction object exists


def _has_pending_writes(session: Session) -> bool:
    """True if some tracked write is not inside a rolled-back savepoint."""
    rolled_back = session.info.get("rolled_back_savepoints", set())
    for owner in session.info.get("uncommitted_writes", ()):
        while owner is not None and owner is not _ROOT and owner not in rolled_back:
            owner = owner.parent
        if owner is None or owner is _ROOT:
            return True
    return False


def _track_writes(session: Session) -> None:
    """Remember ORM writes until the outermost transaction commits or rolls back.

    `after_commit` also fires when a SAVEPOINT (`begin_nested()`) is released, so it must not
    forget writes. Each write is attributed to the innermost open transaction. Releasing a
    savepoint keeps its writes (they now belong to the parent chain), rolling a savepoint back
    discards only the writes made inside it (including nested ones), and only the end of the
    outermost transaction (commit, rollback or close) clears everything.
    """

    def mark(*_args) -> None:
        transaction = session.get_nested_transaction() or session.get_transaction()
        session.info.setdefault("uncommitted_writes", set()).add(
            transaction if transaction is not None else _ROOT
        )

    def on_execute(state) -> None:
        if state.is_insert or state.is_update or state.is_delete:
            mark()

    def on_soft_rollback(_session, previous_transaction) -> None:
        if previous_transaction.parent is not None:  # a savepoint (or subtransaction)
            session.info.setdefault("rolled_back_savepoints", set()).add(previous_transaction)

    def on_transaction_end(_session, transaction) -> None:
        if transaction.parent is None:
            session.info.pop("uncommitted_writes", None)
            session.info.pop("rolled_back_savepoints", None)

    event.listen(session, "after_flush", mark)
    event.listen(session, "do_orm_execute", on_execute)
    event.listen(session, "after_soft_rollback", on_soft_rollback)
    event.listen(session, "after_transaction_end", on_transaction_end)


def get_session() -> Iterator[Session]:
    """FastAPI dependency with explicit commit: the handler calls `session.commit()` itself.

    Always inject it with `SessionDep` (scope="function"), so this cleanup runs before the
    response is sent. Never commits here; rolls back on exceptions and on writes that were
    never committed (raising UncommittedWriteError, which surfaces as 500 instead of a
    silently lost write), then closes.
    """
    session = get_session_factory()()
    _track_writes(session)
    try:
        yield session
        if (
            _has_pending_writes(session)
            or session.new or session.dirty or session.deleted
        ):
            raise UncommittedWriteError("Session has writes that were never committed")
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session, scope="function")]

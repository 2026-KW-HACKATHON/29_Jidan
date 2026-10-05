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


def _track_writes(session: Session) -> None:
    """Remember ORM writes until they are committed or rolled back."""

    def mark(*_args) -> None:
        session.info["uncommitted_writes"] = True

    def clear(*_args) -> None:
        session.info.pop("uncommitted_writes", None)

    def on_execute(state) -> None:
        if state.is_insert or state.is_update or state.is_delete:
            mark()

    event.listen(session, "after_flush", mark)
    event.listen(session, "do_orm_execute", on_execute)
    event.listen(session, "after_commit", clear)
    event.listen(session, "after_rollback", clear)


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
            session.info.get("uncommitted_writes")
            or session.new or session.dirty or session.deleted
        ):
            raise UncommittedWriteError("Session has writes that were never committed")
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session, scope="function")]

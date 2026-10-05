from collections.abc import Iterator
from contextlib import contextmanager

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


def get_session() -> Iterator[Session]:
    """FastAPI dependency. The request handler's transaction ends with the response."""
    with session_scope() as session:
        yield session

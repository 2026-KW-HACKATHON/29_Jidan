"""Commit rule for request handlers (#104/#105 follow it).

Writes are committed by the handler itself (`session.commit()`), before the response - status
and Set-Cookie - leaves the server. If the commit fails the client gets a bare 500 and no
cookie. `SessionDep` (function-scoped dependency) closes the session before the response is
sent; with the default scope FastAPI/Starlette runs that cleanup after the response.
"""

import pytest
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionDep, get_session
from app.db.models import User

seen_by_middleware: list[int] = []
events: list[str] = []


def new_user(sub="sub-1"):
    return User(google_sub=sub, google_email="a@example.com", email_verified=True,
                role="OWNER", name="n", phone_number="1")


@pytest.fixture(params=["sqlite", pytest.param("mysql", marks=pytest.mark.mysql)])
def db_engine(request, monkeypatch):
    engine = request.getfixturevalue("engine" if request.param == "sqlite" else "mysql_engine")
    monkeypatch.setattr("app.db.session.get_session_factory",
                        lambda: sessionmaker(engine, expire_on_commit=False))
    yield engine
    with Session(engine) as cleaner:
        cleaner.execute(delete(User))
        cleaner.commit()


def user_count(engine) -> int:
    with Session(engine) as check:
        return check.scalar(select(func.count()).select_from(User))


@pytest.fixture
def client(db_engine):
    app = FastAPI()
    seen_by_middleware.clear()

    @app.middleware("http")
    async def observe(request, call_next):
        response = await call_next(request)  # returns once the response is about to be sent
        seen_by_middleware.append(user_count(db_engine))
        events.append("response-ready")
        return response

    @app.post("/ok")
    def ok(session: SessionDep, response: Response):
        session.add(new_user())
        session.commit()
        response.set_cookie("sid", "secret-session")
        return {"status": "created"}

    @app.post("/commit-fails")
    def commit_fails(session: SessionDep, response: Response):
        session.add(new_user("dup"))
        session.add(new_user("dup"))  # unique violation surfaces at commit()
        response.set_cookie("sid", "must-not-leak")
        session.commit()
        return {"status": "created"}

    @app.post("/commit-patched")
    def commit_patched(session: SessionDep, response: Response):
        session.add(new_user("patched"))

        def broken():
            raise OperationalError("COMMIT secret-sql", {}, Exception("secret-host:3306"))

        session.commit = broken
        response.set_cookie("sid", "must-not-leak")
        session.commit()
        return {"status": "created"}

    @app.post("/handler-error")
    def handler_error(session: SessionDep):
        session.add(new_user("rolled-back"))
        session.flush()
        raise RuntimeError("handler failed")

    @app.post("/conflict")
    def conflict(session: SessionDep):
        session.add(new_user("conflict"))
        session.flush()
        raise HTTPException(status_code=409, detail="conflict")

    @app.post("/forgot-commit")
    def forgot_commit(session: SessionDep, response: Response):
        session.add(new_user("forgot"))
        response.set_cookie("sid", "must-not-leak")
        return {"status": "created"}

    @app.post("/bulk-forgot-commit")
    def bulk_forgot_commit(session: SessionDep):
        from sqlalchemy import update

        session.execute(update(User).values(name="x"))
        return {"status": "updated"}

    @app.get("/read-only")
    def read_only(session: SessionDep):
        return {"count": session.scalar(select(func.count()).select_from(User))}

    def _savepoint_user(session, sub, fail=False):
        try:
            with session.begin_nested():
                session.add(new_user(sub))
                session.flush()
                if fail:
                    raise ValueError("undo savepoint")
        except ValueError:
            pass

    @app.post("/sp-closed-no-commit")  # add, flush, savepoint closes, no commit
    def sp_closed_no_commit(session: SessionDep):
        session.add(new_user("outer"))
        session.flush()
        with session.begin_nested():
            session.execute(select(1))
        return {"status": "created"}

    @app.post("/sp-closed-commit")
    def sp_closed_commit(session: SessionDep):
        session.add(new_user("outer"))
        session.flush()
        with session.begin_nested():
            session.execute(select(1))
        session.commit()
        return {"status": "created"}

    @app.post("/sp-only-no-commit")
    def sp_only_no_commit(session: SessionDep):
        _savepoint_user(session, "inner")
        return {"status": "created"}

    @app.post("/sp-rollback-then-commit")
    def sp_rollback_then_commit(session: SessionDep):
        _savepoint_user(session, "inner", fail=True)
        session.add(new_user("outer"))
        session.commit()
        return {"status": "created"}

    @app.post("/sp-rollback-only")  # the only write was undone: nothing is lost
    def sp_rollback_only(session: SessionDep):
        _savepoint_user(session, "inner", fail=True)
        return {"status": "nothing"}

    @app.post("/sp-rollback-then-forgot")  # undone savepoint must not hide the outer write
    def sp_rollback_then_forgot(session: SessionDep):
        _savepoint_user(session, "inner", fail=True)
        session.add(new_user("outer"))
        return {"status": "created"}

    @app.post("/sp-nested-no-commit")
    def sp_nested_no_commit(session: SessionDep):
        session.add(new_user("outer"))
        session.flush()
        with session.begin_nested(), session.begin_nested():
            session.add(new_user("inner"))
            session.flush()
        return {"status": "created"}

    @app.post("/sp-nested-commit")
    def sp_nested_commit(session: SessionDep):
        with session.begin_nested():
            session.add(new_user("outer"))
            session.flush()
            with session.begin_nested():
                session.add(new_user("inner"))
                session.flush()
        session.commit()
        return {"status": "created"}

    @app.post("/sp-read-only")
    def sp_read_only(session: SessionDep):
        with session.begin_nested():
            session.scalar(select(func.count()).select_from(User))
        return {"status": "ok"}

    @app.post("/sp-write-after-closed-no-commit")  # write, savepoint, write again, no commit
    def sp_write_after(session: SessionDep):
        _savepoint_user(session, "inner")
        session.add(new_user("later"))
        return {"status": "created"}

    def tracked_session():
        try:
            yield from get_session()
        finally:
            events.append("cleanup")

    @app.post("/default-scope")  # what NOT to do: cleanup runs after the response
    def default_scope(session: Session = Depends(tracked_session)):  # noqa: B008
        session.add(new_user("late"))
        return {"status": "created"}

    return TestClient(app, raise_server_exceptions=False)


def assert_bare_500(response):
    assert response.status_code == 500
    assert "set-cookie" not in response.headers
    assert response.text == "Internal Server Error"


def test_commit_finishes_before_the_response_is_sent(client, db_engine):
    response = client.post("/ok")
    assert response.status_code == 200 and response.cookies.get("sid") == "secret-session"
    assert seen_by_middleware == [1]  # a separate session already saw the committed row
    assert user_count(db_engine) == 1


def test_commit_failure_returns_bare_500_without_cookie(client, db_engine):
    response = client.post("/commit-fails")
    assert_bare_500(response)
    assert user_count(db_engine) == 0


def test_patched_commit_failure_leaks_nothing(client, db_engine):
    response = client.post("/commit-patched")
    assert_bare_500(response)
    for leaked in ("secret-sql", "secret-host", "OperationalError", "must-not-leak"):
        assert leaked not in response.text and leaked not in str(response.headers)
    assert user_count(db_engine) == 0


def test_handler_exception_rolls_back(client, db_engine):
    assert_bare_500(client.post("/handler-error"))
    assert user_count(db_engine) == 0


def test_http_exception_after_write_rolls_back_and_keeps_status(client, db_engine):
    response = client.post("/conflict")
    assert response.status_code == 409 and response.json() == {"detail": "conflict"}
    assert user_count(db_engine) == 0


def test_forgotten_commit_fails_loudly_instead_of_losing_the_write(client, db_engine):
    assert_bare_500(client.post("/forgot-commit"))
    assert user_count(db_engine) == 0


def test_forgotten_commit_after_bulk_update_is_detected(client, db_engine):
    assert_bare_500(client.post("/bulk-forgot-commit"))


def test_read_only_handler_needs_no_commit(client, db_engine):
    assert client.get("/read-only").json() == {"count": 0}


def test_default_scope_dependency_cleans_up_after_the_response(client, db_engine):
    """Documents why SessionDep is mandatory: the default scope cannot turn failures into 500."""
    events.clear()
    response = client.post("/default-scope")
    assert response.status_code == 200  # success already sent although the write is lost
    assert events == ["response-ready", "cleanup"]
    assert user_count(db_engine) == 0


def test_function_scope_cleans_up_before_the_response(client, db_engine):
    events.clear()
    client.post("/ok")
    assert events == ["response-ready"]  # SessionDep cleanup already finished (row visible)
    assert seen_by_middleware == [1]


def test_connections_are_returned_to_the_pool(client, db_engine):
    client.post("/ok")
    client.post("/handler-error")
    client.post("/commit-fails")
    if hasattr(db_engine.pool, "checkedout"):  # MySQL QueuePool
        assert db_engine.pool.checkedout() == 0


# --- savepoints must not clear the tracking of the outer transaction -------------------------


@pytest.mark.parametrize("path", ["/sp-closed-no-commit", "/sp-only-no-commit",
                                  "/sp-nested-no-commit", "/sp-write-after-closed-no-commit",
                                  "/sp-rollback-then-forgot"])
def test_savepoint_does_not_hide_a_forgotten_commit(client, db_engine, path):
    assert_bare_500(client.post(path))
    assert user_count(db_engine) == 0


@pytest.mark.parametrize("path,rows", [
    ("/sp-closed-commit", 1), ("/sp-rollback-then-commit", 1), ("/sp-nested-commit", 2)])
def test_commit_after_savepoints_persists(client, db_engine, path, rows):
    assert client.post(path).status_code == 200
    assert user_count(db_engine) == rows


def test_rolled_back_savepoint_alone_is_not_an_error(client, db_engine):
    assert client.post("/sp-rollback-only").status_code == 200
    assert user_count(db_engine) == 0


def test_read_only_savepoint_is_not_flagged(client, db_engine):
    assert client.post("/sp-read-only").status_code == 200

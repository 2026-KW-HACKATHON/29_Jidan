"""The local browser sign-in helper (e2e/browser_login.py): sessions the app accepts, guards and
refusals, on SQLite and MySQL."""
import os
import threading
from http.cookies import SimpleCookie
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db.models import AuthSession, RegistrationSession, User
from e2e import browser_login
from tests.factories import make_store, make_user, make_worker

FRONTEND = "http://127.0.0.1:5190"
KEY = "test-key-0123456789abcdef"


@pytest.fixture
def safe_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", os.getenv("DB_NAME") or "jidan_browser_test")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)


def demo_owner(db_engine, status="ACTIVE") -> str:
    with Session(db_engine) as db:
        user = make_user(db, "OWNER", google_sub="demo-seed:owner", status=status)
        make_store(db, user)
        db.commit()
        return user.id


def cookies_of(set_cookies: list[str]) -> dict[str, "SimpleCookie"]:
    jar = {}
    for value in set_cookies:
        cookie = SimpleCookie()
        cookie.load(value)
        jar.update({name: morsel for name, morsel in cookie.items()})
    return jar


def test_demo_account_gets_a_member_session_the_app_accepts(api, db_engine, safe_env):
    owner_id = demo_owner(db_engine)
    target, set_cookies = browser_login.sign_in("as=owner", None, FRONTEND)
    assert target == f"{FRONTEND}/__auth/session"
    jar = cookies_of(set_cookies)
    session = jar[auth.SESSION_COOKIE_NAME]
    # Same attributes as the OAuth callback on APP_ENV=local: usable on http://127.0.0.1.
    assert session["path"] == "/" and session["httponly"] and session["samesite"].lower() == "lax"
    assert not session["secure"] and not session["domain"]
    assert jar[auth.REGISTRATION_COOKIE_NAME].value == "" and jar[auth.REGISTRATION_COOKIE_NAME]["max-age"] == "0"
    api.cookies.set(auth.SESSION_COOKIE_NAME, session.value)
    body = api.get("/api/auth/session").json()
    assert body["user"]["id"] == owner_id and body["user"]["role"] == "OWNER"


def test_new_identity_registers_then_signs_in_as_a_member(api, db_engine, safe_env):
    target, set_cookies = browser_login.sign_in("as=new:kim-1&email=Kim1@Browser.Jidan.Example", None, FRONTEND)
    assert target == f"{FRONTEND}/__auth/signup"
    jar = cookies_of(set_cookies)
    registration = jar[auth.REGISTRATION_COOKIE_NAME]
    assert registration["path"] == "/api/auth" and not registration["secure"]
    assert jar[auth.SESSION_COOKIE_NAME]["max-age"] == "0"
    api.cookies.set(auth.REGISTRATION_COOKIE_NAME, registration.value, path="/api/auth")
    context = api.get("/api/auth/registration").json()
    assert context["identity"]["email"] == "kim1@browser.jidan.example"
    with Session(db_engine) as db:
        assert db.scalar(select(RegistrationSession.google_sub)) == "e2e:browser:kim-1"
        make_worker(db).google_sub = "e2e:browser:kim-1"  # as if the sign-up API had completed
        db.commit()
    target, set_cookies = browser_login.sign_in("as=new:kim-1", None, FRONTEND)
    assert target == f"{FRONTEND}/__auth/session"
    assert cookies_of(set_cookies)[auth.SESSION_COOKIE_NAME].value


def test_signing_in_again_revokes_the_browsers_previous_sessions(db_engine, safe_env):
    demo_owner(db_engine)
    _, first = browser_login.sign_in("as=owner", None, FRONTEND)
    _, registration = browser_login.sign_in("as=new:other", None, FRONTEND)
    header = (f"{auth.SESSION_COOKIE_NAME}={cookies_of(first)[auth.SESSION_COOKIE_NAME].value}; "
              f"{auth.REGISTRATION_COOKIE_NAME}={cookies_of(registration)[auth.REGISTRATION_COOKIE_NAME].value}")
    browser_login.sign_in("as=owner", header, FRONTEND)
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))) == 1
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 2
        assert db.scalar(select(RegistrationSession.consumed_at)) is not None


def test_next_must_stay_on_the_frontend_origin(db_engine, safe_env):
    demo_owner(db_engine)
    target, _ = browser_login.sign_in(f"as=owner&next={FRONTEND}/home?tab=1", None, FRONTEND)
    assert target == f"{FRONTEND}/home?tab=1"
    for bad in ("http://evil.example/", "http://127.0.0.1:5191/", "https://127.0.0.1:5190/", "//evil.example/",
                "/home", "http://127.0.0.1:5190@evil.example/", "http://127.0.0.1:5190\\@evil.example/",
                "http://127.0.0.1:5190/\r\nSet-Cookie: x=1", "http://127.0.0.1:5190/\tx", ""):
        with pytest.raises(browser_login.Refused) as refused:
            browser_login.sign_in(urlencode({"as": "owner", "next": bad}), None, FRONTEND)
        assert refused.value.status == 400, bad
    with Session(db_engine) as db:  # refused before anything was committed
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1


@pytest.mark.parametrize("query", [
    "", "as=", "as=admin", "as=new:", "as=new:Kim", "as=new:-x", "as=new:" + "a" * 41, "as=new:a_b",
    "as=demo-seed:owner", "as=owner&email=a@b.cd", "as=new:x&email=not-an-email", "as=new:x&email=a@b",
    "as=new:x&email=kim%C3%A9@b.cd", "as=owner&as=jisu", "as=owner&role=OWNER",
])
def test_refuses_malformed_requests(db_engine, safe_env, query):
    with pytest.raises(browser_login.Refused) as refused:
        browser_login.sign_in(query, None, FRONTEND)
    assert refused.value.status == 400
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(RegistrationSession)) == 0


def test_missing_demo_account_and_suspended_account(db_engine, safe_env):
    with pytest.raises(browser_login.Refused) as missing:
        browser_login.sign_in("as=jisu", None, FRONTEND)
    assert missing.value.status == 404 and "demo_seed" in missing.value.message
    demo_owner(db_engine, status="SUSPENDED")
    with pytest.raises(browser_login.Refused) as suspended:
        browser_login.sign_in("as=owner", None, FRONTEND)
    assert suspended.value.status == 403
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(RegistrationSession)) == 0


def test_no_cookie_when_the_commit_fails(db_engine, safe_env, monkeypatch):
    demo_owner(db_engine)
    monkeypatch.setattr(Session, "commit", lambda self: (_ for _ in ()).throw(RuntimeError("commit failed")))
    with pytest.raises(RuntimeError):
        browser_login.sign_in("as=owner", None, FRONTEND)
    monkeypatch.undo()
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


@pytest.mark.parametrize(("env", "message"), [
    ({"APP_ENV": "production", "DB_NAME": "jidan_local"}, "APP_ENV must be local"),
    ({"APP_ENV": "staging", "DB_NAME": "jidan_local"}, "APP_ENV must be local"),
    ({"APP_ENV": "dev", "DB_NAME": "jidan_dev", "COOKIE_SECURE": "false"}, "APP_ENV must be local"),
    ({"APP_ENV": "dev", "DB_NAME": "jidan_local", "COOKIE_SECURE": "false"}, "APP_ENV must be local"),
    ({"APP_ENV": "", "DB_NAME": "jidan_local"}, "APP_ENV must be local"),
    ({"APP_ENV": "Local", "DB_NAME": "jidan_local"}, "APP_ENV must be local"),
    ({"APP_ENV": "local", "DB_NAME": "jidan_dev"}, "_local or _test"),
    ({"APP_ENV": "local", "DB_NAME": "jidan_demo"}, "_local or _test"),
    ({"APP_ENV": "local", "DB_NAME": "jidan"}, "DB_NAME"),
    ({"APP_ENV": "local", "DB_NAME": "jidan_local_backup"}, "DB_NAME"),
    ({"APP_ENV": "local", "DB_NAME": "jidan_local", "COOKIE_SECURE": "true"}, "Secure"),
    ({"APP_ENV": "local", "DB_NAME": "jidan_local", "COOKIE_SECURE": "yes"}, "COOKIE_SECURE"),
])
def test_refuses_unsafe_environments(monkeypatch, capsys, env, message):
    """Only a developer's local stack: never dev/staging/production or a shared _dev/_demo database."""
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert browser_login.main(["--port", "0"]) == 2
    assert browser_login.main(["--cleanup"]) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("frontend", [
    "https://127.0.0.1:5190", "http://127.0.0.1:5190/app", "http://0.0.0.0:5190", "http://127.0.0.1:5190?x=1",
    "http://evil.example:5190", "http://localhost.evil.example:5190", "http://a.b.localhost:5190",
    "http://-x.localhost:5190", "http://user@core.localhost:5190", "http://core.localhost", "http://CORE.localhost:5190x",
])
def test_refuses_a_frontend_on_another_host(monkeypatch, capsys, frontend):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan_local")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert browser_login.main(["--port", "0", "--frontend", frontend]) == 2
    assert "--frontend" in capsys.readouterr().err


def test_cleanup_deletes_e2e_accounts_only(db_engine, safe_env, capsys):
    demo_owner(db_engine)
    browser_login.sign_in("as=new:gone", None, FRONTEND)  # an unfinished registration
    with Session(db_engine) as db:
        make_worker(db).google_sub = "e2e:browser:done"
        real = make_worker(db)
        db.commit()
        real_id = real.id
    assert browser_login.main(["--cleanup"]) == 0
    assert "deleted 1 E2E accounts" in capsys.readouterr().out
    with Session(db_engine) as db:
        assert sorted(db.scalars(select(User.google_sub))) == sorted(["demo-seed:owner", db.get(User, real_id).google_sub])
        assert db.scalar(select(func.count()).select_from(RegistrationSession)) == 0


def test_http_server_on_loopback(db_engine, safe_env):
    demo_owner(db_engine)
    server = browser_login.make_server(0, FRONTEND, KEY)
    host, port = server.server_address
    assert host == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        index = httpx.get(f"{base}/?key={KEY}")
        assert index.status_code == 200 and f"/login?as=owner&amp;key={KEY}" in index.text
        assert index.headers["cache-control"] == "no-store"
        assert httpx.get(f"{base}/other").status_code == 404
        refused = httpx.get(f"{base}/login?as=nobody&key={KEY}")
        assert refused.status_code == 400 and "set-cookie" not in refused.headers
        answer = httpx.get(f"{base}/login?key={KEY}&as=owner&next={FRONTEND}/home")
        assert answer.status_code == 302 and answer.headers["location"] == f"{FRONTEND}/home"
        assert answer.headers["cache-control"] == "no-store"
        names = sorted(value.split("=", 1)[0] for value in answer.headers.get_list("set-cookie"))
        assert names == [auth.REGISTRATION_COOKIE_NAME, auth.SESSION_COOKIE_NAME]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("frontend", ["http://core.localhost:5191", "http://localhost:5191"])
def test_accepts_a_localhost_frontend_and_requires_the_same_host(db_engine, safe_env, frontend):
    """<lane>.localhost resolves to loopback in browsers and keeps each lane's cookies apart."""
    from urllib.parse import urlsplit

    assert browser_login.FRONTEND_HOST.fullmatch(urlsplit(frontend).hostname)
    demo_owner(db_engine)
    server = browser_login.make_server(0, frontend, KEY)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        wrong = httpx.get(f"{base}/login?as=owner&key={KEY}")  # Host: 127.0.0.1 would set the cookie on the wrong host
        assert wrong.status_code == 400 and "set-cookie" not in wrong.headers
        right = httpx.get(f"{base}/login?as=owner&key={KEY}", headers={"Host": f"{urlsplit(frontend).hostname}:{port}"})
        assert right.status_code == 302 and right.headers["location"] == f"{frontend}/__auth/session"
        assert auth.SESSION_COOKIE_NAME in right.headers["set-cookie"]
    finally:
        server.shutdown()
        server.server_close()


def test_refuses_when_app_env_is_unset(monkeypatch, capsys):
    monkeypatch.delenv("APP_ENV", raising=False)  # the app defaults to local, this tool does not
    monkeypatch.setenv("DB_NAME", "jidan_local")
    assert browser_login.main(["--port", "0"]) == 2
    assert "APP_ENV must be local" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["jidan_fe0_local", "jidan_fe0_test"])
def test_allows_local_and_test_databases(monkeypatch, name):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", name)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    browser_login.check_environment()


def test_a_link_from_another_page_cannot_sign_in_or_out(db_engine, safe_env):
    """Login CSRF: a page elsewhere can make the browser GET /login (link, img, redirect) but cannot
    know the per-start key, so nothing is issued, revoked or set."""
    demo_owner(db_engine)
    _, first = browser_login.sign_in("as=owner", None, FRONTEND)
    victim = f"{auth.SESSION_COOKIE_NAME}={cookies_of(first)[auth.SESSION_COOKIE_NAME].value}"
    server = browser_login.make_server(0, FRONTEND, KEY)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        for query in ["as=new:attacker", "as=owner", f"as=owner&key={KEY[:-1]}", f"as=owner&key={KEY}x",
                      "as=owner&key=", f"as=owner&key={KEY}&key={KEY}", f"as=owner&KEY={KEY}",
                      f"as=owner&key={KEY.upper()}"]:
            for path in ("/login", "/"):
                answer = httpx.get(f"{base}{path}?{query}", headers={
                    "Cookie": victim, "Referer": "http://evil.example/", "Sec-Fetch-Site": "cross-site"})
                assert answer.status_code == 403, (path, query)
                assert "set-cookie" not in answer.headers and "location" not in answer.headers
                assert KEY not in answer.text
        with Session(db_engine) as db:  # the victim's session survives; no new session or registration
            assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))) == 1
            assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
            assert db.scalar(select(func.count()).select_from(RegistrationSession)) == 0
    finally:
        server.shutdown()
        server.server_close()


def test_key_rules(tmp_path):
    assert browser_login.take_key(f"as=owner&key={KEY}&next=x", KEY) == "as=owner&next=x"
    assert browser_login.take_key(f"key={KEY}", KEY) == ""
    for query in ["", "as=owner", f"key={KEY}&key={KEY}", f"key={KEY[:8]}"]:
        assert browser_login.take_key(query, KEY) is None
    with pytest.raises(ValueError):
        browser_login.make_server(0, FRONTEND, "short")
    path = tmp_path / "login-key"
    path.write_text("old")
    browser_login.write_key(str(path), KEY)
    assert path.read_text() == KEY + "\n" and (path.stat().st_mode & 0o777) == 0o600


def test_main_writes_the_key_file_and_never_prints_the_key(db_engine, safe_env, monkeypatch, tmp_path, capsys):
    """main() with --key-file: the key goes only to the 0600 file; stdout shows where it is."""
    served = {}
    monkeypatch.setattr(browser_login.ThreadingHTTPServer, "serve_forever", lambda self: served.setdefault("port", self.server_address[1]))
    key_file = tmp_path / "login-key"
    assert browser_login.main(["--port", "0", "--key-file", str(key_file)]) == 0
    key = key_file.read_text().strip()
    out = capsys.readouterr().out
    assert len(key) >= 32 and key not in out and str(key_file) in out and served["port"] > 0

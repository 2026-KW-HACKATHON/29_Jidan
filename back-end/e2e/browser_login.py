"""A local sign-in helper for checking the frontend in a real browser, beside the application.

    cd back-end
    APP_ENV=local DB_HOST=... DB_NAME=jidan_fe0_local DB_USER=... DB_PASSWORD=... \
        python -m e2e.browser_login --port 8120 --frontend http://127.0.0.1:5190 --key-file run/login-key

Then open http://127.0.0.1:8120/login?as=owner&key=<key> in the browser. The key is random per
start (written to --key-file with mode 0600, or printed once): without it a page elsewhere could sign
the browser in (or out) through a plain link, so every request must carry it. Google sign-in cannot be
automated, so this separate process does what the OAuth callback does after Google answers:
it revokes the browser's current session cookies, issues a member session for an existing
account (`create_session`) or a registration session for a new identity
(`create_registration_session`), commits, sets the same cookies (`set_session_cookie` /
`set_registration_cookie`, same name, path, HttpOnly, SameSite and Secure) and redirects to the
frontend's `/__auth/session` or `/__auth/signup`. Cookies are host-only and ignore the port, so
cookies set here on 127.0.0.1 are sent to the frontend (and its /api proxy) on 127.0.0.1.

Accounts (`as=`): the demo seed accounts (`owner`, `jisu`, `minjun`, `seoyeon`, `doyoon`;
run `python -m app.demo_seed` first) and `new:<label>`, a Google identity whose `sub` is
`e2e:browser:<label>`: a registration session until it signs up, a member session after that.
`email=` sets its Google e-mail (default `<label>@browser.jidan.example`), e.g. to accept an
invitation sent to that address.

The application has no backdoor: this is a different process that writes to the same database.
It is for a developer's own machine only: it runs only with APP_ENV=local and a DB name ending in
_local or _test (never dev, staging or production, nor _dev/_demo databases), listens on 127.0.0.1
only, refuses Secure cookies (they would not work over http) and signs in only demo seed accounts
and e2e:browser: identities. `--cleanup` deletes the `e2e:` accounts like `python -m e2e.demo_scenario
--cleanup`; demo seed accounts and real accounts are never touched.
"""
import argparse
import hmac
import html
import os
import re
import secrets
import sys
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from starlette.responses import Response

from app import auth, demo_seed
from app.db import session_scope
from app.db.models import User
from app.operator_cli import operator_cli

HOST = "127.0.0.1"
# Frontend hosts that resolve to loopback. A `<lane>.localhost` name gives every lane its own cookie
# jar: cookies are per host and ignore the port, so lanes sharing 127.0.0.1 overwrite each other.
FRONTEND_HOST = re.compile(r"127\.0\.0\.1|(?:[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?\.)?localhost")
NEW_PREFIX = "e2e:browser:"
CLEANUP_PREFIX = "e2e:"  # same accounts as e2e.demo_scenario --cleanup
DEMO_ACCOUNTS = {"owner": "지단 점주 (승인 매장 + 승인 대기 매장)", **{
    key: f"{values[0]} (근무자)" for key, values in demo_seed.WORKERS.items()}}
LOCAL_DATABASE = re.compile(r"_(local|test)$")
LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
EMAIL = re.compile(r"[a-z0-9._+-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)+")


class Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def check_environment() -> None:
    """Raise demo_seed.UnsafeTarget unless this is a developer's local stack.

    Narrower than the demo seed: APP_ENV must be exactly `local` (not unset, dev or staging) and the
    database a `_local`/`_test` one, so a shared dev server's database can never get sessions this way.
    """
    environment = os.getenv("APP_ENV")
    if environment is None or environment.strip() != "local":
        raise demo_seed.UnsafeTarget("APP_ENV must be local: this tool runs only on a developer's local stack")
    if not LOCAL_DATABASE.search(os.getenv("DB_NAME", "").strip()):
        raise demo_seed.UnsafeTarget("DB_NAME must end with _local or _test")
    if auth.cookie_secure():
        raise demo_seed.UnsafeTarget("COOKIE_SECURE=true: Secure cookies are not stored over http")


def identity(account: str, email: str | None) -> tuple[str, str | None]:
    """(Google sub, e-mail for a new identity) for an `as=` value."""
    if account in DEMO_ACCOUNTS:
        if email is not None:
            raise Refused(400, "email= is only for new:<label>")
        return f"{demo_seed.SUB_PREFIX}{account}", None
    label = account.removeprefix("new:")
    if label == account or not LABEL.fullmatch(label):
        raise Refused(400, f"as= must be one of {', '.join(DEMO_ACCOUNTS)} or new:<label> ([a-z0-9-], up to 40)")
    email = (email if email is not None else f"{label}@browser.jidan.example").strip().lower()
    if not EMAIL.fullmatch(email) or len(email) > 254:
        raise Refused(400, "email= is not a plain ASCII e-mail address")
    return f"{NEW_PREFIX}{label}", email


def frontend_target(frontend: str, next_url: str | None, path: str) -> str:
    """Where to send the browser: `next` if it is on the frontend origin, else frontend + path."""
    if next_url is None:
        return frontend + path
    parts, base = urlsplit(next_url), urlsplit(frontend)
    if (parts.scheme, parts.netloc) != (base.scheme, base.netloc) or "\\" in next_url or any(
            ord(ch) < 0x21 for ch in next_url):
        raise Refused(400, f"next= must stay on {frontend}")
    return next_url


def sign_in(query: str, cookie_header: str | None, frontend: str) -> tuple[str, list[str]]:
    """Issue a session for the `as=` account; returns (redirect URL, Set-Cookie values)."""
    params = parse_qs(query, keep_blank_values=True)
    if any(len(values) > 1 for values in params.values()) or not set(params) <= {"as", "email", "next"}:
        raise Refused(400, "use as=, and optionally email= and next=, each once")
    if "as" not in params:
        raise Refused(400, "as= is required")
    sub, email = identity(params["as"][0], params.get("email", [None])[0])
    jar = SimpleCookie()
    try:
        jar.load(cookie_header or "")
    except CookieError:
        jar = SimpleCookie()
    response = Response(status_code=302)
    with session_scope() as db:
        user = db.scalar(select(User).where(User.google_sub == sub).with_for_update())
        if user is None and sub.startswith(demo_seed.SUB_PREFIX):
            raise Refused(404, "demo account not found: run python -m app.demo_seed for this database")
        if user is not None and user.status != auth.STATUS_ACTIVE:
            raise Refused(403, "the account is suspended")
        # Like the callback: whoever was signed in on this browser is signed out first.
        if auth.SESSION_COOKIE_NAME in jar:
            auth.revoke_session(jar[auth.SESSION_COOKIE_NAME].value, db=db)
        if auth.REGISTRATION_COOKIE_NAME in jar:
            auth.revoke_registration_session(jar[auth.REGISTRATION_COOKIE_NAME].value, db=db)
        if user is None:
            issued, path = auth.create_registration_session(sub, email, db=db), "/__auth/signup"
        else:
            issued, path = auth.create_session(user.id, db=db), "/__auth/session"
        target = frontend_target(frontend, params.get("next", [None])[0], path)
    # Committed: only now do the cookies go out.
    if user is None:
        auth.set_registration_cookie(response, issued)
        auth.clear_session_cookie(response)
    else:
        auth.set_session_cookie(response, issued)
        auth.clear_registration_cookie(response)
    return target, [value.decode("latin-1") for key, value in response.raw_headers if key == b"set-cookie"]


def index_page(port: int, frontend: str, key: str) -> str:
    k = html.escape(key, quote=True)
    links = "".join(
        f'<li><a href="/login?as={name}&amp;key={k}">{name}</a> — {html.escape(label)}</li>'
        for name, label in DEMO_ACCOUNTS.items())
    return (
        "<!doctype html><meta charset=utf-8><title>Jidan local sign-in</title>"
        f"<h1>Jidan local sign-in (127.0.0.1:{port})</h1><p>Frontend: {html.escape(frontend)}</p>"
        f"<ul>{links}<li><a href=\"/login?as=new:sample&amp;key={k}\">new:sample</a> — 새 Google 신원(가입 화면)</li></ul>"
        "<p><code>/login?as=new:&lt;label&gt;&amp;key=&lt;key&gt;&amp;email=&lt;e-mail&gt;&amp;next=&lt;frontend URL&gt;</code></p>")


def take_key(query: str, key: str) -> str | None:
    """The query without `key=` when it carries exactly the expected key, else None."""
    pairs = [pair for pair in query.split("&") if pair] if query else []
    given = [value for name, value in (pair.partition("=")[::2] for pair in pairs) if name == "key"]
    if len(given) != 1 or not hmac.compare_digest(given[0].encode(), key.encode()):
        return None
    return "&".join(pair for pair in pairs if pair.partition("=")[0] != "key")


def make_handler(frontend: str, port: int, key: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "jidan-browser-login"

        def _send(self, status: int, body: str, headers: list[tuple[str, str]] = ()) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if body.startswith("<!doctype")
                             else "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            if parts.path not in ("/", "/login"):
                return self._send(404, "not found")
            # A link from any other page must not sign the browser in or out: require the start key.
            query = take_key(parts.query, key)
            if query is None:
                return self._send(403, "key= is required (printed by the tool / its --key-file)")
            if parts.path == "/":
                return self._send(200, index_page(port, frontend, key))
            # The cookie lands on the host the browser used here; it must be the frontend's host.
            host = urlsplit("//" + (self.headers.get("Host") or "")).hostname
            if host != urlsplit(frontend).hostname:
                return self._send(400, f"open this page as http://{urlsplit(frontend).hostname}:{port}/login (cookies are per host)")
            try:
                target, cookies = sign_in(query, self.headers.get("Cookie"), frontend)
            except Refused as refused:
                return self._send(refused.status, refused.message)
            self._send(302, "", [("Location", target), *(("Set-Cookie", value) for value in cookies)])

        def log_message(self, format, *args) -> None:
            sys.stderr.write(f"browser_login: {self.command} {urlsplit(self.path).path} {args[1] if len(args) > 1 else ''}\n")

    return Handler


def make_server(port: int, frontend: str, key: str) -> ThreadingHTTPServer:
    if len(key) < 16:
        raise ValueError("the key must be at least 16 characters")
    server = ThreadingHTTPServer((HOST, port), None)
    server.RequestHandlerClass = make_handler(frontend, server.server_address[1], key)
    return server


def write_key(path: str, key: str) -> None:
    """Write the key readable by this user only (replacing any previous file)."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(key + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8120)
    parser.add_argument("--frontend", default="http://127.0.0.1:5190",
                        help="frontend origin on 127.0.0.1 or <name>.localhost (where the browser goes after sign-in)")
    parser.add_argument("--key-file", help="write the random per-start key here (mode 0600) instead of printing it")
    parser.add_argument("--cleanup", action="store_true", help="delete the e2e: accounts and their data, then exit")
    args = parser.parse_args(argv)
    try:
        check_environment()
    except (demo_seed.UnsafeTarget, auth.ConfigurationError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.cleanup:
        with operator_cli(), session_scope() as db:
            print(f"deleted {demo_seed.delete_accounts(db, CLEANUP_PREFIX)} E2E accounts and their data")
        return 0
    frontend = urlsplit(args.frontend)
    try:
        port_given = frontend.port is not None
    except ValueError:  # e.g. ":5190x"
        port_given = False
    if (frontend.scheme != "http" or not FRONTEND_HOST.fullmatch(frontend.hostname or "") or not port_given
            or frontend.path not in ("", "/") or frontend.query or frontend.username or frontend.password):
        print(f"refused: --frontend must be http://{HOST}:<port> or http://<name>.localhost:<port>", file=sys.stderr)
        return 2
    key = secrets.token_urlsafe(24)
    server = make_server(args.port, f"http://{frontend.hostname}:{frontend.port}", key)
    if args.key_file:
        write_key(args.key_file, key)
    shown = f"<key in {args.key_file}>" if args.key_file else key
    print(f"browser_login: http://{frontend.hostname}:{server.server_address[1]}/login?as=owner&key={shown}"
          f" -> http://{frontend.hostname}:{frontend.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

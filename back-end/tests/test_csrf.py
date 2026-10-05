import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import (
    REGISTRATION_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    create_registration_session,
    create_session,
    csrf_token_for,
)
from app.csrf import (
    CsrfMember,
    CsrfOwner,
    CsrfRegistration,
    CsrfWorker,
    allowed_origins,
    normalize_origin,
    require_allowed_origin,
)
from app.errors import install_error_handlers
from tests.factories import make_user

ORIGIN = "https://app.example.com"


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.api_route("/api/t/origin", methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
    def origin_only(_check: None = Depends(require_allowed_origin)) -> dict:
        return {"ok": True}

    @app.api_route("/api/t/member", methods=["GET", "POST", "DELETE"])
    def member(principal: CsrfMember) -> dict:
        return {"userId": principal.user_id}

    @app.post("/api/t/owner")
    def owner(principal: CsrfOwner) -> dict:
        return {"userId": principal.user_id}

    @app.post("/api/t/worker")
    def worker(principal: CsrfWorker) -> dict:
        return {"userId": principal.user_id}

    @app.post("/api/auth/t/registration")
    def registration(principal: CsrfRegistration) -> dict:
        return {"sub": principal.google_sub}

    return app


@pytest.fixture(autouse=True)
def allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", f"{ORIGIN},http://localhost:5173")


@pytest.fixture
def client():
    return TestClient(build_app())


def code_of(response):
    return response.json()["code"]


# --- Origin matching -------------------------------------------------------------------------

@pytest.mark.parametrize("origin", [
    ORIGIN,
    "http://localhost:5173",
    "HTTPS://APP.EXAMPLE.COM",  # scheme and host are case-insensitive
    "https://App.Example.Com",
    "https://app.example.com:443",  # explicit default port is the same origin
])
def test_matching_origins_are_accepted(client, origin):
    assert client.post("/api/t/origin", headers={"Origin": origin}).status_code == 200


@pytest.mark.parametrize("origin", [
    "http://app.example.com",  # scheme differs
    "https://app.example.com:8443",  # port differs
    "http://localhost:5174",
    "https://localhost:5173",
    "http://localhost",  # default port is not 5173
    "https://evil.app.example.com",  # subdomain
    "https://app.example.com.evil.com",  # allowed host as a prefix
    "https://xapp.example.com",
    "https://example.com",
    "https://app.example.com/",  # trailing slash is not an origin
    "https://app.example.com/path",
    "https://app.example.com?x=1",
    "https://app.example.com#x",
    "https://app.example.com@evil.com",
    "https://evil.com@app.example.com",
    "https://evil.com\\@app.example.com",
    "https://evil.com/https://app.example.com",
    " https://app.example.com",
    "https://app.example.com ",
    "https://app.example.com:",
    "https://app.example.com:99999",
    "https://app.example.com:abc",
    "ftp://app.example.com",
    "//app.example.com",
    "app.example.com",
    "null",
    "*",
    "",
])
def test_non_matching_origins_are_rejected(client, origin):
    response = client.post("/api/t/origin", headers={"Origin": origin})
    assert response.status_code == 403
    assert code_of(response) == "CSRF_INVALID"


def test_missing_origin_is_rejected(client):
    response = client.post("/api/t/origin")
    assert response.status_code == 403 and code_of(response) == "CSRF_INVALID"


def test_duplicate_origin_headers_are_rejected(client):
    response = client.post(
        "/api/t/origin", headers=[("Origin", ORIGIN), ("Origin", ORIGIN)],
    )
    assert response.status_code == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_unsafe_method_is_checked(client, method):
    assert client.request(method, "/api/t/origin").status_code == 403
    assert client.request(method, "/api/t/origin", headers={"Origin": ORIGIN}).status_code == 200


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_skip_the_origin_check(client, method):
    assert client.request(method, "/api/t/origin").status_code == 200
    assert client.request(method, "/api/t/origin", headers={"Origin": "https://evil.com"}).status_code == 200


def test_unset_or_empty_allow_list_rejects_everything(client, monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS")
    assert client.post("/api/t/origin", headers={"Origin": ORIGIN}).status_code == 403
    monkeypatch.setenv("ALLOWED_ORIGINS", " , ,")
    assert client.post("/api/t/origin", headers={"Origin": ORIGIN}).status_code == 403


def test_wildcard_is_not_supported(client, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    assert client.post("/api/t/origin", headers={"Origin": "https://evil.com"}).status_code == 403
    assert allowed_origins() == frozenset()


def test_configuration_is_tolerant_of_spaces_and_trailing_slash_but_skips_garbage(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", " https://a.example.com/ , not-an-origin,https://b.example.com:8443,*")
    assert allowed_origins() == {"https://a.example.com:443", "https://b.example.com:8443"}


def test_configuration_is_read_per_request(client, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://other.example.com")
    assert client.post("/api/t/origin", headers={"Origin": ORIGIN}).status_code == 403
    assert client.post("/api/t/origin", headers={"Origin": "https://other.example.com"}).status_code == 200


@pytest.mark.parametrize(("value", "expected"), [
    ("https://a.com", "https://a.com:443"),
    ("http://a.com", "http://a.com:80"),
    ("http://[::1]:3000", "http://[::1]:3000"),
    ("https://a.com/", None),
    ("https://", None),
    ("", None),
])
def test_normalize_origin(value, expected):
    assert normalize_origin(value) == expected


# --- CSRF token ------------------------------------------------------------------------------

def add_user(engine, role="WORKER") -> str:
    with Session(engine) as session:
        user_id = make_user(session, role).id
        session.commit()
    return user_id


def write_headers(token, csrf="", origin=ORIGIN, cookie_name=SESSION_COOKIE_NAME) -> dict:
    headers = {"Cookie": f"{cookie_name}={token}"}
    if origin is not None:
        headers["Origin"] = origin
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


@pytest.fixture
def api(db_engine):
    return TestClient(build_app())


def test_write_with_matching_token_and_origin_succeeds(api, db_engine):
    user_id = add_user(db_engine)
    issued = create_session(user_id)
    response = api.post("/api/t/member", headers=write_headers(issued.token, issued.csrf_token))
    assert response.status_code == 200 and response.json() == {"userId": user_id}


def test_token_is_bound_to_the_session_and_not_the_cookie_value(db_engine):
    user_id = add_user(db_engine)
    first, second = create_session(user_id), create_session(user_id)
    assert first.csrf_token == csrf_token_for(first.token)  # stable per session
    assert first.csrf_token != second.csrf_token
    assert first.token not in first.csrf_token
    assert len(first.csrf_token) >= 43


@pytest.mark.parametrize("csrf", [None, "", "wrong", " ", "a" * 5000])
def test_missing_or_wrong_token_is_rejected(api, db_engine, csrf):
    issued = create_session(add_user(db_engine))
    response = api.post("/api/t/member", headers=write_headers(issued.token, csrf))
    assert response.status_code == 403 and code_of(response) == "CSRF_INVALID"


def test_token_must_match_exactly(api, db_engine):
    issued = create_session(add_user(db_engine))
    for tampered in (issued.csrf_token.swapcase(), issued.csrf_token + " ", issued.csrf_token[:-1],
                     " " + issued.csrf_token):
        response = api.post("/api/t/member", headers=write_headers(issued.token, tampered))
        assert response.status_code == 403, tampered


def test_another_sessions_token_is_rejected(api, db_engine):
    user_id = add_user(db_engine)
    mine, other = create_session(user_id), create_session(add_user(db_engine))
    response = api.post("/api/t/member", headers=write_headers(mine.token, other.csrf_token))
    assert response.status_code == 403
    # even the same user's other session
    sibling = create_session(user_id)
    assert api.post("/api/t/member", headers=write_headers(mine.token, sibling.csrf_token)).status_code == 403


def test_the_cookie_value_itself_is_not_a_valid_csrf_token(api, db_engine):
    issued = create_session(add_user(db_engine))
    response = api.post("/api/t/member", headers=write_headers(issued.token, issued.token))
    assert response.status_code == 403


@pytest.mark.parametrize("origin", [None, "https://evil.com", "null", ORIGIN + "/"])
def test_valid_token_does_not_help_with_a_bad_origin(api, db_engine, origin):
    issued = create_session(add_user(db_engine))
    response = api.post("/api/t/member", headers=write_headers(issued.token, issued.csrf_token, origin))
    assert response.status_code == 403 and code_of(response) == "CSRF_INVALID"


def test_origin_is_checked_before_authentication(api):
    anonymous_bad_origin = api.post("/api/t/member", headers={"Origin": "https://evil.com"})
    assert anonymous_bad_origin.status_code == 403
    assert code_of(anonymous_bad_origin) == "CSRF_INVALID"


def test_anonymous_write_with_good_origin_is_unauthenticated_not_csrf(api):
    response = api.post("/api/t/member", headers={"Origin": ORIGIN, "X-CSRF-Token": "x"})
    assert response.status_code == 401 and code_of(response) == "SESSION_EXPIRED"


def test_safe_methods_need_neither_token_nor_origin(api, db_engine):
    issued = create_session(add_user(db_engine))
    response = api.get("/api/t/member", headers={"Cookie": f"{SESSION_COOKIE_NAME}={issued.token}"})
    assert response.status_code == 200


def test_delete_is_protected_like_post(api, db_engine):
    issued = create_session(add_user(db_engine))
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={issued.token}"}
    assert api.delete("/api/t/member", headers=cookie).status_code == 403
    assert api.delete("/api/t/member", headers=write_headers(issued.token, issued.csrf_token)).status_code == 200


def test_role_checks_still_apply_after_csrf_passes(api, db_engine):
    worker = create_session(add_user(db_engine, "WORKER"))
    owner = create_session(add_user(db_engine, "OWNER"))
    response = api.post("/api/t/owner", headers=write_headers(worker.token, worker.csrf_token))
    assert response.status_code == 403 and code_of(response) == "FORBIDDEN"
    assert api.post("/api/t/owner", headers=write_headers(owner.token, owner.csrf_token)).status_code == 200
    assert api.post("/api/t/worker", headers=write_headers(worker.token, worker.csrf_token)).status_code == 200
    assert api.post("/api/t/worker", headers=write_headers(owner.token, owner.csrf_token)).status_code == 403


def test_registration_session_has_its_own_token(api, db_engine):
    issued = create_registration_session("sub-1", "new@example.com")
    headers = write_headers(issued.token, issued.csrf_token, cookie_name=REGISTRATION_COOKIE_NAME)
    response = api.post("/api/auth/t/registration", headers=headers)
    assert response.status_code == 200 and response.json() == {"sub": "sub-1"}
    member = create_session(add_user(db_engine))
    headers = write_headers(issued.token, member.csrf_token, cookie_name=REGISTRATION_COOKIE_NAME)
    assert api.post("/api/auth/t/registration", headers=headers).status_code == 403


def test_registration_write_without_a_session_is_unauthenticated(api):
    response = api.post("/api/auth/t/registration", headers={"Origin": ORIGIN, "X-CSRF-Token": "x"})
    assert response.status_code == 401 and code_of(response) == "SESSION_EXPIRED"

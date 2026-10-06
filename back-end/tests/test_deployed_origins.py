"""Verify deployed Origin isolation using real member and registration sessions."""
import pytest
from fastapi.testclient import TestClient

from app.auth import REGISTRATION_COOKIE_NAME, create_registration_session, create_session
from tests.test_csrf import add_user, build_app, code_of, write_headers

ORIGINS = {
    "dev": "https://dev-jidan.leehyowon14.dev",
    "production": "https://jidan.leehyowon14.dev",
}


@pytest.mark.parametrize("environment", ORIGINS)
@pytest.mark.parametrize("kind", ["member", "registration"])
@pytest.mark.parametrize("case", [
    "valid", "opposite-origin", "arbitrary-origin", "missing-origin",
    "missing-token", "wrong-token", "duplicate-origin", "another-session-token",
])
def test_environment_origin_and_session_token_are_both_required(
    db_engine, monkeypatch, environment, kind, case,
):
    origin = ORIGINS[environment]
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("ALLOWED_ORIGINS", origin)
    client = TestClient(build_app())
    if kind == "member":
        user_id = add_user(db_engine)
        issued = create_session(user_id)
        path = "/api/t/member"
        headers = write_headers(issued.token, issued.csrf_token, origin)
    else:
        issued = create_registration_session("origin-sub", "origin@example.com")
        path = "/api/auth/t/registration"
        headers = write_headers(
            issued.token, issued.csrf_token, origin, cookie_name=REGISTRATION_COOKIE_NAME,
        )

    if case == "opposite-origin":
        headers["Origin"] = next(value for value in ORIGINS.values() if value != origin)
    elif case == "arbitrary-origin":
        headers["Origin"] = "https://evil.example.com"
    elif case == "missing-origin":
        del headers["Origin"]
    elif case == "missing-token":
        del headers["X-CSRF-Token"]
    elif case == "wrong-token":
        headers["X-CSRF-Token"] = "invalid-token"
    elif case == "another-session-token":
        if kind == "member":
            other = create_session(add_user(db_engine))
        else:
            other = create_registration_session("other-sub", "other@example.com")
        headers["X-CSRF-Token"] = other.csrf_token
    elif case == "duplicate-origin":
        headers = [*headers.items(), ("Origin", origin)]

    response = client.post(path, headers=headers)
    if case == "valid":
        assert response.status_code == 200
        if kind == "member":
            assert response.json() == {"userId": user_id}
        else:
            assert response.json() == {"sub": "origin-sub"}
    else:
        assert response.status_code == 403
        assert code_of(response) == "CSRF_INVALID"

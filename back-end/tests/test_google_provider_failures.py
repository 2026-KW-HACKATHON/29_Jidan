import httpx
import pytest

from app import oauth
from tests.test_auth_routes import auth_api as _auth_api
from tests.test_auth_routes import begin

auth_api = _auth_api


@pytest.mark.parametrize("status,payload,expected_status,expected_code", [
    (400, {"error": "invalid_grant", "error_description": "EXPIRED-CODE-SECRET"}, 400, "OAUTH_CODE_INVALID"),
    (429, {}, 502, "GOOGLE_UNAVAILABLE"),
    (503, {}, 502, "GOOGLE_UNAVAILABLE"),
    (200, {}, 401, "GOOGLE_IDENTITY_INVALID"),
])
def test_provider_failures_follow_callback_contract(auth_api, monkeypatch, status, payload, expected_status, expected_code):
    params = begin(auth_api)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return httpx.Response(status, json=payload)
    monkeypatch.setattr(oauth.httpx, "Client", Client)
    r = auth_api.get("/api/auth/google/callback", params={"state": params["state"], "code": "EXPIRED-CODE-SECRET"})
    assert r.status_code == expected_status and r.json()["code"] == expected_code
    assert "EXPIRED-CODE-SECRET" not in r.text + str(r.headers)
    assert oauth.OAUTH_COOKIE not in auth_api.cookies


def test_provider_timeout_is_retryable_bad_gateway(auth_api, monkeypatch):
    params = begin(auth_api)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): raise httpx.ReadTimeout("SECRET-CODE")
    monkeypatch.setattr(oauth.httpx, "Client", Client)
    r = auth_api.get("/api/auth/google/callback", params={"state": params["state"], "code": "CODE"})
    assert r.status_code == 502 and r.json()["code"] == "GOOGLE_UNAVAILABLE"
    assert "SECRET-CODE" not in r.text and oauth.OAUTH_COOKIE not in auth_api.cookies

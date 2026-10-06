import pytest
from sqlalchemy.orm import Session

from tests.test_worker_profile import PATH
from tests.test_worker_profile import profile_api as _profile_api
from tests.test_worker_registration import headers
from tests.test_worker_registration import worker_api as _worker_api

worker_api = _worker_api
profile_api = _profile_api
BASIC = PATH + "/basic"


def test_patch_partial_normalized_and_session(profile_api):
    before = profile_api.get(PATH).json()
    response = profile_api.patch(BASIC, json={"name": " 김지민 "}, headers=headers(profile_api))
    assert response.status_code == 200
    after = response.json()
    assert after["name"] == "김지민" and after["updatedAt"] > before["updatedAt"]
    for key in before.keys() - {"name", "updatedAt"}:
        assert after[key] == before[key]
    assert profile_api.get("/api/auth/session").json()["user"]["name"] == "김지민"
    assert profile_api.get(PATH).json() == after
    repeated = profile_api.patch(BASIC, json={"name": "김지민"}, headers=headers(profile_api))
    assert repeated.json() == after
    response = profile_api.patch(BASIC, json={"phoneNumber": "01099999999", "birthDate": "2000-02-29", "gender": "MALE"}, headers=headers(profile_api))
    assert response.status_code == 200
    assert response.json()["birthDate"] == "2000-02-29"
    assert profile_api.get("/api/auth/session").json()["user"]["phoneNumber"] == "01099999999"


@pytest.mark.parametrize("body", [
    {}, *[{key: None} for key in ("name", "phoneNumber", "birthDate", "gender")],
    *[{key: "injected"} for key in ("email", "identity", "provider", "id", "role", "updatedAt", "careers", "availabilities", "password", "unknown")],
    {"name": " "}, {"name": "a" * 51}, {"name": 12},
    {"phoneNumber": "010-1234-5678"}, {"phoneNumber": "01112345678"},
    {"phoneNumber": "010１２３４５６７８"}, {"birthDate": "2999-01-01"},
    {"birthDate": "2001-02-29"}, {"birthDate": "2000-1-01"}, {"birthDate": 0},
    {"gender": "female"},
])
def test_invalid_patch_is_atomic(profile_api, body):
    before = profile_api.get(PATH).json()
    response = profile_api.patch(BASIC, json=body, headers=headers(profile_api))
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR" and response.json()["fieldErrors"]
    assert response.headers["cache-control"] == "no-store"
    assert profile_api.get(PATH).json() == before


@pytest.mark.parametrize("kind", ["missing-origin", "bad-origin", "missing-csrf", "bad-csrf"])
def test_patch_guards(profile_api, kind):
    before = profile_api.get(PATH).json()
    values = headers(profile_api)
    if kind == "missing-origin": values.pop("Origin")
    if kind == "bad-origin": values["Origin"] = "http://frontend.test.evil"
    if kind == "missing-csrf": values.pop("X-CSRF-Token")
    if kind == "bad-csrf": values["X-CSRF-Token"] = "bad"
    response = profile_api.patch(BASIC, json={"name": "변경"}, headers=values)
    assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    assert profile_api.get(PATH).json() == before


def test_commit_failure_preserves_profile(profile_api, monkeypatch):
    before = profile_api.get(PATH).json()
    original = Session.commit
    def fail(self):
        # Only the profile write; successful read-only requests remain usable.
        from app.db.models import User
        if any(isinstance(row, User) and row.name == "변경" for row in self.identity_map.values()):
            raise RuntimeError("private commit detail")
        return original(self)
    monkeypatch.setattr(Session, "commit", fail)
    response = profile_api.patch(BASIC, json={"name": "변경"}, headers=headers(profile_api))
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert "private commit detail" not in response.text and "set-cookie" not in response.headers
    assert profile_api.get(PATH).json() == before


def test_name_and_birth_date_boundaries(profile_api, monkeypatch):
    from datetime import date

    monkeypatch.setattr("app.registration_inputs.today", lambda: date(2026, 10, 6))
    for body in ({"name": "가"}, {"name": "가" * 50}, {"birthDate": "2026-10-06"}):
        assert profile_api.patch(BASIC, json=body, headers=headers(profile_api)).status_code == 200
    assert profile_api.patch(BASIC, json={"birthDate": "2026-10-07"}, headers=headers(profile_api)).status_code == 422

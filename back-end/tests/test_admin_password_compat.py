"""Remote PBKDF2 configuration must work through the existing protected admin API."""

import base64
import hashlib
import sys

import pytest
from sqlalchemy.orm import Session

from app import admin_password
from app.admin_password_config import AdminPasswordConfigError, Pbkdf2Hash, parse_password_hash
from app.ratelimit import reset_all_limits
from tests.api_contract import ORIGIN
from tests.factories import make_store_with_request, make_user

PASSWORD = "  compatible 관리자 password  "
SALT = base64.b64encode(b"s" * 16).decode()
KEY = base64.b64encode(b"d" * 32).decode()
REMOTE_FORMAT = f"pbkdf2_sha256$600000${SALT}${KEY}"
SEARCH = "/api/admin/store-approval-requests/search"


@pytest.fixture(scope="module")
def stored():
    # Independent derivation: catches generator/verifier bugs shared by both functions.
    salt = b"remote-format-16"
    key = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode("utf-8"), salt, 600_000)
    return "pbkdf2_sha256$600000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


@pytest.fixture(autouse=True)
def admin_env(monkeypatch, stored):
    monkeypatch.setenv(admin_password.HASH_ENV, stored)
    reset_all_limits()
    yield
    reset_all_limits()


def test_remote_format_verification_and_exact_password():
    assert admin_password.verify_password(PASSWORD)
    for wrong in (PASSWORD.strip(), PASSWORD + " ", "wrong", ""):
        assert not admin_password.verify_password(wrong)


def test_new_generator_is_remote_compatible_and_random(monkeypatch):
    stored = admin_password.password_hash(PASSWORD)
    parsed = parse_password_hash(stored)
    assert isinstance(parsed, Pbkdf2Hash)
    assert parsed.iterations == 600_000
    assert PASSWORD not in stored
    monkeypatch.setenv(admin_password.HASH_ENV, stored)
    assert admin_password.verify_password(PASSWORD)
    assert admin_password.password_hash(PASSWORD) != stored
    with pytest.raises(ValueError):
        admin_password.password_hash("   ")


@pytest.mark.parametrize("args,scheme", [([], "pbkdf2_sha256"), (["--scheme", "scrypt"], "scrypt")])
def test_operator_cli_generates_a_supported_hash_without_password_echo(monkeypatch, capsys, args, scheme):
    monkeypatch.setattr(sys, "argv", ["admin_password", *args])
    monkeypatch.setattr("getpass.getpass", lambda prompt: PASSWORD)
    admin_password.main()
    output = capsys.readouterr()
    assert output.err == "" and PASSWORD not in output.out
    stored = output.out.strip()
    assert stored.startswith(scheme + "$")
    parse_password_hash(stored)
    monkeypatch.setenv(admin_password.HASH_ENV, stored)
    assert admin_password.verify_password(PASSWORD)


@pytest.mark.parametrize("rounds", [600_000, 2_000_000])
def test_remote_parameter_bounds(rounds):
    assert parse_password_hash(REMOTE_FORMAT.replace("$600000$", f"${rounds}$")).iterations == rounds


@pytest.mark.parametrize("stored", [
    REMOTE_FORMAT.replace("$600000$", "$599999$"),
    REMOTE_FORMAT.replace("$600000$", "$2000001$"),
    REMOTE_FORMAT.replace("$600000$", "$0600000$"),
    REMOTE_FORMAT.replace("$600000$", "$+600000$"),
    REMOTE_FORMAT.replace("$600000$", "$٦٠٠٠٠٠$"),
    REMOTE_FORMAT.replace(SALT, base64.b64encode(b"s" * 15).decode()),
    REMOTE_FORMAT.replace(SALT, base64.b64encode(b"s" * 65).decode()),
    REMOTE_FORMAT.replace(KEY, base64.b64encode(b"d" * 31).decode()),
    REMOTE_FORMAT.replace(SALT, SALT.rstrip("=")),
    REMOTE_FORMAT.replace(SALT, SALT[:-3] + "x=="),  # noncanonical pad bits
    REMOTE_FORMAT + "$extra", REMOTE_FORMAT + " ", " " + REMOTE_FORMAT,
    REMOTE_FORMAT.replace(SALT, "!!!!"), REMOTE_FORMAT.replace(KEY, "비밀"),
])
def test_bad_remote_hash_fails_closed_without_value(stored):
    with pytest.raises(AdminPasswordConfigError) as caught:
        parse_password_hash(stored)
    assert str(caught.value) == "ADMIN_PASSWORD_HASH is malformed"
    assert caught.value.__cause__ is None


def test_remote_hash_keeps_origin_and_password_protection(api):
    api.headers["Origin"] = "https://evil.example"
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 403
    api.headers["Origin"] = ORIGIN
    assert api.post(SEARCH, json={"password": "wrong"}).status_code == 401
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200
    response = api.post(SEARCH, content=b'{"password":"\\ud800"}',
                        headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_remote_hash_approval_replay_is_safe(api, db_engine):
    api.headers["Origin"] = ORIGIN
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        _store, request = make_store_with_request(db, owner)
        db.commit()
        path = f"/api/admin/store-approval-requests/{request.id}/approve"
    first = api.post(path, json={"password": PASSWORD})
    assert first.status_code == 200
    again = api.post(path, json={"password": PASSWORD})
    assert again.status_code == 200 and again.json() == first.json()


def test_remote_hash_keeps_concurrency_gate(api, monkeypatch):
    api.headers["Origin"] = ORIGIN
    monkeypatch.setattr(admin_password, "HASH_WAIT_SECONDS", 0.01)
    slots = [admin_password._hash_slots.acquire(timeout=1)
             for _ in range(admin_password.MAX_CONCURRENT_HASHES)]
    try:
        assert all(slots)
        response = api.post(SEARCH, json={"password": PASSWORD})
        assert response.status_code == 429 and response.headers["Retry-After"] == "1"
    finally:
        for held in slots:
            if held:
                admin_password._hash_slots.release()
    assert api.post(SEARCH, json={"password": PASSWORD}).status_code == 200

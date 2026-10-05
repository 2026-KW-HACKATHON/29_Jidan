import threading
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app import ratelimit
from app.errors import ApiError, install_error_handlers
from app.ratelimit import (
    RateLimiter,
    client_address,
    enforce_admin_password_rate_limit,
    enforce_login_rate_limit,
    record_admin_password_failure,
    record_admin_password_success,
    reset_all_limits,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def blocked(limiter, key="k") -> ApiError | None:
    try:
        limiter.hit(key)
    except ApiError as error:
        return error
    return None


def test_allows_up_to_the_limit_then_blocks():
    clock = FakeClock()
    limiter = RateLimiter(3, 60, clock=clock)
    assert [blocked(limiter) for _ in range(3)] == [None, None, None]
    error = blocked(limiter)
    assert error.status_code == 429 and error.code == "RATE_LIMITED"
    assert error.headers == {"Retry-After": "60"}


def test_blocked_requests_do_not_extend_the_window():
    clock = FakeClock()
    limiter = RateLimiter(1, 60, clock=clock)
    limiter.hit("k")
    for _ in range(50):
        clock.now += 1
        assert blocked(limiter) is not None or clock.now >= 1060
    clock.now = 1060.0
    assert blocked(limiter) is None  # the window is measured from the allowed hit


def test_window_slides_and_retry_after_counts_down():
    clock = FakeClock()
    limiter = RateLimiter(2, 60, clock=clock)
    limiter.hit("k")
    clock.now += 10
    limiter.hit("k")
    clock.now += 20
    assert blocked(limiter).headers == {"Retry-After": "30"}  # oldest event frees up at +60
    clock.now += 30
    assert blocked(limiter) is None  # first event expired, one slot back
    assert blocked(limiter) is not None


def test_retry_after_is_at_least_one_second_and_rounds_up():
    clock = FakeClock()
    limiter = RateLimiter(1, 10, clock=clock)
    limiter.hit("k")
    clock.now += 9.2
    assert blocked(limiter).headers == {"Retry-After": "1"}
    clock.now += 0.7
    assert blocked(limiter).headers == {"Retry-After": "1"}


def test_boundary_exactly_at_window_end_is_free_again():
    clock = FakeClock()
    limiter = RateLimiter(1, 60, clock=clock)
    limiter.hit("k")
    clock.now += 59.999
    assert blocked(limiter) is not None
    clock.now += 0.001
    assert blocked(limiter) is None


def test_keys_are_independent():
    limiter = RateLimiter(1, 60, clock=FakeClock())
    assert blocked(limiter, "a") is None
    assert blocked(limiter, "b") is None
    assert blocked(limiter, "a") is not None


def test_check_does_not_count_but_record_does():
    limiter = RateLimiter(2, 60, clock=FakeClock())
    for _ in range(10):
        limiter.check("k")  # never raises, never records
    limiter.record("k")
    limiter.check("k")
    limiter.record("k")
    with pytest.raises(ApiError):
        limiter.check("k")


def test_reset_forgets_a_key():
    limiter = RateLimiter(1, 60, clock=FakeClock())
    limiter.hit("k")
    limiter.reset("k")
    limiter.reset("never-seen")
    assert blocked(limiter) is None


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (-1, 60), (1, 0), (1, -5)])
def test_invalid_configuration_is_rejected(limit, window):
    with pytest.raises(ValueError, match="positive"):
        RateLimiter(limit, window)


def test_memory_is_bounded_and_active_keys_survive():
    clock = FakeClock()
    limiter = RateLimiter(5, 60, max_keys=100, clock=clock)
    for index in range(1000):
        limiter.hit(f"ip-{index}")
    assert len(limiter._events) <= 100
    # expired keys are purged first, so a quiet period frees the whole table
    clock.now += 61
    limiter.hit("fresh")
    assert len(limiter._events) == 1


def test_a_flood_of_new_keys_cannot_evict_the_most_recent_one():
    limiter = RateLimiter(1, 60, max_keys=10, clock=FakeClock())
    limiter.hit("victim")
    for index in range(5):
        limiter.hit(f"other-{index}")
    assert blocked(limiter, "victim") is not None  # still tracked


def test_concurrent_hits_never_exceed_the_limit():
    limiter = RateLimiter(10, 60)
    allowed = []

    def worker():
        for _ in range(20):
            if blocked(limiter) is None:
                allowed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(allowed) == 10


# --- HTTP integration ------------------------------------------------------------------------

def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/t/login", dependencies=[Depends(enforce_login_rate_limit)])
    def login() -> dict:
        return {"ok": True}

    @app.post("/api/t/admin", dependencies=[Depends(enforce_admin_password_rate_limit)])
    def admin(request: Request, password: str) -> dict:
        if password != "right":
            record_admin_password_failure(request)
            raise ApiError(401, "ADMIN_PASSWORD_INVALID")
        record_admin_password_success(request)
        return {"ok": True}

    return app


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv("TRUST_FORWARDED_FOR", raising=False)
    reset_all_limits()
    yield
    reset_all_limits()


@pytest.fixture
def client():
    return TestClient(build_app())


def test_login_endpoint_returns_429_with_contract_body_and_retry_after(client):
    statuses = [client.get("/api/t/login").status_code for _ in range(ratelimit.LOGIN_LIMIT + 3)]
    assert statuses[: ratelimit.LOGIN_LIMIT] == [200] * ratelimit.LOGIN_LIMIT
    assert statuses[ratelimit.LOGIN_LIMIT:] == [429] * 3
    response = client.get("/api/t/login")
    body = response.json()
    assert body["code"] == "RATE_LIMITED" and body["fieldErrors"] == []
    assert 1 <= int(response.headers["Retry-After"]) <= ratelimit.LOGIN_WINDOW_SECONDS


def test_admin_failures_lock_out_correct_passwords_too(client):
    for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT):
        assert client.post("/api/t/admin?password=wrong").status_code == 401
    blocked_response = client.post("/api/t/admin?password=right")
    assert blocked_response.status_code == 429
    assert "Retry-After" in blocked_response.headers


def test_successful_admin_password_clears_the_clients_failures(client):
    for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT - 1):
        client.post("/api/t/admin?password=wrong")
    assert client.post("/api/t/admin?password=right").status_code == 200
    for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT):
        assert client.post("/api/t/admin?password=wrong").status_code == 401
    assert client.post("/api/t/admin?password=wrong").status_code == 429


def test_successful_attempts_alone_are_never_limited(client):
    for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT * 3):
        assert client.post("/api/t/admin?password=right").status_code == 200


def test_global_admin_budget_blocks_many_addresses(client, monkeypatch):
    monkeypatch.setenv("TRUST_FORWARDED_FOR", "true")
    for index in range(ratelimit.ADMIN_PASSWORD_GLOBAL_LIMIT):
        response = client.post(
            "/api/t/admin?password=wrong", headers={"X-Forwarded-For": f"10.0.{index // 250}.{index % 250}"},
        )
        assert response.status_code == 401
    response = client.post("/api/t/admin?password=right", headers={"X-Forwarded-For": "192.0.2.99"})
    assert response.status_code == 429


def test_forwarded_header_is_ignored_unless_trusted(client):
    for index in range(ratelimit.LOGIN_LIMIT):
        client.get("/api/t/login", headers={"X-Forwarded-For": f"203.0.113.{index}"})
    assert client.get("/api/t/login", headers={"X-Forwarded-For": "203.0.113.200"}).status_code == 429


def test_trusted_forwarded_header_separates_clients_and_uses_the_proxy_appended_entry(
    client, monkeypatch,
):
    monkeypatch.setenv("TRUST_FORWARDED_FOR", "true")
    for _ in range(ratelimit.LOGIN_LIMIT):
        # the client prepends a fake address; the proxy appends the real one last
        client.get("/api/t/login", headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.7"})
    spoofed = client.get("/api/t/login", headers={"X-Forwarded-For": "7.7.7.7, 198.51.100.7"})
    assert spoofed.status_code == 429
    other = client.get("/api/t/login", headers={"X-Forwarded-For": "198.51.100.8"})
    assert other.status_code == 200


def test_client_address_falls_back_without_a_peer(monkeypatch):
    monkeypatch.setenv("TRUST_FORWARDED_FOR", "true")

    request = SimpleNamespace(headers={"x-forwarded-for": " , "}, client=None)
    assert client_address(request) == "unknown"

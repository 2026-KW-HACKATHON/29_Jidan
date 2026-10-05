import threading
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import ratelimit
from app.errors import ApiError, install_error_handlers
from app.ratelimit import (
    AdminAttempt,
    AdminPasswordAttempt,
    RateLimiter,
    client_address,
    enforce_admin_password_rate_limit,
    enforce_login_rate_limit,
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


def test_memory_is_bounded_and_expired_keys_are_reclaimed():
    clock = FakeClock()
    limiter = RateLimiter(5, 60, max_keys=100, clock=clock)
    for index in range(1000):
        if index < 100:
            limiter.hit(f"ip-{index}")
        else:
            assert blocked(limiter, f"ip-{index}").status_code == 429  # full of live keys
    assert len(limiter._events) == 100
    clock.now += 61
    limiter.hit("fresh")
    assert "fresh" in limiter._events and len(limiter._events) <= 100


def fill(limiter, count, prefix="ip"):
    for index in range(count):
        limiter.hit(f"{prefix}-{index}")


def test_full_table_refuses_new_keys_with_retry_after_and_records_nothing():
    clock = FakeClock()
    limiter = RateLimiter(5, 60, max_keys=10, clock=clock)
    fill(limiter, 10)
    clock.now += 20
    error = blocked(limiter, "newcomer")
    assert error.status_code == 429 and error.code == "RATE_LIMITED"
    assert error.headers == {"Retry-After": "40"}  # the oldest key frees the table at +60
    assert "newcomer" not in limiter._events and len(limiter._events) == 10


def test_flood_of_distinct_keys_cannot_release_an_already_blocked_key():
    clock = FakeClock()
    limiter = RateLimiter(1, 60, max_keys=50, clock=clock)
    limiter.hit("attacker")
    assert blocked(limiter, "attacker") is not None
    for index in range(5000):
        blocked(limiter, f"forged-{index}")
    assert blocked(limiter, "attacker") is not None  # still blocked
    clock.now += 59
    assert blocked(limiter, "attacker") is not None
    clock.now += 1
    assert blocked(limiter, "attacker") is None  # only the window frees it


def test_flood_cannot_reset_a_partly_used_budget():
    clock = FakeClock()
    limiter = RateLimiter(3, 60, max_keys=20, clock=clock)
    limiter.hit("victim")
    limiter.hit("victim")
    for index in range(2000):
        blocked(limiter, f"forged-{index}")
    assert blocked(limiter, "victim") is None  # third and last allowed hit
    assert blocked(limiter, "victim") is not None  # budget was not reset by the flood


def test_work_per_new_key_does_not_grow_with_the_table(monkeypatch):
    clock = FakeClock()
    calls = {"count": 0}
    original = RateLimiter._live

    def counting(self, key, now):
        calls["count"] += 1
        return original(self, key, now)

    monkeypatch.setattr(RateLimiter, "_live", counting)
    for size in (100, 10_000):
        limiter = RateLimiter(5, 60, max_keys=size, clock=clock)
        fill(limiter, size)
        calls["count"] = 0
        for index in range(500):
            blocked(limiter, f"flood-{size}-{index}")
        assert calls["count"] <= 3 * 500  # constant per refused key, whatever the table size


def test_expiry_cleanup_is_amortized_constant():
    clock = FakeClock()
    calls = {"count": 0}
    limiter = RateLimiter(5, 60, max_keys=2000, clock=clock)
    fill(limiter, 2000)
    clock.now += 61  # every key expired at once
    original = RateLimiter._live

    def counting(self, key, now):
        calls["count"] += 1
        return original(self, key, now)

    RateLimiter._live = counting
    try:
        fill(limiter, 2000, prefix="new")
    finally:
        RateLimiter._live = original
    assert calls["count"] <= 4 * 2000  # each expired key is examined about once overall
    assert len(limiter._events) == 2000


def test_pending_marks_of_dropped_expired_keys_are_cleaned():
    clock = FakeClock()
    limiter = RateLimiter(5, 60, max_keys=1, clock=clock)
    limiter.reserve("a")
    clock.now += 61
    limiter.hit("b")
    assert limiter._pending == set()


def test_concurrent_flood_keeps_the_bound_and_the_blocked_key():
    limiter = RateLimiter(1, 60, max_keys=200)
    limiter.hit("attacker")

    def flood(worker):
        for index in range(1000):
            blocked(limiter, f"w{worker}-{index}")
            assert blocked(limiter, "attacker") is not None

    threads = [threading.Thread(target=flood, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(limiter._events) <= 200
    assert blocked(limiter, "attacker") is not None


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

    @app.post("/api/t/admin")
    def admin(attempt: AdminAttempt, password: str) -> dict:
        if password == "boom":
            raise RuntimeError("unexpected")  # leaves without reporting an outcome
        if password != "right":
            attempt.failed()
            raise ApiError(401, "ADMIN_PASSWORD_INVALID")
        attempt.succeeded()
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


# --- atomic reservation ----------------------------------------------------------------------

def test_reserve_counts_immediately_and_release_returns_the_slot():
    limiter = RateLimiter(2, 60, clock=FakeClock())
    first, second = limiter.reserve("k"), limiter.reserve("k")
    assert blocked_reserve(limiter) is not None
    assert limiter.release(first) is True
    assert limiter.release(first) is False  # idempotent: the slot is only given back once
    limiter.reserve("k")
    assert blocked_reserve(limiter) is not None
    limiter.settle(second)
    assert blocked_reserve(limiter) is not None  # a settled failure keeps its slot


def blocked_reserve(limiter, key="k") -> ApiError | None:
    try:
        limiter.reserve(key)
    except ApiError as error:
        return error
    return None


def test_release_after_the_window_does_not_remove_another_event():
    clock = FakeClock()
    limiter = RateLimiter(2, 10, clock=clock)
    old = limiter.reserve("k")
    clock.now += 11  # old expired
    fresh = limiter.reserve("k")
    assert limiter.release(old) is False
    assert limiter.release(fresh) is True


def test_reset_settled_keeps_attempts_still_in_flight():
    limiter = RateLimiter(3, 60, clock=FakeClock())
    done = limiter.reserve("k")
    limiter.settle(done)
    in_flight = limiter.reserve("k")
    limiter.reset_settled("k")
    limiter.reserve("k")
    limiter.reserve("k")
    assert blocked_reserve(limiter) is not None  # in_flight + 2 new fill the budget of 3
    limiter.settle(in_flight)


def test_concurrent_reservations_never_exceed_the_limit():
    limiter = RateLimiter(5, 60)
    barrier = threading.Barrier(40)
    granted = []

    def worker():
        barrier.wait()
        if blocked_reserve(limiter) is None:
            granted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(granted) == 5


def request_from(address="198.51.100.1"):
    return SimpleNamespace(headers={}, client=SimpleNamespace(host=address))


def test_concurrent_admin_guesses_cannot_exceed_the_ip_budget():
    barrier = threading.Barrier(30)
    attempts: list[AdminPasswordAttempt] = []
    rejected = []

    def worker():
        barrier.wait()
        try:
            attempt = enforce_admin_password_rate_limit(request_from())
        except ApiError as error:
            rejected.append(error)
            return
        attempts.append(attempt)
        attempt.failed()

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(attempts) == ratelimit.ADMIN_PASSWORD_IP_LIMIT
    assert len(rejected) == 30 - ratelimit.ADMIN_PASSWORD_IP_LIMIT
    assert all(error.status_code == 429 and "Retry-After" in error.headers for error in rejected)


def test_concurrent_http_guesses_cannot_exceed_the_budget(client):
    barrier = threading.Barrier(24)
    statuses = []

    def worker():
        barrier.wait()
        statuses.append(client.post("/api/t/admin?password=wrong").status_code)

    threads = [threading.Thread(target=worker) for _ in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert statuses.count(401) == ratelimit.ADMIN_PASSWORD_IP_LIMIT
    assert statuses.count(429) == 24 - ratelimit.ADMIN_PASSWORD_IP_LIMIT


def test_concurrent_global_budget_is_not_exceeded_across_clients():
    total = ratelimit.ADMIN_PASSWORD_GLOBAL_LIMIT + 20
    barrier = threading.Barrier(total)
    granted = []

    def worker(index):
        barrier.wait()
        try:
            granted.append(enforce_admin_password_rate_limit(request_from(f"10.1.{index}.1")))
        except ApiError:
            pass

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(granted) == ratelimit.ADMIN_PASSWORD_GLOBAL_LIMIT


def test_a_full_global_budget_gives_the_ip_slot_back():
    for index in range(ratelimit.ADMIN_PASSWORD_GLOBAL_LIMIT):
        enforce_admin_password_rate_limit(request_from(f"10.2.{index}.1")).failed()
    for _ in range(3):
        with pytest.raises(ApiError):
            enforce_admin_password_rate_limit(request_from("203.0.113.5"))
    # the rejected requests did not eat that client's own budget
    assert ratelimit.admin_password_ip_limiter.reserve("203.0.113.5")


def test_success_releases_its_slot_and_forgets_earlier_failures():
    request = request_from()
    for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT - 1):
        enforce_admin_password_rate_limit(request).failed()
    enforce_admin_password_rate_limit(request).succeeded()
    for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT):
        enforce_admin_password_rate_limit(request).failed()
    with pytest.raises(ApiError):
        enforce_admin_password_rate_limit(request)


def test_success_does_not_erase_slots_of_guesses_still_running():
    request = request_from()
    running = [enforce_admin_password_rate_limit(request) for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT - 1)]
    enforce_admin_password_rate_limit(request).succeeded()
    enforce_admin_password_rate_limit(request).failed()  # budget: running + this one = limit
    with pytest.raises(ApiError):
        enforce_admin_password_rate_limit(request)
    for attempt in running:
        attempt.failed()


def test_global_budget_is_not_reset_by_a_success():
    for index in range(ratelimit.ADMIN_PASSWORD_GLOBAL_LIMIT - 1):
        enforce_admin_password_rate_limit(request_from(f"10.3.{index}.1")).failed()
    enforce_admin_password_rate_limit(request_from("203.0.113.9")).succeeded()
    enforce_admin_password_rate_limit(request_from("203.0.113.10")).failed()
    with pytest.raises(ApiError):
        enforce_admin_password_rate_limit(request_from("203.0.113.11"))


def test_reporting_an_outcome_twice_is_harmless():
    request = request_from()
    attempt = enforce_admin_password_rate_limit(request)
    attempt.succeeded()
    attempt.succeeded()
    attempt.failed()  # ignored: the first report wins
    for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT):
        enforce_admin_password_rate_limit(request).failed()


def test_an_unreported_attempt_keeps_its_slot(client):
    errors = TestClient(build_app(), raise_server_exceptions=False)
    for _ in range(ratelimit.ADMIN_PASSWORD_IP_LIMIT):
        assert errors.post("/api/t/admin?password=boom").status_code == 500
    assert errors.post("/api/t/admin?password=right").status_code == 429

"""Minimal in-memory rate limiting for login and the admin password check (`429 RATE_LIMITED`).

Known limits, by design of this first version:

* State lives in the memory of one process. With several workers or hosts each keeps its own
  counters, so the effective limit is `limit x processes`, and a restart clears everything.
  Move the counters to the database or a cache before relying on exact limits.
* Clients are told apart by IP address. Behind a reverse proxy set `TRUST_FORWARDED_FOR=true`
  so the address the proxy appended to `X-Forwarded-For` is used; without it every request
  looks like it comes from the proxy and shares one budget. Never enable it unless a proxy you
  control sets that header, because clients could otherwise choose their own key.
"""

import math
import os
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi import Request

from app.errors import ApiError, ErrorCode

MAX_TRACKED_KEYS = 10_000

LOGIN_LIMIT = 20  # requests per client per window
LOGIN_WINDOW_SECONDS = 60
ADMIN_PASSWORD_IP_LIMIT = 5  # failed attempts per client per window
ADMIN_PASSWORD_GLOBAL_LIMIT = 50  # failed attempts across all clients per window
ADMIN_PASSWORD_WINDOW_SECONDS = 600


class RateLimiter:
    """Sliding window: at most `limit` recorded events per key within `window_seconds`."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        max_keys: int = MAX_TRACKED_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit and window must be positive")
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            return deque()
        while events and events[0] <= now - self.window:
            events.popleft()
        return events

    def _retry_after(self, events: deque[float], now: float) -> int:
        return max(1, math.ceil(events[0] + self.window - now))

    def _raise_if_limited(self, key: str, now: float) -> deque[float]:
        events = self._live(key, now)
        if len(events) >= self.limit:
            raise ApiError(
                429, ErrorCode.RATE_LIMITED,
                headers={"Retry-After": str(self._retry_after(events, now))},
            )
        return events

    def _add(self, key: str, now: float) -> None:
        events = self._events.get(key)
        if events is None:
            if len(self._events) >= self.max_keys:
                self._make_room(now)
            events = self._events[key] = deque()
        events.append(now)
        self._events.move_to_end(key)

    def _make_room(self, now: float) -> None:
        for key in [k for k in self._events if not self._live(k, now)]:
            del self._events[key]
        while len(self._events) >= self.max_keys:
            self._events.popitem(last=False)  # least recently active key

    def check(self, key: str) -> None:
        """Raise 429 if `key` is out of budget; records nothing."""
        with self._lock:
            self._raise_if_limited(key, self._clock())

    def record(self, key: str) -> None:
        """Count one event (e.g. a failed password attempt)."""
        with self._lock:
            self._add(key, self._clock())

    def hit(self, key: str) -> None:
        """Check and count in one step: the request that exceeds the limit gets 429."""
        with self._lock:
            now = self._clock()
            self._raise_if_limited(key, now)
            self._add(key, now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


def client_address(request: Request) -> str:
    """The client's IP for rate limiting (see the module docstring on proxies)."""
    if os.getenv("TRUST_FORWARDED_FOR", "").strip().lower() == "true":
        forwarded = request.headers.get("x-forwarded-for", "")
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


login_limiter = RateLimiter(LOGIN_LIMIT, LOGIN_WINDOW_SECONDS)
admin_password_ip_limiter = RateLimiter(ADMIN_PASSWORD_IP_LIMIT, ADMIN_PASSWORD_WINDOW_SECONDS)
admin_password_global_limiter = RateLimiter(
    ADMIN_PASSWORD_GLOBAL_LIMIT, ADMIN_PASSWORD_WINDOW_SECONDS,
)
_ADMIN_GLOBAL_KEY = "admin"


def enforce_login_rate_limit(request: Request) -> None:
    """Dependency for the Google login start and callback endpoints."""
    login_limiter.hit(client_address(request))


def enforce_admin_password_rate_limit(request: Request) -> None:
    """Dependency for admin endpoints taking a password: 429 once failures exceed the budget.

    Only failures count, so call `record_admin_password_failure` when the password is wrong
    and `record_admin_password_success` when it is right.
    """
    admin_password_ip_limiter.check(client_address(request))
    admin_password_global_limiter.check(_ADMIN_GLOBAL_KEY)


def record_admin_password_failure(request: Request) -> None:
    admin_password_ip_limiter.record(client_address(request))
    admin_password_global_limiter.record(_ADMIN_GLOBAL_KEY)


def record_admin_password_success(request: Request) -> None:
    admin_password_ip_limiter.reset(client_address(request))


def reset_all_limits() -> None:
    """Forget every counter (tests and operational recovery)."""
    for limiter in (login_limiter, admin_password_ip_limiter, admin_password_global_limiter):
        limiter.clear()

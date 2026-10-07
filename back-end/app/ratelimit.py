"""Minimal in-memory rate limiting for login and the admin password check (`429 RATE_LIMITED`).

Known limits, by design of this first version:

* State lives in the memory of one process. With several workers or hosts each keeps its own
  counters, so the effective limit is `limit x processes`, and a restart clears everything.
  Move the counters to the database or a cache before relying on exact limits.
* Clients are told apart by IP address. Behind a reverse proxy set `TRUST_FORWARDED_FOR=true`
  so the address the proxy appended to `X-Forwarded-For` is used; without it every request
  looks like it comes from the proxy and shares one budget. Never enable it unless a proxy you
  control sets that header, because clients could otherwise choose their own key.
* At most `MAX_TRACKED_KEYS` keys are tracked. Expired keys are removed first; a key still
  inside its window is never evicted, because that would reset its budget. If the table is full
  of live keys a *new* key gets 429 until the oldest one expires (fail-closed). A flood of
  distinct addresses (possible when `TRUST_FORWARDED_FOR` lets clients pick their key) can
  therefore make new clients wait up to one window, but it cannot burn CPU or clear any
  existing client's limit.
"""

import itertools
import logging
import math
import os
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.errors import ApiError, ErrorCode

MAX_TRACKED_KEYS = 10_000

LOGIN_LIMIT = 20  # requests per client per window
LOGIN_WINDOW_SECONDS = 60
ADMIN_PASSWORD_IP_LIMIT = 5  # failed attempts per client per window
ADMIN_PASSWORD_GLOBAL_LIMIT = 50  # failed attempts across all clients per window
ADMIN_PASSWORD_WINDOW_SECONDS = 600


@dataclass(frozen=True)
class Reservation:
    """One slot taken by `RateLimiter.reserve`; hand it back to `settle` or `release`."""

    key: str
    event_id: int


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
        self._events: OrderedDict[str, deque[tuple[float, int]]] = OrderedDict()
        self._pending: set[int] = set()  # reserved attempts whose outcome is not known yet
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> deque[tuple[float, int]]:
        events = self._events.get(key)
        if events is None:
            return deque()
        while events and events[0][0] <= now - self.window:
            self._pending.discard(events.popleft()[1])
        return events

    def _retry_after(self, events: deque[tuple[float, int]], now: float) -> int:
        return max(1, math.ceil(events[0][0] + self.window - now))

    def _raise_if_limited(self, key: str, now: float) -> deque[tuple[float, int]]:
        events = self._live(key, now)
        if len(events) >= self.limit:
            raise ApiError(
                429, ErrorCode.RATE_LIMITED,
                headers={"Retry-After": str(self._retry_after(events, now))},
            )
        return events

    def _add(self, key: str, now: float) -> int:
        events = self._events.get(key)
        if events is None:
            if len(self._events) >= self.max_keys:
                self._make_room(now)
            events = self._events[key] = deque()
        event_id = next(self._ids)
        events.append((now, event_id))
        self._events.move_to_end(key)
        return event_id

    def _make_room(self, now: float) -> None:
        """Free one slot for a new key by dropping expired keys, or refuse it (429).

        `_events` is ordered by each key's latest event (`_add` moves a key to the end), so the
        keys that expire first are at the front. Expired keys are popped from the front only,
        which makes the work amortized O(1) per new key instead of a scan of every key under the
        lock. A key that is still inside its window is never evicted: dropping it would reset
        its budget, which a flood of forged keys could use to escape a limit. When the table is
        full of live keys the new key is refused instead (fail-closed).
        """
        while len(self._events) >= self.max_keys:
            oldest = next(iter(self._events))
            events = self._live(oldest, now)
            if events:
                raise ApiError(
                    429, ErrorCode.RATE_LIMITED,
                    headers={"Retry-After": str(max(1, math.ceil(events[-1][0] + self.window - now)))},
                )
            del self._events[oldest]

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

    def reserve(self, key: str) -> Reservation:
        """Atomically take one slot, or raise 429 if none is left.

        The slot counts immediately, so concurrent callers cannot all pass a check and then
        exceed the limit together. Afterwards call `settle` (the attempt failed: the slot stays
        used) or `release` (it succeeded: give the slot back).
        """
        with self._lock:
            now = self._clock()
            self._raise_if_limited(key, now)
            event_id = self._add(key, now)
            self._pending.add(event_id)
            return Reservation(key, event_id)

    def settle(self, reservation: Reservation) -> None:
        """Keep the reserved slot as a recorded event; it is no longer in flight."""
        with self._lock:
            self._pending.discard(reservation.event_id)

    def release(self, reservation: Reservation) -> bool:
        """Give a reserved slot back. False if it already expired or was dropped."""
        with self._lock:
            self._pending.discard(reservation.event_id)
            events = self._events.get(reservation.key)
            if events is None:
                return False
            for index, (_, event_id) in enumerate(events):
                if event_id == reservation.event_id:
                    del events[index]
                    return True
            return False

    def reset_settled(self, key: str) -> None:
        """Forget the finished events of `key`, keeping attempts that are still in flight."""
        with self._lock:
            events = self._events.get(key)
            if events is None:
                return
            kept = deque(entry for entry in events if entry[1] in self._pending)
            if kept:
                self._events[key] = kept
            else:
                del self._events[key]

    def reset(self, key: str) -> None:
        with self._lock:
            self._drop(key)

    def _drop(self, key: str) -> None:
        events = self._events.pop(key, ())
        self._pending.difference_update(event_id for _, event_id in events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._pending.clear()


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
_admin_logger = logging.getLogger("jidan.admin")


def enforce_login_rate_limit(request: Request) -> None:
    """Dependency for the Google login start and callback endpoints."""
    login_limiter.hit(client_address(request))


class AdminPasswordAttempt:
    """One reserved attempt at the admin password, from `enforce_admin_password_rate_limit`.

    The attempt already counts against both budgets when the endpoint starts, so concurrent
    guesses cannot slip past a check-then-record gap. Report the outcome exactly once:

    * `failed()` (wrong password): the slots stay used.
    * `succeeded()` (right password): both slots are given back, and the client's earlier
      finished failures are forgotten. Other attempts still in flight keep their slots.

    An attempt left unreported (an unexpected error, or a body that failed validation) is
    settled as failed when the request ends (`AdminAttempt`), which is the safe direction.
    """

    def __init__(self, ip_key: str, ip_slot: Reservation, global_slot: Reservation) -> None:
        self._ip_key = ip_key
        self._ip_slot = ip_slot
        self._global_slot = global_slot
        self._done = False
        self._lock = threading.Lock()

    def _finish(self) -> bool:
        with self._lock:
            first = not self._done
            self._done = True
            return first

    def failed(self) -> None:
        if self._finish():
            admin_password_ip_limiter.settle(self._ip_slot)
            admin_password_global_limiter.settle(self._global_slot)

    def abandoned(self) -> None:
        """The password was never checked (server busy): give both slots back, forgive nothing."""
        if self._finish():
            admin_password_ip_limiter.release(self._ip_slot)
            admin_password_global_limiter.release(self._global_slot)

    def succeeded(self) -> None:
        if self._finish():
            admin_password_ip_limiter.release(self._ip_slot)
            admin_password_global_limiter.release(self._global_slot)
            admin_password_ip_limiter.reset_settled(self._ip_key)


def enforce_admin_password_rate_limit(request: Request) -> AdminPasswordAttempt:
    """Dependency for admin endpoints taking a password: reserves an attempt or answers 429.

    The endpoint then calls `attempt.failed()` or `attempt.succeeded()`. Both budgets (this
    client and all clients) are reserved together; if the second is full the first is returned.
    """
    ip_key = client_address(request)
    ip_slot = admin_password_ip_limiter.reserve(ip_key)
    try:
        global_slot = admin_password_global_limiter.reserve(_ADMIN_GLOBAL_KEY)
    except ApiError:
        admin_password_ip_limiter.release(ip_slot)
        raise
    return AdminPasswordAttempt(ip_key, ip_slot, global_slot)


def admin_password_attempt(request: Request) -> Iterator[AdminPasswordAttempt]:
    """`enforce_admin_password_rate_limit` that settles an unreported attempt as failed.

    FastAPI resolves this dependency before validating the body, so a 400/422 request never
    reaches the endpoint. Left reserved, such a slot stayed "in flight" for the whole window:
    still counted, but never forgiven by a later success the way a wrong password is.
    """
    try:
        attempt = enforce_admin_password_rate_limit(request)
    except ApiError as error:
        # The endpoint never runs, so its operation record is written here (path only: it holds
        # at most the approval request id; never the body or the client address).
        _admin_logger.warning(
            "Admin operation refused: path=%s result=%s", request.url.path, error.code,
        )
        raise
    try:
        yield attempt
    finally:
        attempt.failed()  # no-op once the endpoint reported failed() or succeeded()


AdminAttempt = Annotated[AdminPasswordAttempt, Depends(admin_password_attempt, scope="function")]


def reset_all_limits() -> None:
    """Forget every counter (tests and operational recovery)."""
    for limiter in (login_limiter, admin_password_ip_limiter, admin_password_global_limiter):
        limiter.clear()

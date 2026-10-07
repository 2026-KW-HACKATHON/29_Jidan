"""Shared admin password check for the store approval API (docs/store-approval-design.md).

The server keeps only a hash of the shared admin password, in the `ADMIN_PASSWORD_HASH`
environment variable (runtime.env, never in the image or the repository):

    scrypt$<log2 N>$<r>$<p>$<salt, base64url>$<key, base64url>
    pbkdf2_sha256$<iterations>$<salt, standard base64>$<key, standard base64>

The format check lives in `app.admin_password_config` (standard library only) so `/api/health`
and the deploy gate reject a missing or malformed hash before anyone tries to log in.
Generate it on the server with `python -m app.admin_password` (reads the password twice
without echo and prints only the hash). The password is compared as submitted: no trimming or
truncation. It never reaches logs, responses or error messages; the derived keys are compared
in constant time.

`authenticate_admin(password, attempt)` reports the outcome to the rate limiter exactly once
(`AdminPasswordAttempt.failed` / `succeeded`) and raises 401 ADMIN_PASSWORD_INVALID on a
mismatch. A missing or malformed hash is a server misconfiguration: 500, attempt not reported
here; `AdminAttempt` settles it as failed (still counted, the safe direction).
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import threading

from app.admin_password_config import (  # noqa: F401 - re-exported for callers and tests
    HASH_ENV,
    KEY_BYTES,
    MAX_MEMORY,
    MIN_LOG2_N,
    PBKDF2_ITERATIONS,
    PBKDF2_SCHEME,
    SALT_BYTES,
    SCHEME,
    AdminPasswordConfigError,
    Pbkdf2Hash,
    parse_password_hash,
)
from app.errors import ApiError, ErrorCode
from app.ratelimit import AdminPasswordAttempt

logger = logging.getLogger("jidan.admin")

DEFAULT_LOG2_N = 15
DEFAULT_R = 8
DEFAULT_P = 1
# Each check allocates about 128 * r * N bytes (32 MiB at the defaults). The rate limiter admits
# up to ADMIN_PASSWORD_GLOBAL_LIMIT attempts in flight, which together could take more than a
# GiB on the RPi5. Only this many derive at once per process; a check that cannot start within
# HASH_WAIT_SECONDS is 429 without being counted as a failed guess.
MAX_CONCURRENT_HASHES = 2
HASH_WAIT_SECONDS = 2
_hash_slots = threading.BoundedSemaphore(MAX_CONCURRENT_HASHES)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _derive(password: str, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**log2_n, r=r, p=p, maxmem=MAX_MEMORY,
        dklen=KEY_BYTES,
    )


def hash_password(password: str, *, log2_n: int = DEFAULT_LOG2_N, r: int = DEFAULT_R, p: int = DEFAULT_P) -> str:
    """The `ADMIN_PASSWORD_HASH` value for `password` with a fresh random salt."""
    if not password or not password.strip():
        raise ValueError("password must contain a non-space character")
    salt = secrets.token_bytes(SALT_BYTES)
    key = _derive(password, salt, log2_n, r, p)
    return f"{SCHEME}${log2_n}${r}${p}${_b64encode(salt)}${_b64encode(key)}"


def verify_password(password: str) -> bool:
    """True if `password` matches `ADMIN_PASSWORD_HASH`. Raises AdminPasswordConfigError."""
    parsed = parse_password_hash(os.getenv(HASH_ENV, ""))
    if isinstance(parsed, Pbkdf2Hash):
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), parsed.salt,
                                     parsed.iterations, dklen=KEY_BYTES)
        return hmac.compare_digest(actual, parsed.key)
    salt, log2_n, r, p, expected = parsed
    return hmac.compare_digest(_derive(password, salt, log2_n, r, p), expected)


def password_hash(password: str) -> str:
    """Generate the approved remote PBKDF2 format without rotating existing scrypt hashes."""
    if not password or not password.strip():
        raise ValueError("password must contain a non-space character")
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS,
                              dklen=KEY_BYTES)
    salt_text, key_text = (base64.b64encode(raw).decode("ascii") for raw in (salt, key))
    return f"{PBKDF2_SCHEME}${PBKDF2_ITERATIONS}${salt_text}${key_text}"


def authenticate_admin(password: str, attempt: AdminPasswordAttempt, *, action: str) -> None:
    """Check the admin password, report the attempt once, 401 on a mismatch."""
    if not _hash_slots.acquire(timeout=HASH_WAIT_SECONDS):
        attempt.abandoned()
        raise ApiError(429, ErrorCode.RATE_LIMITED, headers={"Retry-After": "1"})
    try:
        valid = verify_password(password)
    except AdminPasswordConfigError:
        logger.error("Admin password hash is not configured correctly (action=%s)", action)
        raise ApiError(500, ErrorCode.INTERNAL_ERROR) from None
    finally:
        _hash_slots.release()
    if not valid:
        attempt.failed()  # the endpoint's operation record logs the ADMIN_PASSWORD_INVALID result
        raise ApiError(401, ErrorCode.ADMIN_PASSWORD_INVALID)
    attempt.succeeded()


def main() -> None:  # pragma: no cover - interactive helper
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description="Generate ADMIN_PASSWORD_HASH")
    parser.add_argument("--scheme", choices=(PBKDF2_SCHEME, SCHEME), default=PBKDF2_SCHEME)
    args = parser.parse_args()
    first = getpass.getpass("Admin password: ")
    if first != getpass.getpass("Repeat: "):
        raise SystemExit("Passwords do not match")
    print(password_hash(first) if args.scheme == PBKDF2_SCHEME else hash_password(first))


if __name__ == "__main__":  # pragma: no cover
    main()

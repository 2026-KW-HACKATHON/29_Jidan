"""`ADMIN_PASSWORD_HASH` format check shared by login, `/api/health` and the deploy gate.

Standard library only: deploy/scripts/check_runtime_env.py imports it on the deployment host,
which has none of the API dependencies. Both deployed formats are accepted:

    scrypt$<log2 N>$<r>$<p>$<salt, base64url>$<key, base64url>
    pbkdf2_sha256$<iterations>$<salt, standard base64>$<key, standard base64>

Errors never carry the configured value.
"""

import base64
import re
from typing import NamedTuple

HASH_ENV = "ADMIN_PASSWORD_HASH"
SCHEME = "scrypt"
PBKDF2_SCHEME = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
PBKDF2_MAX_ITERATIONS = 2_000_000
MIN_LOG2_N = 14  # N >= 16384 (OWASP minimum for scrypt with r=8, p=1)
MAX_LOG2_N = 20
SALT_BYTES = 16
KEY_BYTES = 32
MAX_MEMORY = 256 * 1024 * 1024

_NUMBER = re.compile(r"[0-9]{1,3}")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")


class AdminPasswordConfigError(ValueError):
    """`ADMIN_PASSWORD_HASH` is missing or malformed. Never carries the configured value."""


class Pbkdf2Hash(NamedTuple):
    iterations: int
    salt: bytes
    key: bytes


def _parse_pbkdf2(stored: str) -> Pbkdf2Hash:
    """Match the approved remote parser, including canonical decimal/base64 encodings."""
    try:
        scheme, rounds_text, salt_text, key_text = stored.split("$")
        rounds = int(rounds_text)
        salt = base64.b64decode(salt_text, validate=True)
        key = base64.b64decode(key_text, validate=True)
        if (
            scheme != PBKDF2_SCHEME
            or not PBKDF2_ITERATIONS <= rounds <= PBKDF2_MAX_ITERATIONS
            or str(rounds) != rounds_text
            or not SALT_BYTES <= len(salt) <= 64
            or len(key) != KEY_BYTES
            or base64.b64encode(salt).decode("ascii") != salt_text
            or base64.b64encode(key).decode("ascii") != key_text
        ):
            raise ValueError("invalid format")
    except ValueError:
        raise AdminPasswordConfigError(f"{HASH_ENV} is malformed") from None
    return Pbkdf2Hash(rounds, salt, key)


def _b64decode(text: str) -> bytes:
    if not _BASE64URL.fullmatch(text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text.rstrip("=") + "=" * (-len(text.rstrip("=")) % 4))


def parse_password_hash(stored: str) -> tuple[bytes, int, int, int, bytes] | Pbkdf2Hash:
    """Legacy scrypt tuple or Pbkdf2Hash; both login and deployment use this parser."""
    if not stored.strip():
        raise AdminPasswordConfigError(f"{HASH_ENV} is not set")
    if stored.startswith(PBKDF2_SCHEME + "$"):
        return _parse_pbkdf2(stored)
    try:
        scheme, log2_n, r, p, salt, key = stored.strip().split("$")
        if not all(_NUMBER.fullmatch(number) for number in (log2_n, r, p)):
            raise ValueError("not a decimal number")
        log2_n, r, p = int(log2_n), int(r), int(p)
        salt_bytes, key_bytes = _b64decode(salt), _b64decode(key)
    except ValueError:
        raise AdminPasswordConfigError(f"{HASH_ENV} is malformed") from None
    if (
        scheme != SCHEME or not MIN_LOG2_N <= log2_n <= MAX_LOG2_N or not 1 <= r <= 32
        or not 1 <= p <= 16 or len(salt_bytes) < SALT_BYTES or len(key_bytes) != KEY_BYTES
        # OpenSSL's scrypt requires N < 2**(16 * r) (so r=1 allows at most 2**15)...
        or log2_n >= 16 * r
        # ...and must fit in MAX_MEMORY. Either failure would 500 every login instead.
        or 128 * r * (2**log2_n + p + 2) > MAX_MEMORY
    ):
        raise AdminPasswordConfigError(f"{HASH_ENV} is malformed")
    return salt_bytes, log2_n, r, p, key_bytes

"""Shared column conventions: CHAR(36) UUIDs and UTC timestamps."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import CHAR, DateTime, String, TypeDecorator


def new_uuid() -> str:
    return str(uuid.uuid4())


def iso_utc(value: datetime | None) -> str | None:
    """The API notation of an instant: RFC 3339 in UTC with `+00:00` (`isoformat()`), e.g.
    `2026-10-05T03:00:00+00:00`. Every response time goes through this; naive values are a bug."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("response times must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def normalize_optional_text(value: str | None) -> str | None:
    """Trim optional free text; omitted, empty and whitespace-only values are stored as NULL."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def cs_string(length: int) -> String:
    """VARCHAR compared case-sensitively on MySQL (utf8mb4_0900_as_cs); SQLite already is.

    utf8mb4_0900_as_cs is NO PAD, so 'WORKER ' != 'WORKER'. The older utf8mb4_bin is PAD SPACE:
    it would let `role = 'WORKER '` pass the CHECK and make UNIQUE treat 'a' and 'a ' as equal.

    MySQL's default collation (utf8mb4_0900_ai_ci) is case- and accent-insensitive, so
    `role IN ('WORKER')` would accept 'worker' and UNIQUE would treat 'Ab' and 'ab' as equal.
    Use it for enum-like CHECK columns and opaque identifiers (google_sub, token_hash).
    Emails use `email_string` instead. Never use it on a FK/PK column unless both sides match.
    """
    return String(length).with_variant(String(length, collation="utf8mb4_0900_as_cs"), "mysql")


def email_string(length: int) -> String:
    """E-mail VARCHAR: case-insensitive but accent-sensitive on MySQL (utf8mb4_0900_as_ci, NO PAD).

    The default utf8mb4_0900_ai_ci also folds accents and ligatures, so 'josé@x.com' = 'jose@x.com'
    and 'straße@x.com' = 'strasse@x.com' in SQL. as_ci keeps those apart while 'JOSE@x.com' still
    equals 'jose@x.com'. It still treats full-width letters and zero-width characters as equal to
    their plain forms, so callers keep comparing exactly in Python (`app.email_match.email_is`).
    SQLite compares bytes; services store and compare lower-cased values (`normalize_email`).
    """
    return String(length).with_variant(String(length, collation="utf8mb4_0900_as_ci"), "mysql")


def cs_char(length: int) -> CHAR:
    """Fixed-length CHAR with the same case-sensitive MySQL collation as `cs_string`.

    For a UUID the client sends (not generated here), where MySQL's default collation would fold
    'ABC...' and 'abc...' into one value. Server-generated UUID keys stay plain CHAR(36).
    """
    return CHAR(length).with_variant(CHAR(length, collation="utf8mb4_0900_as_cs"), "mysql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """DATETIME(6) holding naive UTC; values in and out are timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            from sqlalchemy.dialects.mysql import DATETIME

            return dialect.type_descriptor(DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed; use timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)

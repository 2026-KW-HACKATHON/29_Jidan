"""Shared column conventions: CHAR(36) UUIDs and UTC timestamps."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, TypeDecorator


def new_uuid() -> str:
    return str(uuid.uuid4())


def normalize_optional_text(value: str | None) -> str | None:
    """Trim optional free text; omitted, empty and whitespace-only values are stored as NULL."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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

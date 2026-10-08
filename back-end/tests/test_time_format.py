"""API instants are written one way: RFC 3339 UTC with `+00:00` (`app.db.iso_utc`)."""

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db import iso_utc

APP = Path(__file__).resolve().parents[1] / "app"


def test_iso_utc_writes_plus_zero_offsets():
    assert iso_utc(datetime(2026, 10, 5, 3, 0, tzinfo=UTC)) == "2026-10-05T03:00:00+00:00"
    assert iso_utc(datetime(2026, 10, 5, 3, 0, 0, 120, tzinfo=UTC)) == "2026-10-05T03:00:00.000120+00:00"
    seoul = timezone(timedelta(hours=9))
    assert iso_utc(datetime(2026, 10, 5, 12, 0, tzinfo=seoul)) == "2026-10-05T03:00:00+00:00"
    assert iso_utc(None) is None
    with pytest.raises(ValueError):
        iso_utc(datetime(2026, 10, 5, 3, 0))  # noqa: DTZ001 - the naive value under test


def test_no_module_rewrites_the_offset_to_z():
    pattern = re.compile(r"""replace\(\s*["']\+00:00["']\s*,\s*["']Z["']\s*\)|strftime\([^)]*Z""")
    offenders = [str(path.relative_to(APP)) for path in APP.rglob("*.py") if pattern.search(path.read_text())]
    assert offenders == []


def test_shared_serializers_use_the_same_notation():
    from app.jobs.common import iso as jobs_iso
    from app.manual_content import iso as manual_iso
    from app.media.transcription import iso as media_iso
    from app.stores import iso as store_iso

    value = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
    assert {f(value) for f in (jobs_iso, manual_iso, media_iso, store_iso)} == {"2026-10-05T03:00:00+00:00"}

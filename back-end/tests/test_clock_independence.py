"""The suite must not depend on the day it runs (tests/clock_shift.py).

Fixed test data (tests/factories.NOW = 2026-10-05 03:00 UTC, jobs on 10-07..10-10) once broke 156
tests the moment the real clock passed 10-06 03:00 UTC (fc76d07). This guard re-runs files that
mix fixed dates with "now" (rows, grants, expiries, certificates, file ages) in a child pytest
whose clock is about 400 days ahead of today, so a test that reads the real date where it should
use the fixed one fails here on any day, not only after the date passes. SQLite only: the
database is not what is under test. The full-suite check is in tests/clock_shift.py's docstring.
"""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

BACK_END = Path(__file__).resolve().parents[1]
GUARDED = (
    "tests/test_collation.py",          # the original failure: created_at "now" vs fixed expiry
    "tests/test_store_workers.py",      # grant validity and EXPIRING windows around NOW
    "tests/test_worker_stores.py",      # stores listed only while a grant is valid
    "tests/test_media_retention.py",    # media ages, TTLs and file times
    "tests/test_mail_smtp.py",          # TLS certificate validity
)


def _future() -> str:
    return (datetime.now(UTC) + timedelta(days=400)).replace(microsecond=0).isoformat()


@pytest.mark.skipif(bool(os.getenv("JIDAN_TEST_CLOCK")), reason="already running with a moved clock")
def test_clock_sensitive_files_pass_with_the_clock_moved_ahead():
    environment = {key: value for key, value in os.environ.items() if not key.startswith("DB_")}
    environment.update(JIDAN_TEST_CLOCK=_future(), JIDAN_REQUIRE_MYSQL="0")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.clock_shift",
         "-m", "not mysql", *GUARDED],
        cwd=BACK_END, env=environment, capture_output=True, text=True, timeout=600, check=False,
    )
    summary = result.stdout.strip().splitlines()[-30:]
    assert result.returncode == 0, "\n".join(summary)


def test_the_plugin_moves_every_clock_the_suite_reads():
    code = (
        "import time\n"
        "from datetime import UTC, date, datetime\n"
        "import tests.clock_shift as clock\n"
        "clock.pytest_configure(None)\n"
        "from app.db import utcnow\n"
        "print(datetime.now(UTC).date(), date.today(), utcnow().date(),"
        " datetime.fromtimestamp(time.time(), UTC).date())\n"
        "clock.pytest_unconfigure(None)\n"
    )
    environment = dict(os.environ, JIDAN_TEST_CLOCK="2031-02-03T12:00:00+00:00")
    result = subprocess.run([sys.executable, "-c", code], cwd=BACK_END, env=environment,
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["2031-02-03"] * 4
    assert datetime.now(UTC).year < 2031  # this process is untouched

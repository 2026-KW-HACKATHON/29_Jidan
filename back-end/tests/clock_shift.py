"""Run the test suite with the wall clock moved, to find tests that depend on today's date.

    JIDAN_TEST_CLOCK=2027-01-01T00:00:00Z pytest -p tests.clock_shift -m "not mysql"

The suite's fixed test data (tests/factories.NOW = 2026-10-05 03:00 UTC, jobs on 10-07..10-10, ...)
must keep working whatever day the suite is run on, so every test either injects a clock or keeps
its values consistent with that data. This plugin moves `datetime.now`, `date.today` and
`time.time` (time-machine patches them at the C level, so the application, SQLAlchemy defaults
and the tests all see the same shifted, still ticking clock) before any test module is imported.
Without JIDAN_TEST_CLOCK it does nothing.

Files on disk keep their real modification times; the orphan-file sweep compares those with
`time.time()`, so a large shift makes every just-written file look old. Its tests set explicit
ages instead of relying on the real clock.
"""

import os
from datetime import UTC, datetime

import time_machine

_traveller = None


def target() -> datetime | None:
    value = os.getenv("JIDAN_TEST_CLOCK")
    return datetime.fromisoformat(value) if value else None


def pytest_configure(config):
    global _traveller
    destination = target()
    if destination is None:
        return
    _traveller = time_machine.travel(destination, tick=True)
    _traveller.start()


def pytest_unconfigure(config):
    global _traveller
    if _traveller is not None:
        _traveller.stop()
        _traveller = None


def pytest_terminal_summary(terminalreporter):
    """Evidence that the clock really moved in this run: the application's own clock next to
    the real one (time-machine's escape hatch), with the process ID."""
    if _traveller is None:
        return
    from app.db import utcnow

    real = time_machine.escape_hatch.datetime.datetime.now(UTC)
    terminalreporter.write_line(
        f"clock_shift: target={target().isoformat()} pid={os.getpid()} "
        f"app_utcnow={utcnow().isoformat()} real_now={real.isoformat()}"
    )


def pytest_report_header(config):
    destination = target()
    return f"clock shifted to {destination.isoformat()}" if destination else None

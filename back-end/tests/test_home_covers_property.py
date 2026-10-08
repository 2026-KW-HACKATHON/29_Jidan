"""`app.home.covers` against a brute-force minute check over random availability and shifts.

Random weekly windows (half-hour grid, up to 24 hours, overnight allowed) and random shifts on
real dates are compared with an independent check: every minute of the shift must lie in some
window expanded over a week of real Seoul dates around it. This covers midnight crossings,
SUN -> MON, adjacent windows, touching ends and 24-hour windows without listing them by hand.
"""
import random
from datetime import date, time, timedelta

import pytest

from app.home import WEEKDAYS, covers
from app.jobs import common

CASES = 3000


def _window(rng: random.Random):
    days = set(rng.sample(WEEKDAYS, rng.randint(1, 7)))
    start = rng.randrange(48) * 30
    length = rng.randrange(1, 49) * 30  # 30 minutes .. 24 hours
    end = (start + length) % 1440
    return days, time(start // 60, start % 60), time(end // 60, end % 60), start + length >= 1440


def _shift(rng: random.Random, step: int):
    work_date = date(2026, 1, 1) + timedelta(days=rng.randrange(730))
    start = rng.randrange(1440 // step) * step
    length = rng.randrange(1, 1440 // step) * step  # under 24 hours
    end = (start + length) % 1440
    return common.shift_times(work_date, time(start // 60, start % 60), time(end // 60, end % 60),
                              start + length >= 1440)


def _brute_force(windows, start_at, end_at) -> bool:
    first = common.seoul_today(start_at) - timedelta(days=3)
    spans = []
    for offset in range(8):
        day = first + timedelta(days=offset)
        for weekdays, start, end, next_day in windows:
            if WEEKDAYS[day.weekday()] in weekdays:
                times = common.shift_times(day, start, end, next_day)
                spans.append((times.start_at, times.end_at))
    minute = start_at
    while minute < end_at:
        if not any(a <= minute < b for a, b in spans):
            return False
        minute += timedelta(minutes=1)
    return True


@pytest.mark.parametrize("seed,step", [(1, 30), (2, 30), (3, 10)])
def test_covers_matches_a_minute_by_minute_check(seed, step):
    rng = random.Random(seed)
    covered = 0
    for _ in range(CASES):
        windows = [_window(rng) for _ in range(rng.randint(1, 4))]
        shift = _shift(rng, step)
        expected = _brute_force(windows, shift.start_at, shift.end_at)
        assert covers(windows, shift.start_at, shift.end_at) == expected, (windows, shift)
        covered += expected
    # Both outcomes occur often enough for the comparison to mean something.
    assert CASES * 0.1 < covered < CASES * 0.9

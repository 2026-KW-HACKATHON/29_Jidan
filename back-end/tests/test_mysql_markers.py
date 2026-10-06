"""Every test that runs against MySQL is marked `mysql` (tests/mysql_marker_audit.py).

An unmarked MySQL test still runs, but `-m mysql` misses it, the conftest's "JIDAN_REQUIRE_MYSQL=1
but MySQL is unavailable" exit does not cover it, and the "mysql: ran=..." summary under-reports
MySQL coverage. Override `db_engine` with `mysql_only`-style marks: `pytest.mark.mysql` together
with `parametrize("db_engine", ["mysql"], indirect=True)`.
"""

import os
import subprocess
import sys
from pathlib import Path

BACK_END = Path(__file__).resolve().parents[1]


def test_every_mysql_test_carries_the_mysql_marker(tmp_path):
    out = tmp_path / "audit.txt"
    environment = dict(os.environ, JIDAN_MARKER_AUDIT_OUT=str(out))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--collect-only",
         "-p", "tests.mysql_marker_audit"],
        cwd=BACK_END, env=environment, capture_output=True, text=True, timeout=300, check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    header, *unmarked = out.read_text(encoding="utf-8").splitlines()
    assert int(header.removeprefix("checked=")) > 1000  # the audit really saw the MySQL tests
    assert unmarked == []

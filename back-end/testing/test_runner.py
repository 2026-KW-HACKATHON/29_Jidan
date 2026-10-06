"""Failure propagation and cleanup isolation of the actual shell runner."""
import os
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).with_name("run-e2e.sh")


@pytest.mark.parametrize("phase,cleanup_fails,expected", [
    ("none", False, 0), ("up", False, 7), ("run", False, 9),
    ("none", True, 1), ("run", True, 9),
])
def test_runner_propagates_failure_and_only_cleans_its_project(tmp_path, phase, cleanup_fails, expected):
    log = tmp_path / "commands"
    docker = tmp_path / "docker"
    docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_COMMAND_LOG"
case "$*" in
  *" up "*) [ "$FAIL_PHASE" != up ] || exit 7 ;;
  *" run "*) [ "$FAIL_PHASE" != run ] || exit 9 ;;
  *" down "*) [ "$FAIL_CLEANUP" != yes ] || exit 4 ;;
esac
''')
    docker.chmod(0o755)
    reports = tmp_path / "reports with spaces"
    environment = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                   "TEST_COMMAND_LOG": str(log), "FAIL_PHASE": phase,
                   "FAIL_CLEANUP": "yes" if cleanup_fails else "no",
                   "JIDAN_E2E_REPORT_DIR": str(reports)}
    result = subprocess.run(["sh", str(RUNNER)], env=environment, capture_output=True, timeout=10, check=False)
    assert result.returncode == expected, result.stderr.decode()
    commands = log.read_text().splitlines()
    projects = {line.split()[2] for line in commands}
    assert len(projects) == 1 and next(iter(projects)).startswith("jidan-e2e-")
    assert "jidan-sandbox" not in log.read_text()
    assert commands[-1].endswith("down --volumes --remove-orphans")
    assert (reports / "services.log").exists()
    if phase == "up":
        assert not any(" run " in line for line in commands)

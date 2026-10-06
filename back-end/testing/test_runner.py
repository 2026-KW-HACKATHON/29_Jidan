"""Failure propagation and cleanup isolation of the actual shell runner."""
import os
import signal
import subprocess
import time
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


@pytest.mark.parametrize("sent_signal,expected", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_runner_interrupts_waiting_child_and_cleans_its_project(tmp_path, sent_signal, expected, cleanup_fails):
    docker = tmp_path / "docker"
    docker.write_text('''#!/usr/bin/env python3
import os, signal, sys, time
from pathlib import Path
log = Path(os.environ["TEST_COMMAND_LOG"])
with log.open("a") as out:
    out.write(" ".join(sys.argv[1:]) + "\\n")
if "run" in sys.argv:
    def stopped(*_):
        Path(os.environ["TEST_CHILD_STOPPED"]).write_text("stopped")
        sys.exit(0)
    signal.signal(signal.SIGTERM, stopped)
    Path(os.environ["TEST_CHILD_READY"]).write_text(str(os.getpid()))
    time.sleep(120)
if "down" in sys.argv and os.getenv("FAIL_CLEANUP") == "yes":
    sys.exit(4)
''')
    docker.chmod(0o755)
    log, ready, stopped = (tmp_path / name for name in ("commands", "ready", "stopped"))
    environment = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                   "TEST_COMMAND_LOG": str(log), "TEST_CHILD_READY": str(ready),
                   "TEST_CHILD_STOPPED": str(stopped), "FAIL_CLEANUP": "yes" if cleanup_fails else "no",
                   "JIDAN_E2E_REPORT_DIR": str(tmp_path / "reports")}
    process = subprocess.Popen(["sh", str(RUNNER)], env=environment, start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "runner never entered the waiting phase"
        # Signal only the runner, not its process group: it must forward cancellation.
        process.send_signal(sent_signal)
        _, error = process.communicate(timeout=5)
        assert process.returncode == expected, error.decode()
        deadline = time.monotonic() + 2
        while not stopped.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stopped.exists(), "waiting Docker child was not stopped"
        commands = log.read_text().splitlines()
        assert len({line.split()[2] for line in commands}) == 1
        assert "jidan-sandbox" not in log.read_text()
        assert commands[-1].endswith("down --volumes --remove-orphans")
        assert (tmp_path / "reports" / "services.log").exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        if ready.exists() and not stopped.exists():
            try:
                os.kill(int(ready.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

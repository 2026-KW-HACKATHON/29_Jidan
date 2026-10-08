"""Real Uvicorn helpers for PR review regressions; no external service calls."""
import os
import socket
import subprocess
import sys
from contextlib import contextmanager

from app.admin_password import hash_password
from e2e.demo_scenario import server_env


def local_env():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    extra, password = server_env(origin, 25)
    # No external calls or background writes; startup still validates every setting.
    extra.update(BACKGROUND_JOBS="off", TASK_RUNNER_WORKERS="2", TASK_RUNNER_POLL_SECONDS="2",
                 SMTP_TIMEOUT_SECONDS="10", ADMIN_PASSWORD_HASH=hash_password(password, log2_n=14))
    return {**os.environ, **extra}, origin, port, password


@contextmanager
def server(env, port, tmp_path):
    with (tmp_path / "uvicorn.log").open("w+") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                                    "--host", "127.0.0.1", "--port", str(port)],
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            yield process, log
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)



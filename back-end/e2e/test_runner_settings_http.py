"""Reject invalid settings before the real Uvicorn API becomes available."""
import httpx
import pytest
from sqlalchemy import select

from app.db.models import StoreApprovalRequest
from e2e.review_helpers import local_env, server


@pytest.mark.parametrize("name,value", [
    *(("TASK_RUNNER_WORKERS", value) for value in ("0", "-1", "1.5", "nan", "inf", "x", "")),
    *(("TASK_RUNNER_POLL_SECONDS", value) for value in ("0", "-1", "nan", "inf", "-inf", "x", "")),
])
def test_invalid_settings_stop_real_api_startup(real_db, tmp_path, name, value):
    env, origin, port, _ = local_env()
    env[name] = value
    with real_db.connect() as db:
        before = db.execute(select(StoreApprovalRequest.__table__)).all()
    with server(env, port, tmp_path) as (process, log):
        assert process.wait(timeout=20) != 0
        log.seek(0)
        output = log.read()
        assert name in output and "Application startup failed" in output
        with httpx.Client(trust_env=False, timeout=1) as client, pytest.raises(httpx.ConnectError):
            client.get(origin + "/api/health")
    with real_db.connect() as db:
        assert db.execute(select(StoreApprovalRequest.__table__)).all() == before

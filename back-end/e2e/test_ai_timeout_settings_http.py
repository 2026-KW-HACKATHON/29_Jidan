"""Reject invalid settings before the real Uvicorn API becomes available."""
import httpx
import pytest
from sqlalchemy import select

from app.db.models import StoreApprovalRequest
from e2e.review_helpers import local_env, server


@pytest.mark.parametrize("name", ["OPENAI_TIMEOUT_SECONDS", "OPENAI_TRANSCRIBE_TIMEOUT_SECONDS"])
@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf"])
def test_invalid_settings_stop_real_api_startup(real_db, tmp_path, name, value):
    env, origin, port, _ = local_env()
    env.update(BACKGROUND_JOBS="on", TASK_RUNNER_MODE="background", AI_PROVIDER="openai",
               OPENAI_API_KEY="sk-test-not-used", OPENAI_TIMEOUT_SECONDS="60",
               OPENAI_TRANSCRIBE_TIMEOUT_SECONDS="120")
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

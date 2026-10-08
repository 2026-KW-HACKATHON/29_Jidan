"""Admin search contract via real Uvicorn HTTP and independent MySQL reads."""
import time

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Store, StoreApprovalRequest, User
from e2e.review_helpers import local_env, server
from tests.factories import make_store_with_request, make_user

SEARCH = "/api/admin/store-approval-requests/search"


@pytest.mark.parametrize("workers,poll,timeout", [("1", "0.01", "1"), ("2", "2", "60")])
def test_admin_search_contract_and_preservation(real_db, tmp_path, workers, poll, timeout):
    env, origin, port, password = local_env()
    env.update(TASK_RUNNER_WORKERS=workers, TASK_RUNNER_POLL_SECONDS=poll, SMTP_TIMEOUT_SECONDS=timeout)
    with Session(real_db) as db:
        owner = make_user(db, "OWNER")
        store, request = make_store_with_request(db, owner)
        db.commit()
        owner_id, store_id, request_id = owner.id, store.id, request.id

    def snapshot():
        with real_db.connect() as db:
            return {model.__tablename__: tuple(db.execute(select(model.__table__).where(
                model.id == row_id)).one()) for model, row_id in (
                    (User, owner_id), (Store, store_id), (StoreApprovalRequest, request_id))}

    before = snapshot()
    with (server(env, port, tmp_path) as (process, _log),
          httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=3, trust_env=False) as client):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert process.poll() is None, "server exited before health check"
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("server did not become healthy")
        for page in (1_000_001, 2**64, 10**100):
            response = client.post(SEARCH, json={"password": password, "page": page})
            assert response.status_code == 200, response.text
            assert response.json()["items"] == [] and response.json()["page"] == page
        for body in ({"status": None}, {"page": -1}, {"page": True}, {"page": "1"}, {"page": None}, {"page": 1.5}):
            response = client.post(SEARCH, json={"password": password, **body})
            assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
            # Real successful authentication clears settled failures before the next edge case.
            assert client.post(SEARCH, json={"password": password}).status_code == 200
        for status in (None, "PENDING", "APPROVED"):
            body = {"password": password, "size": 100}
            if status is not None:
                body["status"] = status
            response = client.post(SEARCH, json=body)
            assert response.status_code == 200, response.text
            ids = [item["id"] for item in response.json()["items"]]
            assert (request_id in ids) == (status != "APPROVED")
        assert snapshot() == before

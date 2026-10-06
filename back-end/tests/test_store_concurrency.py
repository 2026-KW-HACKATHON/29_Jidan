from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth, owner_stores
from app.db.models import Store, StoreApprovalRequest
from tests.factories import make_user
from tests.store_contract import StoreContractClient
from tests.test_store_creation import BODY
from tests.test_store_creation import address_provider as _address_provider
from tests.test_store_creation import store_api as _store_api
from tests.test_worker_registration import headers

store_api, address_provider = _store_api, _address_provider


@pytest.mark.parametrize("shared_key", [False, True])
def test_concurrent_store_creation(store_api, db_engine, shared_key):
    if db_engine.dialect.name != "mysql":
        pytest.skip("MySQL concurrency required")
    start = Barrier(3)
    h = headers(store_api)
    def create(_):
        request_headers = dict(h)
        if not shared_key:
            request_headers["Idempotency-Key"] = str(uuid4())
        start.wait(timeout=10)
        return store_api.post("/api/stores", json=BODY, headers=request_headers)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(create, range(3)))
    if shared_key:
        assert [r.status_code for r in responses] == [201] * 3
        assert all(r.json() == responses[0].json() for r in responses)
        assert sum("Idempotent-Replayed" in r.headers for r in responses) == 2
    else:
        assert sorted(r.status_code for r in responses) == [201, 409, 409]
        assert all(r.json()["code"] == "STORE_ALREADY_REGISTERED"
                   for r in responses if r.status_code == 409)
    with Session(db_engine) as db:
        for model in (Store, StoreApprovalRequest):
            assert db.scalar(select(func.count()).select_from(model)) == 1


def test_two_owners_race_for_same_business_number(store_api, db_engine, monkeypatch):
    if db_engine.dialect.name != "mysql":
        pytest.skip("MySQL UNIQUE contention required")
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        issued = auth.create_session(owner.id, db=db)
        db.commit()
    other_api = StoreContractClient(store_api.app, raise_server_exceptions=False)
    other_api.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
    h1, h2 = headers(store_api), headers(other_api)
    provider_barrier = Barrier(2)
    def validate(body):
        provider_barrier.wait(timeout=10)
        return body.address
    monkeypatch.setattr(owner_stores, "verify_store_address", validate)
    def create(args):
        api, h = args
        return api.post("/api/stores", json=BODY, headers=h)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(create, [(store_api, h1), (other_api, h2)]))
    assert sorted(r.status_code for r in responses) == [201, 409]
    winner = responses[0].status_code == 201
    assert len(store_api.get("/api/owners/me/stores").json()["items"]) == int(winner)
    assert len(other_api.get("/api/owners/me/stores").json()["items"]) == int(not winner)

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.db import utcnow
from app.db.models import Store, User
from tests.api_contract import login
from tests.factories import (
    make_regular_grant,
    make_store_with_request,
    make_temporary_grant,
    make_user,
)
from tests.invitation_helpers import APPROVED_AT, MISSING, make_world

LIST = "/api/users/me/stores"


def _access(store_id):
    return f"/api/users/me/stores/{store_id}/access"


def _setup(db_engine):
    """A worker with: regular access (store A), temporary access ending soon (B), a revoked
    access (C), an expired access (D), access to a store whose owner is suspended (E)."""
    now = utcnow()
    world = make_world(db_engine)
    with Session(db_engine) as db:
        worker = db.get(User, world.worker_id)
        a = db.get(Store, world.store_id)

        def store(name):
            created, _ = make_store_with_request(db, make_user(db, "OWNER"), approved_at=APPROVED_AT, name=name)
            return created

        b, c, d, e = store("B"), store("C"), store("D"), store("E")
        make_regular_grant(db, a, worker, granted_at=now - timedelta(days=3))
        make_temporary_grant(db, b, worker, granted_at=now - timedelta(hours=1), valid_until=now + timedelta(hours=5))
        make_regular_grant(db, c, worker, granted_at=now - timedelta(days=2), revoked_at=now - timedelta(days=1))
        make_regular_grant(db, d, worker, granted_at=now - timedelta(days=9), valid_until=now - timedelta(days=1))
        make_regular_grant(db, e, worker, granted_at=now - timedelta(days=1))
        db.get(User, e.owner_id).status = "SUSPENDED"
        db.commit()
        return world, {"A": a.id, "B": b.id, "C": c.id, "D": d.id, "E": e.id}


def test_lists_only_currently_usable_stores(api, db_engine):
    world, stores = _setup(db_engine)
    login(api, world.worker_id)
    response = api.get(LIST)
    assert response.status_code == 200
    body = response.json()
    assert [item["store"]["id"] for item in body["items"]] == [stores["B"], stores["A"]]
    assert (body["page"], body["size"], body["totalItems"]) == (0, 20, 2)
    assert "totalPages" not in body
    temp, regular = body["items"]
    assert temp["store"] == {
        "id": stores["B"], "name": "B", "industry": "CAFE", "address": "서울 노원구 월계1동", "neighborhood": "월계1동",
    }
    assert temp["access"]["accessStatus"] == "EXPIRING" and temp["access"]["workerId"] == world.worker_id
    assert temp["access"]["accessGrants"][0]["type"] == "TEMPORARY"
    assert regular["access"]["accessStatus"] == "ACTIVE"
    assert temp["publishedVersionId"] is None  # store B has no published manual
    page = api.get(f"{LIST}?page=1&size=1").json()
    assert [item["store"]["id"] for item in page["items"]] == [stores["A"]] and page["totalItems"] == 2
    assert api.get(f"{LIST}?page=3").json()["items"] == []


def test_store_pending_approval_is_not_listed(api, db_engine):
    world, stores = _setup(db_engine)
    with Session(db_engine) as db:
        store = db.get(Store, stores["A"])
        store.approval_status, store.approved_at = "PENDING", None
        db.commit()
    login(api, world.worker_id)
    assert [item["store"]["id"] for item in api.get(LIST).json()["items"]] == [stores["B"]]
    assert api.get(_access(stores["A"])).status_code == 404


def test_access_recheck(api, db_engine):
    world, stores = _setup(db_engine)
    login(api, world.worker_id)
    ok = api.get(_access(stores["A"].upper()))
    assert ok.status_code == 200 and ok.json()["store"]["id"] == stores["A"]
    assert ok.json()["access"]["accessStatus"] == "ACTIVE"
    for label in ("C", "D", "E"):
        response = api.get(_access(stores[label]))
        assert response.status_code == 404 and response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert api.get(_access(MISSING)).status_code == 404
    other = make_world(db_engine, worker_email="other@example.com")
    assert api.get(_access(other.store_id)).status_code == 404
    assert api.get(_access("bad")).status_code == 422


def test_revocation_takes_effect_on_the_next_request(api, db_engine):
    world, stores = _setup(db_engine)
    login(api, world.worker_id)
    assert api.get(_access(stores["A"])).status_code == 200
    owner = login(api, world.owner_id)
    assert api.delete(f"/api/stores/{stores['A']}/workers/{world.worker_id}/access",
                      headers=owner.headers()).status_code == 204
    login(api, world.worker_id)
    assert api.get(_access(stores["A"])).status_code == 404
    assert [item["store"]["id"] for item in api.get(LIST).json()["items"]] == [stores["B"]]


def test_empty(api, db_engine):
    world = make_world(db_engine)
    login(api, world.worker_id)
    body = api.get(LIST).json()
    assert {key: body[key] for key in ("items", "page", "size", "totalItems")} == {
        "items": [], "page": 0, "size": 20, "totalItems": 0,
    }


@pytest.mark.parametrize("path", [LIST, _access(MISSING)])
def test_worker_only(api, db_engine, path):
    world = make_world(db_engine)
    assert api.get(path).status_code == 401
    login(api, world.owner_id)
    response = api.get(path)
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"
    login(api, world.worker_id)
    with Session(db_engine) as db:
        db.get(User, world.worker_id).status = "SUSPENDED"
        db.commit()
    assert api.get(path).json()["code"] == "ACCOUNT_SUSPENDED"


@pytest.mark.parametrize("query", ["page=-1", "size=101", "size=x"])
def test_list_pagination_validation(api, db_engine, query):
    world = make_world(db_engine)
    login(api, world.worker_id)
    assert api.get(f"{LIST}?{query}").status_code == 422


def test_published_manual_version_is_linked(api, db_engine):
    from app.db.models import StoreManual
    from tests.factories import make_manual_draft

    world, stores = _setup(db_engine)
    with Session(db_engine) as db:
        store = db.get(Store, stores["A"])
        draft = make_manual_draft(db, store)  # a draft alone is not published
        db.commit()
        version_id, owner_id = draft.id, store.owner_id
    login(api, world.worker_id)
    assert {item["store"]["id"]: item["publishedVersionId"] for item in api.get(LIST).json()["items"]}[stores["A"]] is None
    with Session(db_engine) as db:
        version = db.get(type(draft), version_id)
        version.status, version.generation_status = "PUBLISHED", "READY"
        version.published_at, version.published_by_owner_id = utcnow(), owner_id
        db.flush()
        db.get(StoreManual, version.manual_id).current_published_version_id = version.id
        db.commit()
    items = {item["store"]["id"]: item["publishedVersionId"] for item in api.get(LIST).json()["items"]}
    assert items[stores["A"]] == version_id and items[stores["B"]] is None
    assert api.get(_access(stores["A"])).json()["publishedVersionId"] == version_id

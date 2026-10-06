"""B08: the worker's store list reads the page's stores as one set, so its database round trips
do not grow with the number of stores, and every item still equals the per-store access body."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app import worker_stores
from app.db import utcnow
from app.db.models import StoreManual, User
from app.pagination import PageParams
from tests.api_contract import login
from tests.factories import (
    NOW,
    make_manual_draft,
    make_regular_grant,
    make_store_with_request,
    make_temporary_grant,
    make_user,
    make_worker,
)

LIST = "/api/users/me/stores"


class SelectCounter:
    def __init__(self, engine):
        self.engine, self.statements = engine, []

    def _record(self, _connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_exc):
        event.remove(self.engine, "before_cursor_execute", self._record)


def worker_with_stores(engine, count: int) -> str:
    with Session(engine) as db:
        worker = make_worker(db)
        for index in range(count):
            store, _ = make_store_with_request(db, approved_at=NOW, created_at=NOW)
            make_regular_grant(db, store, worker, granted_at=NOW - timedelta(minutes=index))
        db.commit()
        return worker.id


def list_selects(engine, worker_id: str, monkeypatch) -> tuple[int, dict]:
    monkeypatch.setattr(worker_stores, "utcnow", lambda: NOW + timedelta(hours=1))
    with Session(engine) as db, SelectCounter(engine) as counter:
        body = worker_stores.list_my_stores(SimpleNamespace(user_id=worker_id), db, PageParams(0, 100))
    return len(counter.statements), body


def test_select_count_does_not_grow_with_the_stores(db_engine, monkeypatch):
    counts = {}
    for stores in (1, 10, 100):
        worker_id = worker_with_stores(db_engine, stores)
        counts[stores], body = list_selects(db_engine, worker_id, monkeypatch)
        assert len(body["items"]) == stores and body["totalItems"] == stores
        assert all(item["access"]["accessStatus"] == "ACTIVE" for item in body["items"])
    assert counts[1] == counts[10] == counts[100] <= 6, counts


def test_http_list_round_trips_are_constant(api, db_engine):
    few, many = worker_with_stores(db_engine, 2), worker_with_stores(db_engine, 30)
    measured = {}
    for name, worker_id in (("few", few), ("many", many)):
        login(api, worker_id)
        with SelectCounter(db_engine) as counter:
            response = api.get(f"{LIST}?size=100")
        assert response.status_code == 200
        measured[name] = len(counter.statements)
    assert measured["few"] == measured["many"], measured


def test_items_match_the_per_store_access_body_in_order(api, db_engine):
    """Several grants per store (regular, temporary with a job title, revoked, ended), a store
    whose owner is suspended, expired access, and a published manual: the batched list keeps
    the order (latest grant start first) and each item equals GET .../{storeId}/access."""
    now = utcnow()
    with Session(db_engine) as db:
        worker = make_worker(db)

        def store(name):
            created, _ = make_store_with_request(db, make_user(db, "OWNER"), approved_at=NOW, name=name)
            return created

        a, b, c, d, e = store("A"), store("B"), store("C"), store("D"), store("E")
        make_regular_grant(db, a, worker, granted_at=now - timedelta(days=5))
        make_temporary_grant(db, a, worker, granted_at=now - timedelta(hours=2), valid_until=now + timedelta(hours=3),
                             title="주말 마감 대타")
        make_regular_grant(db, b, worker, granted_at=now - timedelta(days=1), revoked_at=now - timedelta(hours=1))
        make_regular_grant(db, b, worker, granted_at=now - timedelta(days=4))
        make_temporary_grant(db, c, worker, granted_at=now - timedelta(hours=1), valid_until=now + timedelta(hours=10))
        make_regular_grant(db, d, worker, granted_at=now - timedelta(days=9), valid_until=now - timedelta(days=1))
        make_regular_grant(db, e, worker, granted_at=now - timedelta(minutes=5))
        db.get(User, e.owner_id).status = "SUSPENDED"
        draft = make_manual_draft(db, a)
        draft.status, draft.generation_status = "PUBLISHED", "READY"
        draft.published_at, draft.published_by_owner_id = now, a.owner_id
        db.flush()
        db.get(StoreManual, draft.manual_id).current_published_version_id = draft.id
        db.commit()
        ids = {"A": a.id, "B": b.id, "C": c.id, "worker": worker.id, "version": draft.id}
    login(api, ids["worker"])
    body = api.get(LIST).json()
    assert [item["store"]["id"] for item in body["items"]] == [ids["C"], ids["A"], ids["B"]]
    for item in body["items"]:
        single = api.get(f"{LIST}/{item['store']['id']}/access").json()
        assert item["store"] == single["store"] and item["publishedVersionId"] == single["publishedVersionId"]
        assert {**item["access"], "asOf": None} == {**single["access"], "asOf": None}
    by_store = {item["store"]["id"]: item for item in body["items"]}
    assert by_store[ids["A"]]["publishedVersionId"] == ids["version"]
    assert [g["type"] for g in by_store[ids["A"]]["access"]["accessGrants"]] == ["TEMPORARY", "REGULAR"]
    assert by_store[ids["A"]]["access"]["accessGrants"][0]["dutyLabel"] == "주말 마감 대타"
    assert len(by_store[ids["B"]]["access"]["accessGrants"]) == 2  # revoked history stays visible
    assert by_store[ids["C"]]["access"]["accessStatus"] == "EXPIRING"


@pytest.mark.parametrize("page,size,expected", [(0, 2, 2), (2, 2, 1), (3, 2, 0)])
def test_pages_of_the_batched_list(api, db_engine, page, size, expected):
    worker_id = worker_with_stores(db_engine, 5)
    login(api, worker_id)
    everything = [item["store"]["id"] for item in api.get(f"{LIST}?size=100").json()["items"]]
    body = api.get(f"{LIST}?page={page}&size={size}").json()
    assert [item["store"]["id"] for item in body["items"]] == everything[page * size:page * size + size]
    assert len(body["items"]) == expected
    assert body["totalItems"] == 5

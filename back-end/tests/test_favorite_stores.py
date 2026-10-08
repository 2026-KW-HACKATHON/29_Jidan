"""PUT/DELETE/GET /api/users/me/favorite-stores against openapi.yaml."""
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FavoriteStore, JobApplication, Store, StoreAccessGrant, User
from tests.api_contract import ORIGIN, login
from tests.factories import NOW, make_store, make_user, make_worker

BASE = "/api/users/me/favorite-stores"


def seed(db_engine, build):
    with Session(db_engine, expire_on_commit=False) as session:
        result = build(session)
        session.commit()
        return result


def approved_store(session, **overrides) -> Store:
    return make_store(session, approval_status="APPROVED", approved_at=NOW, **overrides)


def world(session):
    """A worker, an approved store and a pending one."""
    return make_worker(session), approved_store(session), make_store(session)


def save(api, member, store_id, key=None, body=None):
    return api.put(f"{BASE}/{store_id}", json={} if body is None else body,
                   headers=member.headers(key or str(uuid.uuid4())))


def favorites(db_engine) -> list[FavoriteStore]:
    with Session(db_engine) as session:
        return list(session.scalars(select(FavoriteStore)))


# --- PUT ---------------------------------------------------------------------------------------


def test_save_returns_the_public_store_card(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    response = save(api, member, store.id)
    assert response.status_code == 200
    body = response.json()
    assert body["store"] == {
        "id": store.id, "name": store.name, "industry": "CAFE", "address": store.address,
        "neighborhood": "월계1동",
    }
    [favorite] = favorites(db_engine)
    assert favorite.saved_at.isoformat() == body["savedAt"]
    assert store.phone_number not in response.text and store.business_registration_number not in response.text


def test_saving_again_keeps_the_first_saved_at(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    first = save(api, member, store.id).json()
    again = save(api, member, store.id)  # a new key
    assert again.status_code == 200 and again.json() == first
    assert len(favorites(db_engine)) == 1


def test_same_key_replays_and_reuse_elsewhere_is_409(api, db_engine):
    def build(session):
        worker, store, _ = world(session)
        return worker, store, approved_store(session)
    worker, store, other = seed(db_engine, build)
    member = login(api, worker.id)
    key = str(uuid.uuid4())
    first = save(api, member, store.id, key)
    replay = save(api, member, store.id, key)
    assert replay.headers["Idempotent-Replayed"] == "true" and replay.json() == first.json()
    reused = save(api, member, other.id, key)
    assert reused.status_code == 409 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(favorites(db_engine)) == 1


def test_unapproved_and_missing_stores_are_the_same_404(api, db_engine):
    worker, _, pending = seed(db_engine, world)
    member = login(api, worker.id)
    for store_id in (pending.id, str(uuid.uuid4())):
        response = save(api, member, store_id)
        assert response.status_code == 404 and response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert favorites(db_engine) == []


def test_replay_rechecks_that_the_store_is_still_approved(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    key = str(uuid.uuid4())
    assert save(api, member, store.id, key).status_code == 200

    def unapprove(session):
        row = session.get(Store, store.id)
        row.approval_status, row.approved_at = "PENDING", None
    seed(db_engine, unapprove)
    assert save(api, member, store.id, key).status_code == 404


def test_upper_case_store_id_is_the_same_store(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    assert save(api, member, store.id.upper()).json()["store"]["id"] == store.id


@pytest.mark.parametrize("bad_id", ["abc", "0" * 32, "%7B" + "0" * 32 + "%7D"])
def test_malformed_store_id_is_422(api, db_engine, bad_id):
    member = login(api, seed(db_engine, make_worker).id)
    response = save(api, member, bad_id)
    assert response.status_code == 422 and response.json()["fieldErrors"][0]["field"] == "storeId"


@pytest.mark.parametrize("body", [{"note": "x"}, [], "x"])
def test_save_body_must_be_the_empty_object(api, db_engine, body):
    worker, store, _ = seed(db_engine, world)
    assert save(api, login(api, worker.id), store.id, body=body).status_code == 422
    assert favorites(db_engine) == []


def test_save_needs_key_session_worker_role_and_csrf(api, db_engine):
    def build(session):
        worker, store, _ = world(session)
        return worker, store, make_user(session, "OWNER")
    worker, store, owner = seed(db_engine, build)
    url = f"{BASE}/{store.id}"
    anonymous = api.put(url, json={}, headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())})
    assert anonymous.status_code == 401
    owner_member = login(api, owner.id)
    forbidden = save(api, owner_member, store.id)
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN"
    member = login(api, worker.id)
    assert api.put(url, json={}, headers=member.headers()).status_code == 422
    headers = member.headers(str(uuid.uuid4()))
    for broken in ({**headers, "X-CSRF-Token": "x"}, {**headers, "Origin": "http://evil.test"}):
        response = api.put(url, json={}, headers=broken)
        assert response.status_code == 403 and response.json()["code"] == "CSRF_INVALID"
    seed(db_engine, lambda s: setattr(s.get(User, worker.id), "status", "SUSPENDED"))
    assert save(api, member, store.id).json()["code"] == "ACCOUNT_SUSPENDED"
    assert favorites(db_engine) == []


def test_saving_grants_no_access_and_applies_nowhere(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    save(api, login(api, worker.id), store.id)
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(StoreAccessGrant)) == 0
        assert session.scalar(select(func.count()).select_from(JobApplication)) == 0


def test_concurrent_saves_create_one_relation(api, db_engine, monkeypatch):
    from app import idempotency

    monkeypatch.setattr(idempotency, "POLL_INTERVAL_SECONDS", 0.01)
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    barrier = threading.Barrier(5)
    results = [None] * 5

    def run(index):
        barrier.wait()
        results[index] = save(api, member, store.id)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert [r.status_code for r in results] == [200] * 5, [r.text for r in results]
    [favorite] = favorites(db_engine)
    assert {r.json()["savedAt"] for r in results} == {favorite.saved_at.isoformat()}


def test_other_workers_save_independently(api, db_engine):
    def build(session):
        worker, store, _ = world(session)
        return worker, make_worker(session), store
    worker, other, store = seed(db_engine, build)
    save(api, login(api, worker.id), store.id)
    later = save(api, login(api, other.id), store.id)
    assert later.status_code == 200 and len(favorites(db_engine)) == 2


def test_saved_at_is_not_moved_by_time(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    seed(db_engine, lambda s: s.add(FavoriteStore(worker_id=worker.id, store_id=store.id,
                                                  saved_at=NOW - timedelta(days=3))))
    response = save(api, login(api, worker.id), store.id)
    assert response.json()["savedAt"] == (NOW - timedelta(days=3)).isoformat()


# --- DELETE ------------------------------------------------------------------------------------


def remove(api, member, store_id, key=None):
    return api.delete(f"{BASE}/{store_id}", headers=member.headers(key or str(uuid.uuid4())))


def test_remove_deletes_only_my_relation(api, db_engine):
    def build(session):
        worker, store, _ = world(session)
        other = make_worker(session)
        session.add_all([FavoriteStore(worker_id=worker.id, store_id=store.id),
                         FavoriteStore(worker_id=other.id, store_id=store.id)])
        return worker, other, store
    worker, other, store = seed(db_engine, build)
    response = remove(api, login(api, worker.id), store.id)
    assert response.status_code == 204 and response.content == b""
    assert [(f.worker_id, f.store_id) for f in favorites(db_engine)] == [(other.id, store.id)]


def test_remove_of_a_missing_relation_or_store_is_still_204(api, db_engine):
    worker, store, pending = seed(db_engine, world)
    member = login(api, worker.id)
    for store_id in (store.id, pending.id, str(uuid.uuid4())):
        assert remove(api, member, store_id).status_code == 204


def test_remove_works_after_the_store_lost_approval(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    save(api, member, store.id)

    def unapprove(session):
        row = session.get(Store, store.id)
        row.approval_status, row.approved_at = "PENDING", None
    seed(db_engine, unapprove)
    assert remove(api, member, store.id).status_code == 204
    assert favorites(db_engine) == []


def test_remove_replays_and_rejects_key_reuse(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    key = str(uuid.uuid4())
    assert remove(api, member, store.id, key).status_code == 204
    replay = remove(api, member, store.id, key)
    assert replay.status_code == 204 and replay.headers["Idempotent-Replayed"] == "true"
    reused = save(api, member, store.id, key)  # same key, other endpoint
    assert reused.status_code == 409 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_save_after_remove_starts_a_new_saved_at(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    seed(db_engine, lambda s: s.add(FavoriteStore(worker_id=worker.id, store_id=store.id,
                                                  saved_at=NOW - timedelta(days=3))))
    member = login(api, worker.id)
    remove(api, member, store.id)
    assert save(api, member, store.id).json()["savedAt"] != (NOW - timedelta(days=3)).isoformat()


def test_remove_needs_key_session_worker_role_and_csrf(api, db_engine):
    def build(session):
        worker, store, _ = world(session)
        session.add(FavoriteStore(worker_id=worker.id, store_id=store.id))
        return worker, store, make_user(session, "OWNER")
    worker, store, owner = seed(db_engine, build)
    url = f"{BASE}/{store.id}"
    assert api.delete(url, headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())}).status_code == 401
    assert remove(api, login(api, owner.id), store.id).json()["code"] == "FORBIDDEN"
    member = login(api, worker.id)
    assert api.delete(url, headers=member.headers()).status_code == 422
    assert api.delete(f"{BASE}/not-a-uuid", headers=member.headers(str(uuid.uuid4()))).status_code == 422
    bad = {**member.headers(str(uuid.uuid4())), "X-CSRF-Token": "x"}
    assert api.delete(url, headers=bad).json()["code"] == "CSRF_INVALID"
    assert len(favorites(db_engine)) == 1


def test_concurrent_save_and_remove_leave_a_consistent_state(api, db_engine, monkeypatch):
    """Save/remove races serialize on the primary key: the final state is one of the two outcomes."""
    from app import idempotency

    monkeypatch.setattr(idempotency, "POLL_INTERVAL_SECONDS", 0.01)
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    barrier = threading.Barrier(6)
    results = [None] * 6

    def run(index):
        barrier.wait()
        results[index] = (save if index % 2 else remove)(api, member, store.id)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(r.status_code for r in results) == [200, 200, 200, 204, 204, 204], [r.text for r in results]
    assert len(favorites(db_engine)) in (0, 1)


# --- GET ---------------------------------------------------------------------------------------


def saved(session, worker, store, minutes=0):
    session.add(FavoriteStore(worker_id=worker.id, store_id=store.id, saved_at=NOW + timedelta(minutes=minutes)))


def test_list_is_newest_first_with_store_id_tie_break(api, db_engine):
    def build(session):
        worker = make_worker(session)
        stores = [approved_store(session) for _ in range(4)]
        for store, minutes in zip(stores, (0, 5, 5, 10), strict=True):
            saved(session, worker, store, minutes)
        return worker, stores
    worker, stores = seed(db_engine, build)
    login(api, worker.id)
    body = api.get(BASE).json()
    tied = sorted((stores[1].id, stores[2].id), reverse=True)
    assert [i["store"]["id"] for i in body["items"]] == [stores[3].id, *tied, stores[0].id]
    assert body["totalItems"] == 4 and (body["page"], body["size"]) == (0, 20)


def test_list_and_count_exclude_unapproved_stores(api, db_engine):
    def build(session):
        worker, store, pending = world(session)
        saved(session, worker, store)
        saved(session, worker, pending)  # e.g. approval withdrawn after saving
        return worker, store
    worker, store = seed(db_engine, build)
    login(api, worker.id)
    body = api.get(BASE).json()
    assert [i["store"]["id"] for i in body["items"]] == [store.id] and body["totalItems"] == 1


def test_list_shows_only_my_favorites(api, db_engine):
    def build(session):
        worker, other, store = make_worker(session), make_worker(session), approved_store(session)
        saved(session, other, store)
        return worker
    login(api, seed(db_engine, build).id)
    body = api.get(BASE).json()
    assert body["items"] == [] and body["totalItems"] == 0


def test_list_pages_and_past_the_end(api, db_engine):
    def build(session):
        worker = make_worker(session)
        stores = [approved_store(session) for _ in range(5)]
        for index, store in enumerate(stores):
            saved(session, worker, store, index % 2)
        return worker, stores
    worker, stores = seed(db_engine, build)
    login(api, worker.id)
    seen = []
    for page in range(3):
        body = api.get(BASE, params={"page": page, "size": 2}).json()
        assert body["totalItems"] == 5
        seen += [i["store"]["id"] for i in body["items"]]
    assert sorted(seen) == sorted(s.id for s in stores)
    assert api.get(BASE, params={"page": 3, "size": 2}).json()["items"] == []


@pytest.mark.parametrize("query", ["size=0", "size=101", "page=-1", "page=x", "size=1&size=2"])
def test_list_rejects_bad_paging(api, db_engine, query):
    login(api, seed(db_engine, make_worker).id)
    assert api.get(f"{BASE}?{query}").status_code == 422


def test_list_needs_a_worker_session(api, db_engine):
    assert api.get(BASE).status_code == 401
    login(api, seed(db_engine, lambda s: make_user(s, "OWNER")).id)
    response = api.get(BASE)
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"


def test_saved_store_appears_in_the_list(api, db_engine):
    worker, store, _ = seed(db_engine, world)
    member = login(api, worker.id)
    saved_body = save(api, member, store.id).json()
    assert api.get(BASE).json()["items"] == [saved_body]
    remove(api, member, store.id)
    assert api.get(BASE).json()["totalItems"] == 0


def test_remove_key_reused_for_another_store_is_409(api, db_engine):
    def build(session):
        worker, store, _ = world(session)
        return worker, store, approved_store(session)
    worker, store, other = seed(db_engine, build)
    member = login(api, worker.id)
    idem = str(uuid.uuid4())
    assert remove(api, member, store.id, idem).status_code == 204
    reused = remove(api, member, other.id, idem)
    assert reused.status_code == 409 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize("operation", ["list", "save", "remove"])
def test_unexpected_failure_is_a_bare_500_and_changes_nothing(api, db_engine, monkeypatch, operation):
    from app import favorite_stores

    def fail(*_args, **_kwargs):
        raise RuntimeError("SELECT secret FROM somewhere")

    worker, store, _ = seed(db_engine, world)
    seed(db_engine, lambda s: s.add(FavoriteStore(worker_id=worker.id, store_id=store.id, saved_at=NOW)))
    member = login(api, worker.id)
    if operation == "list":
        monkeypatch.setattr(favorite_stores, "favorite_body", fail)
        response = api.get(BASE)
    elif operation == "save":
        monkeypatch.setattr(favorite_stores, "favorite_body", fail)
        response = save(api, member, store.id)
    else:
        monkeypatch.setattr(favorite_stores, "IdempotentResult", fail)
        response = remove(api, member, store.id)
    assert response.status_code == 500 and response.json()["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.text
    assert len(favorites(db_engine)) == 1  # the remove rolled back

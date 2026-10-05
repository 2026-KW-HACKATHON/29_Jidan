import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import registration, store_address
from app.db.models import Store, StoreApprovalRequest, User
from app.errors import ApiError, ErrorCode
from app.registration_inputs import StoreInput
from tests.test_worker_registration import headers
from tests.test_worker_registration import worker_api as _worker_api

worker_api = _worker_api

OWNER = {"name": "점주", "phoneNumber": "01012345678", "store": {
    "name": "매장", "industry": "CAFE", "postalCode": "01897", "address": "서울특별시 노원구 광운로 20",
    "businessRegistrationNumber": "1234567890", "phoneNumber": "029123456",
}}


def test_owner_success_pending_and_retry(worker_api, db_engine, monkeypatch):
    monkeypatch.setattr(registration, "verify_store_address", lambda store: store.address)
    h = headers(worker_api)
    r = worker_api.post("/api/auth/registrations/owners", json=OWNER, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["nextAction"] == "OWNER_APPROVAL_PENDING"
    assert r.json()["user"]["stores"][0]["permissions"] == ["READ_STORE_STATUS"]
    retry = worker_api.post("/api/auth/registrations/owners", json=OWNER, headers=headers(worker_api, h["Idempotency-Key"]))
    assert retry.status_code == 201 and retry.json() == r.json() and "set-cookie" not in retry.headers
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(Store)).approval_status == "PENDING"
        assert db.scalar(select(StoreApprovalRequest)).status == "PENDING"


@pytest.mark.parametrize("code,status", [(ErrorCode.STORE_OUTSIDE_SERVICE_AREA, 422), (ErrorCode.INTERNAL_ERROR, 500)])
def test_address_failure_rolls_back(worker_api, db_engine, monkeypatch, code, status):
    def fail(store): raise ApiError(status, code)
    monkeypatch.setattr(registration, "verify_store_address", fail)
    r = worker_api.post("/api/auth/registrations/owners", json=OWNER, headers=headers(worker_api))
    assert r.status_code == status and r.json()["code"] == code and "set-cookie" not in r.headers
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(Store)) == 0
        assert db.scalar(select(func.count()).select_from(StoreApprovalRequest)) == 0


@pytest.mark.parametrize("scenario,expected", [
    ("ok", None), ("postal", "VALIDATION_ERROR"), ("no_match", "VALIDATION_ERROR"),
    ("outside", "STORE_OUTSIDE_SERVICE_AREA"), ("legal_only", "VALIDATION_ERROR"),
    ("http", "INTERNAL_ERROR"), ("ambiguous", "VALIDATION_ERROR"),
])
def test_kakao_administrative_dong(monkeypatch, scenario, expected):
    import httpx
    monkeypatch.setenv("KAKAO_REST_API_KEY", "SECRET-KEY")
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 5
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            if scenario == "http": return httpx.Response(503)
            if "search/address" in url:
                row = {"x": "127.0", "y": "37.0", "road_address": {
                    "address_name": OWNER["store"]["address"], "zone_no": "99999" if scenario == "postal" else "01897",
                }}
                docs = [] if scenario == "no_match" else [row, row] if scenario == "ambiguous" else [row]
            else:
                docs = [{"region_type": "B" if scenario == "legal_only" else "H", "region_1depth_name": "서울특별시",
                         "region_2depth_name": "노원구", "region_3depth_name": "월계2동" if scenario == "outside" else "월계1동"}]
            return httpx.Response(200, json={"documents": docs})
    monkeypatch.setattr(store_address.httpx, "Client", Client)
    if expected:
        with pytest.raises(ApiError) as exc:
            store_address.verify_store_address(StoreInput.model_validate(OWNER["store"]))
        assert exc.value.code == expected
        assert "SECRET-KEY" not in exc.value.message
    else:
        assert store_address.verify_store_address(StoreInput.model_validate(OWNER["store"])) == OWNER["store"]["address"]


def test_duplicate_business_number_is_not_access_grant(worker_api, db_engine, monkeypatch):
    from tests.factories import make_store, make_user
    with Session(db_engine) as db:
        owner = make_user(db, "OWNER")
        make_store(db, owner, business_registration_number=OWNER["store"]["businessRegistrationNumber"])
        db.commit()
    monkeypatch.setattr(registration, "verify_store_address", lambda store: pytest.fail("duplicate must stop first"))
    r = worker_api.post("/api/auth/registrations/owners", json=OWNER, headers=headers(worker_api))
    assert r.status_code == 409 and r.json()["code"] == "STORE_ALREADY_REGISTERED"
    with Session(db_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Store)) == 1

"""Owner identity/store fixtures skip Kakao signup; session serialization is real HTTP."""
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth
from app.db import utcnow
from app.db.models import AuthSession, Store, User


def test_owner_session_follows_persisted_approval_and_excludes_other_owner(real_db, base_url):
    subject = f"owner-session-e2e-{uuid.uuid4()}"
    with Session(real_db) as db:
        owner = User(google_sub=subject, google_email=f"{subject}@e2e.test", email_verified=True,
                     role="OWNER", status="ACTIVE", name="테스트 점주", phone_number="01012345678")
        other = User(google_sub=subject + "-other", google_email=f"{subject}-other@e2e.test", email_verified=True,
                     role="OWNER", status="ACTIVE", name="다른 점주", phone_number="01087654321")
        db.add_all([owner, other])
        db.flush()
        issued = auth.create_session(owner.id, db=db)
        db.commit()
        owner_id = owner.id
        other_id = other.id
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False,
                      headers={"Cookie": f"{auth.SESSION_COOKIE_NAME}={issued.token}"}) as client:
        empty = client.get("/api/auth/session")
        assert empty.status_code == 200 and empty.json()["nextAction"] == "OWNER_APPROVAL_PENDING"
        assert empty.json()["user"]["id"] == owner_id and empty.json()["user"]["role"] == "OWNER"
        assert empty.json()["user"]["stores"] == []
        assert empty.json()["user"]["permissions"] == ["READ_OWN_STORE_STATUS"]
        number = uuid.uuid4().int % 10**10
        with Session(real_db) as db:
            common = {"industry": "CAFE", "postal_code": "01897", "address": "서울 노원구 광운로 20",
                      "detail_address": "", "phone_number": "021234567"}
            own_store = Store(owner_id=owner_id, name="검토 중 매장", approval_status="PENDING",
                              business_registration_number=f"{number:010}", **common)
            foreign_store = Store(owner_id=other_id, name="다른 점주 매장", approval_status="APPROVED",
                                  approved_at=utcnow(), business_registration_number=f"{(number + 1) % 10**10:010}", **common)
            db.add_all([own_store, foreign_store])
            db.commit()
            store_id = own_store.id
        pending = client.get("/api/auth/session")
        assert pending.status_code == 200 and pending.json()["nextAction"] == "OWNER_APPROVAL_PENDING"
        assert pending.json()["user"]["stores"] == [{"storeId": store_id, "storeName": "검토 중 매장",
                                                      "approvalStatus": "PENDING", "permissions": ["READ_STORE_STATUS"]}]
        with Session(real_db) as db:
            second_store = Store(owner_id=owner_id, name="승인된 두 번째 매장", approval_status="APPROVED",
                                 approved_at=utcnow(), business_registration_number=f"{(number + 2) % 10**10:010}", **common)
            db.add(second_store)
            db.commit()
            second_id = second_store.id
        mixed = client.get("/api/auth/session")
        assert mixed.status_code == 200 and mixed.json()["nextAction"] == "OWNER_HOME"
        mixed_stores = {store["storeId"]: store for store in mixed.json()["user"]["stores"]}
        assert set(mixed_stores) == {store_id, second_id}
        assert mixed_stores[store_id]["approvalStatus"] == "PENDING"
        assert mixed_stores[store_id]["permissions"] == ["READ_STORE_STATUS"]
        assert mixed_stores[second_id]["approvalStatus"] == "APPROVED"
        assert mixed_stores[second_id]["permissions"] == ["READ_STORE_STATUS", "MANAGE_STORE", "INVITE_WORKERS",
                                                        "MANAGE_JOB_POSTINGS", "MANAGE_MANUALS"]
        with Session(real_db) as db:
            db.delete(db.get(Store, second_id))
            db.commit()
        with Session(real_db) as db:
            store = db.get(Store, store_id)
            store.approval_status = "APPROVED"
            store.approved_at = utcnow()
            db.commit()
        approved = client.get("/api/auth/session")
        assert approved.status_code == 200 and approved.json()["nextAction"] == "OWNER_HOME"
        stores = approved.json()["user"]["stores"]
        assert len(stores) == 1 and stores[0]["storeId"] == store_id and stores[0]["approvalStatus"] == "APPROVED"
        assert stores[0]["permissions"] == ["READ_STORE_STATUS", "MANAGE_STORE", "INVITE_WORKERS",
                                            "MANAGE_JOB_POSTINGS", "MANAGE_MANUALS"]
        assert approved.headers["cache-control"] == "no-store"
        with Session(real_db) as db:
            assert db.get(Store, store_id).owner_id == owner_id
            assert db.get(Store, store_id).approval_status == "APPROVED"

        with Session(real_db) as db:
            db.get(User, owner_id).status = "SUSPENDED"
            db.commit()
        suspended = client.get("/api/auth/session")
        assert suspended.status_code == 403 and suspended.json()["code"] == "ACCOUNT_SUSPENDED"
        with Session(real_db) as db:
            assert db.scalar(select(AuthSession).where(AuthSession.user_id == owner_id)).revoked_at is not None
            db.get(User, owner_id).status = "ACTIVE"
            db.commit()
        restored = client.get("/api/auth/session")
        assert restored.status_code == 401 and restored.json()["code"] == "SESSION_EXPIRED"

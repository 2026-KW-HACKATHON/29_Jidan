from fastapi import FastAPI
from sqlalchemy.orm import Session

from app import auth
from app.auth_views import router
from app.db import engine as engine_module
from app.errors import install_error_handlers
from tests.auth_contract import ContractClient
from tests.factories import make_store, make_user


def test_session_permissions_reflect_approval(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "get_engine", lambda: engine)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    api = ContractClient(app)
    assert api.get("/api/auth/session").status_code == 401
    with Session(engine) as db:
        user = make_user(db, "OWNER")
        store = make_store(db, user, approval_status="PENDING")
        store_id = store.id
        issued = auth.create_session(user.id, db=db)
        db.commit()
    api.cookies.set(auth.SESSION_COOKIE_NAME, issued.token)
    pending = api.get("/api/auth/session").json()
    assert pending["nextAction"] == "OWNER_APPROVAL_PENDING"
    assert pending["user"]["stores"][0]["permissions"] == ["READ_STORE_STATUS"]
    with Session(engine) as db:
        from app.db import utcnow
        from app.db.models import Store
        row = db.get(Store, store_id)
        row.approval_status, row.approved_at = "APPROVED", utcnow()
        db.commit()
    assert api.get("/api/auth/session").json()["nextAction"] == "OWNER_HOME"
    assert api.get("/api/auth/csrf").json()["csrfToken"] == issued.csrf_token

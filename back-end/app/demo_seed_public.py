"""Initialize the fixed public reading sample, without resetting any visitor workspace."""
from sqlalchemy.orm import Session

from app.db import session_scope, utcnow
from app.db.models import Store, User
from app.demo import SAMPLE_OWNER, SAMPLE_STORE, new_store, require_demo
from app.manual_demo_seed import seed_published_manual
from app.operator_cli import operator_cli


def seed(db: Session) -> None:
    require_demo()
    if db.get(Store, SAMPLE_STORE) is not None:
        return
    db.add(User(id=SAMPLE_OWNER, google_sub="demo-sample:owner", google_email="sample@demo.invalid",
                email_verified=True, role="OWNER", name="샘플 점주", phone_number="01000000000"))
    db.flush()
    new_store(db, SAMPLE_OWNER, store_id=SAMPLE_STORE)
    seed_published_manual(db, {"cafe": SAMPLE_STORE}, utcnow())


if __name__ == "__main__":
    with operator_cli(), session_scope() as db:
        seed(db)

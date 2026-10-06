"""`python -m e2e.demo_scenario --cleanup`: deletes only the runner's accounts, behind the seed guards."""
import sys
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import demo_seed
from app.auth import create_registration_session
from app.db import utcnow
from app.db.models import (
    IdempotencyRecord,
    JobApplication,
    JobPosting,
    Notification,
    RegistrationSession,
    Store,
    StoreAccessGrant,
    User,
)
from app.operator_cli import operator_cli
from e2e import demo_scenario
from tests.factories import (
    make_application,
    make_invitation,
    make_job,
    make_notification,
    make_regular_grant,
    make_store,
    make_user,
    make_worker,
)
from tests.test_demo_seed import snapshot


@pytest.fixture
def safe_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan_cleanup_test")


def run_main(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["e2e.demo_scenario", *args])
    return demo_scenario.main()


def subject(sub: str) -> str:
    return demo_seed._subject(sub)


def idempotency_row(sub: str) -> IdempotencyRecord:
    now = utcnow()
    return IdempotencyRecord(subject_id=subject(sub), idempotency_key="0" * 8 + "-0000-4000-8000-" + "0" * 12,
                             endpoint="POST /api/auth/registrations/owners", request_hash="0" * 64,
                             state="PROCESSING", expires_at=now + timedelta(hours=1))


def e2e_run(db: Session, run: str, demo_job_id: str) -> dict:
    """What a runner leaves behind: owner with store, posting and invitation; a worker with a
    grant, an application (also one to a demo posting), notifications; plus a registration that
    never completed with its idempotency record."""
    owner = make_user(db, "OWNER", google_sub=f"e2e:{run}:owner")
    worker = make_user(db, "WORKER", google_sub=f"e2e:{run}:worker-a")
    store = make_store(db, owner)
    job = make_job(db, store)
    make_application(db, job, worker)
    make_application(db, db.get(JobPosting, demo_job_id), worker)
    make_invitation(db, store)
    make_regular_grant(db, store, worker)
    make_notification(db, owner)
    make_notification(db, worker)
    db.add(idempotency_row(f"e2e:{run}:owner"))
    db.commit()
    create_registration_session(f"e2e:{run}:worker-c", "c@jidan.example", db=db)
    db.add(idempotency_row(f"e2e:{run}:worker-c"))
    db.commit()
    return {"users": [owner.id, worker.id], "store": store.id, "job": job.id}


def count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_cleanup_deletes_runner_accounts_only(db_engine, safe_env, monkeypatch):
    seeded = demo_seed.run()
    with Session(db_engine) as db:
        # Real accounts, including subs that only look similar: none may be matched.
        lookalikes = [make_user(db, google_sub=sub).id for sub in ("e2e", "e2e-x:1", "xe2e:1", "E2E:1", "e2e%:1")]
        outsider = make_worker(db)
        outsider_store = make_store(db, make_user(db, "OWNER"))
        make_notification(db, outsider)
        db.commit()
        outsider_id, outsider_store_id = outsider.id, outsider_store.id
    before = snapshot(db_engine)
    with Session(db_engine) as db:
        first = e2e_run(db, "aaaa1111", seeded["open_job"])
        second = e2e_run(db, "bbbb2222", seeded["open_job"])

    assert run_main(monkeypatch, "--cleanup") == 0

    # Demo seed and everyone else's data are exactly as before; the runner's application to a
    # demo posting went with the runner's worker.
    assert snapshot(db_engine) == before
    with Session(db_engine) as db:
        for created in (first, second):
            assert all(db.get(User, user_id) is None for user_id in created["users"])
            assert db.get(Store, created["store"]) is None and db.get(JobPosting, created["job"]) is None
        assert count(db, User, demo_seed._starts(User.google_sub, "e2e:")) == 0
        assert count(db, RegistrationSession, demo_seed._starts(RegistrationSession.google_sub, "e2e:")) == 0
        assert count(db, IdempotencyRecord) == 0  # both the member's and the unfinished registration's
        assert all(db.get(User, user_id) is not None for user_id in lookalikes)
        assert db.get(User, outsider_id) is not None and db.get(Store, outsider_store_id) is not None
        assert count(db, Notification, Notification.recipient_user_id == outsider_id) == 1
    # Nothing left to clean: running again is a no-op.
    assert run_main(monkeypatch, "--cleanup") == 0
    assert snapshot(db_engine) == before


@pytest.mark.parametrize(("env", "name"), [
    ({"APP_ENV": "production"}, "jidan_local"), ({"APP_ENV": "staging"}, "jidan_local"),
    ({"APP_ENV": "local"}, "jidan"), ({"APP_ENV": "local"}, "jidan_local_backup"), ({"APP_ENV": "local"}, ""),
])
def test_cleanup_refuses_unsafe_targets_without_deleting(db_engine, monkeypatch, capsys, env, name):
    with Session(db_engine) as db:
        user_id = make_user(db, "OWNER", google_sub="e2e:cccc3333:owner").id
        db.commit()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DB_NAME", name)
    assert run_main(monkeypatch, "--cleanup") == 2
    assert "refused:" in capsys.readouterr().err
    with Session(db_engine) as db:
        assert db.get(User, user_id) is not None


@pytest.mark.parametrize("prefix", ["", "e2e", "demo-seed"])
def test_delete_accounts_requires_a_namespace_prefix(db_engine, prefix):
    with Session(db_engine) as db, pytest.raises(ValueError):
        demo_seed.delete_accounts(db, prefix)


def test_delete_accounts_matches_the_prefix_literally(db_engine):
    """LIKE wildcards in a prefix are not wildcards: `e_e:` does not match `e2e:`."""
    with Session(db_engine) as db:
        kept = make_user(db, google_sub="e2e:dddd4444:owner").id
        gone = make_user(db, google_sub="e_e:1").id
        db.commit()
        with operator_cli():  # what the CLI entry points set (tests/lock_scope.py exemption)
            assert demo_seed.delete_accounts(db, "e_e:") == 1
            db.commit()
        assert db.get(User, kept) is not None and db.get(User, gone) is None


def test_cleanup_handles_rows_others_made_against_runner_stores(db_engine, safe_env, monkeypatch):
    """An outsider's application and grant at an E2E store go with the store; the outsider stays."""
    with Session(db_engine) as db:
        store = make_store(db, make_user(db, "OWNER", google_sub="e2e:eeee5555:owner"))
        outsider = make_worker(db)
        make_application(db, make_job(db, store), outsider)
        make_regular_grant(db, store, outsider)
        db.commit()
        outsider_id = outsider.id
    assert run_main(monkeypatch, "--cleanup") == 0
    with Session(db_engine) as db:
        assert db.get(User, outsider_id) is not None
        assert count(db, JobApplication, JobApplication.worker_id == outsider_id) == 0
        assert count(db, StoreAccessGrant, StoreAccessGrant.worker_id == outsider_id) == 0

"""The lock scope classifier behind the autouse `lock_scope_guard` (tests/lock_scope.py)."""
import pytest

from tests.lock_scope import violation


@pytest.mark.parametrize("statement", [
    "SELECT stores.id FROM stores WHERE stores.id = %(id_1)s FOR UPDATE",
    "SELECT users.id FROM users WHERE users.id = %(id_1)s LOCK IN SHARE MODE",
    "SELECT id FROM users WHERE id = %s FOR UPDATE",  # hand-written SQL, bare column
    # composite unique key, every column given
    ("SELECT * FROM favorite_stores WHERE favorite_stores.worker_id = %(a)s "
     "AND favorite_stores.store_id = %(b)s FOR UPDATE"),
    # generated unique helper
    "SELECT * FROM manual_versions WHERE manual_versions.active_draft_manual_id = %(a)s FOR UPDATE",
    "SELECT * FROM work_requests WHERE work_requests.status = %(s)s LIMIT %(n)s FOR UPDATE SKIP LOCKED",
    ("SELECT * FROM manual_photo_attachments WHERE manual_photo_attachments.media_id = %(m)s "
     "LIMIT %(n)s LOCK IN SHARE MODE"),  # reviewed exception
    "SELECT * FROM work_requests WHERE work_requests.status = %(s)s",  # not a locking read
    # writes by key (ORM flushes and app.db.keyed), with re-check conditions
    "UPDATE stores SET name=%(name)s WHERE stores.id = %(id_1)s",
    ("UPDATE invitation_mail_outbox SET claim_token=%(t)s WHERE invitation_mail_outbox.id = %(id_1)s "
     "AND invitation_mail_outbox.status = %(s)s"),
    "DELETE FROM idempotency_records WHERE idempotency_records.id = %(id_1)s",
    "DELETE FROM favorite_stores WHERE favorite_stores.worker_id = %(a)s AND favorite_stores.store_id = %(b)s",
    "INSERT INTO stores (id) VALUES (%(id)s)",  # not a lock-scope statement
    # hand-written SQL: backtick-quoted identifiers, lower-case keywords, qmark placeholders
    "UPDATE `stores` SET `name` = ? WHERE `id` = ?",
    "update stores set name = %s where stores.id = %s",
    "delete from `idempotency_records` where `idempotency_records`.`id` = %s",
    "select * from stores where stores.id = %s for update",
    "SELECT * FROM work_requests WHERE work_requests.status = %(s)s LIMIT 5 for update skip locked",
])
def test_key_lookups_and_reviewed_exceptions_pass(statement):
    assert violation(statement) is None


@pytest.mark.parametrize(("statement", "problem"), [
    (("SELECT * FROM work_requests JOIN job_applications ON job_applications.id = work_requests.application_id "
      "WHERE job_applications.job_id = %(j)s AND work_requests.status = %(s)s FOR UPDATE"), "JOIN"),
    ("SELECT * FROM job_applications WHERE job_applications.job_id = %(j)s FOR UPDATE", "non-unique"),
    # part of a composite unique key is a range
    ("SELECT * FROM favorite_stores WHERE favorite_stores.worker_id = %(a)s FOR UPDATE", "non-unique"),
    ("SELECT * FROM interview_intent_reviews WHERE interview_intent_reviews.session_id = %(s)s FOR UPDATE",
     "non-unique"),
    (("SELECT * FROM manual_versions WHERE manual_versions.manual_id = %(m)s "
      "AND manual_versions.status = %(s)s FOR UPDATE"), "non-unique"),
    ("SELECT * FROM stores WHERE stores.owner_id = %(o)s LOCK IN SHARE MODE", "non-unique"),
    ("SELECT * FROM job_applications WHERE job_applications.job_id IN (%(a)s, %(b)s) FOR UPDATE", "non-unique"),
    ("SELECT * FROM stores FOR UPDATE", "without WHERE"),
    # B05: a primary key IN list was planned as a clustered index scan (190 row locks, 1213)
    ("SELECT * FROM qa_media WHERE qa_media.id IN (%(id_1_1)s, %(id_1_2)s) ORDER BY qa_media.id FOR UPDATE",
     "primary key IN list"),
    ("DELETE FROM idempotency_records WHERE idempotency_records.id IN (%(id_1_1)s, %(id_1_2)s)",
     "primary key IN list"),
    (("UPDATE store_access_grants SET revoked_at=%(r)s WHERE store_access_grants.id IN (%(a)s) "
      "AND store_access_grants.revoked_at IS NULL"), "primary key IN list"),
    (("UPDATE auth_sessions SET revoked_at=%(r)s WHERE auth_sessions.user_id = %(u)s "
      "AND auth_sessions.revoked_at IS NULL"), "non-unique"),
    ("DELETE FROM manual_sections WHERE manual_sections.version_id = %(v)s", "non-unique"),
    ("DELETE FROM notifications", "without WHERE"),
    # several tables in one write lock rows of each
    ("UPDATE stores, users SET stores.name = %(n)s WHERE stores.id = %(s)s AND users.id = stores.owner_id",
     "multi-table"),
    (("UPDATE stores JOIN users ON users.id = stores.owner_id SET stores.name = %(n)s "
      "WHERE stores.id = %(s)s"), "multi-table"),
    (("DELETE stores FROM stores JOIN users ON users.id = stores.owner_id "
      "WHERE users.id = %(u)s"), "multi-table"),
    ("DELETE FROM stores USING stores, users WHERE users.id = stores.owner_id", "multi-table"),
    (("DELETE FROM interview_turn_photos WHERE interview_turn_photos.turn_id IN "
      "(SELECT interview_turns.id FROM interview_turns WHERE interview_turns.session_id = %(s)s)"),
     "subquery in a write"),
    ("UPDATE stores SET name = (SELECT users.name FROM users WHERE users.id = %(u)s) WHERE stores.id = %(s)s",
     "subquery in a write"),
    # the same shapes in lower case or with backticks are still caught
    ("update stores, users set stores.name = %s where stores.id = %s", "multi-table"),
    ("UPDATE `stores` JOIN `users` ON `users`.`id` = `stores`.`owner_id` SET `stores`.`name` = ?", "multi-table"),
    ("delete from idempotency_records where idempotency_records.id in (%s, %s)", "primary key IN list"),
    ("select * from job_applications where job_applications.job_id = %s for update", "non-unique"),
    ("UPDATE `auth_sessions` SET `revoked_at` = ? WHERE `user_id` = ?", "non-unique"),
])

def test_ranges_and_joins_are_violations(statement, problem):
    assert problem in violation(statement)


def test_operator_exemption_needs_the_cli_entry_point(db_engine):
    """Calling a demo helper from ordinary application code is checked; the same call inside
    `operator_cli()` (what `demo_seed.run` and the E2E cleanup set) is exempt."""
    if db_engine.dialect.name != "mysql":
        pytest.skip("the guard records MySQL statements")
    from sqlalchemy.orm import Session

    from app import demo_seed
    from app.operator_cli import operator_cli
    from tests import lock_scope
    from tests.factories import make_user

    with Session(db_engine) as db:
        make_user(db, "WORKER", google_sub=f"{demo_seed.SUB_PREFIX}scope-check")
        db.commit()
    with Session(db_engine) as db:
        demo_seed.delete_accounts(db, demo_seed.SUB_PREFIX)
        db.rollback()
    with lock_scope._record_lock:
        found, lock_scope._recorded[:] = list(lock_scope._recorded), []
    assert found, "a demo helper called outside operator_cli() must be checked"
    with operator_cli(), Session(db_engine) as db:
        demo_seed.delete_accounts(db, demo_seed.SUB_PREFIX)
        db.commit()
    with lock_scope._record_lock:
        assert lock_scope._recorded == []

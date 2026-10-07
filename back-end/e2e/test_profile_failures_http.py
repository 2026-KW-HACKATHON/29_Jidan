"""Conditional real MySQL child-write errors restore previous rows before retry."""
import uuid

import pytest
from sqlalchemy.orm import Session

from e2e.conftest import worker_snapshot
from e2e.test_profile_http import PATH, profile_headers
from e2e.test_profile_transactions_http import (
    AVAIL_B,
    CAREERS_A,
    CAREERS_B,
    assert_persisted_profile,
)


@pytest.mark.parametrize("suffix,body,table,condition", [
    ("/careers", CAREERS_B, "worker_careers", "NEW.worker_id = %s AND NEW.sort_order = 1"),
    ("/availabilities", AVAIL_B, "availability_days",
     "EXISTS (SELECT 1 FROM availability_rules WHERE id = NEW.rule_id AND worker_id = %s AND sort_order = 1)"),
])
def test_profile_partial_insert_error_restores_previous_rows_and_retries(member, real_db, suffix, body, table, condition):
    case, user_id = member
    assert case.client.put(PATH + "/careers", json=CAREERS_A, headers=profile_headers(case)).status_code == 200
    prior = case.client.get(PATH).json()
    with Session(real_db) as db:
        saved = worker_snapshot(db, user_id)
    trigger = f"e2e_profile_failure_{uuid.uuid4().hex}"
    with real_db.begin() as connection:
        connection.exec_driver_sql(f"""CREATE TRIGGER `{trigger}` BEFORE INSERT ON `{table}`
            FOR EACH ROW BEGIN
              IF {condition} THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'E2E injected profile write failure';
              END IF;
            END""", (user_id,))
    try:
        failed = case.client.put(PATH + suffix, json=body, headers=profile_headers(case))
        assert failed.status_code == 500 and failed.json()["code"] == "INTERNAL_ERROR"
        assert "set-cookie" not in failed.headers and case.client.get(PATH).json() == prior
        with Session(real_db) as db:
            assert worker_snapshot(db, user_id) == saved
    finally:
        with real_db.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS `{trigger}`")
    retry = case.client.put(PATH + suffix, json=body, headers=profile_headers(case))
    assert retry.status_code == 200 and {key: retry.json()[key] for key in body} == body
    assert case.client.get(PATH).json() == retry.json()
    assert {key: value for key, value in retry.json().items() if key not in {*body, "updatedAt"}} == {
        key: value for key, value in prior.items() if key not in {*body, "updatedAt"}}
    with Session(real_db) as db:
        assert_persisted_profile(db, user_id, retry.json())

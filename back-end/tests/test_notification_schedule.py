"""The notification sweep registry and the WORK_REMINDER sweep, with an injected clock."""
import asyncio
import logging
import threading
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import lifespan as app_lifespan
from app import notification_sweeps, work_reminders
from app.db.models import Notification
from app.oauth_cleanup import oauth_cleanup_lifespan
from app.work_reminders import due_work_date, reminder_hour, sweep_work_reminders
from tests.factories import (
    make_application,
    make_job,
    make_request,
    make_shift,
    make_store,
    make_worker,
)

KST_18 = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)  # 2026-10-09 18:00 Asia/Seoul
TOMORROW = date(2026, 10, 10)


def shift_on(session, work_date, *, withdrawn=False, store=None, **job_overrides):
    job = make_job(session, store or make_store(session), work_date=work_date, **job_overrides)
    worker = make_worker(session)
    application = make_application(session, job, worker, status="CONFIRMED")
    request = make_request(session, application, job.created_by_owner_id, status="ACCEPTED",
                           responded_at=KST_18 - timedelta(days=2), ended_at=KST_18 - timedelta(days=2))
    shift = make_shift(session, job, request, worker.id)
    if withdrawn:
        shift.withdrawn_at = shift.confirmed_at + timedelta(hours=1)
    return shift


def seed(db_engine, build):
    with Session(db_engine, expire_on_commit=False) as session:
        result = build(session)
        session.commit()
        return result


def reminders(db_engine) -> list[Notification]:
    with Session(db_engine) as session:
        return list(session.scalars(select(Notification).where(Notification.event_type == "WORK_REMINDER")))


# --- policy ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "hour"), [(None, 18), ("", 18), (" ", 18), ("0", 0), ("23", 23), ("09", 9)])
def test_reminder_hour_setting(monkeypatch, raw, hour):
    if raw is None:
        monkeypatch.delenv("WORK_REMINDER_HOUR", raising=False)
    else:
        monkeypatch.setenv("WORK_REMINDER_HOUR", raw)
    assert reminder_hour() == hour


@pytest.mark.parametrize("raw", ["24", "-1", "18:00", "abc", "１８", "1.5"])
def test_malformed_reminder_hour_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("WORK_REMINDER_HOUR", raw)
    with pytest.raises(work_reminders.ReminderConfigurationError):
        reminder_hour()


def test_due_work_date_follows_seoul_time():
    assert due_work_date(KST_18 - timedelta(microseconds=1), 18) is None
    assert due_work_date(KST_18, 18) == TOMORROW
    assert due_work_date(KST_18 + timedelta(hours=5, minutes=59), 18) == TOMORROW  # 23:59 KST
    assert due_work_date(KST_18 + timedelta(hours=6), 18) is None  # 00:00 KST on the work day
    # 00:30 KST is still the previous UTC day; Seoul decides
    assert due_work_date(datetime(2026, 10, 9, 15, 30, tzinfo=UTC), 0) == date(2026, 10, 11)
    with pytest.raises(ValueError):
        due_work_date(datetime(2026, 10, 9, 9, 0), 18)  # noqa: DTZ001


# --- WORK_REMINDER sweep -----------------------------------------------------------------------


def test_reminds_each_active_shift_for_tomorrow_exactly_once(db_engine):
    def build(session):
        return {
            "due": [shift_on(session, TOMORROW), shift_on(session, TOMORROW)],
            "withdrawn": shift_on(session, TOMORROW, withdrawn=True),
            "today": shift_on(session, TOMORROW - timedelta(days=1)),
            "past": shift_on(session, TOMORROW - timedelta(days=5)),
            "later": shift_on(session, TOMORROW + timedelta(days=1)),
        }
    shifts = seed(db_engine, build)
    assert sweep_work_reminders(KST_18 - timedelta(minutes=1)) == 0  # 17:59 KST: not yet
    assert sweep_work_reminders(KST_18) == 2
    assert sweep_work_reminders(KST_18 + timedelta(minutes=1)) == 0  # rerun: nothing new
    assert sweep_work_reminders(KST_18 + timedelta(hours=5)) == 0
    rows = reminders(db_engine)
    assert sorted(r.target_id for r in rows) == sorted(s.id for s in shifts["due"])
    for row in rows:
        shift = next(s for s in shifts["due"] if s.id == row.target_id)
        assert row.recipient_user_id == shift.worker_id
        assert row.created_at == KST_18 and row.dedupe_key == f"WORK_REMINDER:{shift.id}"
        assert row.target_context["workDate"] == "2026-10-10"
        assert row.target_context["eventId"] == shift.id


def test_reminder_text_and_target(db_engine):
    def build(session):
        store = make_store(session, name="컴포즈커피 광운대점")
        shift = shift_on(session, TOMORROW, store=store, start_time=time(22, 0), end_time=time(6, 0),
                         ends_next_day=True)
        return shift, store
    shift, store = seed(db_engine, build)
    sweep_work_reminders(KST_18)
    [row] = reminders(db_engine)
    assert row.title == "내일 근무가 있어요"
    assert row.body == "컴포즈커피 광운대점 10월 10일 22:00–06:00(익일)"
    assert row.recipient_user_id == shift.worker_id
    assert row.target_context == {
        "type": "WORK_SCHEDULE", "eventId": shift.id, "storeId": store.id, "workDate": "2026-10-10",
    }


def test_store_name_that_looks_sensitive_falls_back_to_a_neutral_body(db_engine):
    seed(db_engine, lambda s: shift_on(s, TOMORROW, store=make_store(s, name="매장 01012345678")))
    assert sweep_work_reminders(KST_18) == 1
    [row] = reminders(db_engine)
    assert row.body == "10월 10일 18:00–22:00 근무가 있어요"


def test_shift_confirmed_later_that_evening_is_reminded_at_the_next_sweep(db_engine):
    seed(db_engine, lambda s: shift_on(s, TOMORROW))
    assert sweep_work_reminders(KST_18) == 1
    seed(db_engine, lambda s: shift_on(s, TOMORROW))
    assert sweep_work_reminders(KST_18 + timedelta(hours=3)) == 1
    assert len(reminders(db_engine)) == 2


def test_withdrawal_before_the_sweep_prevents_the_reminder(db_engine):
    shift = seed(db_engine, lambda s: shift_on(s, TOMORROW))

    def withdraw(session):
        session.get(type(shift), shift.id).withdrawn_at = KST_18 - timedelta(hours=1)
    seed(db_engine, withdraw)
    assert sweep_work_reminders(KST_18) == 0 and reminders(db_engine) == []


def test_configured_hour_moves_the_window(db_engine, monkeypatch):
    seed(db_engine, lambda s: shift_on(s, TOMORROW))
    monkeypatch.setenv("WORK_REMINDER_HOUR", "20")
    assert sweep_work_reminders(KST_18 + timedelta(hours=1)) == 0  # 19:00 KST
    assert sweep_work_reminders(KST_18 + timedelta(hours=2)) == 1  # 20:00 KST


def test_sweep_is_bounded_and_resumes(db_engine, monkeypatch):
    monkeypatch.setattr(work_reminders, "BATCH_SIZE", 2)
    monkeypatch.setattr(work_reminders, "MAX_BATCHES", 1)
    seed(db_engine, lambda s: [shift_on(s, TOMORROW) for _ in range(3)])
    assert sweep_work_reminders(KST_18) == 2
    assert sweep_work_reminders(KST_18) == 1
    assert sweep_work_reminders(KST_18) == 0
    assert len(reminders(db_engine)) == 3


def test_reminders_are_listed_to_the_worker(api, db_engine, monkeypatch):
    from app.db import utcnow
    from tests.api_contract import login

    # The real clock this time: the list hides rows stamped after its asOf.
    monkeypatch.setenv("WORK_REMINDER_HOUR", "0")
    now = utcnow()
    shift = seed(db_engine, lambda s: shift_on(s, due_work_date(now, 0)))
    assert sweep_work_reminders(now) == 1
    login(api, shift.worker_id)
    [item] = api.get("/api/users/me/notifications").json()["items"]
    assert item["type"] == "WORK_REMINDER" and item["target"]["eventId"] == shift.id


@pytest.mark.mysql
def test_concurrent_sweeps_remind_each_shift_once(db_engine, monkeypatch):
    monkeypatch.setattr(work_reminders, "BATCH_SIZE", 3)
    shifts = seed(db_engine, lambda s: [shift_on(s, TOMORROW) for _ in range(7)])
    barrier = threading.Barrier(4)
    errors = []

    def run():
        try:
            barrier.wait()
            sweep_work_reminders(KST_18)
        except Exception as error:  # noqa: BLE001 - surfaced below
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    rows = reminders(db_engine)
    assert sorted(r.target_id for r in rows) == sorted(s.id for s in shifts)


# --- sweep registry ----------------------------------------------------------------------------


def test_registry_runs_every_sweep_at_one_now_and_isolates_failures(monkeypatch, caplog):
    seen = []

    def ok(now):
        seen.append(now)
        return 3

    def broken(now):
        raise RuntimeError("SELECT secret FROM users")

    monkeypatch.setattr(notification_sweeps, "SWEEPS", [("first", broken), ("second", ok)])
    with caplog.at_level(logging.ERROR, logger="jidan.errors"):
        assert notification_sweeps.run_notification_sweeps(KST_18) == {"first": None, "second": 3}
    assert seen == [KST_18]
    assert "first" in caplog.text and "secret" not in caplog.text


def test_registry_rejects_a_naive_clock_and_defaults_to_now(monkeypatch):
    with pytest.raises(ValueError):
        notification_sweeps.run_notification_sweeps(datetime(2026, 10, 9))  # noqa: DTZ001
    seen = []
    monkeypatch.setattr(notification_sweeps, "SWEEPS", [("x", lambda now: seen.append(now) or 0)])
    notification_sweeps.run_notification_sweeps()
    assert seen[0].tzinfo is not None


def test_work_reminder_is_registered():
    assert ("work-reminder", sweep_work_reminders) in notification_sweeps.SWEEPS


# --- lifespan registration --------------------------------------------------------------


def test_default_lifespans_keep_oauth_cleanup_and_add_the_sweeps():
    # Other domains append their own lifespans (e.g. the AI/STT task runner) after these two.
    assert app_lifespan.LIFESPANS[:2] == [oauth_cleanup_lifespan, app_lifespan.periodic_jobs_lifespan]
    assert [job.name for job in app_lifespan.PERIODIC_JOBS][:1] == ["notification-sweeps"]
    assert app_lifespan.PERIODIC_JOBS[0].run is notification_sweeps.run_notification_sweeps


def test_bad_reminder_setting_stops_startup(monkeypatch):
    monkeypatch.setenv("WORK_REMINDER_HOUR", "25")
    monkeypatch.setattr(app_lifespan, "LIFESPANS", [])

    async def run():
        async with app_lifespan.lifespan(None):
            pass

    with pytest.raises(work_reminders.ReminderConfigurationError):
        asyncio.run(run())

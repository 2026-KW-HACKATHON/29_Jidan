import logging
import threading
import time
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import invitation_mail, lifespan
from app.auth import ConfigurationError
from app.db import utcnow
from app.db.models import InvitationMailOutbox, Store, StoreInvitation
from tests.invitation_helpers import configure_mail, make_world, seed_invitation

TOKEN = "t" * 43


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, delta):
        self.now += delta


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(invitation_mail, "utcnow", value)
    return value


class Sender:
    """Fails `failures` times, then records; `during` runs inside send (to race other actions)."""

    def __init__(self, failures=0, during=None, delay=0.0):
        self.failures = failures
        self.during = during
        self.delay = delay
        self.sent = []
        self.lock = threading.Lock()

    def send(self, message):
        if self.during:
            self.during()
        time.sleep(self.delay)
        with self.lock:
            if self.failures:
                self.failures -= 1
                raise ConnectionError("provider down for someone@example.com")
            self.sent.append(message)


def _queue(db_engine, *, email="jisu@example.com", access_expires_at=None):
    world = make_world(db_engine, worker_email=email)
    invitation_id, _ = seed_invitation(db_engine, world.store_id, email=email, access_expires_at=access_expires_at)
    with Session(db_engine) as db:
        invitation = db.get(StoreInvitation, invitation_id)
        row = invitation_mail.enqueue_invitation_mail(db, invitation, db.get(Store, world.store_id).name, TOKEN)
        db.commit()
        return row.id, invitation_id


def _row(db_engine, row_id):
    with Session(db_engine) as db:
        return db.get(InvitationMailOutbox, row_id)


# --- retries and backoff ----------------------------------------------------------------------

def test_failures_back_off_then_send(db_engine, clock, caplog):
    row_id, _ = _queue(db_engine)
    sender = Sender(failures=2)
    with caplog.at_level(logging.DEBUG):
        assert invitation_mail.deliver_queued_mail(sender=sender) == 0
        row = _row(db_engine, row_id)
        assert (row.status, row.attempts, row.claim_token) == ("QUEUED", 1, None)
        assert row.next_attempt_at == clock.now + invitation_mail.RETRY_DELAYS[0]
        # Not due yet: nothing is attempted.
        clock.advance(invitation_mail.RETRY_DELAYS[0] - timedelta(microseconds=1))
        assert invitation_mail.deliver_queued_mail(sender=sender) == 0 and sender.failures == 1
        clock.advance(timedelta(microseconds=1))
        assert invitation_mail.deliver_queued_mail(sender=sender) == 0
        assert _row(db_engine, row_id).attempts == 2
        clock.advance(invitation_mail.RETRY_DELAYS[1])
        assert invitation_mail.deliver_queued_mail(sender=sender) == 1
    row = _row(db_engine, row_id)
    assert (row.status, row.payload, row.attempts, row.claim_token, row.claimed_until) == ("SENT", None, 2, None, None)
    [message] = sender.sent
    assert message.link.endswith("#token=" + TOKEN) and TOKEN not in repr(message)
    for secret in (TOKEN, "example.com"):
        assert secret not in caplog.text


def test_gives_up_after_max_attempts(db_engine, clock):
    row_id, _ = _queue(db_engine)
    sender = Sender(failures=invitation_mail.MAX_ATTEMPTS)
    for attempt in range(invitation_mail.MAX_ATTEMPTS):
        invitation_mail.deliver_queued_mail(sender=sender)
        if attempt < len(invitation_mail.RETRY_DELAYS):
            clock.advance(invitation_mail.RETRY_DELAYS[attempt])
    row = _row(db_engine, row_id)
    assert (row.status, row.payload, row.attempts) == ("FAILED", None, invitation_mail.MAX_ATTEMPTS)
    clock.advance(timedelta(days=1))
    assert invitation_mail.deliver_queued_mail(sender=Sender()) == 0


def test_undecryptable_payload_fails_without_sending(db_engine, clock, monkeypatch):
    row_id, _ = _queue(db_engine)
    monkeypatch.setenv(invitation_mail.KEY_ENV, Fernet.generate_key().decode())  # key rotated
    sender = Sender()
    assert invitation_mail.deliver_queued_mail(sender=sender) == 0
    assert sender.sent == [] and _row(db_engine, row_id).status == "FAILED"
    assert _row(db_engine, row_id).payload is None


# --- invitations that changed -----------------------------------------------------------------

@pytest.mark.parametrize("change", ["canceled_at", "declined", "link_expired", "access_ended"])
def test_mail_for_an_invitation_no_longer_pending_is_discarded(db_engine, clock, change):
    row_id, invitation_id = _queue(db_engine)
    with Session(db_engine) as db:
        invitation = db.get(StoreInvitation, invitation_id)
        if change == "canceled_at":
            invitation.canceled_at = clock.now
        elif change == "declined":
            # The actor CHECK only needs a user id; this test is about the outbox.
            invitation.declined_at = clock.now
            invitation.declined_by_worker_id = invitation.inviter_owner_id
        elif change == "access_ended":
            invitation.access_expires_at = clock.now
        db.commit()
        expires_at = invitation.expires_at
    if change == "link_expired":
        clock.now = expires_at
    sender = Sender()
    assert invitation_mail.deliver_queued_mail(sender=sender) == 0
    row = _row(db_engine, row_id)
    assert (row.status, row.payload, sender.sent) == ("DISCARDED", None, [])


def test_resend_during_delivery_keeps_the_discard(db_engine, clock):
    """A resend that discards the row while its mail is being sent wins; the row is not SENT."""
    row_id, invitation_id = _queue(db_engine)

    def resend_now():
        with Session(db_engine) as db:
            invitation_mail.discard_queued_mail(db, invitation_id, now=clock.now)
            db.commit()

    sender = Sender(during=resend_now)
    assert invitation_mail.deliver_queued_mail(sender=sender) == 0
    row = _row(db_engine, row_id)
    assert (row.status, row.payload, row.claim_token) == ("DISCARDED", None, None)


# --- claims: no double delivery ---------------------------------------------------------------

def test_claimed_rows_are_skipped_until_the_lease_ends(db_engine, clock):
    row_id, _ = _queue(db_engine)
    with Session(db_engine) as db:
        row = db.get(InvitationMailOutbox, row_id)
        row.claim_token, row.claimed_until = "x" * 36, clock.now + invitation_mail.CLAIM_LEASE
        db.commit()
    sender = Sender()
    assert invitation_mail.deliver_queued_mail(sender=sender) == 0 and sender.sent == []
    clock.advance(invitation_mail.CLAIM_LEASE)  # the claimant died: the lease runs out
    assert invitation_mail.deliver_queued_mail(sender=sender) == 1
    assert _row(db_engine, row_id).status == "SENT"


def test_concurrent_runs_send_each_mail_once(db_engine):
    rows = [_queue(db_engine, email=f"w{i}@example.com")[0] for i in range(6)]
    sender = Sender(delay=0.05)
    barrier = threading.Barrier(4)
    totals = []

    def run():
        barrier.wait(5)
        totals.append(invitation_mail.deliver_queued_mail(sender=sender))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sum(totals) == 6 and len(sender.sent) == 6
    assert sorted(m.to for m in sender.sent) == sorted(f"w{i}@example.com" for i in range(6))
    assert {_row(db_engine, row_id).status for row_id in rows} == {"SENT"}


def test_periodic_job_drains_in_batches(db_engine, monkeypatch):
    monkeypatch.setattr(invitation_mail, "BATCH_SIZE", 2)
    rows = [_queue(db_engine, email=f"p{i}@example.com")[0] for i in range(5)]
    invitation_mail.run_invitation_mail_delivery()
    assert {_row(db_engine, row_id).status for row_id in rows} == {"SENT"}
    assert len(invitation_mail.memory_sender.messages) == 5


def test_job_is_registered_and_settings_are_validated_at_startup():
    names = [job.name for job in lifespan.PERIODIC_JOBS]
    assert "invitation-mail" in names
    job = next(job for job in lifespan.PERIODIC_JOBS if job.name == "invitation-mail")
    assert job.run is invitation_mail.run_invitation_mail_delivery
    assert job.interval_seconds == invitation_mail.INTERVAL_SECONDS


# --- settings ---------------------------------------------------------------------------------

@pytest.mark.parametrize("env,backend", [
    ({"APP_ENV": "local"}, "memory"),
    ({"APP_ENV": "dev"}, "disabled"),
    ({"APP_ENV": "production"}, "disabled"),
    ({"APP_ENV": "dev", "INVITATION_MAIL_BACKEND": "memory"}, "memory"),
    ({"APP_ENV": "production", "INVITATION_MAIL_BACKEND": " Disabled "}, "disabled"),
])
def test_backend_selection(monkeypatch, env, backend):
    monkeypatch.delenv("INVITATION_MAIL_BACKEND", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert invitation_mail._backend() == backend
    invitation_mail.validate_mail_settings()


@pytest.mark.parametrize("env", [
    {"INVITATION_MAIL_BACKEND": "sendgrid"},
    {"INVITATION_MAIL_BACKEND": "memory", "APP_ENV": "production"},
    {"INVITATION_MAIL_BACKEND": "smtp"},  # no SMTP_HOST
    {"INVITATION_MAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.test", "MAIL_FROM": "a@jidan.test",
     "INVITATION_MAIL_KEY": "bad"},
    {"INVITATION_MAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.test", "MAIL_FROM": "a@jidan.test",
     "FRONTEND_ORIGIN": "not-an-origin"},
])
def test_invalid_settings_stop_startup(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigurationError) as caught:
        invitation_mail.validate_mail_settings()
    for value in env.values():
        if value not in ("smtp", "memory", "production"):
            assert value not in str(caught.value)


def test_invalid_backend_at_runtime_sends_nothing(db_engine, monkeypatch, caplog):
    row_id, _ = _queue(db_engine)
    monkeypatch.setenv(invitation_mail.BACKEND_ENV, "typo")
    with caplog.at_level(logging.ERROR):
        assert invitation_mail.deliver_queued_mail() == 0
    assert _row(db_engine, row_id).status == "QUEUED"
    assert "typo" not in caplog.text


def test_disabled_backend_leaves_rows_queued(db_engine, monkeypatch, caplog):
    monkeypatch.setenv(invitation_mail.BACKEND_ENV, "disabled")
    row_id, _ = _queue(db_engine)
    assert invitation_mail.configured_sender() is None
    assert invitation_mail.deliver_queued_mail() == 0
    assert _row(db_engine, row_id).status == "QUEUED"
    with caplog.at_level(logging.WARNING):
        invitation_mail.validate_mail_settings()
    assert "disabled" in caplog.text


@pytest.mark.parametrize("origin", ["", "not a url", "https://app.test/path", "javascript:alert(1)"])
def test_bad_frontend_origin_is_unavailable(monkeypatch, origin):
    monkeypatch.setenv(invitation_mail.FRONTEND_ORIGIN_ENV, origin)
    with pytest.raises(invitation_mail.DeliveryUnavailable):
        invitation_mail.invitation_link(TOKEN)


def test_bad_key_is_unavailable(db_engine, monkeypatch):
    monkeypatch.setenv(invitation_mail.KEY_ENV, "short")
    with pytest.raises(invitation_mail.DeliveryUnavailable):
        _queue(db_engine)
    with Session(db_engine) as db:
        assert db.scalars(select(InvitationMailOutbox)).all() == []


def test_links_use_the_fragment(monkeypatch):
    monkeypatch.setenv(invitation_mail.FRONTEND_ORIGIN_ENV, "https://app.test/")
    assert invitation_mail.invitation_link("abc") == "https://app.test/invitations/accept#token=abc"


# --- rendering --------------------------------------------------------------------------------

def test_render_korean_text_and_html_with_seoul_times():
    message = invitation_mail.render({
        "to": "jisu@example.com", "storeName": "명랑<핫도그> & 카페", "link": "https://app.test/invitations/accept#token=abc",
        "expiresAt": "2026-10-12T01:00:00+00:00", "accessExpiresAt": "2026-10-31T15:00:00+00:00",
    })
    assert message.subject == "[Jidan] 명랑<핫도그> & 카페 근무자 초대"
    assert "2026년 10월 12일 10:00 (한국 시간) 전까지" in message.body
    assert "2026년 11월 1일 00:00 (한국 시간) 전까지" in message.body
    assert "https://app.test/invitations/accept#token=abc" in message.body
    assert "명랑&lt;핫도그&gt; &amp; 카페" in message.html and "<핫도그>" not in message.html
    assert 'href="https://app.test/invitations/accept#token=abc"' in message.html
    unlimited = invitation_mail.render({
        "to": "a@b.co", "storeName": "매장", "link": "https://x.test/#token=a",
        "expiresAt": "2026-10-12T01:00:00+00:00", "accessExpiresAt": None,
    })
    assert "점주가 종료할 때까지" in unlimited.body and "점주가 종료할 때까지" in unlimited.html


# --- key rotation and missing keys (review 21c53f2, ported to the lease design) -------------

def test_rotated_key_list_still_delivers_mail_queued_under_the_old_key(db_engine, monkeypatch):
    import os

    old_key = os.environ[invitation_mail.KEY_ENV]
    old_row, _ = _queue(db_engine)
    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv(invitation_mail.KEY_ENV, f"{new_key}, {old_key}")
    new_row, _ = _queue(db_engine, email="second@example.com")
    # New mail is encrypted with the first key only.
    Fernet(new_key.encode()).decrypt(_row(db_engine, new_row).payload.encode())
    with pytest.raises(InvalidToken):
        Fernet(old_key.encode()).decrypt(_row(db_engine, new_row).payload.encode())
    sender = Sender()
    assert invitation_mail.deliver_queued_mail(sender=sender) == 2
    assert {_row(db_engine, r).status for r in (old_row, new_row)} == {"SENT"}
    invitation_mail.validate_mail_settings()


def test_dropped_old_key_fails_only_its_mail(db_engine, monkeypatch):
    old_row, _ = _queue(db_engine)
    monkeypatch.setenv(invitation_mail.KEY_ENV, Fernet.generate_key().decode())
    new_row, _ = _queue(db_engine, email="second@example.com")
    assert invitation_mail.deliver_queued_mail(sender=Sender()) == 1
    assert (_row(db_engine, old_row).status, _row(db_engine, new_row).status) == ("FAILED", "SENT")


@pytest.mark.parametrize("broken", ["", "  ", "short", "short,also-short"])
def test_missing_key_at_delivery_keeps_the_queue_untouched(db_engine, clock, monkeypatch, caplog, broken):
    import os

    key = os.environ[invitation_mail.KEY_ENV]
    row_id, _ = _queue(db_engine)
    # A row already waiting for its second attempt keeps its backoff.
    with Session(db_engine) as db:
        row = db.get(InvitationMailOutbox, row_id)
        row.attempts, row.next_attempt_at = 1, clock.now
        db.commit()
    before = _row(db_engine, row_id)
    sender = Sender()
    monkeypatch.setenv(invitation_mail.KEY_ENV, broken)
    with caplog.at_level(logging.ERROR):
        assert invitation_mail.deliver_queued_mail(sender=sender) == 0
        invitation_mail.run_invitation_mail_delivery()
    after = _row(db_engine, row_id)
    assert (after.status, after.attempts, after.next_attempt_at, after.payload) == (
        "QUEUED", 1, before.next_attempt_at, before.payload,
    )
    assert after.claim_token is None and sender.sent == []
    assert key not in caplog.text and (not broken.strip() or broken not in caplog.text)
    monkeypatch.setenv(invitation_mail.KEY_ENV, key)  # configuration restored
    assert invitation_mail.deliver_queued_mail(sender=sender) == 1
    assert _row(db_engine, row_id).status == "SENT"


# --- retention ----------------------------------------------------------------------------------

def test_finished_rows_are_purged_after_retention(db_engine, clock):
    rows = {}
    for status in ("SENT", "FAILED", "DISCARDED", "QUEUED"):
        row_id, _ = _queue(db_engine, email=f"{status.lower()}@example.com")
        rows[status] = row_id
        if status != "QUEUED":
            with Session(db_engine) as db:
                row = db.get(InvitationMailOutbox, row_id)
                row.status, row.payload, row.processed_at = status, None, clock.now
                db.commit()
    # Just inside the retention nothing goes; the queued row is never purged.
    edge = clock.now + invitation_mail.FINISHED_RETENTION - timedelta(microseconds=1)
    assert invitation_mail.purge_finished_mail(now=edge) == 0
    assert invitation_mail.purge_finished_mail(now=clock.now + invitation_mail.FINISHED_RETENTION) == 3
    with Session(db_engine) as db:
        assert [row.id for row in db.scalars(select(InvitationMailOutbox))] == [rows["QUEUED"]]
        assert db.get(StoreInvitation, _row(db_engine, rows["QUEUED"]).invitation_id) is not None


def test_periodic_job_purges_in_batches(db_engine, clock, monkeypatch):
    monkeypatch.setattr(invitation_mail, "PURGE_BATCH_SIZE", 2)
    for i in range(5):
        row_id, _ = _queue(db_engine, email=f"old{i}@example.com")
        with Session(db_engine) as db:
            row = db.get(InvitationMailOutbox, row_id)
            row.status, row.payload = "SENT", None
            row.processed_at = clock.now - invitation_mail.FINISHED_RETENTION
            db.commit()
    invitation_mail.run_invitation_mail_delivery()
    with Session(db_engine) as db:
        assert db.scalars(select(InvitationMailOutbox)).all() == []

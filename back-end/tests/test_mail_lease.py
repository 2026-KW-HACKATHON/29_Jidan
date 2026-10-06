"""Invitation mail claims under slow SMTP: no second sender for a row another run still owns.

Delivery stays at-least-once: a process that dies after the server accepted a message but before
the row is marked SENT leaves it to be sent again after the lease. These tests pin both halves.
"""

import asyncio
import threading
import time
from collections import Counter
from datetime import timedelta

import pytest
from aiosmtpd.controller import Controller
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import invitation_mail
from app.db.models import InvitationMailOutbox, Store, StoreInvitation
from app.mail_smtp import SmtpMailSender, smtp_settings
from tests.invitation_helpers import configure_mail, make_world, seed_invitation
from tests.test_mail_smtp import _free_port, _parse

TOKEN = "t" * 43
BATCH = 20


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    return configure_mail(monkeypatch)


class Clock:
    def __init__(self):
        self.now = invitation_mail.utcnow()
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def advance(self, delta):
        with self.lock:
            self.now += delta


def queue_batch(db_engine, count=BATCH) -> dict[str, str]:
    """`count` queued mails for one store; returns {outbox row id: recipient}."""
    world = make_world(db_engine, worker_email="w0@example.com")
    rows = {}
    for n in range(count):
        email = f"w{n}@example.com"
        invitation_id, _ = seed_invitation(db_engine, world.store_id, email=email)
        with Session(db_engine) as db:
            invitation = db.get(StoreInvitation, invitation_id)
            row = invitation_mail.enqueue_invitation_mail(db, invitation, db.get(Store, world.store_id).name, TOKEN)
            db.commit()
            rows[row.id] = email
    return rows


def statuses(db_engine) -> Counter:
    with Session(db_engine) as db:
        return Counter(db.scalars(select(InvitationMailOutbox.status)))


class Recorder:
    def __init__(self, name, sent, on_send=None):
        self.name = name
        self.sent = sent  # shared [(worker, recipient)]
        self.on_send = on_send

    def send(self, message):
        if self.on_send:
            self.on_send(message)
        self.sent.append((self.name, message.to))


def test_slow_batch_does_not_resend_rows_taken_over_after_the_lease(db_engine, monkeypatch):
    """20 rows claimed together, each SMTP exchange 1/15 of the lease: rows 16-20 outlive it.

    Worker B runs once the lease has run out and takes the remaining rows; worker A must then
    skip them instead of sending them a second time.
    """
    clock = Clock()
    monkeypatch.setattr(invitation_mail, "utcnow", clock)
    rows = queue_batch(db_engine)
    start = clock.now
    sent: list[tuple[str, str]] = []
    b_runs = []
    worker_b = Recorder("B", sent)

    def slow_smtp(_message):
        clock.advance(invitation_mail.CLAIM_LEASE / 15)
        if not b_runs and clock.now - start >= invitation_mail.CLAIM_LEASE:
            b_runs.append(invitation_mail.deliver_queued_mail(sender=worker_b))

    worker_a = Recorder("A", sent, on_send=slow_smtp)
    a_total = invitation_mail.deliver_queued_mail(sender=worker_a)

    per_recipient = Counter(recipient for _, recipient in sent)
    assert len(sent) == BATCH and set(per_recipient.values()) == {1}, sorted(per_recipient.items())
    assert sorted(per_recipient) == sorted(rows.values())
    assert a_total + sum(b_runs) == BATCH and b_runs and b_runs[0] > 0
    assert statuses(db_engine) == Counter({"SENT": BATCH})


def test_renewal_is_needed_even_without_a_competitor(db_engine, monkeypatch):
    """A slow batch with nobody else running still sends every row once and marks it SENT."""
    clock = Clock()
    monkeypatch.setattr(invitation_mail, "utcnow", clock)
    queue_batch(db_engine, count=5)
    sent = []
    sender = Recorder("A", sent, on_send=lambda _m: clock.advance(invitation_mail.CLAIM_LEASE))
    assert invitation_mail.deliver_queued_mail(sender=sender) == 5
    assert len(sent) == 5 and statuses(db_engine) == Counter({"SENT": 5})


def test_row_discarded_while_the_batch_waits_is_not_sent(db_engine, monkeypatch):
    """A resend discards a row of the batch before its turn: it is skipped, not sent."""
    clock = Clock()
    monkeypatch.setattr(invitation_mail, "utcnow", clock)
    rows = queue_batch(db_engine, count=3)
    with Session(db_engine) as db:
        invitations = {db.get(InvitationMailOutbox, row_id).invitation_id: email for row_id, email in rows.items()}
    sent, victims = [], []

    def resend_another(message):
        if not victims:
            invitation_id = next(i for i, email in invitations.items() if email != message.to)
            victims.append(invitations[invitation_id])
            with Session(db_engine) as db:
                invitation_mail.discard_queued_mail(db, invitation_id, now=clock.now)
                db.commit()

    assert invitation_mail.deliver_queued_mail(sender=Recorder("A", sent, on_send=resend_another)) == 2
    assert victims[0] not in [recipient for _, recipient in sent] and len(sent) == 2
    assert statuses(db_engine) == Counter({"SENT": 2, "DISCARDED": 1})


# --- real SMTP: a slow server and two workers -------------------------------------------------

class SlowInbox:
    def __init__(self, delay):
        self.delay = delay
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        await asyncio.sleep(self.delay)
        self.messages.append(envelope.content)
        return "250 OK"


@pytest.fixture
def slow_smtp(monkeypatch):
    controllers = []

    def start(delay):
        inbox = SlowInbox(delay)
        port = _free_port()
        controller = Controller(inbox, hostname="localhost", port=port)
        controller.start()
        controllers.append(controller)
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", str(port))
        monkeypatch.setenv("SMTP_SECURITY", "none")
        monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "5")
        monkeypatch.setenv("MAIL_FROM", "Jidan <no-reply@jidan.test>")
        monkeypatch.setenv(invitation_mail.BACKEND_ENV, "smtp")
        monkeypatch.setenv("APP_ENV", "local")
        monkeypatch.delenv("SMTP_USERNAME", raising=False)
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        return inbox

    yield start
    for controller in controllers:
        controller.stop()


def inbox_ids(inbox) -> tuple[Counter, Counter]:
    """(messages per recipient, messages per Message-ID) of what the server accepted."""
    parsed = [_parse(raw) for raw in inbox.messages]
    return Counter(m["To"] for m in parsed), Counter(m["Message-ID"] for m in parsed)


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_two_workers_with_slow_smtp_deliver_each_mail_once(db_engine, slow_smtp, monkeypatch):
    """20 rows, 0.15 s per accepted message (3 s per batch), a 1 s lease, two workers looping."""
    monkeypatch.setattr(invitation_mail, "CLAIM_LEASE", timedelta(seconds=1))
    inbox = slow_smtp(delay=0.15)
    rows = queue_batch(db_engine)
    barrier = threading.Barrier(2)
    totals: Counter[str] = Counter()

    def worker(name):
        barrier.wait(5)
        deadline = time.monotonic() + 30
        while statuses(db_engine).get("QUEUED") and time.monotonic() < deadline:
            totals[name] += invitation_mail.deliver_queued_mail()
            time.sleep(0.05)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(40)

    per_recipient, per_message_id = inbox_ids(inbox)
    assert len(inbox.messages) == BATCH, sorted(per_recipient.items())
    assert sorted(per_recipient) == sorted(rows.values()) and set(per_recipient.values()) == {1}
    assert len(per_message_id) == BATCH
    assert sum(totals.values()) == BATCH and statuses(db_engine) == Counter({"SENT": BATCH})


class Killed(BaseException):
    """The process dies right after the SMTP server accepted the message."""


@pytest.mark.mysql
@pytest.mark.parametrize("db_engine", ["mysql"], indirect=True)
def test_crash_after_smtp_acceptance_sends_again_with_the_same_message_id(db_engine, slow_smtp, monkeypatch):
    """At-least-once, not exactly-once: the second copy carries the row's stable Message-ID so a
    mail client (or an operator) can tell it is the same logical mail."""
    clock = Clock()
    monkeypatch.setattr(invitation_mail, "utcnow", clock)
    inbox = slow_smtp(delay=0)
    rows = queue_batch(db_engine, count=1)
    real = SmtpMailSender(smtp_settings())

    class DiesAfterAcceptance:
        def send(self, message):
            real.send(message)
            raise Killed

    with pytest.raises(Killed):
        invitation_mail.deliver_queued_mail(sender=DiesAfterAcceptance())
    assert statuses(db_engine) == Counter({"QUEUED": 1})
    assert invitation_mail.deliver_queued_mail(sender=real) == 0  # the dead run's lease holds
    clock.advance(invitation_mail.CLAIM_LEASE)
    assert invitation_mail.deliver_queued_mail(sender=real) == 1

    per_recipient, per_message_id = inbox_ids(inbox)
    assert len(inbox.messages) == 2 and list(per_recipient.values()) == [2]
    assert list(per_message_id.values()) == [2]
    (message_id,) = per_message_id
    assert message_id == f"<invitation-{next(iter(rows))}@jidan.test>"
    assert statuses(db_engine) == Counter({"SENT": 1})

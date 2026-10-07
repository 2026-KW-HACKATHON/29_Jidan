"""Invitation e-mail: an encrypted outbox written with the invitation, delivered afterwards.

`enqueue_invitation_mail(db, invitation, store_name, token)` adds a QUEUED outbox row on the
caller's session, so the invitation (or its new token on resend) and the mail request commit or
roll back together. It raises `DeliveryUnavailable` (-> 503 DELIVERY_UNAVAILABLE) when the
queue cannot be written safely: no encryption key or frontend origin configured.

The link is `FRONTEND_ORIGIN + INVITATION_LINK_PATH + "#token=" + token`; the token sits in the
fragment so it never reaches a server log. The recipient, store name and link exist only in the
Fernet-encrypted payload (keys: `INVITATION_MAIL_KEY`, comma-separated for rotation), which is
cleared when the row is sent,
given up on, or discarded. Nothing here logs a token, link, address, password or mail body.

Delivery (`deliver_queued_mail`) runs right after the response that queued a mail
(BackgroundTask) and every minute from the lifespan (`PERIODIC_JOBS`), in any number of
processes. No database transaction stays open while the mail server is contacted:

1. claim: in one short transaction, discard queued rows whose invitation is no longer
   acceptable, pick due rows (`next_attempt_at` reached, no live claim), lock them by primary
   key with `FOR UPDATE SKIP LOCKED` and stamp them with this run's `claim_token` and a lease
   (plain reads and primary-key updates only; see `_claim`);
2. right before sending each claimed row, renew its lease with an UPDATE conditioned on the
   claim token (`_renew`). A row whose claim another run took over after the batch lease ran
   out (slow SMTP earlier in the batch), or that a resend/cancel discarded, is skipped;
3. send it outside any transaction. `CLAIM_LEASE` is longer than one send can take
   (`SEND_STEPS` x the largest SMTP timeout), so the renewed claim outlives the send;
4. finish with an UPDATE conditioned on the claim token: SENT, or a retry at the next backoff
   step (`RETRY_DELAYS`), or FAILED after `MAX_ATTEMPTS`. The payload is cleared whenever a row
   leaves QUEUED.

So two live runs never send the same row. Delivery is still at-least-once, not exactly-once:
a process that dies after the server accepted a message but before step 4 leaves the row to be
sent again once its lease ends, and nothing (not the token check after sending) can undo the
first copy. Both copies carry the same Message-ID (`<invitation-{row id}@domain>`). A resend or
cancel discards queued rows (and their claims); a mail already in flight carries a link that
no longer works because the token hash was replaced or the invitation is no longer pending.

Senders (`INVITATION_MAIL_BACKEND`): `smtp` (app.mail_smtp), `memory` (process memory; the
default for `APP_ENV=local`, used by tests, refused in production) and `disabled` (queue only).
`validate_mail_settings()` runs at startup and stops the app on a malformed setting.
"""

import html
import json
import logging
import os
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.auth import ConfigurationError
from app.csrf import normalize_origin
from app.db import iso_utc, new_uuid, session_scope, utcnow
from app.db.keyed import delete_by_key, update_by_key
from app.db.models import InvitationMailOutbox, StoreInvitation
from app.mail_smtp import MAX_TIMEOUT_SECONDS, SEND_STEPS, SmtpMailSender, smtp_settings
from app.store_access import pending_invitation_clause

logger = logging.getLogger("jidan.mail")

KEY_ENV = "INVITATION_MAIL_KEY"
BACKEND_ENV = "INVITATION_MAIL_BACKEND"
FRONTEND_ORIGIN_ENV = "FRONTEND_ORIGIN"
INVITATION_LINK_PATH = "/invitations/accept"
BACKENDS = ("smtp", "memory", "disabled")
MAX_ATTEMPTS = 5
# Wait after failed attempt 1, 2, 3, 4; attempt 5 failing ends in FAILED.
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))
# Longer than one SMTP exchange at the largest allowed timeout (connect + TLS + auth + send, each
# step bounded by the timeout), plus a margin. Renewed right before each send.
CLAIM_LEASE = timedelta(minutes=15)
assert CLAIM_LEASE.total_seconds() >= SEND_STEPS * MAX_TIMEOUT_SECONDS + 60
BATCH_SIZE = 20
MAX_BATCHES_PER_RUN = 5
INTERVAL_SECONDS = 60
# Finished rows (SENT/FAILED/DISCARDED) hold no secrets (payload already cleared) and are kept
# this long for delivery troubleshooting, then deleted. The design fixes no period; proposal.
FINISHED_RETENTION = timedelta(days=30)
PURGE_BATCH_SIZE = 500
SEOUL = ZoneInfo("Asia/Seoul")

QUEUED = "QUEUED"
SENT = "SENT"
FAILED = "FAILED"
DISCARDED = "DISCARDED"


class DeliveryUnavailable(RuntimeError):
    """The mail request cannot be queued; the caller answers 503 and rolls everything back."""


@dataclass(frozen=True)
class MailMessage:
    to: str = field(repr=False)
    subject: str
    body: str = field(repr=False)
    html: str = field(repr=False)
    link: str = field(repr=False)
    message_id: str | None = None  # stable per logical mail (outbox row); None: a fresh one


class MailSender(Protocol):
    def send(self, message: MailMessage) -> None: ...


class MemoryMailSender:
    """Keeps messages in memory for local development and tests. Never logs them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: list[MailMessage] = []

    def send(self, message: MailMessage) -> None:
        with self._lock:
            self._messages.append(message)

    @property
    def messages(self) -> list[MailMessage]:
        with self._lock:
            return list(self._messages)

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()


memory_sender = MemoryMailSender()


# --- configuration ----------------------------------------------------------------------------

def _backend() -> str:
    backend = os.getenv(BACKEND_ENV, "").strip().lower()
    environment = os.getenv("APP_ENV", "local").strip() or "local"
    if not backend:
        return "memory" if environment == "local" else "disabled"
    if backend not in BACKENDS:
        raise ConfigurationError(f"{BACKEND_ENV} must be one of {', '.join(BACKENDS)}")
    if backend == "memory" and environment not in ("local", "dev"):
        raise ConfigurationError(f"{BACKEND_ENV}=memory would drop real mail outside local/dev")
    return backend


def configured_sender() -> MailSender | None:
    """The sender for the current settings; None when delivery is disabled."""
    backend = _backend()
    if backend == "smtp":
        return SmtpMailSender(smtp_settings())
    if backend == "memory":
        return memory_sender
    return None


def validate_mail_settings() -> None:
    """Startup check: a bad backend, SMTP setting, key or origin stops the app.

    With SMTP every piece needed to queue and send must be valid. Without a configured backend
    outside local the invitations still queue, and a warning says mail is not being sent.
    """
    backend = _backend()
    if backend == "smtp":
        smtp_settings()
        try:
            _fernet()
            _frontend_origin()
        except DeliveryUnavailable as error:
            raise ConfigurationError(str(error)) from None
    elif backend == "disabled":
        logger.warning("Invitation mail delivery is disabled; queued mail is not sent")


def _fernet() -> MultiFernet:
    """Comma-separated keys: the first encrypts, every one decrypts.

    Rotate by putting the new key first and keeping the old ones until the mail queued under
    them is delivered; a single replaced key would make that mail undecryptable.
    """
    keys = [key.strip() for key in os.getenv(KEY_ENV, "").split(",") if key.strip()]
    if not keys:
        raise DeliveryUnavailable(f"{KEY_ENV} is not set")
    try:
        return MultiFernet([Fernet(key.encode("ascii")) for key in keys])
    except (ValueError, UnicodeEncodeError):
        raise DeliveryUnavailable(f"{KEY_ENV} is malformed") from None


def _frontend_origin() -> str:
    raw = os.getenv(FRONTEND_ORIGIN_ENV, "").strip().removesuffix("/")
    if not raw or normalize_origin(raw) is None:
        raise DeliveryUnavailable(f"{FRONTEND_ORIGIN_ENV} is not a valid origin")
    return raw


def invitation_link(token: str) -> str:
    return f"{_frontend_origin()}{INVITATION_LINK_PATH}#token={token}"


def _iso(value: datetime | None) -> str | None:
    return iso_utc(value)


# --- queueing -----------------------------------------------------------------------------------

def enqueue_invitation_mail(
    db: Session, invitation: StoreInvitation, store_name: str, token: str,
) -> InvitationMailOutbox:
    """Queue the link mail for `invitation` on `db` (the caller commits). See module doc."""
    fernet = _fernet()
    payload = {
        "to": invitation.invited_email,
        "storeName": store_name,
        "link": invitation_link(token),
        "expiresAt": _iso(invitation.expires_at),
        "accessExpiresAt": _iso(invitation.access_expires_at),
    }
    row = InvitationMailOutbox(
        invitation_id=invitation.id, status=QUEUED, attempts=0, created_at=utcnow(),
        payload=fernet.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii"),
    )
    db.add(row)
    db.flush()
    return row


_DROP = {"payload": None, "claim_token": None, "claimed_until": None}


def discard_queued_mail(db: Session, invitation_id: str, *, now: datetime | None = None) -> int:
    """Drop unsent mail of an invitation (resend replaced its token, or it was cancelled).

    Callers hold the invitation's lock, so no other transaction adds mail for it meanwhile. The
    rows are found with a plain read and changed by primary key: a range UPDATE on the
    `invitation_id` index takes next-key locks that other stores' outbox INSERTs deadlock on.
    A claim held by a delivery run is dropped with the row; that run's finishing UPDATE then
    matches nothing, so the discard wins.
    """
    ids = list(db.scalars(select(InvitationMailOutbox.id).where(
        InvitationMailOutbox.invitation_id == invitation_id, InvitationMailOutbox.status == QUEUED,
    )))
    if not ids:
        return 0
    return update_by_key(db, InvitationMailOutbox, ids, {"status": DISCARDED, "processed_at": now or utcnow(), **_DROP},
                         InvitationMailOutbox.status == QUEUED)


# --- rendering ----------------------------------------------------------------------------------

def seoul_text(value: str) -> str:
    moment = datetime.fromisoformat(value).astimezone(SEOUL)
    return f"{moment.year}년 {moment.month}월 {moment.day}일 {moment:%H:%M} (한국 시간)"


def render(payload: dict) -> MailMessage:
    store = payload["storeName"]
    link = payload["link"]
    link_end = seoul_text(payload["expiresAt"])
    access = (
        f"{seoul_text(payload['accessExpiresAt'])} 전까지" if payload.get("accessExpiresAt")
        else "점주가 종료할 때까지"
    )
    subject = f"[Jidan] {store} 근무자 초대"
    text = "\n".join([
        f"{store}에서 Jidan 근무자로 초대했습니다.",
        "",
        "초대를 받은 Google 계정으로 로그인한 뒤 아래 링크에서 수락하거나 거절해 주세요.",
        link,
        "",
        f"- 초대 링크: {link_end} 전까지 사용할 수 있습니다.",
        f"- 매장 자료 접근: 수락하면 {access} 매뉴얼·체크리스트·AI 질문을 이용할 수 있습니다.",
        "",
        "링크는 다른 사람에게 전달하지 마세요. 초대를 요청하지 않았다면 이 메일을 무시해도 됩니다.",
    ])
    e = html.escape
    body = f"""<!doctype html>
<html lang="ko"><body style="font-family: sans-serif; line-height: 1.6; color: #222;">
<p><strong>{e(store)}</strong>에서 Jidan 근무자로 초대했습니다.</p>
<p>초대를 받은 Google 계정으로 로그인한 뒤 아래 버튼에서 수락하거나 거절해 주세요.</p>
<p><a href="{e(link, quote=True)}" style="display: inline-block; padding: 10px 16px; background: #2563eb;
color: #fff; text-decoration: none; border-radius: 6px;">초대 확인하기</a></p>
<ul>
<li>초대 링크: {e(link_end)} 전까지 사용할 수 있습니다.</li>
<li>매장 자료 접근: 수락하면 {e(access)} 매뉴얼·체크리스트·AI 질문을 이용할 수 있습니다.</li>
</ul>
<p style="color: #666; font-size: 13px;">링크는 다른 사람에게 전달하지 마세요. 초대를 요청하지 않았다면 이 메일을 무시해도 됩니다.</p>
</body></html>
"""
    return MailMessage(to=payload["to"], subject=subject, body=text, html=body, link=link)


# --- delivery -----------------------------------------------------------------------------------

def _due(now: datetime):
    return and_(
        InvitationMailOutbox.status == QUEUED,
        or_(InvitationMailOutbox.next_attempt_at.is_(None), InvitationMailOutbox.next_attempt_at <= now),
        _unclaimed(now),
    )


def _unclaimed(now: datetime):
    return or_(InvitationMailOutbox.claimed_until.is_(None), InvitationMailOutbox.claimed_until <= now)


def _claim(limit: int, now: datetime) -> tuple[str, list[tuple[str, str, int]]]:
    """Discard stale mail and claim up to `limit` due rows; returns (token, claimed rows).

    Locks only outbox rows by primary key. Candidates are found with plain (non-locking) reads,
    and every change is a primary-key UPDATE conditioned on the row still qualifying. A range
    UPDATE/`FOR UPDATE` on an outbox index (next-key gap locks) or a locking read of
    `store_invitations` would deadlock invitation requests that hold the invitation row and
    INSERT their outbox row (MySQL 1213).
    """
    token = new_uuid()
    with session_scope() as db:
        # Mail for an invitation that can no longer be accepted (cancelled, answered, expired) is
        # dropped instead of sent. Rows another run has claimed are left to that run.
        stale = list(db.scalars(
            select(InvitationMailOutbox.id)
            .join(StoreInvitation, StoreInvitation.id == InvitationMailOutbox.invitation_id)
            .where(InvitationMailOutbox.status == QUEUED, _unclaimed(now), ~pending_invitation_clause(now))
            .limit(limit)
        ))
        if stale:
            update_by_key(db, InvitationMailOutbox, stale, {"status": DISCARDED, "processed_at": now, **_DROP},
                          InvitationMailOutbox.status == QUEUED, _unclaimed(now))
        candidates = list(db.scalars(
            select(InvitationMailOutbox.id).where(_due(now))
            .order_by(InvitationMailOutbox.created_at, InvitationMailOutbox.id).limit(limit)
        ))
        ids = list(db.scalars(
            select(InvitationMailOutbox.id).where(InvitationMailOutbox.id.in_(candidates), _due(now))
            .with_for_update(skip_locked=True)  # point locks; rows another process holds are skipped
        )) if candidates else []
        if ids:
            update_by_key(db, InvitationMailOutbox, ids, {"claim_token": token, "claimed_until": now + CLAIM_LEASE},
                          _due(now))
    if not ids:
        return token, []
    with session_scope() as db:
        rows = db.execute(
            select(InvitationMailOutbox.id, InvitationMailOutbox.payload, InvitationMailOutbox.attempts)
            .where(InvitationMailOutbox.id.in_(ids), InvitationMailOutbox.claim_token == token)
            .order_by(InvitationMailOutbox.created_at, InvitationMailOutbox.id)
        ).all()
    return token, [tuple(row) for row in rows]


def _renew(row_id: str, token: str) -> bool:
    """Re-check and extend this run's claim right before sending. False: the row was discarded,
    or its lease ran out and another run claimed it; then it must not be sent from here."""
    with session_scope() as db:
        return db.execute(
            update(InvitationMailOutbox)
            .where(
                InvitationMailOutbox.id == row_id, InvitationMailOutbox.status == QUEUED,
                InvitationMailOutbox.claim_token == token,
            )
            .values(claimed_until=utcnow() + CLAIM_LEASE)
        ).rowcount == 1


def _finish(row_id: str, token: str, values: dict) -> bool:
    """Apply `values` if this run still holds the claim (a resend or cancel may have dropped it)."""
    with session_scope() as db:
        return db.execute(
            update(InvitationMailOutbox)
            .where(
                InvitationMailOutbox.id == row_id, InvitationMailOutbox.status == QUEUED,
                InvitationMailOutbox.claim_token == token,
            )
            .values(claim_token=None, claimed_until=None, **values)
        ).rowcount == 1


def deliver_queued_mail(*, sender: MailSender | None = None, limit: int = BATCH_SIZE) -> int:
    """Claim and send up to `limit` due mails; returns how many were sent. Safe to run anywhere."""
    if sender is None:
        try:
            sender = configured_sender()
        except ConfigurationError:
            logger.error("Invitation mail settings are invalid; queued mail is not sent")
            return 0
    if sender is None:
        return 0
    try:
        fernet = _fernet()
    except DeliveryUnavailable:
        # A missing or malformed key is configuration, not the mail. Checked before claiming, so
        # no row is claimed, retried or failed: the queue (payloads, attempts, backoff) waits as
        # it is and is delivered once the key is back.
        logger.error("Invitation mail key is not configured correctly; queued mail is kept")
        return 0
    token, claimed = _claim(limit, utcnow())
    sent = 0
    for row_id, ciphertext, attempts in claimed:
        try:
            message = replace(render(json.loads(fernet.decrypt(ciphertext.encode("ascii")))),
                              message_id=f"invitation-{row_id}")
        except (InvalidToken, ValueError, KeyError, TypeError):
            # No configured key decrypts it (its key was dropped from the list) or it is corrupt:
            # it can never be sent.
            _finish(row_id, token, {"status": FAILED, "payload": None, "processed_at": utcnow()})
            continue
        if not _renew(row_id, token):
            continue
        try:
            sender.send(message)
        except Exception:  # noqa: BLE001 - provider failures are retried; their text is never logged
            failures = attempts + 1
            if failures >= MAX_ATTEMPTS:
                _finish(row_id, token, {
                    "status": FAILED, "payload": None, "attempts": failures, "processed_at": utcnow(),
                })
            else:
                _finish(row_id, token, {
                    "attempts": failures, "next_attempt_at": utcnow() + RETRY_DELAYS[failures - 1],
                })
            continue
        if _finish(row_id, token, {"status": SENT, "payload": None, "processed_at": utcnow()}):
            sent += 1
    return sent


def purge_finished_mail(*, now: datetime | None = None, limit: int = PURGE_BATCH_SIZE) -> int:
    """Delete finished outbox rows past `FINISHED_RETENTION`; returns how many were deleted.

    Same locking discipline as `_claim`: ids by plain read, deletion by primary key. QUEUED rows
    are never deleted.
    """
    cutoff = (now or utcnow()) - FINISHED_RETENTION
    with session_scope() as db:
        ids = list(db.scalars(
            select(InvitationMailOutbox.id)
            .where(InvitationMailOutbox.status != QUEUED, InvitationMailOutbox.processed_at <= cutoff)
            .limit(limit)
        ))
        if not ids:
            return 0
        return delete_by_key(db, InvitationMailOutbox, ids, InvitationMailOutbox.status != QUEUED,
                             InvitationMailOutbox.processed_at <= cutoff)


def run_invitation_mail_delivery() -> None:
    """Periodic job (app.lifespan): drain due mail in bounded batches, then purge old rows."""
    for _ in range(MAX_BATCHES_PER_RUN):
        if deliver_queued_mail() < BATCH_SIZE:
            break
    for _ in range(MAX_BATCHES_PER_RUN):
        if purge_finished_mail() < PURGE_BATCH_SIZE:
            break

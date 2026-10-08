"""Real SMTP round trips against a local aiosmtpd server (plain, STARTTLS + AUTH, implicit TLS)."""
import datetime as dt
import email
import email.policy
import logging
import socket
import ssl
import threading

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from sqlalchemy.orm import Session

from app import invitation_mail, mail_smtp
from app.auth import ConfigurationError
from app.db.models import InvitationMailOutbox, Store, StoreInvitation
from tests.invitation_helpers import configure_mail, make_world, seed_invitation

PASSWORD = "smtp-secret-pw"
TOKEN = "k" * 43


@pytest.fixture(autouse=True)
def mail_env(monkeypatch):
    configure_mail(monkeypatch)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv(invitation_mail.BACKEND_ENV, "smtp")
    monkeypatch.setenv("MAIL_FROM", "Jidan <no-reply@jidan.test>")
    for name in ("SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    """A self-signed certificate for `localhost` and a client context that trusts only it."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    # A wide validity window: OpenSSL checks it against the system clock, which is not the
    # Python clock when the suite runs with a moved clock (tests/clock_shift.py).
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=3650))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    folder = tmp_path_factory.mktemp("tls")
    cert_file, key_file = folder / "cert.pem", folder / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(cert_file, key_file)

    def client():
        context = ssl.create_default_context(cafile=str(cert_file))
        return context

    return server, client


class Inbox:
    def __init__(self, reply=None):
        self.messages = []
        self.reply = reply

    async def handle_DATA(self, server, session, envelope):
        if self.reply:
            return self.reply
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content,
                              getattr(session, "authenticated", False)))
        return "250 OK"


def _authenticator(server, session, envelope, mechanism, auth_data):
    ok = auth_data.login == b"mailer" and auth_data.password == PASSWORD.encode()
    return AuthResult(success=ok)


@pytest.fixture
def smtp_server(monkeypatch, certificate):
    controllers = []

    def start(security="none", reply=None, auth=False):
        server_tls, client_tls = certificate
        inbox = Inbox(reply)
        port = _free_port()
        # server_hostname: without it aiosmtpd greets each connection with socket.getfqdn(), a
        # reverse DNS lookup of this machine that can outlast the client's SMTP timeout.
        kwargs = {"hostname": "localhost", "port": port, "server_hostname": "localhost"}
        if security == "starttls":
            kwargs.update(tls_context=server_tls, require_starttls=True)
        if security == "ssl":
            kwargs.update(ssl_context=server_tls)
        if auth:
            # aiosmtpd does not count implicit TLS as TLS for AUTH; the socket is encrypted anyway.
            kwargs.update(authenticator=_authenticator, auth_require_tls=security != "ssl")
        controller = Controller(inbox, **kwargs)
        controller.start()
        controllers.append(controller)
        monkeypatch.setenv("SMTP_HOST", "localhost")
        monkeypatch.setenv("SMTP_PORT", str(port))
        monkeypatch.setenv("SMTP_SECURITY", security)
        monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "5")
        if auth:
            monkeypatch.setenv("SMTP_USERNAME", "mailer")
            monkeypatch.setenv("SMTP_PASSWORD", PASSWORD)
        monkeypatch.setattr(mail_smtp, "tls_context", client_tls)
        return inbox

    yield start
    for controller in controllers:
        controller.stop()


def _queue(db_engine, store_name="명랑핫도그 광운대점"):
    world = make_world(db_engine)
    invitation_id, _ = seed_invitation(db_engine, world.store_id)
    with Session(db_engine) as db:
        store = db.get(Store, world.store_id)
        store.name = store_name
        row = invitation_mail.enqueue_invitation_mail(db, db.get(StoreInvitation, invitation_id), store_name, TOKEN)
        db.commit()
        return row.id


def _row(db_engine, row_id):
    with Session(db_engine) as db:
        return db.get(InvitationMailOutbox, row_id)


def _deliver(db_engine, row_id, monkeypatch) -> dict:
    """Run delivery once and describe the row, so a failure names its path instead of `0 == 1`.

    Paths that return 0: the send raised (QUEUED, attempts 1, next attempt set, `send_error` =
    exception class; e.g. a timeout or a refused/blocked local connection), the payload could
    not be decrypted (FAILED), nothing was claimed or the row was discarded (QUEUED/DISCARDED,
    attempts 0). Only the exception class is kept: its text may hold addresses.
    """
    errors = []
    send = mail_smtp.SmtpMailSender.send

    def recording(self, message):
        try:
            return send(self, message)
        except Exception as error:
            errors.append(type(error).__name__)
            raise
    monkeypatch.setattr(mail_smtp.SmtpMailSender, "send", recording)
    sent = invitation_mail.deliver_queued_mail()
    row = _row(db_engine, row_id)
    return {"sent": sent, "status": row.status, "attempts": row.attempts,
            "retry_scheduled": row.next_attempt_at is not None, "send_error": errors}


DELIVERED = {"sent": 1, "status": "SENT", "attempts": 0, "retry_scheduled": False, "send_error": []}


def _parse(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.default)


@pytest.mark.parametrize("security,auth", [("none", False), ("starttls", True), ("ssl", True), ("starttls", False)])
def test_smtp_round_trip(db_engine, smtp_server, security, auth, caplog, monkeypatch):
    inbox = smtp_server(security, auth=auth)
    row_id = _queue(db_engine)
    with caplog.at_level(logging.DEBUG):
        assert _deliver(db_engine, row_id, monkeypatch) == DELIVERED
    [(mail_from, rcpt, raw, authenticated)] = inbox.messages
    assert mail_from == "no-reply@jidan.test" and rcpt == ["jisu@example.com"]
    assert bool(authenticated) is auth
    message = _parse(raw)
    assert message["Subject"] == "[Jidan] 명랑핫도그 광운대점 근무자 초대"
    assert message["To"] == "jisu@example.com" and message["Message-ID"]
    assert message.get_content_type() == "multipart/alternative"
    text = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    link = f"https://app.jidan.test/invitations/accept#token={TOKEN}"
    assert link in text and f'href="{link}"' in html
    assert "(한국 시간)" in text and "명랑핫도그 광운대점" in html
    # The test server's own "mail.log" logger is not ours; nothing of the application may leak.
    ours = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("mail.log"))
    for secret in (PASSWORD, TOKEN, "jisu@example.com"):
        assert secret not in ours


def test_round_trip_needs_no_host_name_lookup(db_engine, smtp_server, monkeypatch):
    # Only the client may look up its own name (EHLO, after the greeting arrived). A lookup on
    # the server's thread delays the greeting, and a slow resolver then fails the send.
    server_lookups = []
    lookup = socket.getfqdn

    def recording(name=""):
        if threading.current_thread() is not threading.main_thread():
            server_lookups.append(name)
        return lookup(name)
    monkeypatch.setattr(socket, "getfqdn", recording)
    smtp_server("none")
    row_id = _queue(db_engine)
    assert _deliver(db_engine, row_id, monkeypatch) == DELIVERED
    assert server_lookups == []


def test_server_rejection_is_retried(db_engine, smtp_server):
    smtp_server("none", reply="550 mailbox unavailable")
    row_id = _queue(db_engine)
    assert invitation_mail.deliver_queued_mail() == 0
    row = _row(db_engine, row_id)
    assert (row.status, row.attempts) == ("QUEUED", 1) and row.next_attempt_at is not None


def test_unreachable_server_is_retried(db_engine, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(_free_port()))  # nothing listens
    monkeypatch.setenv("SMTP_SECURITY", "none")
    monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "2")
    row_id = _queue(db_engine)
    assert invitation_mail.deliver_queued_mail() == 0
    assert _row(db_engine, row_id).attempts == 1


def test_wrong_password_and_untrusted_certificate_fail(db_engine, smtp_server, monkeypatch):
    inbox = smtp_server("starttls", auth=True)
    monkeypatch.setenv("SMTP_PASSWORD", "wrong")
    row_id = _queue(db_engine)
    assert invitation_mail.deliver_queued_mail() == 0
    monkeypatch.setenv("SMTP_PASSWORD", PASSWORD)
    monkeypatch.setattr(mail_smtp, "tls_context", ssl.create_default_context)  # system CAs only
    with Session(db_engine) as db:
        db.get(InvitationMailOutbox, row_id).next_attempt_at = None
        db.commit()
    assert invitation_mail.deliver_queued_mail() == 0
    assert _row(db_engine, row_id).attempts == 2 and inbox.messages == []


def test_header_injection_is_neutralised(db_engine, smtp_server, monkeypatch):
    inbox = smtp_server("none")
    row_id = _queue(db_engine, store_name="매장\r\nBcc: victim@example.com")
    assert _deliver(db_engine, row_id, monkeypatch) == DELIVERED
    [(_, rcpt, raw, _)] = inbox.messages
    assert rcpt == ["jisu@example.com"]
    assert _parse(raw)["Bcc"] is None


# --- settings ---------------------------------------------------------------------------------

BASE = {"SMTP_HOST": "smtp.example.com", "MAIL_FROM": "no-reply@jidan.test"}


@pytest.mark.parametrize("env,expected", [
    ({}, ("smtp.example.com", 587, "starttls", None, 10.0)),
    ({"SMTP_TIMEOUT_SECONDS": "1"}, ("smtp.example.com", 587, "starttls", None, 1.0)),
    ({"SMTP_SECURITY": "SSL"}, ("smtp.example.com", 465, "ssl", None, 10.0)),
    ({"SMTP_SECURITY": "none"}, ("smtp.example.com", 25, "none", None, 10.0)),
    ({"SMTP_PORT": "2525", "SMTP_USERNAME": "u", "SMTP_PASSWORD": " p w ", "SMTP_TIMEOUT_SECONDS": "60"},
     ("smtp.example.com", 2525, "starttls", "u", 60.0)),
])
def test_valid_settings(monkeypatch, env, expected):
    for key, value in {**BASE, **env}.items():
        monkeypatch.setenv(key, value)
    settings = mail_smtp.smtp_settings()
    assert (settings.host, settings.port, settings.security, settings.username, settings.timeout) == expected
    if "SMTP_PASSWORD" in env:
        assert settings.password == " p w "  # passwords are not trimmed
        assert " p w " not in repr(settings)


@pytest.mark.parametrize("env", [
    {"SMTP_HOST": ""}, {"SMTP_HOST": "bad host"}, {"SMTP_SECURITY": "tls"}, {"SMTP_PORT": "0"},
    {"SMTP_PORT": "70000"}, {"SMTP_PORT": "abc"}, {"SMTP_USERNAME": "u"}, {"SMTP_PASSWORD": "p"},
    {"SMTP_SECURITY": "none", "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p"},
    {"SMTP_TIMEOUT_SECONDS": "0.5"}, {"SMTP_TIMEOUT_SECONDS": "61"}, {"SMTP_TIMEOUT_SECONDS": "x"},
    *({"SMTP_TIMEOUT_SECONDS": value} for value in ("nan", "NaN", "inf", "-inf")),
    {"MAIL_FROM": ""}, {"MAIL_FROM": "not-an-address"}, {"MAIL_FROM": "a@b.test\r\nBcc: x@y.test"},
    {"SMTP_SECURITY": "none", "APP_ENV": "production"}, {"SMTP_SECURITY": "none", "APP_ENV": "dev"},
])
def test_invalid_settings(monkeypatch, env):
    for key, value in {**BASE, **env}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigurationError) as caught:
        mail_smtp.smtp_settings()
    assert "p" != str(caught.value) and PASSWORD not in str(caught.value)


def test_startup_fails_on_bad_smtp_settings(monkeypatch):
    from app import lifespan

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_SECURITY", "smtps")
    with pytest.raises(ConfigurationError):
        lifespan.validate_background_settings()
    monkeypatch.setenv("SMTP_SECURITY", "starttls")
    lifespan.validate_background_settings()

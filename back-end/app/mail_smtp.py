"""SMTP delivery for invitation mail (`INVITATION_MAIL_BACKEND=smtp`).

Settings (runtime.env; validated at startup by `smtp_settings()`, a bad value stops the app):

* `SMTP_HOST` (required), `SMTP_PORT` (default 587 / 465 / 25 by security)
* `SMTP_SECURITY`: `starttls` (default), `ssl` (implicit TLS) or `none` (plain, `APP_ENV=local`
  only). TLS always verifies the server certificate and host name.
* `SMTP_USERNAME` / `SMTP_PASSWORD`: both or neither; never sent over a plain connection.
* `SMTP_TIMEOUT_SECONDS`: connect/read timeout, 1-60 (default 10)
* `MAIL_FROM`: sender address, e.g. `Jidan <no-reply@jidan.example.com>`

Nothing here logs. smtplib's debug output stays off, and callers must not log the exceptions
it raises: they can contain addresses or server replies.
"""

import os
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from app.auth import ConfigurationError

SECURITY_DEFAULT_PORTS = {"starttls": 587, "ssl": 465, "none": 25}
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0
# Blocking steps of one `send`: connect+greeting, EHLO, STARTTLS, EHLO, AUTH, MAIL, RCPT, DATA,
# message+end of data, QUIT. Each is bounded by the timeout, so one send takes at most this many
# timeouts (a server that keeps dripping bytes inside one reply can still stretch it).
SEND_STEPS = 10
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    username: str | None
    password: str | None = field(repr=False)
    timeout: float
    mail_from: str


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def smtp_settings() -> SmtpSettings:
    """Parse and check the SMTP settings; raises ConfigurationError naming the bad variable only."""
    host = _env("SMTP_HOST")
    if not host or _CONTROL.search(host) or " " in host:
        raise ConfigurationError("SMTP_HOST must be a host name")
    security = (_env("SMTP_SECURITY") or "starttls").lower()
    if security not in SECURITY_DEFAULT_PORTS:
        raise ConfigurationError("SMTP_SECURITY must be starttls, ssl or none")
    if security == "none" and (_env("APP_ENV") or "local") != "local":
        raise ConfigurationError("SMTP_SECURITY=none is only allowed for APP_ENV=local")
    raw_port = _env("SMTP_PORT")
    if raw_port:
        if not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
            raise ConfigurationError("SMTP_PORT must be 1-65535")
        port = int(raw_port)
    else:
        port = SECURITY_DEFAULT_PORTS[security]
    username = _env("SMTP_USERNAME") or None
    password = os.getenv("SMTP_PASSWORD") or None  # kept exactly as configured
    if (username is None) != (password is None):
        raise ConfigurationError("SMTP_USERNAME and SMTP_PASSWORD must be set together")
    if username is not None and security == "none":
        raise ConfigurationError("SMTP credentials need SMTP_SECURITY=starttls or ssl")
    raw_timeout = _env("SMTP_TIMEOUT_SECONDS")
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        raise ConfigurationError("SMTP_TIMEOUT_SECONDS must be a number") from None
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ConfigurationError("SMTP_TIMEOUT_SECONDS must be 1-60")
    mail_from = _env("MAIL_FROM")
    _, address = parseaddr(mail_from)
    if not mail_from or _CONTROL.search(mail_from) or not _ADDRESS.match(address):
        raise ConfigurationError("MAIL_FROM must be an e-mail address")
    return SmtpSettings(host, port, security, username, password, timeout, mail_from)


def tls_context() -> ssl.SSLContext:
    """Certificate and host name verification on (tests replace this to trust a local CA)."""
    return ssl.create_default_context()


def _header(value: str) -> str:
    return _CONTROL.sub(" ", value)


class SmtpMailSender:
    """Sends one message per connection; raises on any failure so the caller retries."""

    def __init__(self, settings: SmtpSettings) -> None:
        self.settings = settings

    def build(self, message) -> EmailMessage:
        email = EmailMessage()
        email["Subject"] = _header(message.subject)
        email["From"] = self.settings.mail_from
        email["To"] = _header(message.to)
        email["Date"] = formatdate(usegmt=True)
        domain = parseaddr(self.settings.mail_from)[1].split("@")[-1]
        # A stable ID per logical mail lets a client (and an operator) recognise a resend of the
        # same mail after a crash; delivery is at-least-once.
        key = getattr(message, "message_id", None)
        email["Message-ID"] = f"<{_ID_UNSAFE.sub('', key)}@{domain}>" if key else make_msgid(domain=domain)
        email.set_content(message.body)
        email.add_alternative(message.html, subtype="html")
        return email

    def send(self, message) -> None:
        settings = self.settings
        email = self.build(message)
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout, context=tls_context())
        else:
            client = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
        with client:
            if settings.security == "starttls":
                client.starttls(context=tls_context())
            if settings.username is not None:
                client.login(settings.username, settings.password)
            client.send_message(email)

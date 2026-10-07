"""A local SMTP server (aiosmtpd) that keeps what the application sends, for the demo E2E.

The server under test runs with `INVITATION_MAIL_BACKEND=smtp`, `SMTP_SECURITY=none` (allowed for
`APP_ENV=local` only) and `SMTP_HOST`/`SMTP_PORT` pointing here, so invitation mail travels the
real delivery path: outbox row -> periodic delivery -> SMTP -> this inbox.
"""
import email
import email.policy
import socket
import threading
import time
from email.message import EmailMessage
from urllib.parse import parse_qs, urlsplit

from aiosmtpd.controller import Controller


def _free_port(host: str) -> int:
    with socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


class _Collector:
    def __init__(self) -> None:
        self.messages: list[tuple[list[str], EmailMessage]] = []
        self.lock = threading.Lock()

    async def handle_DATA(self, _server, _session, envelope):
        message = email.message_from_bytes(envelope.content, policy=email.policy.default)
        with self.lock:
            self.messages.append((list(envelope.rcpt_tos), message))
        return "250 OK"


class Mailbox:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._collector = _Collector()
        self.port = port or _free_port(host)
        self._controller = Controller(self._collector, hostname=host, port=self.port)

    def start(self) -> "Mailbox":
        self._controller.start()
        return self

    def stop(self) -> None:
        self._controller.stop()

    def wait_for(self, recipient: str, timeout: float) -> EmailMessage | None:
        """The newest message to `recipient`, waiting up to `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while True:
            with self._collector.lock:
                found = [m for rcpts, m in self._collector.messages if recipient in rcpts]
            if found:
                return found[-1]
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.5)


def text_of(message: EmailMessage) -> str:
    return message.get_body(preferencelist=("plain",)).get_content()


def token_from_link(link: str) -> str | None:
    """The invitation token from `<origin>/invitations/accept#token=...` (fragment, never sent)."""
    values = parse_qs(urlsplit(link).fragment).get("token")
    return values[0] if values else None

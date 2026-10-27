"""Read-only access to the alerts mailbox over IMAP (stdlib only).

The mailbox is opened with EXAMINE (read-only) and messages are fetched with BODY.PEEK, so reading
never marks anything as seen, and nothing is ever moved, deleted or sent.
"""

from __future__ import annotations

import email
import imaplib
from collections.abc import Iterator
from datetime import date
from email.message import Message
from types import TracebackType
from typing import Final, Protocol

GMAIL_IMAP_HOST: Final = "imap.gmail.com"
# IMAP dates use English month names whatever the machine's locale, so strftime("%b") is not used.
_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MailboxError(Exception):
    """Login or protocol failure, with a message fit to show the user."""


class Mailbox(Protocol):
    def messages(self, sender: str, since: date) -> Iterator[Message]:
        """Messages from `sender` received on or after `since`, oldest first."""
        ...


def imap_date(day: date) -> str:
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


class ImapMailbox:
    def __init__(
        self,
        account: str,
        app_password: str,
        host: str = GMAIL_IMAP_HOST,
        folder: str = "INBOX",
        timeout_s: float = 30.0,
    ) -> None:
        self._account = account
        self._password = app_password
        self._host = host
        self._folder = folder
        self._timeout_s = timeout_s
        self._imap: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> ImapMailbox:
        imap = imaplib.IMAP4_SSL(self._host, timeout=self._timeout_s)
        try:
            imap.login(self._account, self._password)
        except imaplib.IMAP4.error as exc:
            imap.shutdown()
            raise MailboxError(f"login to {self._host} as {self._account} failed: {exc}") from exc
        status, _ = imap.select(self._folder, readonly=True)
        if status != "OK":
            imap.logout()
            raise MailboxError(f"cannot open folder {self._folder!r}")
        self._imap = imap
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._imap is not None:
            self._imap.logout()
            self._imap = None

    def messages(self, sender: str, since: date) -> Iterator[Message]:
        if self._imap is None:
            raise RuntimeError("use ImapMailbox as a context manager")
        status, data = self._imap.search(None, "FROM", f'"{sender}"', "SINCE", imap_date(since))
        if status != "OK":
            raise MailboxError(f"search failed: {data!r}")
        for number in data[0].split():
            status, fetched = self._imap.fetch(number.decode(), "(BODY.PEEK[])")
            raw = fetched[0][1] if status == "OK" and fetched and isinstance(fetched[0], tuple) else None
            if not isinstance(raw, bytes):
                raise MailboxError(f"fetch of message {number.decode()} failed")
            yield email.message_from_bytes(raw)

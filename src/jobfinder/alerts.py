"""Import LinkedIn job alerts from the alerts mailbox into the job store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from email.message import Message
from email.utils import parsedate_to_datetime

from jobfinder.mailbox import Mailbox
from jobfinder.sources.linkedin_alerts import SENDER, alert_text, parse_alert
from jobfinder.storage import JobStore


@dataclass(slots=True)
class AlertImport:
    messages: int = 0
    jobs: int = 0
    new: int = 0
    skipped: list[str] = field(default_factory=list)


def received_at(message: Message, fallback: datetime) -> datetime:
    """When the alert was sent: the job was first seen then, not when the mailbox is read."""
    try:
        sent = parsedate_to_datetime(message["Date"] or "")
    except (TypeError, ValueError):
        return fallback
    return sent if sent.tzinfo is not None else fallback


def import_alerts(mailbox: Mailbox, store: JobStore, since: date, now: datetime) -> AlertImport:
    report = AlertImport()
    for message in mailbox.messages(SENDER, since):
        report.messages += 1
        text = alert_text(message)
        if text is None:
            report.skipped.append(f"alert {message['Date']}: no text part")
            continue
        parsed = parse_alert(text)
        report.jobs += len(parsed.jobs)
        report.skipped.extend(parsed.skipped)
        if parsed.jobs:
            report.new += store.record_sightings(parsed.jobs, received_at(message, now))
    return report

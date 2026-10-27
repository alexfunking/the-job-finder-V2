"""LinkedIn job-alert emails: the jobs LinkedIn itself sends to the user, read from the alerts mailbox.

This is not scraping (ADR-001): nothing is fetched from LinkedIn. An alert names each job's title,
company and location with a link, but no description, so these jobs have `description_text=None`.

The text/plain part of an alert is a list of blocks separated by dashed lines. A job block is:

                                    <- a line of spaces opens the job
    Junior MLOps Engineer           <- title
    Dell Technologies               <- company
    Herzliya                        <- location
    <optional lines: "This company is actively hiring", "16 connections", ...>
    View job: https://www.linkedin.com/comm/jobs/view/4481322254/?trackingId=...

The first block also carries the alert's header above the line of spaces. Alerts come in English
("View job:") and Hebrew ("צפייה במשרה:"), with invisible direction marks around Hebrew text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.message import Message
from typing import Final

from pydantic import ValidationError

from jobfinder.models import Job

SOURCE: Final = "linkedin"
SLUG: Final = "alerts"
SENDER: Final = "jobalerts-noreply@linkedin.com"

_JOB_LINK: Final = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")
_SEPARATOR: Final = re.compile(r"^-{10,}\s*$")
# Usually a line of its own; in single-job Hebrew alerts the spaces trail the header line instead.
_SPACER: Final = re.compile(r" {4,}$")
# Left-to-right / right-to-left marks and embeddings LinkedIn wraps around Hebrew and mixed text.
_DIRECTION_MARKS: Final = re.compile("[‎‏‪-‮⁦-⁩]")


@dataclass(frozen=True, slots=True)
class AlertJobs:
    jobs: list[Job] = field(default_factory=list)
    # One reason per job link that could not be turned into a Job.
    skipped: list[str] = field(default_factory=list)


def alert_text(message: Message) -> str | None:
    """The decoded text/plain part of an alert email, or None if it has none."""
    for part in message.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return None


def job_url(job_id: str) -> str:
    """The posting's canonical link, without the tracking parameters the email adds."""
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def parse_alert(text: str) -> AlertJobs:
    result = AlertJobs()
    lines = [_DIRECTION_MARKS.sub("", line.rstrip("\r")) for line in text.split("\n")]
    block: list[str] = []
    for line in lines:
        if _SEPARATOR.match(line):
            block = []
            continue
        link = _JOB_LINK.search(line)
        if link is None:
            block.append(line)
            continue
        _add_job(result, link.group(1), block)
        block = []
    return result


def _add_job(result: AlertJobs, job_id: str, block: list[str]) -> None:
    spacers = [index for index, line in enumerate(block) if _SPACER.search(line)]
    if not spacers:
        result.skipped.append(f"{job_id}: no job block before the link")
        return
    fields = [line.strip() for line in block[spacers[-1] + 1 :] if line.strip()]
    if len(fields) < 3:
        result.skipped.append(f"{job_id}: expected title, company and location, got {fields!r}")
        return
    title, company, location = fields[:3]
    try:
        result.jobs.append(
            Job(
                source=SOURCE,
                slug=SLUG,
                job_id=job_id,
                company=company,
                title=title,
                location=location,
                url=job_url(job_id),
            )
        )
    except ValidationError as exc:
        invalid = ", ".join(str(error["loc"][0]) for error in exc.errors())
        result.skipped.append(f"{job_id}: invalid {invalid}")

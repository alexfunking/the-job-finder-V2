"""The contract every source implements. Storage and the CLI depend only on this, never on a concrete ATS."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from jobfinder.http import Fetcher
from jobfinder.models import Job


@dataclass(frozen=True, slots=True)
class SourceResult:
    """A successful fetch of one board.

    `skipped` holds postings the source received but could not turn into a valid Job (for example an
    empty description), one human-readable reason each, so they show up in the scan summary.
    """

    jobs: list[Job]
    skipped: list[str] = field(default_factory=list)


class Source(Protocol):
    @property
    def name(self) -> str:
        """The ATS name used as Job.source, e.g. "greenhouse"."""
        ...

    def fetch_jobs(self, slug: str, company: str, fetcher: Fetcher) -> SourceResult:
        """Fetch every open posting on one board.

        Raises FetchError when the board cannot be read, including HTTP 404 for a configured board:
        the caller must then leave that board's stored jobs open (ADR-005) rather than close them all.
        """
        ...

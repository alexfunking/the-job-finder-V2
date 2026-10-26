"""One scan: fetch every configured board and record the boards that answered."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from jobfinder.config import Board
from jobfinder.http import FetchError, Fetcher
from jobfinder.sources import SOURCES, Source
from jobfinder.storage import BoardUpdate, JobStore


@dataclass(frozen=True, slots=True)
class BoardOutcome:
    board: Board
    open_jobs: int = 0
    update: BoardUpdate | None = None
    skipped: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ScanReport:
    started_at: datetime
    outcomes: tuple[BoardOutcome, ...]

    @property
    def failed(self) -> tuple[BoardOutcome, ...]:
        return tuple(o for o in self.outcomes if o.error is not None)

    def total(self, field: str) -> int:
        return sum(getattr(o.update, field) for o in self.outcomes if o.update is not None)


def scan(
    boards: Sequence[Board],
    store: JobStore,
    fetcher: Fetcher,
    now: datetime,
    sources: Mapping[str, Source] = SOURCES,
) -> ScanReport:
    """Fetch each board and record it. A board whose fetch fails is reported and left untouched (ADR-005)."""
    outcomes: list[BoardOutcome] = []
    for board in boards:
        try:
            result = sources[board.source].fetch_jobs(board.slug, board.company, fetcher)
        except FetchError as exc:
            outcomes.append(BoardOutcome(board=board, error=str(exc)))
            continue
        update = store.record_board(board.source, board.slug, result.jobs, now)
        outcomes.append(BoardOutcome(
            board=board, open_jobs=len(result.jobs), update=update, skipped=tuple(result.skipped),
        ))
    return ScanReport(started_at=now, outcomes=tuple(outcomes))

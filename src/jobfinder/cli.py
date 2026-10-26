"""Command line entry point: `jobfinder scan`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from jobfinder.config import ConfigError, load_boards
from jobfinder.http import Fetcher, UrllibFetcher
from jobfinder.scan import BoardOutcome, ScanReport, scan
from jobfinder.storage import JobStore

EXIT_OK = 0
EXIT_BOARD_ERRORS = 1  # the scan ran, but at least one board could not be read
EXIT_CONFIG_ERROR = 2


def board_name(outcome: BoardOutcome) -> str:
    return f"{outcome.board.company} ({outcome.board.label})"


def render_outcome(outcome: BoardOutcome, width: int = 44) -> str:
    # ASCII only: Windows consoles and Task Scheduler logs may not be UTF-8.
    name = board_name(outcome)
    if outcome.error is not None:
        return f"{name:<{width}} ERROR {outcome.error}"
    update = outcome.update
    assert update is not None
    line = f"{name:<{width}} {outcome.open_jobs:>4} open  +{update.new} new  -{update.closed} closed"
    if update.reopened:
        line += f"  {update.reopened} reopened"
    if outcome.skipped:
        line += f"  {len(outcome.skipped)} skipped"
    return line


def render_summary(report: ScanReport, store: JobStore) -> str:
    lines = [
        f"Boards: {len(report.outcomes)} scanned, {len(report.failed)} failed",
        f"Jobs:   +{report.total('new')} new, -{report.total('closed')} closed, "
        f"{report.total('reopened')} reopened; {store.count(include_closed=False)} open in store",
    ]
    skipped = [(o.board.label, reason) for o in report.outcomes for reason in o.skipped]
    if skipped:
        lines.append(f"Skipped postings ({len(skipped)}):")
        lines.extend(f"  {label}: {reason}" for label, reason in skipped[:20])
    if report.failed:
        lines.append("Failed boards keep their stored jobs open; they are retried on the next scan.")
    return "\n".join(lines)


def run_scan(sources_path: Path, db_path: Path, fetcher: Fetcher | None = None) -> int:
    try:
        boards = load_boards(sources_path)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    store = JobStore.open(db_path)
    try:
        report = scan(boards, store, fetcher or UrllibFetcher(), datetime.now(tz=UTC))
        width = max((len(board_name(o)) for o in report.outcomes), default=0)
        for outcome in report.outcomes:
            print(render_outcome(outcome, width))
        print()
        print(render_summary(report, store))
    finally:
        store.close()
    return EXIT_BOARD_ERRORS if report.failed else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobfinder", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan_parser = commands.add_parser("scan", help="fetch every board in the sources file and store the jobs")
    scan_parser.add_argument("--sources", type=Path, default=Path("data/sources.toml"))
    scan_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    args = parser.parse_args(argv)
    if args.command == "scan":
        return run_scan(args.sources, args.db)
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line entry point: `jobfinder scan` and `jobfinder label`."""

from __future__ import annotations

import argparse
import shutil
import sys
import webbrowser
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from jobfinder.config import ConfigError, load_boards
from jobfinder.http import Fetcher, UrllibFetcher
from jobfinder.labeling import Console, run_session
from jobfinder.labels import load_labels
from jobfinder.sampling import labeling_queue
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


def terminal_console() -> Console:
    # Descriptions are UTF-8; a console that cannot show a character should print "?" rather than crash.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    width = min(shutil.get_terminal_size().columns - 1, 100)
    return Console(read=input, write=print, open_url=webbrowser.open, width=width)


def run_label(db_path: Path, labels_path: Path, target: int, seed: int, console: Console | None = None) -> int:
    if not db_path.exists():
        print(f"no job database at {db_path}: run `jobfinder scan` first", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        labels = load_labels(labels_path)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    store = JobStore.open(db_path)
    try:
        candidates = [stored.job for stored in store.open_jobs()]
    finally:
        store.close()
    queue = labeling_queue(candidates, labels, seed)
    try:
        run_session(queue, labels, labels_path, target, console or terminal_console(), lambda: datetime.now(tz=UTC))
    except KeyboardInterrupt:
        print("\nStopped; every answer so far is saved.")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobfinder", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan_parser = commands.add_parser("scan", help="fetch every board in the sources file and store the jobs")
    scan_parser.add_argument("--sources", type=Path, default=Path("data/sources.toml"))
    scan_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    label_parser = commands.add_parser("label", help="label jobs as fit / not fit for the eval set")
    label_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    label_parser.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    label_parser.add_argument("--target", type=int, default=80, help="stop at this many fit / not-fit labels")
    label_parser.add_argument("--seed", type=int, default=2026, help="fixes the order jobs are offered in")
    args = parser.parse_args(argv)
    if args.command == "scan":
        return run_scan(args.sources, args.db)
    if args.command == "label":
        return run_label(args.db, args.labels, args.target, args.seed)
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line entry point: `jobfinder scan`, `jobfinder alerts`, `jobfinder label` and `jobfinder filter`."""

from __future__ import annotations

import argparse
import shutil
import sys
import webbrowser
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobfinder.alerts import import_alerts
from jobfinder.config import ConfigError, load_boards
from jobfinder.envfile import secret
from jobfinder.filtering import FilterResult, prefilter
from jobfinder.http import Fetcher, UrllibFetcher
from jobfinder.labeling import Console, run_session
from jobfinder.labels import Label, load_labels
from jobfinder.mailbox import ImapMailbox, Mailbox, MailboxError
from jobfinder.models import Job
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


ALERTS_ACCOUNT_VAR = "ALERTS_EMAIL_ACCOUNT"
ALERTS_PASSWORD_VAR = "ALERTS_EMAIL_APP_PASSWORD"  # a Gmail app password, never the account password


def run_alerts(db_path: Path, env_file: Path, days: int, mailbox: Mailbox | None = None) -> int:
    now = datetime.now(tz=UTC)
    since = (now - timedelta(days=days)).date()
    store = JobStore.open(db_path)
    try:
        if mailbox is not None:
            report = import_alerts(mailbox, store, since, now)
        else:
            account, password = secret(ALERTS_ACCOUNT_VAR, env_file), secret(ALERTS_PASSWORD_VAR, env_file)
            if not account or not password:
                print(f"set {ALERTS_ACCOUNT_VAR} and {ALERTS_PASSWORD_VAR} in {env_file}", file=sys.stderr)
                return EXIT_CONFIG_ERROR
            try:
                with ImapMailbox(account, password.replace(" ", "")) as imap:
                    report = import_alerts(imap, store, since, now)
            except MailboxError as exc:
                print(f"mailbox error: {exc}", file=sys.stderr)
                return EXIT_CONFIG_ERROR
            except OSError as exc:
                print(f"cannot reach the mailbox: {exc}", file=sys.stderr)
                return EXIT_BOARD_ERRORS
        print(f"Alerts since {since.isoformat()}: {report.messages} emails, {report.jobs} job listings, "
              f"{report.new} new jobs; {store.count(include_closed=False)} open in store")
        if report.skipped:
            print(f"Skipped listings ({len(report.skipped)}):")
            for reason in report.skipped[:20]:
                print(f"  {reason}")
    finally:
        store.close()
    return EXIT_OK


def _replace_unprintable_output() -> None:
    # Job text is UTF-8; a console that cannot show a character should print "?" rather than crash.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")


def terminal_console() -> Console:
    _replace_unprintable_output()
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


def job_line(job: Job, result: FilterResult) -> str:
    reasons = "; ".join(result.rejections + result.notes)
    line = f"  {job.company} | {job.title} | {job.location or '(none)'}"
    return f"{line}  [{reasons}]" if reasons else line


def render_filter_summary(results: Sequence[tuple[Job, FilterResult]]) -> str:
    kept = [result for _, result in results if result.kept]
    rejected = [result for _, result in results if not result.kept]

    def rejected_for(prefix: str) -> int:
        return sum(1 for r in rejected if any(reason.startswith(prefix) for reason in r.rejections))

    return "\n".join([
        f"Open jobs: {len(results)}",
        f"Kept:      {len(kept)} ({sum(1 for r in kept if r.notes)} with a location that names no place)",
        f"Rejected:  {len(rejected)} (location {rejected_for('location')}, senior title {rejected_for('senior')}; "
        "a job can fail both)",
    ])


def render_label_check(labels: Mapping[str, Label]) -> str:
    """How the filter treats the hand-labeled jobs: every rejected "fit" job is a filter mistake."""
    counted = [label for label in labels.values() if label.counts]
    if not counted:
        return "No fit / not-fit labels yet: run `jobfinder label` to check the filter against them."
    checked = [(label, prefilter(label.job)) for label in counted]
    wrongly_rejected = [(l, r) for l, r in checked if l.verdict == "fit" and not r.kept]
    lines = [
        f"Against {len(checked)} labels:",
        f"  fit, kept         {sum(1 for l, r in checked if l.verdict == 'fit' and r.kept)}",
        f"  fit, rejected     {len(wrongly_rejected)}  <- filter mistakes",
        f"  not fit, rejected {sum(1 for l, r in checked if l.verdict == 'no_fit' and not r.kept)}"
        "  (never reach the extractor)",
        f"  not fit, kept     {sum(1 for l, r in checked if l.verdict == 'no_fit' and r.kept)}"
        "  (left for the extractor and scoring)",
    ]
    lines.extend(f"{job_line(l.job, r)}  you said: {l.reason}" for l, r in wrongly_rejected)
    return "\n".join(lines)


def run_filter(db_path: Path, labels_path: Path, show: str | None) -> int:
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
        jobs = [stored.job for stored in store.open_jobs()]
    finally:
        store.close()
    results = [(job, prefilter(job)) for job in jobs]

    _replace_unprintable_output()
    print(render_filter_summary(results))
    print()
    print(render_label_check(labels))
    if show is not None:
        print()
        print(f"{show.capitalize()} jobs:")
        for job, result in sorted(results, key=lambda pair: (pair[0].company, pair[0].title)):
            if is_shown(result, show):
                print(job_line(job, result))
    return EXIT_OK


def is_shown(result: FilterResult, show: str) -> bool:
    if show == "kept":
        return result.kept
    if show == "rejected":
        return not result.kept
    return result.kept and bool(result.notes)  # "unplaced"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobfinder", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan_parser = commands.add_parser("scan", help="fetch every board in the sources file and store the jobs")
    scan_parser.add_argument("--sources", type=Path, default=Path("data/sources.toml"))
    scan_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    alerts_parser = commands.add_parser("alerts", help="import LinkedIn job alerts from the alerts mailbox")
    alerts_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    alerts_parser.add_argument("--env", type=Path, default=Path(".env"), help="file with the mailbox credentials")
    alerts_parser.add_argument("--days", type=int, default=14, help="read alerts received in the last N days")
    label_parser = commands.add_parser("label", help="label jobs as fit / not fit for the eval set")
    label_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    label_parser.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    label_parser.add_argument("--target", type=int, default=80, help="stop at this many fit / not-fit labels")
    label_parser.add_argument("--seed", type=int, default=2026, help="fixes the order jobs are offered in")
    filter_parser = commands.add_parser("filter", help="apply the location / seniority pre-filter and report")
    filter_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    filter_parser.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    filter_parser.add_argument("--show", choices=("kept", "rejected", "unplaced"), help="also list these jobs")
    args = parser.parse_args(argv)
    if args.command == "scan":
        return run_scan(args.sources, args.db)
    if args.command == "alerts":
        return run_alerts(args.db, args.env, args.days)
    if args.command == "label":
        return run_label(args.db, args.labels, args.target, args.seed)
    if args.command == "filter":
        return run_filter(args.db, args.labels, args.show)
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())

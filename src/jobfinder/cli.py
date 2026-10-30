"""Command line entry point: `jobfinder scan`, `alerts`, `label`, `filter`, `extract`, `eval` and `ui`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import webbrowser
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobfinder.alerts import import_alerts
from jobfinder.config import ConfigError, load_boards
from jobfinder.envfile import secret
from jobfinder.evaluation import evaluate, extractor_names, render_report
from jobfinder.extraction import EXTRACTORS, CachedExtractor, ExtractionError, Extractor, build_extractor
from jobfinder.filtering import FilterResult, prefilter
from jobfinder.http import Fetcher, UrllibFetcher, UrllibPoster
from jobfinder.labeling import Console, accept_suggestions, run_session
from jobfinder.labels import Label, load_labels, load_suggestions
from jobfinder.mailbox import ImapMailbox, Mailbox, MailboxError
from jobfinder.models import Job
from jobfinder.sampling import labeling_queue, review_queue
from jobfinder.scan import BoardOutcome, ScanReport, scan
from jobfinder.scoring import PrioritiesError, load_priorities
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


def run_label(
    db_path: Path,
    labels_path: Path,
    target: int,
    seed: int,
    console: Console | None = None,
    suggestions_path: Path | None = None,
    accept_rest: bool = False,
) -> int:
    if not db_path.exists():
        print(f"no job database at {db_path}: run `jobfinder scan` first", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        labels = load_labels(labels_path)
        suggestions = load_suggestions(suggestions_path) if suggestions_path else {}
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    store = JobStore.open(db_path)
    try:
        candidates = [stored.job for stored in store.open_jobs()]
        if accept_rest:
            found = {key: stored.job for key in suggestions if (stored := store.get(key)) is not None}
            added = accept_suggestions(suggestions, labels, found, labels_path, lambda: datetime.now(tz=UTC))
            print(f"Accepted {added} suggestions in bulk; {len(labels)} labels in {labels_path}")
            return EXIT_OK
    finally:
        store.close()
    queue = review_queue(candidates, labels, suggestions, seed)
    try:
        run_session(
            queue, labels, labels_path, target, console or terminal_console(), lambda: datetime.now(tz=UTC),
            suggestions,
        )
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


def run_extract(
    db_path: Path,
    env_file: Path,
    kind: str,
    model: str | None,
    limit: int,
    seed: int,
    extractor: Extractor | None = None,
    use_cache: bool = True,
    all_kept: bool = False,
) -> int:
    """Run an extractor on the first `limit` jobs of the labeling order and print what it reads.

    With `all_kept`, run it instead on every open job the pre-filter keeps that has a description (a
    title-only alert gives a model nothing more to read), which is what `jobfinder ui` scores.
    Answers are saved in the job store and reused while the job's text and the prompt stay the same, so
    a rerun costs nothing; `use_cache=False` asks the model again (and overwrites the saved answer).
    """
    if not db_path.exists():
        print(f"no job database at {db_path}: run `jobfinder scan` first", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if extractor is None:
        try:
            extractor = build_extractor(kind, model, env_file, UrllibPoster())
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return EXIT_CONFIG_ERROR
    store = JobStore.open(db_path)
    try:
        candidates = [stored.job for stored in store.open_jobs()]
        if use_cache:
            extractor = CachedExtractor(extractor, store, lambda: datetime.now(tz=UTC))
        if all_kept:
            jobs = [job for job in candidates if job.description_text is not None and prefilter(job).kept]
        else:
            jobs = labeling_queue(candidates, {}, seed)[:limit]
        return _print_extractions(extractor, jobs)
    finally:
        store.close()


def _print_extractions(extractor: Extractor, jobs: Sequence[Job]) -> int:
    _replace_unprintable_output()
    print(f"Extractor: {extractor.name}")
    failed = cached = 0
    input_tokens = output_tokens = 0
    started = time.monotonic()
    for job in jobs:
        print()
        print(f"{job.company} | {job.title} | {job.location or '(none)'}"
              + ("  [title only]" if job.description_text is None else ""))
        try:
            result = extractor.extract(job)
        except ExtractionError as exc:
            failed += 1
            print(f"  ERROR {exc}")
            continue
        print(f"  {json.dumps(result.extraction.model_dump(), ensure_ascii=False)}"
              + ("  (saved)" if result.cached else ""))
        if result.cached:
            cached += 1
        else:
            input_tokens += result.input_tokens or 0
            output_tokens += result.output_tokens or 0
    print()
    print(f"{len(jobs) - failed} extracted ({cached} from saved answers), {failed} failed "
          f"in {time.monotonic() - started:.0f}s; tokens spent: {input_tokens} in, {output_tokens} out")
    return EXIT_BOARD_ERRORS if failed else EXIT_OK


def run_eval(db_path: Path, labels_path: Path, priorities_path: Path) -> int:
    """Compare the pipeline with the labels, once per extractor and once from the titles alone."""
    if not db_path.exists():
        print(f"no job database at {db_path}: run `jobfinder scan` first", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        priorities = load_priorities(priorities_path)
        labels = load_labels(labels_path)
    except (PrioritiesError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if not any(label.counts for label in labels.values()):
        print("No fit / not-fit labels yet: label jobs in `jobfinder ui` or `jobfinder label` first.")
        return EXIT_OK
    _replace_unprintable_output()
    store = JobStore.open(db_path)
    try:
        reports = [
            render_report(name, evaluate(labels, store, priorities, name)) for name in extractor_names(labels, store)
        ]
        reports.append(render_report("titles alone", evaluate(labels, store, priorities, None)))
    finally:
        store.close()
    print("\n\n".join(reports))
    return EXIT_OK


def run_ui(
    db_path: Path,
    labels_path: Path,
    priorities_path: Path,
    suggestions_path: Path,
    prefer: Sequence[str],
    seed: int,
    port: int,
    open_browser: bool,
) -> int:
    if not db_path.exists():
        print(f"no job database at {db_path}: run `jobfinder scan` first", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        load_priorities(priorities_path)
        load_labels(labels_path)
        load_suggestions(suggestions_path)
    except (PrioritiesError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG_ERROR
    # Imported here so the other commands do not load the web server.
    from jobfinder.ui import serve

    try:
        serve(db_path, labels_path, priorities_path, suggestions_path, prefer, seed, port, open_browser)
    except OSError as exc:
        print(f"cannot listen on port {port}: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return EXIT_OK


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
    label_parser.add_argument(
        "--suggestions", type=Path, default=Path("data/suggestions.jsonl"), help="proposed labels to accept or override"
    )
    label_parser.add_argument(
        "--accept-rest", action="store_true", help="save every unanswered suggestion as a label, marked as bulk"
    )
    filter_parser = commands.add_parser("filter", help="apply the location / seniority pre-filter and report")
    filter_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    filter_parser.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    filter_parser.add_argument("--show", choices=("kept", "rejected", "unplaced"), help="also list these jobs")
    extract_parser = commands.add_parser("extract", help="try an extractor on a few jobs and print its answers")
    extract_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    extract_parser.add_argument("--env", type=Path, default=Path(".env"), help="file with the API key")
    extract_parser.add_argument("--extractor", choices=EXTRACTORS, default="gemini")
    extract_parser.add_argument("--model", help="model name; each extractor has a default")
    extract_parser.add_argument("--limit", type=int, default=5, help="how many jobs to run on")
    extract_parser.add_argument("--seed", type=int, default=2026, help="same job order as `jobfinder label`")
    extract_parser.add_argument("--no-cache", action="store_true", help="ask the model again, ignoring saved answers")
    extract_parser.add_argument(
        "--all", action="store_true", help="every job the pre-filter keeps that has a description (ignores --limit)"
    )
    eval_parser = commands.add_parser("eval", help="measure the pre-filter and scoring against the labels")
    eval_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    eval_parser.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    eval_parser.add_argument("--priorities", type=Path, default=Path("priorities.txt"))
    ui_parser = commands.add_parser("ui", help="show the scored jobs in a local web page")
    ui_parser.add_argument("--db", type=Path, default=Path("data/jobs.db"))
    ui_parser.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    ui_parser.add_argument("--priorities", type=Path, default=Path("priorities.txt"))
    ui_parser.add_argument("--suggestions", type=Path, default=Path("data/suggestions.jsonl"))
    ui_parser.add_argument("--seed", type=int, default=2026, help="same job order as `jobfinder label`")
    ui_parser.add_argument(
        "--prefer", action="append", default=[],
        help="extractor name to use first when several read a job, e.g. gemini:gemini-3.5-flash (repeatable)",
    )
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--no-browser", action="store_true", help="do not open the page in a browser")
    args = parser.parse_args(argv)
    if args.command == "scan":
        return run_scan(args.sources, args.db)
    if args.command == "alerts":
        return run_alerts(args.db, args.env, args.days)
    if args.command == "label":
        return run_label(
            args.db, args.labels, args.target, args.seed, suggestions_path=args.suggestions,
            accept_rest=args.accept_rest,
        )
    if args.command == "filter":
        return run_filter(args.db, args.labels, args.show)
    if args.command == "extract":
        return run_extract(
            args.db, args.env, args.extractor, args.model, args.limit, args.seed, use_cache=not args.no_cache,
            all_kept=args.all,
        )
    if args.command == "eval":
        return run_eval(args.db, args.labels, args.priorities)
    if args.command == "ui":
        return run_ui(
            args.db, args.labels, args.priorities, args.suggestions, args.prefer, args.seed, args.port,
            not args.no_browser,
        )
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())

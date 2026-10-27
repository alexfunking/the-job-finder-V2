"""The interactive `jobfinder label` session: show one job at a time and record fit / not fit and why.

Every answer is appended to the labels file as soon as it is given, so quitting (or a crash) loses nothing.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from jobfinder.labels import Label, Verdict, append_label
from jobfinder.models import Job

PROMPT: Final = "[y] fit  [n] not fit  [s] skip  [o] open in browser  [b] back  [q] quit > "
ANSWERS: Final[dict[str, Verdict]] = {"y": "fit", "n": "no_fit", "s": "skip"}
VERDICT_NAMES: Final[dict[Verdict, str]] = {"fit": "fit", "no_fit": "not fit", "skip": "skipped"}
# Below this many "fit" labels, recall is measured on too few jobs to mean much.
MIN_FIT_LABELS: Final = 15


@dataclass(frozen=True, slots=True)
class Console:
    """Terminal I/O, injected so a session can be driven by a script in tests."""

    read: Callable[[str], str]
    write: Callable[[str], None]
    open_url: Callable[[str], object]
    width: int = 100


def render_job(job: Job, width: int) -> str:
    published = job.published_at.date().isoformat() if job.published_at else "unknown"
    lines = [
        "=" * width,
        f"{job.company} | {job.title}",
        f"Location:  {job.location or '(none)'}",
        f"Published: {published}",
        f"URL:       {job.url}",
        "-" * width,
    ]
    if job.description_text is None:
        lines.append("(No description: this job came from an email alert. Press [o] to read it.)")
    for line in (job.description_text or "").splitlines():
        if not line.strip():
            lines.append("")
            continue
        indent = "  " if line.startswith("- ") else ""
        lines.append(textwrap.fill(line, width, subsequent_indent=indent))
    lines.append("=" * width)
    return "\n".join(lines)


def tally(labels: Mapping[str, Label]) -> dict[Verdict, int]:
    counts: dict[Verdict, int] = {"fit": 0, "no_fit": 0, "skip": 0}
    for label in labels.values():
        counts[label.verdict] += 1
    return counts


def progress_line(labels: Mapping[str, Label], target: int) -> str:
    counts = tally(labels)
    done = counts["fit"] + counts["no_fit"]
    return f"[{done}/{target}]  fit {counts['fit']}  not fit {counts['no_fit']}  skipped {counts['skip']}"


def run_session(
    queue: Sequence[Job],
    labels: MutableMapping[str, Label],
    labels_path: Path,
    target: int,
    console: Console,
    now: Callable[[], datetime],
) -> None:
    """Offer jobs from `queue` until the eval set has `target` fit / not-fit labels or the user quits.

    `labels` is updated in place with every answer, next to the line appended to `labels_path`.
    """
    index = 0
    while index < len(queue):
        counts = tally(labels)
        if counts["fit"] + counts["no_fit"] >= target:
            console.write(f"Reached the target of {target} labels.")
            break
        job = queue[index]
        console.write(render_job(job, console.width))
        previous = labels.get(job.key)
        if previous is not None:
            console.write(f"Currently: {VERDICT_NAMES[previous.verdict]}. {previous.reason}".rstrip())
        console.write(progress_line(labels, target))

        answer = _read(console, PROMPT)
        if answer is None or answer == "q":
            break
        if answer == "o":
            console.open_url(job.url)
            continue
        if answer == "b":
            if index == 0:
                console.write("Nothing to go back to in this session.")
            else:
                index -= 1
            continue
        verdict = ANSWERS.get(answer)
        if verdict is None:
            console.write(f"Unknown answer {answer!r}.")
            continue
        reason = ""
        if verdict != "skip":
            reason_answer = _read_reason(console)
            if reason_answer is None:
                break
            reason = reason_answer
        label = Label(verdict=verdict, reason=reason, labeled_at=now(), job=job)
        append_label(labels_path, label)
        labels[job.key] = label
        index += 1
    else:
        console.write("No more jobs to label in the store.")

    console.write(summary(labels, target, labels_path))


def summary(labels: Mapping[str, Label], target: int, labels_path: Path) -> str:
    counts = tally(labels)
    lines = [f"{progress_line(labels, target)}  saved in {labels_path}"]
    if counts["fit"] + counts["no_fit"] >= target and counts["fit"] < MIN_FIT_LABELS:
        lines.append(
            f"Only {counts['fit']} jobs are labeled fit: recall will be noisy. "
            "Consider labeling more, or raising --target."
        )
    return "\n".join(lines)


def _read(console: Console, prompt: str) -> str | None:
    """One trimmed, lower-cased answer, or None when input ends (Ctrl-Z / Ctrl-D)."""
    try:
        return console.read(prompt).strip().lower()
    except EOFError:
        return None


def _read_reason(console: Console) -> str | None:
    while True:
        try:
            reason = console.read("Why? > ").strip()
        except EOFError:
            return None
        if reason:
            return reason
        console.write("A reason is required: it is what the filter and the priorities are built from.")

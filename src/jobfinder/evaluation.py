"""`jobfinder eval`: how the pipeline's answers compare with the labels.

Each labeled job goes through the same steps as a real one: the pre-filter, then scoring with what one
extractor saved for it. Nothing is sent to a model here, so a job that extractor has not read is scored
from its title. Labels the user accepted in bulk are reported apart from the ones answered one at a time,
because the bulk ones were never looked at individually.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from jobfinder.extraction import Extraction
from jobfinder.extraction.base import input_hash
from jobfinder.filtering import prefilter
from jobfinder.labeling import MIN_FIT_LABELS
from jobfinder.labels import Label
from jobfinder.scoring import Priorities, Verdict, find_word, score_job
from jobfinder.storage import JobStore

PRECISION_GOAL: Final = 0.85
RECALL_GOAL: Final = 0.90
FILTERED: Final = "filtered"
STUDENT_WORDS: Final = ("student", "intern", "internship")


@dataclass(frozen=True, slots=True)
class EvalRow:
    label: Label
    outcome: str  # a scoring Verdict value, or FILTERED when the pre-filter dropped the job
    points: int
    read: bool  # the extractor's saved answer was used
    why: tuple[str, ...]

    @property
    def fit(self) -> bool:
        return self.label.verdict == "fit"

    @property
    def student(self) -> bool:
        return find_word(STUDENT_WORDS, self.label.job.title) is not None


def extractor_names(labels: Mapping[str, Label], store: JobStore) -> list[str]:
    """Every extractor with a saved answer for at least one labeled job."""
    names: set[str] = set()
    for label in labels.values():
        names.update(store.extractions_for(label.key, input_hash(label.job)))
    return sorted(names)


def evaluate(
    labels: Mapping[str, Label], store: JobStore, priorities: Priorities, extractor: str | None
) -> list[EvalRow]:
    """Run every fit / not-fit label through the pipeline; `extractor=None` scores from the titles alone."""
    rows: list[EvalRow] = []
    for label in labels.values():
        if not label.counts:
            continue
        result = prefilter(label.job)
        if not result.kept:
            rows.append(EvalRow(label, FILTERED, 0, False, result.rejections))
            continue
        found = store.extractions_for(label.key, input_hash(label.job)).get(extractor or "")
        extraction = Extraction.model_validate_json(found.fields) if found else None
        score = score_job(label.job, extraction, priorities)
        rows.append(EvalRow(label, score.verdict.value, score.points, found is not None, score.rejections))
    return rows


def _share(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}% ({part}/{whole})" if whole else "n/a (0/0)"


def _goal(part: int, whole: int, goal: float) -> str:
    if not whole:
        return "no data"
    return "meets the goal" if part / whole >= goal else f"under the {goal:.0%} goal"


def render_group(name: str, rows: Sequence[EvalRow]) -> list[str]:
    fit = [row for row in rows if row.fit]
    matched = [row for row in rows if row.outcome == Verdict.MATCH]
    shown = [row for row in fit if row.outcome in (Verdict.MATCH, Verdict.MAYBE)]
    student_fit = [row for row in fit if row.student]
    student_shown = [row for row in student_fit if row.outcome in (Verdict.MATCH, Verdict.MAYBE)]
    right = sum(1 for row in matched if row.fit)
    lines = [
        f"  {name}: {len(rows)} labels, {len(fit)} fit",
        f"    precision of match      {_share(right, len(matched))}  {_goal(right, len(matched), PRECISION_GOAL)}",
        f"    recall, match only      {_share(sum(1 for row in fit if row.outcome == Verdict.MATCH), len(fit))}",
        f"    recall, match or maybe  {_share(len(shown), len(fit))}",
        f"    student recall          {_share(len(student_shown), len(student_fit))}  "
        f"{_goal(len(student_shown), len(student_fit), RECALL_GOAL)}",
    ]
    if len(fit) < MIN_FIT_LABELS:
        lines.append(f"    only {len(fit)} fit labels: {MIN_FIT_LABELS} are needed before recall means much")
    return lines


def render_mistakes(rows: Iterable[EvalRow]) -> list[str]:
    lines: list[str] = []
    for row in sorted(rows, key=lambda row: (row.outcome, -row.points)):
        wrong_match = row.outcome == Verdict.MATCH and not row.fit
        missed = row.fit and row.outcome in (Verdict.NO, FILTERED)
        if not wrong_match and not missed:
            continue
        job = row.label.job
        kind = "matched, not fit" if wrong_match else f"missed ({row.outcome})"
        lines.append(f"  {kind}: {job.company} | {job.title} | {job.location or '(none)'}")
        lines.append(f"    label: {row.label.reason}")
        if row.why:
            lines.append(f"    pipeline: {'; '.join(row.why)}")
    return lines


def render_report(name: str, rows: Sequence[EvalRow]) -> str:
    by_hand = [row for row in rows if not row.label.bulk_accepted]
    title_only = sum(1 for row in rows if row.outcome != FILTERED and not row.read)
    lines = [f"{name}: {title_only} kept jobs scored from the title alone"]
    lines.extend(render_group("all labels", rows))
    if len(by_hand) != len(rows):
        lines.extend(render_group("answered one at a time", by_hand))
    mistakes = render_mistakes(rows)
    if mistakes:
        lines.extend(["", "Mistakes:", *mistakes])
    return "\n".join(lines)

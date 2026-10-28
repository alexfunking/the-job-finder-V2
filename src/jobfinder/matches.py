"""The scored list of open jobs that `jobfinder ui` shows (and the Telegram digest will send).

Each open job the pre-filter keeps is scored against the priorities, using the cached extraction for its
current text when an extractor has read it, and the title alone when none has. Nothing is sent to a
model here: run `jobfinder extract --all` to fill the cache.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from jobfinder.extraction import Extraction
from jobfinder.extraction.base import input_hash
from jobfinder.filtering import prefilter
from jobfinder.labels import Label
from jobfinder.scoring import Priorities, Score, Verdict, score_job
from jobfinder.storage import JobStore, StoredJob

VERDICT_ORDER: dict[Verdict, int] = {Verdict.MATCH: 0, Verdict.MAYBE: 1, Verdict.NO: 2}


@dataclass(frozen=True, slots=True)
class MatchRow:
    stored: StoredJob
    score: Score
    extraction: Extraction | None
    extractor: str | None
    label: Label | None

    def to_json(self) -> dict[str, object]:
        job = self.stored.job
        extraction = self.extraction.model_dump() if self.extraction else None
        return {
            "key": job.key,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "source": job.source,
            "title_only": job.description_text is None,
            "first_seen": self.stored.first_seen.isoformat(),
            "points": self.score.points,
            "verdict": self.score.verdict.value,
            "reasons": list(self.score.reasons),
            "rejections": list(self.score.rejections),
            "extraction": extraction,
            "extractor": self.extractor,
            "label": None if self.label is None else {"verdict": self.label.verdict, "reason": self.label.reason},
        }


def _extraction_for(store: JobStore, stored: StoredJob, prefer: Sequence[str]) -> tuple[str, Extraction] | None:
    found = store.extractions_for(stored.job.key, input_hash(stored.job))
    if not found:
        return None
    name = next((name for name in prefer if name in found), next(iter(found)))
    return name, Extraction.model_validate_json(found[name].fields)


def match_rows(
    store: JobStore,
    priorities: Priorities,
    labels: Mapping[str, Label],
    prefer: Sequence[str] = (),
) -> list[MatchRow]:
    """Every open job the pre-filter keeps, best first: matches, then maybes, then the rest, by score.

    `prefer` lists extractor names to use first when several have read a job.
    """
    rows: list[MatchRow] = []
    for stored in store.open_jobs():
        if not prefilter(stored.job).kept:
            continue
        found = _extraction_for(store, stored, prefer)
        extractor, extraction = found if found else (None, None)
        rows.append(MatchRow(
            stored=stored,
            score=score_job(stored.job, extraction, priorities),
            extraction=extraction,
            extractor=extractor,
            label=labels.get(stored.job.key),
        ))
    rows.sort(key=lambda row: (VERDICT_ORDER[row.score.verdict], -row.score.points, _newest_first(row.stored)))
    return rows


def _newest_first(stored: StoredJob) -> float:
    return -stored.first_seen.timestamp()


def summary_counts(rows: Sequence[MatchRow]) -> dict[str, int]:
    counts = {verdict.value: 0 for verdict in Verdict}
    for row in rows:
        counts[row.score.verdict.value] += 1
    return counts


def generated_at(now: datetime) -> str:
    return now.astimezone().strftime("%Y-%m-%d %H:%M")

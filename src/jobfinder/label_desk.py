"""The labeling screen of `jobfinder ui`: the same eval set as `jobfinder label`, with buttons.

Labels go to the same append-only file with the same snapshot of the job, and a job with a suggestion
records what was suggested, so the terminal tool and this screen can be mixed freely.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from jobfinder.labeling import MIN_FIT_LABELS, tally
from jobfinder.labels import Label, Suggestion, append_label, load_labels, load_suggestions
from jobfinder.models import Job
from jobfinder.sampling import review_queue
from jobfinder.storage import JobStore

# Enough for a long session; a reload continues from the labels already saved.
MAX_ITEMS: Final = 200


class LabelError(ValueError):
    """The answer cannot be saved; the message is fit to show the user."""


def progress(labels: Mapping[str, Label]) -> dict[str, int]:
    counts = tally(labels)
    reviewed = [label for label in labels.values() if label.suggestion is not None and label.counts]
    changed = sum(1 for label in reviewed if label.overrode_suggestion)
    return {
        "done": counts["fit"] + counts["no_fit"],
        "fit": counts["fit"],
        "no_fit": counts["no_fit"],
        "skip": counts["skip"],
        "accepted": len(reviewed) - changed,
        "changed": changed,
        "min_fit": MIN_FIT_LABELS,
    }


def _item(job: Job, suggestion: Suggestion | None) -> dict[str, object]:
    return {
        "key": job.key,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "description": job.description_text,
        "suggestion": None if suggestion is None else {"verdict": suggestion.verdict, "reason": suggestion.reason},
    }


class LabelDesk:
    def __init__(
        self, db_path: Path, labels_path: Path, suggestions_path: Path, seed: int, now: Callable[[], datetime]
    ) -> None:
        self._db_path = db_path
        self._labels_path = labels_path
        self._suggestions_path = suggestions_path
        self._seed = seed
        self._now = now
        self._lock = threading.Lock()  # the server is threaded, and each answer appends to one file

    def state(self) -> dict[str, object]:
        """The next jobs to label, in the order `jobfinder label` offers them, and the progress so far."""
        labels = load_labels(self._labels_path)
        suggestions = load_suggestions(self._suggestions_path)
        store = JobStore.open(self._db_path)
        try:
            candidates = [stored.job for stored in store.open_jobs()]
        finally:
            store.close()
        queue = review_queue(candidates, labels, suggestions, self._seed)
        return {
            "items": [_item(job, suggestions.get(job.key)) for job in queue[:MAX_ITEMS]],
            "progress": progress(labels),
        }

    def record(self, key: str, verdict: str, reason: str) -> dict[str, int]:
        """Save one answer and return the new progress. Answering a job again replaces the earlier answer."""
        with self._lock:
            labels = load_labels(self._labels_path)
            suggestion = load_suggestions(self._suggestions_path).get(key)
            store = JobStore.open(self._db_path)
            try:
                stored = store.get(key)
            finally:
                store.close()
            if stored is None:
                raise LabelError(f"no job with key {key!r}")
            reason = reason.strip()
            if not reason and suggestion is not None and suggestion.verdict == verdict:
                reason = suggestion.reason
            try:
                label = Label.model_validate({
                    "verdict": verdict,
                    "reason": reason,
                    "labeled_at": self._now(),
                    "job": stored.job,
                    "suggestion": suggestion.verdict if suggestion else None,
                })
            except ValidationError as exc:
                detail = exc.errors()[0]["msg"].removeprefix("Value error, ")
                raise LabelError(detail) from exc
            append_label(self._labels_path, label)
            labels[key] = label
            return progress(labels)

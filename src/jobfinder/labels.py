"""Hand labels for the eval set, kept in an append-only JSONL file.

Each label carries a full snapshot of the job, so the eval set stays usable after the job closes or the
database is rebuilt. Relabeling appends a new line; when loading, the last line for a key wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, model_validator

from jobfinder.models import Job

Verdict = Literal["fit", "no_fit", "skip"]


class Label(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    verdict: Verdict
    reason: str
    labeled_at: AwareDatetime
    job: Job

    @model_validator(mode="after")
    def _reason_required(self) -> Label:
        # The "why" is half the point of the eval set: it is what the filter and priorities are built from.
        if self.verdict != "skip" and not self.reason:
            raise ValueError(f"a {self.verdict} label needs a reason")
        return self

    @property
    def key(self) -> str:
        return self.job.key

    @property
    def counts(self) -> bool:
        """Whether this label is part of the eval set (skips are only remembered so they are not shown again)."""
        return self.verdict != "skip"


def load_labels(path: Path) -> dict[str, Label]:
    """All labels by job key, the latest one for each key; a missing file means no labels yet."""
    if not path.exists():
        return {}
    labels: dict[str, Label] = {}
    with path.open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                label = Label.model_validate_json(line)
            except ValidationError as exc:
                raise ValueError(f"{path}:{number}: invalid label: {exc}") from exc
            labels[label.key] = label
    return labels


def append_label(path: Path, label: Label) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(label.model_dump_json() + "\n")

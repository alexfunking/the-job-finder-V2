from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobfinder.labels import Label, append_label, load_labels
from jobfinder.models import Job

T1 = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def job(job_id: str = "1", **overrides: str) -> Job:
    fields = {
        "source": "greenhouse",
        "slug": "acme",
        "job_id": job_id,
        "company": "Acme",
        "title": "Backend Engineer",
        "location": "Tel Aviv, Israel",
        "url": f"https://example.test/acme/{job_id}",
        "description_text": "Build things in Python. ניסיון בפייתון",
    } | overrides
    return Job.model_validate(fields)


def label(verdict: str = "fit", reason: str = "Python, Tel Aviv", *, j: Job | None = None, at: datetime = T1) -> Label:
    return Label.model_validate({"verdict": verdict, "reason": reason, "labeled_at": at, "job": j or job()})


def test_missing_file_means_no_labels(tmp_path: Path) -> None:
    assert load_labels(tmp_path / "labels.jsonl") == {}


def test_round_trip_keeps_the_full_job_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "data" / "labels.jsonl"
    original = label()
    append_label(path, original)
    loaded = load_labels(path)
    assert loaded == {original.key: original}
    assert loaded[original.key].job.description_text.endswith("ניסיון בפייתון")


def test_latest_label_for_a_key_wins(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    append_label(path, label("fit", "first look"))
    append_label(path, label("no_fit", "needs 5 years", at=T1 + timedelta(minutes=1)))
    append_label(path, label("fit", "other job", j=job("2")))
    loaded = load_labels(path)
    assert {key: lbl.verdict for key, lbl in loaded.items()} == {
        "greenhouse:acme:1": "no_fit",
        "greenhouse:acme:2": "fit",
    }


def test_fit_and_no_fit_need_a_reason_but_skip_does_not() -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        label("fit", "  ")
    with pytest.raises(ValidationError, match="needs a reason"):
        label("no_fit", "")
    assert label("skip", "").counts is False
    assert label("no_fit", "onsite 5 days").counts is True


def test_unknown_verdict_is_rejected() -> None:
    with pytest.raises(ValidationError):
        label("maybe")


def test_corrupt_line_is_reported_with_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    append_label(path, label())
    with path.open("a", encoding="utf-8") as out:
        out.write("\n{not json\n")
    with pytest.raises(ValueError, match=r"labels\.jsonl:3: invalid label"):
        load_labels(path)

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfinder.cli import EXIT_CONFIG_ERROR, EXIT_OK, run_filter
from jobfinder.labels import Label, append_label
from jobfinder.models import Job
from jobfinder.storage import JobStore

T1 = datetime(2026, 10, 28, tzinfo=UTC)


def job(job_id: str, title: str, location: str = "Tel Aviv") -> Job:
    return Job(
        source="greenhouse",
        slug="acme",
        job_id=job_id,
        company="Acme",
        title=title,
        location=location,
        url=f"https://example.test/{job_id}",
        description_text="Build things.",
    )


JOBS = [
    job("1", "Backend Engineer"),
    job("2", "Senior Backend Engineer"),
    job("3", "Backend Engineer", "London"),
    job("4", "Support Engineer", "Remote"),
]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    store = JobStore.open(path)
    store.record_board("greenhouse", "acme", JOBS, T1)
    store.close()
    return path


def label(j: Job, verdict: str, reason: str = "r") -> Label:
    return Label.model_validate({"verdict": verdict, "reason": reason, "labeled_at": T1, "job": j})


def test_summary_counts_kept_and_rejected(db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_filter(db, tmp_path / "labels.jsonl", show=None) == EXIT_OK
    out = capsys.readouterr().out
    assert "Open jobs: 4" in out
    assert "Kept:      2 (1 with a location that names no place)" in out
    assert "Rejected:  2 (location 1, senior title 1" in out
    assert "No fit / not-fit labels yet" in out


def test_label_check_lists_fit_jobs_the_filter_rejects(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = tmp_path / "labels.jsonl"
    append_label(labels, label(JOBS[0], "fit"))
    append_label(labels, label(JOBS[1], "fit", "senior but small team, worth it"))
    append_label(labels, label(JOBS[2], "no_fit"))
    append_label(labels, label(JOBS[3], "skip", ""))
    run_filter(db, labels, show=None)
    out = capsys.readouterr().out
    assert "Against 3 labels:" in out
    assert "fit, kept         1" in out
    assert "fit, rejected     1  <- filter mistakes" in out
    assert "not fit, rejected 1" in out
    assert "Senior Backend Engineer | Tel Aviv  [senior title: 'Senior']  you said: senior but small team" in out


@pytest.mark.parametrize(
    ("show", "listed", "not_listed"),
    [
        ("kept", ["Support Engineer"], ["Senior Backend Engineer"]),
        ("rejected", ["Senior Backend Engineer", "London"], ["Support Engineer"]),
        ("unplaced", ["Support Engineer"], ["| Backend Engineer | Tel Aviv"]),
    ],
)
def test_show_lists_the_chosen_jobs(
    db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], show: str, listed: list[str], not_listed: list[str]
) -> None:
    run_filter(db, tmp_path / "labels.jsonl", show=show)
    listing = capsys.readouterr().out.split(f"{show.capitalize()} jobs:")[1]
    assert all(text in listing for text in listed)
    assert not any(text in listing for text in not_listed)


def test_missing_database_is_a_config_error(tmp_path: Path) -> None:
    assert run_filter(tmp_path / "nope.db", tmp_path / "labels.jsonl", show=None) == EXIT_CONFIG_ERROR

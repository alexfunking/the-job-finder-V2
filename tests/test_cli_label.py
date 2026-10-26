from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfinder.cli import EXIT_CONFIG_ERROR, EXIT_OK, run_label
from jobfinder.labeling import Console
from jobfinder.labels import load_labels
from jobfinder.models import Job
from jobfinder.storage import JobStore


def make_db(path: Path, count: int) -> None:
    jobs = [
        Job(
            source="greenhouse",
            slug="acme",
            job_id=str(i),
            company="Acme",
            title="Engineer",
            location="Tel Aviv",
            url=f"https://example.test/{i}",
            description_text="Build things.",
        )
        for i in range(count)
    ]
    store = JobStore.open(path)
    store.record_board("greenhouse", "acme", jobs, datetime(2026, 10, 26, tzinfo=UTC))
    store.close()


def console(answers: list[str]) -> Console:
    remaining = iter(answers)

    def read(prompt: str) -> str:
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError from None

    return Console(read=read, write=lambda text: None, open_url=lambda url: None)


def test_missing_database_is_a_config_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_label(tmp_path / "jobs.db", tmp_path / "labels.jsonl", 80, 1, console([])) == EXIT_CONFIG_ERROR
    assert "run `jobfinder scan` first" in capsys.readouterr().err
    assert not (tmp_path / "jobs.db").exists()


def test_corrupt_labels_file_is_a_config_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_db(tmp_path / "jobs.db", 1)
    (tmp_path / "labels.jsonl").write_text("{oops\n", encoding="utf-8")
    assert run_label(tmp_path / "jobs.db", tmp_path / "labels.jsonl", 80, 1, console([])) == EXIT_CONFIG_ERROR
    assert "labels.jsonl:1: invalid label" in capsys.readouterr().err


def test_a_second_session_continues_with_unlabeled_jobs(tmp_path: Path) -> None:
    db, labels_path = tmp_path / "jobs.db", tmp_path / "labels.jsonl"
    make_db(db, 3)
    assert run_label(db, labels_path, 80, 1, console(["y", "fits", "q"])) == EXIT_OK
    assert run_label(db, labels_path, 80, 1, console(["n", "no", "n", "no"])) == EXIT_OK
    labels = load_labels(labels_path)
    assert len(labels) == 3
    assert sorted(label.verdict for label in labels.values()) == ["fit", "no_fit", "no_fit"]

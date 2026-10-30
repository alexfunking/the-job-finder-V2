from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfinder.cli import run_eval, run_label
from jobfinder.evaluation import FILTERED, EvalRow, evaluate, extractor_names, render_report
from jobfinder.extraction import Extraction
from jobfinder.extraction.base import input_hash
from jobfinder.labeling import accept_suggestions
from jobfinder.labels import Label, Suggestion, Verdict, append_label, load_labels
from jobfinder.models import Job
from jobfinder.scoring import Priorities, load_priorities
from jobfinder.storage import JobStore

T1 = datetime(2026, 10, 30, 8, tzinfo=UTC)
EXTRACTOR = "ollama:test"
PRIORITIES_TOML = """
[profile]
skills = ["Python"]

[role]
max_years = 1
entry_title_words = ["student", "junior"]
field_title_words = ["data", "software"]
reject_title_words = ["sales"]

[location]
accept = ["Tel Aviv"]
low_priority = ["Netanya"]
reject = ["Haifa"]

[onsite]
preferred_max_days = 3

[score]
match_threshold = 50
"""


def job(job_id: str, title: str, location: str = "Tel Aviv") -> Job:
    return Job(
        source="greenhouse", slug="acme", job_id=job_id, company="Acme", title=title, location=location,
        url=f"https://example.test/{job_id}", description_text="Build things.",
    )


def label(a_job: Job, verdict: Verdict, bulk: bool = False) -> Label:
    return Label(verdict=verdict, reason="because", labeled_at=T1, job=a_job, bulk_accepted=bulk)


@pytest.fixture
def priorities(tmp_path: Path) -> Priorities:
    path = tmp_path / "priorities.txt"
    path.write_text(PRIORITIES_TOML, encoding="utf-8")
    return load_priorities(path)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore.open(tmp_path / "jobs.db")


def outcomes(rows: list[EvalRow]) -> dict[str, str]:
    return {row.label.job.job_id: row.outcome for row in rows}


def test_each_label_gets_the_outcome_of_the_real_pipeline(store: JobStore, priorities: Priorities) -> None:
    labels = [
        label(job("1", "Junior Software Engineer"), "fit"),
        label(job("2", "Software Engineer"), "no_fit"),
        label(job("3", "Senior Software Engineer"), "no_fit"),
        label(job("4", "Junior Sales Rep"), "no_fit"),
    ]
    rows = evaluate({item.key: item for item in labels}, store, priorities, None)
    assert outcomes(rows) == {"1": "match", "2": "maybe", "3": FILTERED, "4": "no"}


def test_skips_are_left_out(store: JobStore, priorities: Priorities) -> None:
    skipped = Label(verdict="skip", reason="", labeled_at=T1, job=job("1", "Junior Software Engineer"))
    assert evaluate({skipped.key: skipped}, store, priorities, None) == []


def test_only_the_named_extractors_answer_is_used(store: JobStore, priorities: Priorities) -> None:
    a_job = job("1", "Software Engineer")
    store.record_board("greenhouse", "acme", [a_job], T1)
    answer = Extraction(years_required=1, onsite_days=2, is_student=False, location=None, stack=())
    store.save_extraction(a_job.key, EXTRACTOR, input_hash(a_job), answer.model_dump_json(), 1, 1, T1)
    labels = {a_job.key: label(a_job, "fit")}

    assert extractor_names(labels, store) == [EXTRACTOR]
    read = evaluate(labels, store, priorities, EXTRACTOR)[0]
    assert (read.outcome, read.read) == ("match", True)
    unread = evaluate(labels, store, priorities, "gemini:test")[0]
    assert (unread.outcome, unread.read) == ("maybe", False)


def test_report_gives_precision_recall_and_mistakes(store: JobStore, priorities: Priorities) -> None:
    labels = [
        label(job("1", "Junior Software Engineer"), "fit"),
        label(job("2", "Junior Data Analyst"), "no_fit"),
        label(job("3", "Data Student", "Haifa"), "fit"),
    ]
    report = render_report("titles alone", evaluate({item.key: item for item in labels}, store, priorities, None))
    assert "precision of match      50% (1/2)  under the 85% goal" in report
    assert "recall, match or maybe  50% (1/2)" in report
    assert "student recall          0% (0/1)  under the 90% goal" in report
    assert "only 2 fit labels" in report
    assert "matched, not fit: Acme | Junior Data Analyst" in report
    assert "missed (no): Acme | Data Student" in report


def test_report_separates_bulk_labels(store: JobStore, priorities: Priorities) -> None:
    labels = [
        label(job("1", "Junior Software Engineer"), "fit"),
        label(job("2", "Junior Data Analyst"), "fit", bulk=True),
    ]
    report = render_report("titles alone", evaluate({item.key: item for item in labels}, store, priorities, None))
    assert "all labels: 2 labels, 2 fit" in report
    assert "answered one at a time: 1 labels, 1 fit" in report


def test_accept_suggestions_keeps_answers_and_marks_the_rest(tmp_path: Path) -> None:
    answered, open_job = job("1", "A"), job("2", "B")
    path = tmp_path / "labels.jsonl"
    mine = label(answered, "fit")
    append_label(path, mine)
    labels = {mine.key: mine}
    suggestions = {
        key: Suggestion(key=key, verdict="no_fit", reason="suggested")
        for key in (answered.key, open_job.key, "greenhouse:acme:gone")
    }
    jobs = {answered.key: answered, open_job.key: open_job}

    assert accept_suggestions(suggestions, labels, jobs, path, lambda: T1) == 1

    saved = load_labels(path)
    assert saved[answered.key].verdict == "fit"
    assert not saved[answered.key].bulk_accepted
    added = saved[open_job.key]
    assert (added.verdict, added.reason, added.suggestion, added.bulk_accepted) == (
        "no_fit", "suggested", "no_fit", True,
    )


def test_labels_saved_before_the_bulk_field_still_load(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    append_label(path, label(job("1", "A"), "fit"))
    text = path.read_text(encoding="utf-8").replace(',"bulk_accepted":false', "")
    path.write_text(text, encoding="utf-8")
    assert not next(iter(load_labels(path).values())).bulk_accepted


def test_cli_accept_rest_then_eval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db, labels_path = tmp_path / "jobs.db", tmp_path / "labels.jsonl"
    a_job = job("1", "Junior Software Engineer")
    store = JobStore.open(db)
    store.record_board("greenhouse", "acme", [a_job], T1)
    store.close()
    suggestions = tmp_path / "suggestions.jsonl"
    line = Suggestion(key=a_job.key, verdict="fit", reason="junior").model_dump_json()
    suggestions.write_text(line + "\n", encoding="utf-8")
    priorities = tmp_path / "priorities.txt"
    priorities.write_text(PRIORITIES_TOML, encoding="utf-8")

    assert run_label(db, labels_path, 80, 1, suggestions_path=suggestions, accept_rest=True) == 0
    assert run_eval(db, labels_path, priorities) == 0
    out = capsys.readouterr().out
    assert "Accepted 1 suggestions in bulk" in out
    assert "titles alone: 0 kept jobs" not in out
    assert "precision of match      100% (1/1)" in out


def test_cli_eval_without_labels_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "jobs.db"
    JobStore.open(db).close()
    priorities = tmp_path / "priorities.txt"
    priorities.write_text(PRIORITIES_TOML, encoding="utf-8")
    assert run_eval(db, tmp_path / "labels.jsonl", priorities) == 0
    assert "No fit / not-fit labels yet" in capsys.readouterr().out

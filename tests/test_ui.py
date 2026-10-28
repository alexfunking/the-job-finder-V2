from __future__ import annotations

import json
import threading
import urllib.request
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from test_scoring import PRIORITIES_TOML

from jobfinder.extraction import Extraction
from jobfinder.extraction.base import input_hash
from jobfinder.labels import Label, append_label, load_labels
from jobfinder.matches import match_rows
from jobfinder.models import Job
from jobfinder.scoring import load_priorities
from jobfinder.storage import JobStore
from jobfinder.ui import build_payload, make_handler, render_page

T1 = datetime(2026, 10, 28, 8, tzinfo=UTC)


def job(job_id: str, title: str, location: str = "Tel Aviv") -> Job:
    return Job(
        source="greenhouse", slug="acme", job_id=job_id, company="Acme", title=title, location=location,
        url=f"https://example.test/{job_id}", description_text=f"About {title}.",
    )


JOBS = [
    job("1", "Junior Software Engineer"),
    job("2", "Data Engineer"),  # level unknown until an extractor reads it
    job("3", "Junior QA Engineer"),  # unwanted field
    job("4", "Senior Backend Engineer"),  # dropped by the pre-filter, never shown
]


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    db, labels, priorities = tmp_path / "jobs.db", tmp_path / "labels.jsonl", tmp_path / "priorities.txt"
    priorities.write_text(PRIORITIES_TOML, encoding="utf-8")
    store = JobStore.open(db)
    store.record_board("greenhouse", "acme", JOBS, T1)
    store.close()
    return db, labels, priorities


def test_rows_are_kept_jobs_ordered_match_maybe_no(files: tuple[Path, Path, Path]) -> None:
    db, labels, priorities = files
    store = JobStore.open(db)
    rows = match_rows(store, load_priorities(priorities), load_labels(labels))
    store.close()
    assert [(row.stored.job.job_id, row.score.verdict.value) for row in rows] == [
        ("1", "match"), ("2", "maybe"), ("3", "no"),
    ]


def test_a_cached_extraction_for_the_current_text_is_used(files: tuple[Path, Path, Path]) -> None:
    db, labels, priorities = files
    store = JobStore.open(db)
    answer = Extraction(years_required=0, onsite_days=2, is_student=False, location=None, stack=("Python",))
    store.save_extraction("greenhouse:acme:2", "ollama:m", input_hash(JOBS[1]), answer.model_dump_json(), 1, 1, T1)
    store.save_extraction("greenhouse:acme:1", "ollama:m", "stale-hash", answer.model_dump_json(), 1, 1, T1)
    rows = {row.stored.job.job_id: row for row in match_rows(store, load_priorities(priorities), {})}
    store.close()
    assert rows["2"].score.verdict.value == "match"
    assert rows["2"].extractor == "ollama:m"
    assert rows["1"].extraction is None  # saved for other text: not used


def test_payload_counts_and_includes_labels(files: tuple[Path, Path, Path]) -> None:
    db, labels, priorities = files
    append_label(labels, Label(verdict="fit", reason="junior, Tel Aviv", labeled_at=T1, job=JOBS[0]))
    payload = build_payload(db, labels, priorities, (), T1)
    assert payload["counts"] == {"match": 1, "maybe": 1, "no": 1}
    rows = payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["label"] == {"verdict": "fit", "reason": "junior, Tel Aviv"}


def test_page_embeds_data_without_ending_the_script_early() -> None:
    page = render_page({"rows": [{"title": "</script><script>alert(1)</script>"}], "counts": {}})
    assert "</script><script>alert(1)" not in page
    assert "<\\/script>" in page
    assert "__DATA__" not in page


def test_server_answers_page_and_json_on_localhost(files: tuple[Path, Path, Path]) -> None:
    db, labels, priorities = files
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(lambda: build_payload(db, labels, priorities, (), T1)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(f"{base}/", timeout=5) as response:
            assert "Job Matches" in response.read().decode("utf-8")
        with urllib.request.urlopen(f"{base}/api/matches", timeout=5) as response:
            assert json.loads(response.read())["counts"]["match"] == 1
        with pytest.raises(urllib.error.HTTPError, match="404"):
            urllib.request.urlopen(f"{base}/other", timeout=5)
    finally:
        server.shutdown()
        server.server_close()

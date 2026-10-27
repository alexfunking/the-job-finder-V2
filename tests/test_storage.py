from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobfinder.models import Job
from jobfinder.storage import SCHEMA_VERSION, BoardUpdate, JobStore

T1 = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
T2 = T1 + timedelta(days=1)
T3 = T2 + timedelta(days=1)


def job(job_id: str, *, slug: str = "acme", source: str = "greenhouse", title: str = "Engineer") -> Job:
    return Job(
        source=source,
        slug=slug,
        job_id=job_id,
        company="Acme",
        title=title,
        location="Tel Aviv",
        url=f"https://example.test/{slug}/{job_id}",
        description_text="Build things.",
        description_html="<p>Build things.</p>",
        published_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone(timedelta(hours=-4))),
    )


@pytest.fixture
def store() -> Iterator[JobStore]:
    s = JobStore.open(":memory:")
    yield s
    s.close()


def test_first_scan_inserts_everything_as_new(store: JobStore) -> None:
    update = store.record_board("greenhouse", "acme", [job("1"), job("2")], T1)
    assert update == BoardUpdate(new=2, seen_again=0, reopened=0, closed=0)
    stored = store.get("greenhouse:acme:1")
    assert stored is not None
    assert (stored.first_seen, stored.last_seen, stored.closed_at) == (T1, T1, None)


def test_round_trip_preserves_every_field(store: JobStore) -> None:
    original = job("1")
    store.record_board("greenhouse", "acme", [original], T1)
    stored = store.get(original.key)
    assert stored is not None and stored.job == original  # published_at is stored in UTC, same instant


def test_second_scan_updates_last_seen_only(store: JobStore) -> None:
    store.record_board("greenhouse", "acme", [job("1")], T1)
    update = store.record_board("greenhouse", "acme", [job("1")], T2)
    assert update == BoardUpdate(new=0, seen_again=1, reopened=0, closed=0)
    stored = store.get("greenhouse:acme:1")
    assert stored is not None and (stored.first_seen, stored.last_seen) == (T1, T2)


def test_job_missing_from_a_successful_fetch_is_closed(store: JobStore) -> None:
    store.record_board("greenhouse", "acme", [job("1"), job("2")], T1)
    update = store.record_board("greenhouse", "acme", [job("1")], T2)
    assert update == BoardUpdate(new=0, seen_again=1, reopened=0, closed=1)
    closed = store.get("greenhouse:acme:2")
    assert closed is not None and closed.closed_at == T2
    assert [s.job.job_id for s in store.open_jobs()] == ["1"]


def test_closed_job_that_returns_is_reopened_with_original_first_seen(store: JobStore) -> None:
    store.record_board("greenhouse", "acme", [job("1")], T1)
    store.record_board("greenhouse", "acme", [], T2)
    update = store.record_board("greenhouse", "acme", [job("1")], T3)
    assert update == BoardUpdate(new=0, seen_again=0, reopened=1, closed=0)
    stored = store.get("greenhouse:acme:1")
    assert stored is not None and (stored.first_seen, stored.last_seen, stored.closed_at) == (T1, T3, None)


def test_edited_job_is_updated_in_place(store: JobStore) -> None:
    store.record_board("greenhouse", "acme", [job("1")], T1)
    store.record_board("greenhouse", "acme", [job("1", title="Senior Engineer")], T2)
    stored = store.get("greenhouse:acme:1")
    assert stored is not None and stored.job.title == "Senior Engineer"
    assert store.count() == 1


def test_recording_one_board_never_touches_another(store: JobStore) -> None:
    # ADR-005: if a board's fetch failed, the scan simply does not record it, and its jobs stay open.
    store.record_board("greenhouse", "acme", [job("1")], T1)
    store.record_board("greenhouse", "other", [job("9", slug="other")], T1)
    store.record_board("greenhouse", "acme", [], T2)
    other = store.get("greenhouse:other:9")
    assert other is not None and other.closed_at is None


def test_same_slug_on_different_sources_is_a_different_board(store: JobStore) -> None:
    store.record_board("greenhouse", "acme", [job("1")], T1)
    store.record_board("ashby", "acme", [], T1)
    assert store.count(include_closed=False) == 1


def test_ten_identical_scans_leave_no_duplicates(store: JobStore) -> None:
    for day in range(10):
        store.record_board("greenhouse", "acme", [job("1"), job("2"), job("3")], T1 + timedelta(days=day))
    assert store.count() == 3


def test_duplicate_keys_in_one_batch_are_stored_once(store: JobStore) -> None:
    update = store.record_board("greenhouse", "acme", [job("1"), job("1", title="Later copy")], T1)
    assert update.new == 1
    stored = store.get("greenhouse:acme:1")
    assert stored is not None and stored.job.title == "Later copy"


def test_rejects_jobs_from_another_board(store: JobStore) -> None:
    with pytest.raises(ValueError, match="another board"):
        store.record_board("greenhouse", "acme", [job("1", slug="other")], T1)
    assert store.count() == 0


def test_rejects_naive_timestamps(store: JobStore) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        store.record_board("greenhouse", "acme", [job("1")], datetime(2026, 10, 26, 8, 0))


def test_data_survives_reopening_a_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "jobs.db"
    first = JobStore.open(path)
    first.record_board("greenhouse", "acme", [job("1")], T1)
    first.close()
    second = JobStore.open(path)
    assert second.count() == 1
    second.close()


def test_refuses_a_database_from_a_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.close()
    with pytest.raises(RuntimeError, match="newer than this code"):
        JobStore.open(path)


def test_v1_database_is_migrated_keeping_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "v1.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE jobs (
            key TEXT PRIMARY KEY, source TEXT NOT NULL, slug TEXT NOT NULL, job_id TEXT NOT NULL,
            company TEXT NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL, url TEXT NOT NULL,
            description_text TEXT NOT NULL, description_html TEXT, published_at TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, closed_at TEXT
        );
        CREATE INDEX jobs_board ON jobs (source, slug);
        INSERT INTO jobs VALUES ('greenhouse:acme:1', 'greenhouse', 'acme', '1', 'Acme', 'Engineer', 'Tel Aviv',
            'https://example.test/acme/1', 'Build things.', NULL, NULL,
            '2026-10-26T08:00:00+00:00', '2026-10-27T08:00:00+00:00', NULL);
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    store = JobStore.open(path)
    stored = store.get("greenhouse:acme:1")
    assert stored is not None and stored.job.description_text == "Build things."
    assert stored.first_seen == T1
    store.record_sightings([job("2").model_copy(update={"description_text": None})], T2)
    assert store.count() == 2
    store.close()
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2


def alert_job(job_id: str) -> Job:
    return job(job_id, slug="alerts", source="linkedin").model_copy(update={"description_text": None})


def test_sightings_insert_and_report_only_new_jobs(store: JobStore) -> None:
    assert store.record_sightings([alert_job("1"), alert_job("2")], T1) == 2
    assert store.record_sightings([alert_job("2"), alert_job("3")], T2) == 1
    assert store.count(include_closed=False) == 3
    stored = store.get("linkedin:alerts:1")
    assert stored is not None and stored.job.description_text is None


def test_sightings_never_close_jobs_missing_from_the_alert(store: JobStore) -> None:
    store.record_sightings([alert_job("1")], T1)
    store.record_sightings([alert_job("2")], T2)
    assert {s.job.key for s in store.open_jobs()} == {"linkedin:alerts:1", "linkedin:alerts:2"}


def test_sightings_read_out_of_order_keep_the_widest_seen_range(store: JobStore) -> None:
    store.record_sightings([alert_job("1")], T2)
    store.record_sightings([alert_job("1")], T1)
    store.record_sightings([alert_job("1")], T3)
    stored = store.get("linkedin:alerts:1")
    assert stored is not None
    assert (stored.first_seen, stored.last_seen) == (T1, T3)


def test_no_sightings_is_a_no_op(store: JobStore) -> None:
    assert store.record_sightings([], T1) == 0

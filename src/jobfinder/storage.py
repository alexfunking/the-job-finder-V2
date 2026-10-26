"""SQLite storage for collected jobs: one row per job key, with when it was first seen, last seen and closed."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from jobfinder.models import Job

SCHEMA_VERSION: Final = 1

_SCHEMA: Final = """
CREATE TABLE jobs (
    key              TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    slug             TEXT NOT NULL,
    job_id           TEXT NOT NULL,
    company          TEXT NOT NULL,
    title            TEXT NOT NULL,
    location         TEXT NOT NULL,
    url              TEXT NOT NULL,
    description_text TEXT NOT NULL,
    description_html TEXT,
    published_at     TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    closed_at        TEXT
);
CREATE INDEX jobs_board ON jobs (source, slug);
"""

_JOB_COLUMNS: Final = (
    "source", "slug", "job_id", "company", "title", "location", "url",
    "description_text", "description_html", "published_at",
)


@dataclass(frozen=True, slots=True)
class StoredJob:
    job: Job
    first_seen: datetime
    last_seen: datetime
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class BoardUpdate:
    """What one successful fetch of a board changed in the store."""

    new: int
    seen_again: int
    reopened: int
    closed: int


def _to_db_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _from_db_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


class JobStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._db.row_factory = sqlite3.Row
        self._migrate()

    @classmethod
    def open(cls, path: Path | str) -> JobStore:
        """Open (and create if needed) the store; ":memory:" gives a throwaway store for tests."""
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(path))

    def close(self) -> None:
        self._db.close()

    def _migrate(self) -> None:
        version: int = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            raise RuntimeError(f"database schema v{version} is newer than this code (v{SCHEMA_VERSION})")
        with self._db:
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def record_board(self, source: str, slug: str, jobs: Sequence[Job], seen_at: datetime) -> BoardUpdate:
        """Store the complete set of open jobs from one board that was fetched successfully.

        Jobs on this board that are open in the store but absent from `jobs` are closed. Call this only
        for boards whose fetch succeeded: a failed fetch must not be passed as an empty list (ADR-005).
        Runs in one transaction, so a crash never leaves a board half-updated.
        """
        stray = [job.key for job in jobs if (job.source, job.slug) != (source, slug)]
        if stray:
            raise ValueError(f"jobs from another board passed for {source}:{slug}: {stray[:3]}")
        now = _to_db_time(seen_at)
        by_key = {job.key: job for job in jobs}

        with self._db:
            existing = {
                row["key"]: row["closed_at"]
                for row in self._db.execute(
                    "SELECT key, closed_at FROM jobs WHERE source = ? AND slug = ?", (source, slug)
                )
            }
            new = [key for key in by_key if key not in existing]
            reopened = [key for key in by_key if key in existing and existing[key] is not None]
            to_close = [key for key, closed in existing.items() if closed is None and key not in by_key]

            self._db.executemany(
                f"""
                INSERT INTO jobs (key, {", ".join(_JOB_COLUMNS)}, first_seen, last_seen, closed_at)
                VALUES (?, {", ".join("?" for _ in _JOB_COLUMNS)}, ?, ?, NULL)
                ON CONFLICT (key) DO UPDATE SET
                    {", ".join(f"{column} = excluded.{column}" for column in _JOB_COLUMNS)},
                    last_seen = excluded.last_seen,
                    closed_at = NULL
                """,
                [(key, *self._job_values(job), now, now) for key, job in by_key.items()],
            )
            self._db.executemany(
                "UPDATE jobs SET closed_at = ? WHERE key = ?", [(now, key) for key in to_close]
            )

        return BoardUpdate(
            new=len(new),
            seen_again=len(by_key) - len(new) - len(reopened),
            reopened=len(reopened),
            closed=len(to_close),
        )

    def get(self, key: str) -> StoredJob | None:
        row = self._db.execute("SELECT * FROM jobs WHERE key = ?", (key,)).fetchone()
        return None if row is None else self._to_stored(row)

    def open_jobs(self) -> list[StoredJob]:
        rows = self._db.execute("SELECT * FROM jobs WHERE closed_at IS NULL ORDER BY key")
        return [self._to_stored(row) for row in rows]

    def count(self, *, include_closed: bool = True) -> int:
        where = "" if include_closed else " WHERE closed_at IS NULL"
        result: int = self._db.execute(f"SELECT COUNT(*) FROM jobs{where}").fetchone()[0]
        return result

    @staticmethod
    def _job_values(job: Job) -> tuple[str | None, ...]:
        published = _to_db_time(job.published_at) if job.published_at else None
        return (
            job.source, job.slug, job.job_id, job.company, job.title, job.location, job.url,
            job.description_text, job.description_html, published,
        )

    @staticmethod
    def _to_stored(row: sqlite3.Row) -> StoredJob:
        job = Job.model_validate({column: row[column] for column in _JOB_COLUMNS})
        return StoredJob(
            job=job,
            first_seen=_from_db_time(row["first_seen"]),
            last_seen=_from_db_time(row["last_seen"]),
            closed_at=_from_db_time(row["closed_at"]) if row["closed_at"] else None,
        )

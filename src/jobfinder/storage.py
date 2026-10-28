"""SQLite storage for collected jobs: one row per job key, with when it was first seen, last seen and closed.

It also caches what an extractor read from a job, as opaque JSON: the store does not know the fields, so
the engine stays domain-generic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from jobfinder.models import Job

SCHEMA_VERSION: Final = 3

_JOBS_TABLE: Final = """
CREATE TABLE {name} (
    key              TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    slug             TEXT NOT NULL,
    job_id           TEXT NOT NULL,
    company          TEXT NOT NULL,
    title            TEXT NOT NULL,
    location         TEXT NOT NULL,
    url              TEXT NOT NULL,
    description_text TEXT,
    description_html TEXT,
    published_at     TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    closed_at        TEXT
);
"""
_INDEX: Final = "CREATE INDEX jobs_board ON jobs (source, slug);"

# One answer per job and extractor. input_hash identifies the exact input the extractor was given (its
# instructions and the job's text), so an edited posting or a changed prompt is a cache miss.
_EXTRACTIONS_TABLE: Final = """
CREATE TABLE extractions (
    key           TEXT NOT NULL,
    extractor     TEXT NOT NULL,
    input_hash    TEXT NOT NULL,
    fields        TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    extracted_at  TEXT NOT NULL,
    PRIMARY KEY (key, extractor)
);
"""

# v1 -> v2: description_text may be NULL (email alerts carry no description). SQLite cannot drop a
# NOT NULL constraint in place, so the table is rebuilt and the rows copied.
_MIGRATE_1_TO_2: Final = (
    _JOBS_TABLE.format(name="jobs_v2")
    + "INSERT INTO jobs_v2 SELECT * FROM jobs;\n"
    + "DROP TABLE jobs;\n"
    + "ALTER TABLE jobs_v2 RENAME TO jobs;\n"
    + _INDEX
)
_MIGRATE_2_TO_3: Final = _EXTRACTIONS_TABLE
# The script that takes a database from version N to N + 1.
_MIGRATIONS: Final[dict[int, str]] = {1: _MIGRATE_1_TO_2, 2: _MIGRATE_2_TO_3}
_CREATE: Final = _JOBS_TABLE.format(name="jobs") + _INDEX + _EXTRACTIONS_TABLE

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
class StoredExtraction:
    fields: str  # JSON, as the extractor's model dumped it
    input_tokens: int | None
    output_tokens: int | None
    extracted_at: datetime


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
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"database schema v{version} is newer than this code (v{SCHEMA_VERSION})")
        if version == 0:
            script = _CREATE
        else:
            script = "".join(_MIGRATIONS[step] for step in range(version, SCHEMA_VERSION))
        # executescript commits on its own, so the whole migration is wrapped in one explicit transaction.
        self._db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;")

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

    def record_sightings(self, jobs: Sequence[Job], seen_at: datetime) -> int:
        """Store jobs seen outside a full board listing (an email alert) and return how many are new.

        An alert names a few new jobs, not everything that is open, so a job missing from it says
        nothing: sightings never close a job. Alerts may be read out of order (a backfill), so the
        stored first_seen / last_seen only ever move outwards.
        """
        now = _to_db_time(seen_at)
        by_key = {job.key: job for job in jobs}
        with self._db:
            placeholders = ", ".join("?" for _ in by_key)
            known = {
                row["key"]
                for row in self._db.execute(f"SELECT key FROM jobs WHERE key IN ({placeholders})", list(by_key))
            }
            self._db.executemany(
                f"""
                INSERT INTO jobs (key, {", ".join(_JOB_COLUMNS)}, first_seen, last_seen, closed_at)
                VALUES (?, {", ".join("?" for _ in _JOB_COLUMNS)}, ?, ?, NULL)
                ON CONFLICT (key) DO UPDATE SET
                    {", ".join(f"{column} = excluded.{column}" for column in _JOB_COLUMNS)},
                    first_seen = MIN(first_seen, excluded.first_seen),
                    last_seen = MAX(last_seen, excluded.last_seen),
                    closed_at = NULL
                """,
                [(key, *self._job_values(job), now, now) for key, job in by_key.items()],
            )
        return len(by_key.keys() - known)

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

    def get_extraction(self, key: str, extractor: str, input_hash: str) -> StoredExtraction | None:
        """The cached answer of `extractor` for this job, if it was given exactly this input."""
        row = self._db.execute(
            "SELECT * FROM extractions WHERE key = ? AND extractor = ? AND input_hash = ?",
            (key, extractor, input_hash),
        ).fetchone()
        if row is None:
            return None
        return StoredExtraction(
            fields=row["fields"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            extracted_at=_from_db_time(row["extracted_at"]),
        )

    def extractions_for(self, key: str, input_hash: str) -> dict[str, StoredExtraction]:
        """Every extractor's cached answer for this job that was given exactly this input, by extractor."""
        rows = self._db.execute(
            "SELECT * FROM extractions WHERE key = ? AND input_hash = ? ORDER BY extractor", (key, input_hash)
        )
        return {
            row["extractor"]: StoredExtraction(
                fields=row["fields"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                extracted_at=_from_db_time(row["extracted_at"]),
            )
            for row in rows
        }

    def save_extraction(
        self,
        key: str,
        extractor: str,
        input_hash: str,
        fields: str,
        input_tokens: int | None,
        output_tokens: int | None,
        extracted_at: datetime,
    ) -> None:
        """Store an answer, replacing any earlier one by the same extractor for this job."""
        with self._db:
            self._db.execute(
                """
                INSERT INTO extractions (key, extractor, input_hash, fields, input_tokens, output_tokens, extracted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (key, extractor) DO UPDATE SET
                    input_hash = excluded.input_hash, fields = excluded.fields,
                    input_tokens = excluded.input_tokens, output_tokens = excluded.output_tokens,
                    extracted_at = excluded.extracted_at
                """,
                (key, extractor, input_hash, fields, input_tokens, output_tokens, _to_db_time(extracted_at)),
            )

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

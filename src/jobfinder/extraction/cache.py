"""Extractor answers kept in the job store, so a job is sent to a model once per extractor and input."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from jobfinder.extraction.base import Extraction, ExtractionResult, Extractor, input_hash
from jobfinder.models import Job
from jobfinder.storage import JobStore


class CachedExtractor:
    """Wraps an extractor: answers from the store when it has one for exactly this input, else asks and saves.

    Only successful answers are saved, so a failed job is retried on the next run.
    """

    def __init__(self, extractor: Extractor, store: JobStore, now: Callable[[], datetime]) -> None:
        self._extractor = extractor
        self._store = store
        self._now = now

    @property
    def name(self) -> str:
        return self._extractor.name

    def extract(self, job: Job) -> ExtractionResult:
        digest = input_hash(job)
        stored = self._store.get_extraction(job.key, self.name, digest)
        if stored is not None:
            return ExtractionResult(
                Extraction.model_validate_json(stored.fields), stored.input_tokens, stored.output_tokens, cached=True
            )
        result = self._extractor.extract(job)
        self._store.save_extraction(
            job.key, self.name, digest, result.extraction.model_dump_json(),
            result.input_tokens, result.output_tokens, self._now(),
        )
        return result

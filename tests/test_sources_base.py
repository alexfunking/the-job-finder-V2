"""The Source contract, exercised through a minimal in-test source.

The concrete sources (1.2 Greenhouse, 1.3 Ashby) follow this same shape: Fetcher payload in,
SourceResult out, FetchError when the board cannot be read.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fakes import FakeFetcher
from jobfinder.http import FetchError, Fetcher
from jobfinder.models import Job
from jobfinder.sources import Source, SourceResult


class _ListSource:
    name = "example"

    def fetch_jobs(self, slug: str, company: str, fetcher: Fetcher) -> SourceResult:
        payload = fetcher.get_json(f"https://example.test/{slug}")
        if payload is None:
            raise FetchError(f"board not found: {slug}")
        assert isinstance(payload, list)
        jobs: list[Job] = []
        skipped: list[str] = []
        for item in payload:
            try:
                jobs.append(Job.model_validate({"source": self.name, "slug": slug, "company": company, **item}))
            except ValidationError as exc:
                skipped.append(f"{item.get('job_id', '?')}: {exc.error_count()} invalid field(s)")
        return SourceResult(jobs=jobs, skipped=skipped)


def _item(job_id: str, description: str = "Build things.") -> dict[str, str]:
    return {
        "job_id": job_id,
        "title": "Engineer",
        "location": "Haifa",
        "url": f"https://example.test/jobs/{job_id}",
        "description_text": description,
    }


def test_source_protocol_is_satisfied_structurally() -> None:
    source: Source = _ListSource()
    assert source.name == "example"


def test_valid_jobs_are_returned_and_invalid_ones_reported() -> None:
    fetcher = FakeFetcher({"https://example.test/acme": [_item("1"), _item("2", description="")]})
    result = _ListSource().fetch_jobs("acme", "Acme", fetcher)
    assert [job.key for job in result.jobs] == ["example:acme:1"]
    assert result.skipped == ["2: 1 invalid field(s)"]
    assert fetcher.requested == ["https://example.test/acme"]


def test_missing_board_raises_instead_of_returning_empty() -> None:
    fetcher = FakeFetcher({"https://example.test/gone": None})
    with pytest.raises(FetchError, match="board not found"):
        _ListSource().fetch_jobs("gone", "Gone", fetcher)


def test_fake_fetcher_fails_on_unexpected_url() -> None:
    with pytest.raises(AssertionError, match="unexpected request"):
        FakeFetcher({}).get_json("https://example.test/other")


def test_fake_fetcher_raises_canned_errors() -> None:
    fetcher = FakeFetcher({"https://example.test/acme": FetchError("HTTP 429")})
    with pytest.raises(FetchError, match="429"):
        _ListSource().fetch_jobs("acme", "Acme", fetcher)

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fakes import FakeFetcher
from jobfinder.http import FetchError
from jobfinder.sources import Source, SourceResult
from jobfinder.sources.ashby import JOBS_URL, AshbySource

FIXTURE = Path(__file__).parent / "fixtures" / "ashby_lemonade.json"
URL = JOBS_URL.format(slug="lemonade")
TLV_JOB = "9a6153da-639d-413e-b9bf-68ebf5f8c269"
NYC_JOB = "486bead9-04c4-4bb3-81b6-17fabfc5f52a"


def load_fixture() -> dict[str, list[dict[str, object]]]:
    data: dict[str, list[dict[str, object]]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def fetch(payload: object, fetcher: FakeFetcher | None = None) -> SourceResult:
    return AshbySource().fetch_jobs("lemonade", "Lemonade", fetcher or FakeFetcher({URL: payload}))


def test_is_a_source() -> None:
    source: Source = AshbySource()
    assert source.name == "ashby"


def test_requests_one_url() -> None:
    fetcher = FakeFetcher({URL: load_fixture()})
    fetch(None, fetcher)
    assert fetcher.requested == ["https://api.ashbyhq.com/posting-api/job-board/lemonade"]


def test_parses_real_fixture() -> None:
    result = fetch(load_fixture())
    assert result.skipped == []
    assert [job.key for job in result.jobs] == [f"ashby:lemonade:{TLV_JOB}", f"ashby:lemonade:{NYC_JOB}"]
    job = result.jobs[0]
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Lemonade"
    assert job.location == "TLV"
    assert job.url == f"https://jobs.ashbyhq.com/lemonade/{TLV_JOB}"
    assert job.published_at == datetime(2025, 2, 4, 8, 47, 42, 681000, tzinfo=UTC)


def test_description_comes_from_html_with_list_structure() -> None:
    job = fetch(load_fixture()).jobs[0]
    assert job.description_html is not None and "<li>" in job.description_html
    assert job.description_text.startswith("We’re looking for a senior-level backend engineer")
    assert "\n- " in job.description_text
    assert "\n-\n" not in job.description_text
    assert "<" not in job.description_text and "\xa0" not in job.description_text


def test_falls_back_to_plain_text_when_html_is_empty() -> None:
    payload = load_fixture()
    payload["jobs"][0]["descriptionHtml"] = ""
    payload["jobs"][0]["descriptionPlain"] = "Plain description."
    job = fetch(payload).jobs[0]
    assert (job.description_text, job.description_html) == ("Plain description.", None)


def test_secondary_locations_are_appended() -> None:
    payload = load_fixture()
    payload["jobs"][1]["secondaryLocations"] = [
        {"location": "Israel - Tel Aviv", "address": {"postalAddress": {"addressCountry": "Israel"}}},
        {"location": "NYC"},
        {"address": {}},
    ]
    assert fetch(payload).jobs[1].location == "NYC | Israel - Tel Aviv"


def test_unlisted_jobs_are_excluded_silently() -> None:
    payload = load_fixture()
    payload["jobs"][1]["isListed"] = False
    result = fetch(payload)
    assert [job.job_id for job in result.jobs] == [TLV_JOB]
    assert result.skipped == []


def test_job_without_any_description_is_skipped() -> None:
    payload = load_fixture()
    payload["jobs"][1]["descriptionHtml"] = "<p> </p>"
    payload["jobs"][1]["descriptionPlain"] = ""
    result = fetch(payload)
    assert len(result.jobs) == 1
    assert result.skipped == [f"{NYC_JOB}: invalid description_text"]


def test_missing_board_raises() -> None:
    with pytest.raises(FetchError, match="board not found: lemonade"):
        fetch(None)


@pytest.mark.parametrize("payload", [[], {"jobs": None}, {"apiVersion": "1"}])
def test_unexpected_payload_shape_raises(payload: object) -> None:
    with pytest.raises(FetchError, match="unexpected payload shape"):
        fetch(payload)

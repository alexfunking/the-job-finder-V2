from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fakes import FakeFetcher
from jobfinder.http import FetchError
from jobfinder.sources import Source, SourceResult
from jobfinder.sources.greenhouse import JOBS_URL, GreenhouseSource

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_catonetworks.json"
URL = JOBS_URL.format(slug="catonetworks")


def load_fixture() -> dict[str, list[dict[str, object]]]:
    data: dict[str, list[dict[str, object]]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def fetch(payload: object, fetcher: FakeFetcher | None = None) -> SourceResult:
    return GreenhouseSource().fetch_jobs("catonetworks", "Cato Networks", fetcher or FakeFetcher({URL: payload}))


def test_is_a_source() -> None:
    source: Source = GreenhouseSource()
    assert source.name == "greenhouse"


def test_requests_one_url_with_content() -> None:
    fetcher = FakeFetcher({URL: load_fixture()})
    fetch(None, fetcher)
    assert fetcher.requested == ["https://boards-api.greenhouse.io/v1/boards/catonetworks/jobs?content=true"]


def test_parses_real_fixture() -> None:
    result = fetch(load_fixture())
    assert result.skipped == []
    assert [job.key for job in result.jobs] == [
        "greenhouse:catonetworks:4951235101",
        "greenhouse:catonetworks:4889240101",
    ]
    job = result.jobs[0]
    assert job.title == "Agentic AI Engineer - Product Support"  # the API has a leading space
    assert job.company == "Cato Networks"
    assert job.location == "Tel Aviv District, Israel"
    assert job.url.startswith("https://")
    assert job.published_at == datetime(2026, 8, 17, 4, 13, 58, tzinfo=timezone(timedelta(hours=-4)))


def test_description_is_unescaped_and_rendered_as_text() -> None:
    job = fetch(load_fixture()).jobs[0]
    assert job.description_html is not None and job.description_html.startswith("<p>")
    assert job.description_text is not None
    assert job.description_text.startswith("Cato Networks is the converged network and security cloud")
    for leftover in ("<", "&lt;", "&amp;", "&nbsp;", "\xa0", "data-contrast"):
        assert leftover not in job.description_text


def test_job_without_content_is_skipped_not_fatal() -> None:
    payload = load_fixture()
    payload["jobs"][1]["content"] = "&lt;p&gt; &lt;/p&gt;"
    result = fetch(payload)
    assert len(result.jobs) == 1
    assert result.skipped == ["4889240101: invalid description_text"]


def test_missing_location_becomes_empty_string() -> None:
    payload = load_fixture()
    del payload["jobs"][0]["location"]
    assert fetch(payload).jobs[0].location == ""


def test_non_object_entries_are_skipped() -> None:
    payload: dict[str, list[object]] = {"jobs": [*load_fixture()["jobs"], "garbage"]}
    result = fetch(payload)
    assert len(result.jobs) == 2
    assert result.skipped == ["non-object entry: str"]


def test_empty_board_is_a_valid_result() -> None:
    result = fetch({"jobs": [], "meta": {"total": 0}})
    assert result.jobs == [] and result.skipped == []


def test_missing_board_raises() -> None:
    with pytest.raises(FetchError, match="board not found: catonetworks"):
        fetch(None)


@pytest.mark.parametrize("payload", [[], {"jobs": "nope"}, {"meta": {}}])
def test_unexpected_payload_shape_raises(payload: object) -> None:
    with pytest.raises(FetchError, match="unexpected payload shape"):
        fetch(payload)

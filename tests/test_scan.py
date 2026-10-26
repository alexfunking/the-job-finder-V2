from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fakes import FakeFetcher
from jobfinder.cli import EXIT_BOARD_ERRORS, EXIT_CONFIG_ERROR, EXIT_OK, run_scan
from jobfinder.config import Board
from jobfinder.http import FetchError
from jobfinder.scan import scan
from jobfinder.sources.ashby import JOBS_URL as ASHBY_URL
from jobfinder.sources.greenhouse import JOBS_URL as GREENHOUSE_URL
from jobfinder.storage import BoardUpdate, JobStore

FIXTURES = Path(__file__).parent / "fixtures"
CATO = Board(company="Cato Networks", source="greenhouse", slug="catonetworks")
LEMONADE = Board(company="Lemonade", source="ashby", slug="lemonade")
CATO_URL = GREENHOUSE_URL.format(slug="catonetworks")
LEMONADE_URL = ASHBY_URL.format(slug="lemonade")
T1 = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def fixture(name: str) -> dict[str, list[dict[str, object]]]:
    data: dict[str, list[dict[str, object]]] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


@pytest.fixture
def store() -> Iterator[JobStore]:
    s = JobStore.open(":memory:")
    yield s
    s.close()


def test_scan_records_every_board(store: JobStore) -> None:
    fetcher = FakeFetcher({
        CATO_URL: fixture("greenhouse_catonetworks.json"),
        LEMONADE_URL: fixture("ashby_lemonade.json"),
    })
    report = scan([CATO, LEMONADE], store, fetcher, T1)
    assert [o.error for o in report.outcomes] == [None, None]
    assert [o.update for o in report.outcomes] == [BoardUpdate(2, 0, 0, 0), BoardUpdate(2, 0, 0, 0)]
    assert report.total("new") == 4
    assert store.count() == 4


def test_failed_board_is_reported_and_its_jobs_stay_open(store: JobStore) -> None:
    scan([CATO, LEMONADE], store, FakeFetcher({
        CATO_URL: fixture("greenhouse_catonetworks.json"),
        LEMONADE_URL: fixture("ashby_lemonade.json"),
    }), T1)
    report = scan([CATO, LEMONADE], store, FakeFetcher({
        CATO_URL: FetchError("HTTP 503"),
        LEMONADE_URL: fixture("ashby_lemonade.json"),
    }), T1 + timedelta(days=1))
    assert [o.board for o in report.failed] == [CATO]
    assert report.failed[0].error == "HTTP 503"
    assert store.count(include_closed=False) == 4  # Cato's 2 jobs were not closed by the failed fetch


def test_one_board_failing_does_not_stop_the_scan(store: JobStore) -> None:
    report = scan([CATO, LEMONADE], store, FakeFetcher({
        CATO_URL: None,  # board gone: GreenhouseSource raises FetchError
        LEMONADE_URL: fixture("ashby_lemonade.json"),
    }), T1)
    assert report.failed[0].error == "greenhouse board not found: catonetworks"
    assert report.outcomes[1].update == BoardUpdate(2, 0, 0, 0)


def test_skipped_postings_are_carried_into_the_report(store: JobStore) -> None:
    payload = fixture("greenhouse_catonetworks.json")
    payload["jobs"][1]["content"] = ""
    report = scan([CATO], store, FakeFetcher({CATO_URL: payload}), T1)
    assert report.outcomes[0].open_jobs == 1
    assert report.outcomes[0].skipped == ("4889240101: invalid description_text",)


def write_sources(tmp_path: Path, *boards: Board) -> Path:
    path = tmp_path / "sources.toml"
    path.write_text("".join(
        f'[[board]]\ncompany = "{b.company}"\nsource = "{b.source}"\nslug = "{b.slug}"\n\n' for b in boards
    ), encoding="utf-8")
    return path


def test_run_scan_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sources, db = write_sources(tmp_path, CATO, LEMONADE), tmp_path / "data" / "jobs.db"
    fetcher = FakeFetcher({
        CATO_URL: fixture("greenhouse_catonetworks.json"),
        LEMONADE_URL: FetchError("HTTP 429"),
    })
    assert run_scan(sources, db, fetcher) == EXIT_BOARD_ERRORS
    out = capsys.readouterr().out
    assert "Cato Networks (greenhouse:catonetworks)" in out and "2 open  +2 new  -0 closed" in out
    assert "Lemonade (ashby:lemonade)" in out and "ERROR HTTP 429" in out
    assert "Boards: 2 scanned, 1 failed" in out
    assert "Failed boards keep their stored jobs open" in out
    assert out.isascii()

    second = FakeFetcher({CATO_URL: fixture("greenhouse_catonetworks.json"), LEMONADE_URL: fixture("ashby_lemonade.json")})
    assert run_scan(sources, db, second) == EXIT_OK
    out = capsys.readouterr().out
    assert "+0 new" in out and "Jobs:   +2 new, -0 closed, 0 reopened; 4 open in store" in out


def test_run_scan_reports_config_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_scan(tmp_path / "missing.toml", tmp_path / "jobs.db", FakeFetcher({})) == EXIT_CONFIG_ERROR
    assert "config error: sources file not found" in capsys.readouterr().err
    assert not (tmp_path / "jobs.db").exists()

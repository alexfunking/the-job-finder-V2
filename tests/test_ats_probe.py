from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import ats_probe  # noqa: E402
from ats_probe import (  # noqa: E402
    BOARD_APIS,
    BoardHit,
    Company,
    FetchError,
    Posting,
    ProbeResult,
    candidate_slugs,
    detect_fingerprints,
    is_israel_location,
    names_match,
    probe_board,
    probe_company,
    read_companies,
    rejection_reason,
    summarize_board,
)

TODAY = date(2026, 10, 26)


def test_candidate_slugs_strips_suffixes_and_orders_variants() -> None:
    slugs = candidate_slugs("Check Point Software")
    assert slugs[:3] == ("checkpointsoftware", "check-point-software", "check")
    assert "checkpoint" in slugs
    assert "check-point" in slugs


def test_candidate_slugs_handles_domain_style_names() -> None:
    assert candidate_slugs("monday.com")[0] == "mondaycom"
    assert "monday" in candidate_slugs("monday.com")


def test_candidate_slugs_has_no_duplicates() -> None:
    slugs = candidate_slugs("Wiz")
    assert slugs == ("wiz",)


def test_is_israel_location() -> None:
    assert is_israel_location("Tel Aviv, Israel")
    assert is_israel_location("Herzliya")
    assert not is_israel_location("New York, NY")
    assert not is_israel_location("")


def test_is_israel_location_abbreviations_and_hubs() -> None:
    assert is_israel_location("TLV")
    assert is_israel_location("Glilot; Tel Aviv")
    assert is_israel_location("Petah-Tikva")
    assert is_israel_location("Tel Aviv-Yafo")
    assert not is_israel_location("Brazil")
    assert not is_israel_location("London")


def test_is_israel_location_matches_whole_words_only() -> None:
    assert not is_israel_location("Lodz, Poland")
    assert not is_israel_location("Lodi, CA")
    assert not is_israel_location("Holonet HQ")
    assert is_israel_location("Lod")
    assert is_israel_location("Rishon LeZion")
    assert is_israel_location("Israel - Raanana")
    assert is_israel_location("Ra'anana")
    assert is_israel_location("Kiryat Gat")


def test_detect_fingerprints() -> None:
    html = '<script src="https://www.comeet.co/careers-api/widget.js"></script><a href="x.myworkdayjobs.com">'
    assert detect_fingerprints(html) == ["comeet", "workday"]
    assert detect_fingerprints("<html>nothing</html>") == []


def test_verdict_prefers_board_with_most_israel_jobs() -> None:
    result = ProbeResult(company=Company("X", (), None))
    result.hits.extend([BoardHit("lever", "x", 50, 1), BoardHit("greenhouse", "x", 10, 8)])
    assert result.verdict == "greenhouse:x"


def test_verdict_falls_back_to_fingerprint_then_unknown() -> None:
    result = ProbeResult(company=Company("X", (), None))
    assert result.verdict == "unknown"
    result.fingerprints.append("comeet")
    assert result.verdict == "fingerprint:comeet"


def test_parsers_reject_unexpected_shapes() -> None:
    assert ats_probe._postings_greenhouse([]) is None
    assert ats_probe._postings_lever({}) is None
    assert ats_probe._postings_ashby([]) is None
    assert ats_probe._postings_smartrecruiters({}) is None
    assert ats_probe._board_name_greenhouse(None) is None
    assert ats_probe._board_name_smartrecruiters({"content": []}) is None


def test_parsers_read_locations_and_dates() -> None:
    greenhouse = {"jobs": [{"location": {"name": "Tel Aviv"}, "updated_at": "2026-10-20T10:00:00-04:00"}]}
    assert ats_probe._postings_greenhouse(greenhouse) == [Posting("Tel Aviv", date(2026, 10, 20))]
    lever = [{"categories": {"location": "Tel Aviv", "allLocations": ["Haifa"]}, "createdAt": 1761436800000}]
    assert ats_probe._postings_lever(lever) == [Posting("Tel Aviv | Haifa", date(2025, 10, 26))]
    ashby = {"jobs": [{"location": "TLV", "publishedAt": "2026-09-01T08:00:00.000+00:00"}]}
    assert ats_probe._postings_ashby(ashby) == [Posting("TLV", date(2026, 9, 1))]
    smartrecruiters = {"content": [{
        "location": {"city": "Tel Aviv-Yafo", "country": "il"},
        "releasedDate": "2017-06-26T09:00:00.000Z",
        "company": {"name": "Armis"},
    }]}
    assert ats_probe._postings_smartrecruiters(smartrecruiters) == [Posting("Tel Aviv-Yafo Israel", date(2017, 6, 26))]
    assert ats_probe._board_name_smartrecruiters(smartrecruiters) == "Armis"
    assert ats_probe._board_name_greenhouse({"name": "Pagaya "}) == "Pagaya "


def test_ashby_counts_secondary_locations() -> None:
    ashby = {"jobs": [{
        "location": "United Kingdom - London",
        "secondaryLocations": [{"location": "Israel - Tel Aviv"}, {"address": {}}, "junk"],
        "publishedAt": "2026-10-01T00:00:00+00:00",
    }]}
    postings = ats_probe._postings_ashby(ashby)
    assert postings == [Posting("United Kingdom - London | Israel - Tel Aviv", date(2026, 10, 1))]
    assert is_israel_location(postings[0].location)


def test_next_offset_smartrecruiters() -> None:
    assert ats_probe._next_offset_smartrecruiters({"offset": 0, "limit": 100, "totalFound": 250}) == 100
    assert ats_probe._next_offset_smartrecruiters({"offset": 200, "limit": 100, "totalFound": 250}) is None
    assert ats_probe._next_offset_smartrecruiters({"offset": 0, "limit": 100, "totalFound": 100}) is None
    assert ats_probe._next_offset_smartrecruiters({"offset": 0, "limit": 0, "totalFound": 250}) is None
    assert ats_probe._next_offset_smartrecruiters({"offset": "0", "limit": 100, "totalFound": 250}) is None
    assert ats_probe._next_offset_smartrecruiters([]) is None


def _smartrecruiters_page(offset: int, size: int, total: int, city: str, country: str = "il") -> dict[str, object]:
    job = {"location": {"city": city, "country": country},"releasedDate": "2026-10-01T00:00:00Z", "company": {"name": "X"}}
    return {"offset": offset, "limit": size, "totalFound": total, "content": [job] * size}


def test_probe_board_follows_smartrecruiters_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {0: _smartrecruiters_page(0, 2, 5, "Haifa"), 2: _smartrecruiters_page(2, 2, 5, "Haifa"),
             4: _smartrecruiters_page(4, 1, 5, "Haifa")}
    requested: list[str] = []

    def fake_fetch_json(url: str) -> object | None:
        requested.append(url)
        return pages[int(url.rsplit("offset=", 1)[1])]

    monkeypatch.setattr(ats_probe, "_fetch_json", fake_fetch_json)
    api = next(a for a in BOARD_APIS if a.ats == "smartrecruiters")
    hit = probe_board(api, "x")
    assert hit is not None
    assert (hit.total_jobs, hit.israel_jobs, hit.board_name) == (5, 5, "X")
    assert [u.rsplit("offset=", 1)[1] for u in requested] == ["0", "2", "4"]


def test_probe_board_stops_at_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ats_probe, "MAX_PAGES", 3)
    requested: list[str] = []

    def fake_fetch_json(url: str) -> object | None:
        requested.append(url)
        return _smartrecruiters_page(int(url.rsplit("offset=", 1)[1]), 1, 1000, "Berlin", "de")

    monkeypatch.setattr(ats_probe, "_fetch_json", fake_fetch_json)
    api = next(a for a in BOARD_APIS if a.ats == "smartrecruiters")
    hit = probe_board(api, "x")
    assert hit is not None
    assert (hit.total_jobs, hit.israel_jobs) == (3, 0)
    assert len(requested) == 3


def test_fetch_json_only_404_means_no_board(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {"a": (404, ""), "b": (200, '{"jobs": []}'), "c": (200, "<html>"), "d": (429, ""), "e": (503, "")}
    monkeypatch.setattr(ats_probe, "_fetch", lambda url: responses[url])
    assert ats_probe._fetch_json("a") is None
    assert ats_probe._fetch_json("b") == {"jobs": []}
    assert ats_probe._fetch_json("c") is None
    with pytest.raises(FetchError, match="HTTP 429"):
        ats_probe._fetch_json("d")
    with pytest.raises(FetchError, match="HTTP 503"):
        ats_probe._fetch_json("e")


def test_probe_company_records_rate_limit_instead_of_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ats_probe, "_fetch", lambda url: (429, "") if "greenhouse" in url else (404, ""))
    result = probe_company(Company("Wiz", (), None), TODAY)
    assert result.verdict == "unknown"
    assert result.errors == ["greenhouse:wiz: HTTP 429"]


def test_probe_board_keeps_first_page_when_later_page_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch_json(url: str) -> object | None:
        offset = int(url.rsplit("offset=", 1)[1])
        if offset == 0:
            return _smartrecruiters_page(0, 2, 5, "Haifa")
        raise FetchError("HTTP 503")

    monkeypatch.setattr(ats_probe, "_fetch_json", fake_fetch_json)
    api = next(a for a in BOARD_APIS if a.ats == "smartrecruiters")
    hit = probe_board(api, "x")
    assert hit is not None
    assert (hit.total_jobs, hit.israel_jobs, hit.notes) == (2, 2, ("page 2: HTTP 503",))


def test_probe_board_keeps_hit_when_board_name_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = {"jobs": [{"location": {"name": "Tel Aviv"}, "updated_at": "2026-10-20T10:00:00+00:00"}]}

    def fake_fetch_json(url: str) -> object | None:
        if url.endswith("/jobs"):
            return jobs
        raise FetchError("HTTP 500")

    monkeypatch.setattr(ats_probe, "_fetch_json", fake_fetch_json)
    hit = probe_board(BOARD_APIS[0], "x")
    assert hit is not None
    assert (hit.israel_jobs, hit.board_name, hit.notes) == (1, None, ("board name: HTTP 500",))


def test_probe_company_surfaces_board_notes_as_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    hit = BoardHit("greenhouse", "wiz", 3, 1, TODAY, None, ("board name: HTTP 500",))
    monkeypatch.setattr(ats_probe, "probe_board", lambda api, slug: hit if api.ats == "greenhouse" else None)
    result = probe_company(Company("Wiz", (), None), TODAY)
    assert result.verdict == "greenhouse:wiz"
    assert result.errors == ["greenhouse:wiz: board name: HTTP 500"]


def test_parse_date_rejects_garbage() -> None:
    assert ats_probe._parse_date("not a date") is None
    assert ats_probe._parse_date(None) is None
    assert ats_probe._parse_date(True) is None


def test_summarize_board_counts_israel_jobs_and_latest_posting() -> None:
    postings = [Posting("TLV", date(2026, 9, 1)), Posting("NYC", date(2026, 10, 1)), Posting("Haifa", None)]
    hit = summarize_board(BOARD_APIS[0], "x", postings, "X")
    assert hit == BoardHit("greenhouse", "x", 3, 2, date(2026, 10, 1), "X")


def test_names_match_ignores_case_punctuation_and_suffixes() -> None:
    assert names_match("Pagaya", "Pagaya ")
    assert names_match("Cato Networks", "Cato Networks Ltd")
    assert names_match("monday.com", "monday.com")
    assert names_match("Check Point Software", "Check Point")
    assert not names_match("Port", "Portland General Electric")
    assert not names_match("Via", "Viasat")


def test_rejection_reason_name_mismatch() -> None:
    hit = BoardHit("greenhouse", "port", 10, 0, TODAY, "Portland General Electric")
    assert rejection_reason(hit, "Port", TODAY) == "name mismatch: 'Portland General Electric'"


def test_rejection_reason_stale_board() -> None:
    # Real case: smartrecruiters/gong is an unrelated "GONG!" account whose newest posting is from 2017.
    hit = BoardHit("smartrecruiters", "gong", 7, 0, date(2017, 3, 24), "GONG!")
    assert rejection_reason(hit, "Gong", TODAY) == "stale: latest posting 2017-03-24"


def test_rejection_reason_accepts_live_board_and_missing_metadata() -> None:
    assert rejection_reason(BoardHit("greenhouse", "x", 5, 1, date(2026, 10, 1), "X"), "X", TODAY) is None
    assert rejection_reason(BoardHit("ashby", "x", 5, 1, None, None), "X", TODAY) is None
    edge = BoardHit("lever", "x", 1, 0, date.fromordinal(TODAY.toordinal() - ats_probe.STALE_AFTER_DAYS))
    assert rejection_reason(edge, "X", TODAY) is None


def test_read_companies_skips_comments_and_parses_slugs(tmp_path: Path) -> None:
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text(
        "name,slugs,careers_url\n# comment,,\nWiz,wiz;wizinc,\nmonday.com,,https://monday.com/careers\n",
        encoding="utf-8",
    )
    companies = read_companies(csv_path)
    assert companies == [
        Company("Wiz", ("wiz", "wizinc"), None),
        Company("monday.com", (), "https://monday.com/careers"),
    ]

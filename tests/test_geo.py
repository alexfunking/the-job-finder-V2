from __future__ import annotations

from jobfinder.geo import is_israel_location


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

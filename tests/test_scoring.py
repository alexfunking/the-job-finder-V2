from __future__ import annotations

from pathlib import Path

import pytest

from jobfinder.extraction import Extraction
from jobfinder.models import Job
from jobfinder.scoring import Priorities, PrioritiesError, Verdict, find_word, load_priorities, score_job

PRIORITIES_TOML = """
[profile]
skills = ["Python", "SQL"]

[role]
max_years = 1
entry_title_words = ["student", "junior", "intern"]
field_title_words = ["data", "software", "backend", "BI"]
reject_title_words = ["QA", "IT", "sales", "support"]

[location]
accept = ["Tel Aviv", "Rishon", "Remote"]
low_priority = ["Netanya"]
reject = ["Haifa"]

[onsite]
preferred_max_days = 3

[score]
match_threshold = 50
"""


@pytest.fixture
def priorities(tmp_path: Path) -> Priorities:
    path = tmp_path / "priorities.txt"
    path.write_text(PRIORITIES_TOML, encoding="utf-8")
    return load_priorities(path)


def job(title: str = "Junior Software Engineer", location: str = "Tel Aviv", description: str | None = "d") -> Job:
    return Job(
        source="greenhouse", slug="acme", job_id="1", company="Acme", title=title, location=location,
        url="https://example.test/1", description_text=description,
    )


def extraction(
    years: int | None = 0, onsite: int | None = 3, student: bool = False, stack: tuple[str, ...] = ()
) -> Extraction:
    return Extraction(years_required=years, onsite_days=onsite, is_student=student, location=None, stack=stack)


def test_junior_dev_job_in_tel_aviv_is_a_full_match(priorities: Priorities) -> None:
    score = score_job(job(), extraction(), priorities)
    assert (score.points, score.verdict, score.rejections) == (100, Verdict.MATCH, ())


def test_title_only_junior_job_matches_without_an_extraction(priorities: Priorities) -> None:
    score = score_job(job(description=None), None, priorities)
    assert score.verdict is Verdict.MATCH
    assert score.points == 95  # office days unknown: half points
    assert "office 5/10: not stated" in score.reasons


def test_unknown_level_is_a_maybe_however_high_the_rest_scores(priorities: Priorities) -> None:
    score = score_job(job("Data Engineer"), None, priorities)
    assert score.verdict is Verdict.MAYBE
    assert "level 0/40: years not stated" in score.reasons


def test_stated_years_within_the_limit_count_as_entry_level(priorities: Priorities) -> None:
    assert score_job(job("Data Engineer"), extraction(years=1), priorities).verdict is Verdict.MATCH


def test_zero_years_is_not_evidence_of_an_entry_level_job(priorities: Priorities) -> None:
    score = score_job(job("Data Engineer"), extraction(years=0), priorities)
    assert score.verdict is Verdict.MAYBE
    assert "level 0/40: years not stated" in score.reasons


def test_an_entry_level_job_outside_your_fields_is_a_maybe(priorities: Priorities) -> None:
    score = score_job(job("Junior Coordinator"), extraction(), priorities)
    assert score.points >= 50
    assert score.verdict is Verdict.MAYBE


def test_a_word_before_a_loose_hyphen_still_counts(priorities: Priorities) -> None:
    assert find_word(("student",), "Software Engineer Student- Glil Yam") == "Student"
    assert find_word(("IT",), "IT-Security Engineer") is None


def test_too_many_years_rejects_even_a_junior_title(priorities: Priorities) -> None:
    score = score_job(job(), extraction(years=3), priorities)
    assert score.verdict is Verdict.NO
    assert score.rejections == ("asks 3 years (you accept up to 1)",)


def test_student_role_fits_whatever_the_title(priorities: Priorities) -> None:
    score = score_job(job("Data Platform Engineer"), extraction(years=None, student=True), priorities)
    assert score.verdict is Verdict.MATCH
    assert "level 40/40: student role" in score.reasons


@pytest.mark.parametrize(("title", "word"), [("Junior QA Engineer", "QA"), ("Junior IT Specialist", "IT")])
def test_unwanted_field_rejects(priorities: Priorities, title: str, word: str) -> None:
    score = score_job(job(title), extraction(), priorities)
    assert score.verdict is Verdict.NO
    assert score.rejections == (f"field: {word}",)


def test_capital_words_match_only_in_capitals() -> None:
    assert find_word(("IT",), "Make it work") is None
    assert find_word(("IT",), "IT Engineer") == "IT"
    assert find_word(("data",), "Big Data Engineer") == "Data"
    assert find_word(("data",), "Metadata Engineer") is None


def test_shared_skills_give_partial_field_points(priorities: Priorities) -> None:
    score = score_job(job("Junior Engineer"), extraction(stack=("python", "Go")), priorities)
    assert "field 15/30: uses your skills (Python)" in score.reasons


@pytest.mark.parametrize(
    ("location", "points", "verdict"),
    [("Tel Aviv / Netanya", 20, Verdict.MATCH), ("Netanya", 8, Verdict.MATCH), ("Haifa", 0, Verdict.NO),
     ("Herzliya", 10, Verdict.MATCH), ("London", 0, Verdict.NO)],
)
def test_location_parts(priorities: Priorities, location: str, points: int, verdict: Verdict) -> None:
    score = score_job(job(location=location), extraction(), priorities)
    assert score.reasons[2].startswith(f"location {points}/20")
    assert score.verdict is verdict


def test_more_office_days_lower_the_score_but_never_reject(priorities: Priorities) -> None:
    score = score_job(job(), extraction(onsite=5), priorities)
    assert score.verdict is Verdict.MATCH
    assert "office 4/10: 5 days a week" in score.reasons


def test_missing_or_bad_priorities_are_reported(tmp_path: Path) -> None:
    with pytest.raises(PrioritiesError, match="no priorities file"):
        load_priorities(tmp_path / "none.txt")
    path = tmp_path / "p.txt"
    path.write_text(PRIORITIES_TOML.replace("max_years = 1", 'max_years = "one"'), encoding="utf-8")
    with pytest.raises(PrioritiesError, match="max_years must be a whole number"):
        load_priorities(path)
    path.write_text(PRIORITIES_TOML.replace("[onsite]\npreferred_max_days = 3", ""), encoding="utf-8")
    with pytest.raises(PrioritiesError, match=r"missing \[onsite\] preferred_max_days"):
        load_priorities(path)

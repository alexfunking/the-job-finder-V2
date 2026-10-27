from __future__ import annotations

import pytest

from jobfinder.filtering import FilterRules, prefilter
from jobfinder.models import Job


def job(title: str = "Backend Engineer", location: str = "Tel Aviv, Israel") -> Job:
    return Job(
        source="greenhouse",
        slug="acme",
        job_id="1",
        company="Acme",
        title=title,
        location=location,
        url="https://example.test/acme/1",
        description_text="Build things. Requires 5+ years of experience.",
    )


@pytest.mark.parametrize(
    "location",
    ["Tel Aviv, Israel", "Tel Aviv, IL", "Herzliya", "TLV", "Israel - Raanana", "London | Tel Aviv"],
)
def test_jobs_in_israel_pass_the_location_rule(location: str) -> None:
    result = prefilter(job(location=location))
    assert result.kept
    assert result.notes == ()


@pytest.mark.parametrize(
    "location", ["London", "USA - Remote", "Remote - USA", "Remote, Germany", "New York, NY", "Lodz, Poland"]
)
def test_jobs_placed_outside_israel_are_rejected(location: str) -> None:
    result = prefilter(job(location=location))
    assert result.rejections == (f"location outside Israel: {location!r}",)


@pytest.mark.parametrize("location", ["Remote", "remote / hybrid", "EMEA - Remote", "Anywhere"])
def test_a_location_that_names_no_place_is_kept_with_a_note(location: str) -> None:
    result = prefilter(job(location=location))
    assert result.kept
    assert result.notes == (f"location names no place: {location!r}",)


def test_an_empty_location_is_kept_with_a_note() -> None:
    result = prefilter(job(location=""))
    assert result.kept
    assert result.notes == ("no location",)


@pytest.mark.parametrize(
    ("title", "word"),
    [
        ("Senior Backend Engineer", "Senior"),
        ("Sr. Data Engineer", "Sr"),
        ("Data Warehouse Tech Lead", "Lead"),
        ("Full Stack Team Leader", "Leader"),
        ("Staff Software Engineer", "Staff"),
        ("Growth Product Manager", "Manager"),
        ("Head of Data Science", "Head"),
        ("VP R&D", "VP"),
        ("Software Architect", "Architect"),
        ("Mid-Level Python Developer", "Mid-Level"),
    ],
)
def test_senior_titles_are_rejected(title: str, word: str) -> None:
    assert prefilter(job(title=title)).rejections == (f"senior title: {word!r}",)


@pytest.mark.parametrize(
    "title",
    [
        "Junior User Acquisition Manager",
        "Employer Branding Student (Temporary )",
        "Team Lead Intern",
        "Leading Edge Engineer",  # "Leading" is not "Lead"
        "Manager, International Compensation",  # still senior: "International" is not "intern"
    ],
)
def test_student_junior_and_intern_titles_are_exempt_but_only_as_whole_words(title: str) -> None:
    result = prefilter(job(title=title))
    assert result.kept is (title != "Manager, International Compensation")


def test_both_rules_are_reported_when_both_fail() -> None:
    result = prefilter(job(title="Senior Engineer", location="London"))
    assert result.rejections == ("location outside Israel: 'London'", "senior title: 'Senior'")


def test_the_description_is_not_read() -> None:
    # "5+ years" is in the description: deciding on experience is the extractor's job.
    assert prefilter(job()).kept


def test_rules_can_be_replaced() -> None:
    rules = FilterRules(seniority_words=("principal",), seniority_exempt_words=())
    assert prefilter(job(title="Senior Engineer"), rules).kept
    assert not prefilter(job(title="Principal Engineer"), rules).kept


def test_no_seniority_words_rejects_no_title() -> None:
    assert prefilter(job(title="Senior Engineer"), FilterRules(seniority_words=())).kept

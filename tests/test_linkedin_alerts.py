from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

from jobfinder.sources.linkedin_alerts import alert_text, job_url, parse_alert

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / f"linkedin_alert_{name}.txt").read_text(encoding="utf-8")


def summary(name: str) -> list[tuple[str, str, str, str]]:
    result = parse_alert(fixture(name))
    assert result.skipped == []
    return [(j.job_id, j.title, j.company, j.location) for j in result.jobs]


def test_english_alert() -> None:
    assert summary("en") == [
        ("4282849155", "Software Engineering Student", "NiCE", "Raanana"),
        ("4290957071", "QA Intern (Automation and Manual testing)", "Hewlett Packard Enterprise", "Herzliya"),
        ("4282842665", "DevOps Student", "NiCE", "Raanana"),
    ]


def test_hebrew_alert_drops_direction_marks_and_extra_lines() -> None:
    jobs = summary("he")
    assert len(jobs) == 6
    assert jobs[0] == ("4481322254", "Junior MLOps Engineer", "Dell Technologies", "Herzliya")
    # This job has "16 connections" (in Hebrew, wrapped in direction marks) instead of "actively hiring".
    assert jobs[3] == ("4481177279", "Software Developer", "NVIDIA", "Raanana")
    assert jobs[4] == ("4455799034", "2026 Graduate Software Dev Engineer", "Amazon Web Services (AWS)", "Tel Aviv-Yafo")


def test_single_job_hebrew_alert_whose_spacer_trails_the_header() -> None:
    assert summary("he_single") == [
        ("4483212741", "Deep Learning Algorithm Developer - M.Sc. Graduate", "Mobileye", "Jerusalem District, Israel")
    ]


def test_alert_created_email_without_blank_line_before_extra_lines() -> None:
    jobs = summary("created_en")
    assert len(jobs) == 6
    assert jobs[0] == ("4210733882", "QA Automation Engineer-R-245377", "Mastercard", "Ramat Gan, Tel Aviv District, Israel")
    assert jobs[1][1:] == ("Backend Developer", "Experis Israel", "Raanana, Center District, Israel")


def test_jobs_are_keyed_by_linkedin_id_with_a_clean_url_and_no_description() -> None:
    job = parse_alert(fixture("en")).jobs[0]
    assert job.key == "linkedin:alerts:4282849155"
    assert job.url == job_url("4282849155") == "https://www.linkedin.com/jobs/view/4282849155/"
    assert job.description_text is None
    assert job.published_at is None


def test_a_link_without_a_job_block_is_skipped_with_a_reason() -> None:
    text = "Your job alert\nView job: https://www.linkedin.com/comm/jobs/view/123/?x=1\n"
    result = parse_alert(text)
    assert (result.jobs, result.skipped) == ([], ["123: no job block before the link"])


def test_a_block_with_too_few_lines_is_skipped_with_a_reason() -> None:
    text = "header\n            \nOnly a title\nView job: https://www.linkedin.com/comm/jobs/view/123/\n"
    assert parse_alert(text).skipped == ["123: expected title, company and location, got ['Only a title']"]


def test_text_without_job_links_has_no_jobs() -> None:
    result = parse_alert("Manage your job alerts: https://www.linkedin.com/comm/jobs/alerts\n")
    assert (result.jobs, result.skipped) == ([], [])


@pytest.mark.parametrize("charset", ["utf-8", "iso-8859-8"])
def test_alert_text_decodes_the_plain_part(charset: str) -> None:
    message = EmailMessage()
    message.set_content("שלום View job", charset=charset)
    message.add_alternative("<p>html</p>", subtype="html")
    assert (alert_text(message) or "").strip() == "שלום View job"


def test_alert_text_is_none_without_a_plain_part() -> None:
    message = EmailMessage()
    message.set_content("<p>html</p>", subtype="html")
    assert alert_text(message) is None

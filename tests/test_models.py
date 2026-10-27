from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jobfinder.models import Job

VALID: dict[str, object] = {
    "source": "greenhouse",
    "slug": "wizinc",
    "job_id": "4567",
    "company": "Wiz",
    "title": "Backend Engineer",
    "location": "Tel Aviv",
    "url": "https://job-boards.greenhouse.io/wizinc/jobs/4567",
    "description_text": "Build things.",
}


def make(**overrides: object) -> Job:
    return Job.model_validate({**VALID, **overrides})


def test_key_is_source_slug_and_job_id() -> None:
    assert make().key == "greenhouse:wizinc:4567"


def test_same_ids_give_same_key_even_if_content_changes() -> None:
    assert make(title="Backend Engineer II").key == make().key


def test_strips_whitespace() -> None:
    job = make(title="  Backend Engineer \n", company=" Wiz ")
    assert (job.title, job.company) == ("Backend Engineer", "Wiz")


@pytest.mark.parametrize("field", ["source", "slug", "job_id", "company", "title", "url", "description_text"])
def test_rejects_empty_required_text(field: str) -> None:
    with pytest.raises(ValidationError):
        make(**{field: "   "})


def test_description_may_be_missing_but_not_empty() -> None:
    assert make(description_text=None).description_text is None
    without = {k: v for k, v in VALID.items() if k != "description_text"}
    assert Job.model_validate(without).description_text is None


def test_location_may_be_empty() -> None:
    assert make(location="").location == ""


@pytest.mark.parametrize("field", ["source", "slug", "job_id"])
def test_rejects_colon_in_key_parts(field: str) -> None:
    with pytest.raises(ValidationError):
        make(**{field: "a:b"})


def test_rejects_non_http_url() -> None:
    with pytest.raises(ValidationError, match="http"):
        make(url="javascript:alert(1)")


def test_published_at_must_be_timezone_aware() -> None:
    aware = datetime(2026, 10, 20, 10, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert make(published_at=aware).published_at == aware
    assert make(published_at="2026-10-20T10:00:00Z").published_at == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        make(published_at=datetime(2026, 10, 20, 10, 0))


def test_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make(salary="lots")


def test_is_immutable() -> None:
    job = make()
    with pytest.raises(ValidationError):
        job.title = "Other"  # type: ignore[misc]

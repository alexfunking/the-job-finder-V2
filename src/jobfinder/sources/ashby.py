"""Ashby public Posting API: one request per board returns every listed job with its full description."""

from __future__ import annotations

from typing import Final

from pydantic import ValidationError

from jobfinder.http import FetchError, Fetcher
from jobfinder.models import Job
from jobfinder.sources.base import SourceResult
from jobfinder.text import html_to_text

JOBS_URL: Final = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


class AshbySource:
    name = "ashby"

    def fetch_jobs(self, slug: str, company: str, fetcher: Fetcher) -> SourceResult:
        payload = fetcher.get_json(JOBS_URL.format(slug=slug))
        if payload is None:
            raise FetchError(f"ashby board not found: {slug}")
        jobs_raw = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs_raw, list):
            raise FetchError(f"ashby:{slug}: unexpected payload shape")

        jobs: list[Job] = []
        skipped: list[str] = []
        for raw in jobs_raw:
            if not isinstance(raw, dict):
                skipped.append(f"non-object entry: {type(raw).__name__}")
                continue
            # Unlisted postings are reachable only by direct link; the company chose not to advertise them.
            if raw.get("isListed") is False:
                continue
            try:
                jobs.append(self._to_job(raw, slug, company))
            except ValidationError as exc:
                fields = ", ".join(str(error["loc"][0]) for error in exc.errors())
                skipped.append(f"{raw.get('id', '?')}: invalid {fields}")
        return SourceResult(jobs=jobs, skipped=skipped)

    def _to_job(self, raw: dict[str, object], slug: str, company: str) -> Job:
        description_html = raw.get("descriptionHtml")
        description_html = description_html if isinstance(description_html, str) else ""
        # Rendering the HTML keeps list structure and matches Greenhouse's text; descriptionPlain is the fallback.
        description_text = html_to_text(description_html)
        if not description_text:
            plain = raw.get("descriptionPlain")
            description_text = plain if isinstance(plain, str) else ""
        return Job.model_validate({
            "source": self.name,
            "slug": slug,
            "job_id": str(raw.get("id", "")),
            "company": company,
            "title": raw.get("title"),
            "location": _location(raw),
            "url": raw.get("jobUrl"),
            "description_text": description_text,
            "description_html": description_html or None,
            "published_at": raw.get("publishedAt"),
        })


def _location(raw: dict[str, object]) -> str:
    """Primary location plus every secondary one, so a London job also open in Tel Aviv reads as Israel."""
    primary = raw.get("location")
    parts = [primary] if isinstance(primary, str) and primary.strip() else []
    secondary = raw.get("secondaryLocations")
    if isinstance(secondary, list):
        for entry in secondary:
            name = entry.get("location") if isinstance(entry, dict) else None
            if isinstance(name, str) and name.strip() and name not in parts:
                parts.append(name)
    return " | ".join(parts)

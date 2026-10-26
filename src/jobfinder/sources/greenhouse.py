"""Greenhouse public Job Board API: one request per board returns every open job with its full description."""

from __future__ import annotations

import html
from typing import Final

from pydantic import ValidationError

from jobfinder.http import FetchError, Fetcher
from jobfinder.models import Job
from jobfinder.sources.base import SourceResult
from jobfinder.text import html_to_text

JOBS_URL: Final = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


class GreenhouseSource:
    name = "greenhouse"

    def fetch_jobs(self, slug: str, company: str, fetcher: Fetcher) -> SourceResult:
        payload = fetcher.get_json(JOBS_URL.format(slug=slug))
        if payload is None:
            raise FetchError(f"greenhouse board not found: {slug}")
        jobs_raw = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs_raw, list):
            raise FetchError(f"greenhouse:{slug}: unexpected payload shape")

        jobs: list[Job] = []
        skipped: list[str] = []
        for raw in jobs_raw:
            if not isinstance(raw, dict):
                skipped.append(f"non-object entry: {type(raw).__name__}")
                continue
            try:
                jobs.append(self._to_job(raw, slug, company))
            except ValidationError as exc:
                fields = ", ".join(str(error["loc"][0]) for error in exc.errors())
                skipped.append(f"{raw.get('id', '?')}: invalid {fields}")
        return SourceResult(jobs=jobs, skipped=skipped)

    def _to_job(self, raw: dict[str, object], slug: str, company: str) -> Job:
        # Greenhouse returns the description HTML entity-escaped ("&lt;p&gt;"), so it is unescaped once here.
        content = raw.get("content")
        description_html = html.unescape(content) if isinstance(content, str) else ""
        location = raw.get("location")
        location_name = location.get("name") if isinstance(location, dict) else None
        return Job.model_validate({
            "source": self.name,
            "slug": slug,
            "job_id": str(raw.get("id", "")),
            "company": company,
            "title": raw.get("title"),
            "location": location_name if isinstance(location_name, str) else "",
            "url": raw.get("absolute_url"),
            "description_text": html_to_text(description_html),
            "description_html": description_html or None,
            # first_published is the real publish date; updated_at changes on every edit.
            "published_at": raw.get("first_published"),
        })

"""Source-independent data model: every source converts its API payload into these types."""

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
# Parts of Job.key; a ":" inside one would make two different jobs share a key.
KeyPart = Annotated[str, Field(min_length=1, pattern=r"^[^:]+$")]


class Job(BaseModel):
    """One open posting as collected from a source, before any scoring."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    source: KeyPart
    slug: KeyPart
    job_id: KeyPart
    company: NonEmptyStr
    title: NonEmptyStr
    location: str
    url: NonEmptyStr
    # None only for sources that carry no description at all (email alerts). An empty description is
    # still invalid, so a board posting that lost its text is skipped rather than stored blank.
    description_text: NonEmptyStr | None = None
    description_html: str | None = None
    published_at: AwareDatetime | None = None

    @field_validator("url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("url must start with http:// or https://")
        return value

    @property
    def key(self) -> str:
        """Stable identity across scans (ADR-003): the ATS's own job id, scoped by source and board."""
        return f"{self.source}:{self.slug}:{self.job_id}"

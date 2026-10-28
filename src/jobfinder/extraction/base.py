"""Structured fields from a job posting, read by a language model (Phase 2, ADR-006).

The model only reads public posting text and reports facts about the job. It never sees priorities.txt
and never scores: scoring is done in code from these fields. Every implementation (an API model, a local
one through Ollama) gets the same instructions and the same JSON schema, so they can be compared on the
eval set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from jobfinder.models import Job

# Long postings are mostly benefits and boilerplate; the requirements come first. Caps the cost per job.
MAX_DESCRIPTION_CHARS: Final = 12_000
MAX_STACK_ITEMS: Final = 15


class ExtractionError(Exception):
    """The model could not be reached, or its answer is not a valid Extraction."""


class Extraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    years_required: Annotated[int, Field(ge=0, le=40)] | None
    onsite_days: Annotated[int, Field(ge=0, le=5)] | None
    is_student: bool
    location: str | None
    stack: tuple[str, ...]

    @field_validator("location")
    @classmethod
    def _blank_location_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("stack")
    @classmethod
    def _clean_stack(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in (item.strip() for item in value):
            if item and item.lower() not in seen:
                seen.add(item.lower())
                cleaned.append(item)
        return tuple(cleaned[:MAX_STACK_ITEMS])


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    extraction: Extraction
    # Token counts as the provider reports them (thinking tokens count as output); None if not reported.
    input_tokens: int | None
    output_tokens: int | None


class Extractor(Protocol):
    @property
    def name(self) -> str:
        """Provider and model, e.g. "gemini:gemini-3.5-flash": results are compared per name."""
        ...

    def extract(self, job: Job) -> ExtractionResult: ...


INSTRUCTIONS: Final = f"""\
You extract facts from one job posting. Answer only from the posting and do not guess: when the posting \
does not say, use null.

- years_required: the minimum years of professional experience the posting requires, as an integer. For \
a range ("2-4 years") use the lower bound. If it says no experience is needed, 0. A degree is not \
experience.
- onsite_days: days per week in the office. Fully on-site: 5. Fully remote: 0. Hybrid with a number of \
days: that number. Hybrid without a number, or not stated: null.
- is_student: true only if the role is meant for current students (student position, internship, \
part-time for students). New-grad, graduate and junior roles are false unless they are also for students.
- location: the city where the job is based, in English (for example "Tel Aviv"), or "Remote", or null. \
If several are listed, the first one.
- stack: the technologies, programming languages, frameworks and tools the posting asks for, as short \
canonical names (for example "Python", "Kubernetes", "React"), most important first, at most \
{MAX_STACK_ITEMS}. An empty list if none.

Some postings have only a title and no description. Then take from the title only what it states, and \
use null for the rest."""

# Written by hand rather than generated from Extraction, so it stays inside the JSON Schema subset that
# both Gemini (responseJsonSchema) and Ollama (format) accept.
SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        "years_required": {"type": ["integer", "null"], "minimum": 0},
        "onsite_days": {"type": ["integer", "null"], "minimum": 0, "maximum": 5},
        "is_student": {"type": "boolean"},
        "location": {"type": ["string", "null"]},
        "stack": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["years_required", "onsite_days", "is_student", "location", "stack"],
    "additionalProperties": False,
}


def job_prompt(job: Job) -> str:
    lines = [f"Title: {job.title}", f"Company: {job.company}", f"Location: {job.location or '(not given)'}"]
    if job.description_text is None:
        lines.append("Description: (none, only the title is known)")
    else:
        description = job.description_text
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS] + "\n[truncated]"
        lines.extend(["Description:", description])
    return "\n".join(lines)


def parse_extraction(text: str) -> Extraction:
    """The model's JSON answer as an Extraction; anything else is an ExtractionError."""
    # Some models wrap JSON in a Markdown code fence even when asked for JSON only.
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return Extraction.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ExtractionError(f"invalid answer: {exc}; got {text[:200]!r}") from exc

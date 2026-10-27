"""Deterministic pre-filter: drop jobs that are clearly out by location or by seniority in the title.

It runs before the extractor, so every job it drops is one the model never sees. That makes a wrong
rejection expensive and a wrong pass cheap: when in doubt it keeps the job. Anything that needs reading
the description (years of experience, days on site) is the extractor's job, not this one.

Location is matched positively (the job must name a place in Israel) instead of with a list of foreign
places: the old geo_filter rejected ", il" for Illinois, which also rejected "Tel Aviv, IL".
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Final

from jobfinder.geo import is_israel_location
from jobfinder.models import Job


@functools.cache
def _words(words: tuple[str, ...]) -> re.Pattern[str] | None:
    # No words means "never matches"; an empty alternation would match every string.
    if not words:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b", re.IGNORECASE)


def _find_word(words: tuple[str, ...], text: str) -> str | None:
    pattern = _words(words)
    found = pattern.search(text) if pattern else None
    return found.group(0) if found else None


@dataclass(frozen=True, slots=True)
class FilterRules:
    # From the old priorities: titles with these words are rejected, unless the title also says it is
    # a student / intern / junior role.
    seniority_words: tuple[str, ...] = (
        "senior", "sr", "lead", "leader", "principal", "staff", "manager", "director", "vp", "head",
        "chief", "architect", "mid-level", "mid level",
    )
    seniority_exempt_words: tuple[str, ...] = ("student", "intern", "internship", "junior")
    # A location made only of these words does not say where the job is, so it is kept for the extractor.
    unplaced_location_words: tuple[str, ...] = (
        "remote", "anywhere", "worldwide", "global", "emea", "mena", "hybrid",
    )


DEFAULT_RULES: Final = FilterRules()


@dataclass(frozen=True, slots=True)
class FilterResult:
    rejections: tuple[str, ...] = ()
    # Kept jobs the extractor has to look at more closely, e.g. a location that names no place.
    notes: tuple[str, ...] = ()

    @property
    def kept(self) -> bool:
        return not self.rejections


def _is_unplaced(location: str, rules: FilterRules) -> bool:
    tokens = re.findall(r"[a-z]+", location.lower())
    return all(token in rules.unplaced_location_words for token in tokens)


def prefilter(job: Job, rules: FilterRules = DEFAULT_RULES) -> FilterResult:
    rejections: list[str] = []
    notes: list[str] = []

    if not is_israel_location(job.location):
        if _is_unplaced(job.location, rules):
            notes.append(f"location names no place: {job.location!r}" if job.location else "no location")
        else:
            rejections.append(f"location outside Israel: {job.location!r}")

    senior = _find_word(rules.seniority_words, job.title)
    if senior and not _find_word(rules.seniority_exempt_words, job.title):
        rejections.append(f"senior title: {senior!r}")

    return FilterResult(rejections=tuple(rejections), notes=tuple(notes))

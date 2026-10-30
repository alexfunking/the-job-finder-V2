"""Score a job against the user's priorities, in code: the model only reads facts, it never sees these.

The priorities live in `priorities.txt` (TOML, personal, gitignored). A score has four parts that add up
to 100: level (40), field (30), location (20) and days in the office (10). Some findings reject a job
outright (it needs too many years, the field is one the user does not want, it is too far).

A job is a match when nothing rejects it, its level is known to fit, and it scores at least the
threshold. A job that nothing rejects but whose level is unknown (a title-only email alert without
"junior" in it, a posting that names no years) is a "maybe": worth a look, not a recommendation.
"""

from __future__ import annotations

import functools
import re
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from jobfinder.extraction import Extraction
from jobfinder.geo import is_israel_location
from jobfinder.models import Job

LEVEL_POINTS: Final = 40
FIELD_POINTS: Final = 30
SKILLS_ONLY_POINTS: Final = 15
LOCATION_POINTS: Final = 20
LOW_PRIORITY_LOCATION_POINTS: Final = 8
UNLISTED_LOCATION_POINTS: Final = 10
ONSITE_POINTS: Final = 10
UNKNOWN_ONSITE_POINTS: Final = 5
POINTS_PER_EXTRA_OFFICE_DAY: Final = 3


class PrioritiesError(Exception):
    """priorities.txt is missing, is not valid TOML, or lacks a setting."""


@dataclass(frozen=True, slots=True)
class Priorities:
    max_years: int
    entry_title_words: tuple[str, ...]
    field_title_words: tuple[str, ...]
    reject_title_words: tuple[str, ...]
    skills: tuple[str, ...]
    accept_locations: tuple[str, ...]
    low_priority_locations: tuple[str, ...]
    reject_locations: tuple[str, ...]
    preferred_max_onsite_days: int
    match_threshold: int


def load_priorities(path: Path) -> Priorities:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PrioritiesError(f"no priorities file at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PrioritiesError(f"{path}: {exc}") from exc

    def get(section: str, name: str) -> object:
        table = data.get(section)
        if not isinstance(table, dict) or name not in table:
            raise PrioritiesError(f"{path}: missing [{section}] {name}")
        value: object = table[name]
        return value

    def words(section: str, name: str) -> tuple[str, ...]:
        value = get(section, name)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise PrioritiesError(f"{path}: [{section}] {name} must be a list of strings")
        return tuple(str(item) for item in value)

    def number(section: str, name: str) -> int:
        value = get(section, name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise PrioritiesError(f"{path}: [{section}] {name} must be a whole number")
        return value

    return Priorities(
        max_years=number("role", "max_years"),
        entry_title_words=words("role", "entry_title_words"),
        field_title_words=words("role", "field_title_words"),
        reject_title_words=words("role", "reject_title_words"),
        skills=words("profile", "skills"),
        accept_locations=words("location", "accept"),
        low_priority_locations=words("location", "low_priority"),
        reject_locations=words("location", "reject"),
        preferred_max_onsite_days=number("onsite", "preferred_max_days"),
        match_threshold=number("score", "match_threshold"),
    )


@functools.cache
def _pattern(words: tuple[str, ...]) -> re.Pattern[str] | None:
    # All-capital words (IT, HR, QA, BI) match only in capitals, so "IT" never matches the word "it".
    # A hyphen joins a word to the next one ("IT-Security"), but "Student- Glil Yam" still ends the word.
    if not words:
        return None
    alternatives = [re.escape(word) if word.isupper() else f"(?i:{re.escape(word)})" for word in words]
    return re.compile(r"(?<![\w-])(?:" + "|".join(alternatives) + r")(?![\w]|-\w)")


def find_word(words: tuple[str, ...], text: str) -> str | None:
    pattern = _pattern(words)
    found = pattern.search(text) if pattern else None
    return found.group(0) if found else None


class Verdict(StrEnum):
    MATCH = "match"
    MAYBE = "maybe"
    NO = "no"


@dataclass(frozen=True, slots=True)
class Score:
    points: int
    verdict: Verdict
    # Why each part scored what it did, in the order level, field, location, office days.
    reasons: tuple[str, ...]
    rejections: tuple[str, ...]


def score_job(job: Job, extraction: Extraction | None, priorities: Priorities) -> Score:
    """Score one job; `extraction` is None when no extractor has read it yet (the title still counts)."""
    reasons: list[str] = []
    rejections: list[str] = []

    # Level: an entry-level title or a student role fits; otherwise the years the posting asks for decide.
    level_known = True
    years = extraction.years_required if extraction else None
    entry_word = find_word(priorities.entry_title_words, job.title)
    if extraction is not None and extraction.is_student:
        level, note = LEVEL_POINTS, "student role"
    elif entry_word:
        level, note = LEVEL_POINTS, f"entry-level title ({entry_word})"
    elif years and years <= priorities.max_years:  # models write 0 for postings that state no years
        level, note = LEVEL_POINTS, f"asks {years} years"
    else:
        level, level_known = 0, False
        note = f"asks {years} years" if years else "years not stated"
    if years is not None and years > priorities.max_years:
        rejections.append(f"asks {years} years (you accept up to {priorities.max_years})")
    reasons.append(f"level {level}/{LEVEL_POINTS}: {note}")

    # Field: the title says it, or failing that the posting's stack overlaps the user's skills.
    unwanted = find_word(priorities.reject_title_words, job.title)
    wanted = find_word(priorities.field_title_words, job.title)
    shared = _shared_skills(extraction, priorities) if extraction else ()
    if unwanted:
        field, note = 0, f"not your field ({unwanted})"
        rejections.append(f"field: {unwanted}")
    elif wanted:
        field, note = FIELD_POINTS, f"your field ({wanted})"
    elif shared:
        field, note = SKILLS_ONLY_POINTS, f"uses your skills ({', '.join(shared[:3])})"
    else:
        field, note = 0, "field not clearly yours"
    if shared and field == FIELD_POINTS:
        note += f"; uses {', '.join(shared[:3])}"
    reasons.append(f"field {field}/{FIELD_POINTS}: {note}")

    # Location: the posting's own location, plus the extractor's reading of it when there is one.
    where = " / ".join(part for part in (job.location, extraction.location if extraction else None) if part)
    if place := find_word(priorities.accept_locations, where):
        location, note = LOCATION_POINTS, place
    elif place := find_word(priorities.low_priority_locations, where):
        location, note = LOW_PRIORITY_LOCATION_POINTS, f"{place} (far)"
    elif place := find_word(priorities.reject_locations, where):
        location, note = 0, f"{place} (too far)"
        rejections.append(f"location: {place}")
    elif is_israel_location(where):
        location, note = UNLISTED_LOCATION_POINTS, f"{where} (not on your list)"
    else:
        location, note = 0, f"{where or 'no location'} (not in Israel)"
        rejections.append(f"location: {where or 'none'}")
    reasons.append(f"location {location}/{LOCATION_POINTS}: {note}")

    # Days in the office: more than preferred only lowers the score, it never rejects.
    days = extraction.onsite_days if extraction else None
    if days is None:
        onsite, note = UNKNOWN_ONSITE_POINTS, "not stated"
    else:
        extra = max(0, days - priorities.preferred_max_onsite_days)
        onsite, note = max(0, ONSITE_POINTS - POINTS_PER_EXTRA_OFFICE_DAY * extra), f"{days} days a week"
    reasons.append(f"office {onsite}/{ONSITE_POINTS}: {note}")

    points = level + field + location + onsite
    if rejections:
        verdict = Verdict.NO
    elif level_known and field > 0 and points >= priorities.match_threshold:
        verdict = Verdict.MATCH
    else:
        verdict = Verdict.MAYBE
    return Score(points=points, verdict=verdict, reasons=tuple(reasons), rejections=tuple(rejections))


def _shared_skills(extraction: Extraction, priorities: Priorities) -> tuple[str, ...]:
    mine = {skill.lower(): skill for skill in priorities.skills}
    return tuple(mine[item.lower()] for item in extraction.stack if item.lower() in mine)

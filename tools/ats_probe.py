"""Phase 0 probe: find which ATS each company uses and how many Israel jobs it exposes.

Usage:
    python tools/ats_probe.py data/companies.csv --out data/ats_map.csv

Input CSV columns: name, slugs (optional, ';'-separated), careers_url (optional).
For every company the probe tries the public read-only job-board APIs of
Greenhouse, Lever, Ashby and SmartRecruiters with candidate slugs, and, when a
careers_url is given, fingerprints the careers page HTML to detect ATSs that
have no slug-based public API (Comeet, Workday, ...).
A board is rejected when the name it reports does not match the company, or when
its newest posting is older than STALE_AFTER_DAYS (an abandoned account).
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import csv
import functools
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

USER_AGENT: Final = "job-finder-ats-probe/0.1 (personal job search; contact via GitHub)"
TIMEOUT_S: Final = 15.0
MAX_WORKERS: Final = 4
STALE_AFTER_DAYS: Final = 180
MAX_ERRORS_IN_CSV: Final = 3
# Caps paging on huge boards (e.g. 5000+ postings); counts for those are a lower bound.
MAX_PAGES: Final = 10

ISRAEL_KEYWORDS: Final[tuple[str, ...]] = (
    "israel", "tel aviv", "tel-aviv", "tlv", "jaffa", "yafo", "herzliya", "glilot", "haifa",
    "jerusalem", "petah tikva", "petach tikva", "petah-tikva", "petach-tikva", "ramat gan",
    "ramat hahayal", "ramat hachayal", "raanana", "ra'anana", "rehovot", "rishon", "netanya",
    "beer sheva", "be'er sheva", "yokneam", "hod hasharon", "kfar saba", "airport city",
    "or yehuda", "caesarea", "modiin", "givatayim", "bnei brak", "holon", "lod", "ness ziona",
    "rosh haayin", "rosh ha'ayin", "ashdod", "nazareth", "kiryat",
)
# Whole words only: a plain substring check would match "lod" inside "Lodz" or "holon" inside "Holonet".
ISRAEL_PATTERN: Final = re.compile(
    r"\b(?:" + "|".join(re.escape(keyword) for keyword in ISRAEL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

NAME_SUFFIXES: Final[tuple[str, ...]] = (
    " ltd", " inc", " technologies", " technology", " software", " security",
    " networks", " group", " labs", " io", ".com", ".io", ".ai",
)

FINGERPRINTS: Final[dict[str, tuple[str, ...]]] = {
    "greenhouse": ("boards.greenhouse.io", "job-boards.greenhouse.io", "grnh.se"),
    "lever": ("jobs.lever.co",),
    "comeet": ("comeet.co", "comeet.com"),
    "workday": ("myworkdayjobs.com",),
    "smartrecruiters": ("smartrecruiters.com",),
    "ashby": ("jobs.ashbyhq.com",),
    "bamboohr": ("bamboohr.com/careers", ".bamboohr.com"),
    "workable": ("apply.workable.com",),
    "icims": ("icims.com",),
    "successfactors": ("successfactors.com", "sapsf.com"),
}


@dataclass(frozen=True, slots=True)
class Company:
    name: str
    slugs: tuple[str, ...]
    careers_url: str | None


@dataclass(frozen=True, slots=True)
class Posting:
    location: str
    published: date | None


@dataclass(frozen=True, slots=True)
class BoardHit:
    ats: str
    slug: str
    total_jobs: int
    israel_jobs: int
    latest_posting: date | None = None
    board_name: str | None = None
    # Fetch errors after the first page: the hit is kept, but its counts or name may be incomplete.
    notes: tuple[str, ...] = ()


@dataclass(slots=True)
class ProbeResult:
    company: Company
    hits: list[BoardHit] = field(default_factory=list)
    rejected: list[tuple[BoardHit, str]] = field(default_factory=list)
    fingerprints: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def best_hit(self) -> BoardHit | None:
        return max(self.hits, key=lambda h: (h.israel_jobs, h.total_jobs)) if self.hits else None

    @property
    def verdict(self) -> str:
        best = self.best_hit
        if best is not None:
            return f"{best.ats}:{best.slug}"
        if self.fingerprints:
            return "fingerprint:" + "+".join(sorted(set(self.fingerprints)))
        return "unknown"


def _ascii_fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _strip_suffixes(name: str) -> str:
    stripped = name
    for suffix in NAME_SUFFIXES:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].strip()
    return stripped


def candidate_slugs(name: str) -> tuple[str, ...]:
    """Generate likely job-board slugs from a company name, most likely first."""
    base = _ascii_fold(name).lower().strip()
    variants: list[str] = [base, _strip_suffixes(base)]

    slugs: list[str] = []
    for variant in variants:
        words = re.findall(r"[a-z0-9]+", variant)
        if not words:
            continue
        for slug in ("".join(words), "-".join(words), words[0]):
            if slug not in slugs:
                slugs.append(slug)
    return tuple(slugs)


def _name_keys(name: str) -> set[str]:
    # Punctuation becomes spaces so "Wiz, Inc." still ends in " inc"; inner dots survive for ".com" / ".io".
    base = re.sub(r"[^a-z0-9.]+", " ", _ascii_fold(name).lower()).strip(" .")
    keys = {"".join(re.findall(r"[a-z0-9]+", variant)) for variant in (base, _strip_suffixes(base))}
    keys.discard("")
    return keys


def names_match(company_name: str, board_name: str) -> bool:
    """True when the board's reported name is the company's name, ignoring case, punctuation and legal suffixes."""
    return not _name_keys(company_name).isdisjoint(_name_keys(board_name))


def is_israel_location(location: str) -> bool:
    return ISRAEL_PATTERN.search(location) is not None


def rejection_reason(hit: BoardHit, company_name: str, today: date) -> str | None:
    """Why a board that answered for a candidate slug is not the company's live board, or None if it is."""
    if hit.board_name is not None and not names_match(company_name, hit.board_name):
        return f"name mismatch: {hit.board_name.strip()!r}"
    if hit.latest_posting is not None and (today - hit.latest_posting).days > STALE_AFTER_DAYS:
        return f"stale: latest posting {hit.latest_posting.isoformat()}"
    return None


def detect_fingerprints(html: str) -> list[str]:
    lowered = html.lower()
    return [ats for ats, needles in FINGERPRINTS.items() if any(n in lowered for n in needles)]


def _fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            status: int = response.status
            body = response.read().decode("utf-8", errors="replace")
            return status, body
    except urllib.error.HTTPError as exc:
        return exc.code, ""


class FetchError(Exception):
    """An HTTP status that says nothing about whether the board exists (rate limit, server error, ...)."""


FETCH_ERRORS: Final[tuple[type[Exception], ...]] = (urllib.error.URLError, TimeoutError, OSError, FetchError)


def _fetch_json(url: str) -> object | None:
    """Return the parsed JSON, None when the board does not exist (404), or raise FetchError."""
    status, body = _fetch(url)
    if status == 404:
        return None
    if status != 200:
        raise FetchError(f"HTTP {status}")
    if not body:
        return None
    try:
        payload: object = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload


def _parse_date(value: object) -> date | None:
    """Parse an ISO-8601 string or a Unix timestamp in milliseconds (Lever)."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    if isinstance(value, int) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, tz=UTC).date()
    return None


def _postings_greenhouse(payload: object) -> list[Posting] | None:
    if not isinstance(payload, dict):
        return None
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return None
    out: list[Posting] = []
    for job in jobs:
        if isinstance(job, dict):
            location = job.get("location")
            name = location.get("name") if isinstance(location, dict) else None
            out.append(Posting(name if isinstance(name, str) else "", _parse_date(job.get("updated_at"))))
    return out


def _postings_lever(payload: object) -> list[Posting] | None:
    if not isinstance(payload, list):
        return None
    out: list[Posting] = []
    for job in payload:
        if isinstance(job, dict):
            categories = job.get("categories")
            location = categories.get("location") if isinstance(categories, dict) else None
            all_locations = categories.get("allLocations") if isinstance(categories, dict) else None
            parts = [location] if isinstance(location, str) else []
            if isinstance(all_locations, list):
                parts.extend(loc for loc in all_locations if isinstance(loc, str))
            out.append(Posting(" | ".join(parts), _parse_date(job.get("createdAt"))))
    return out


def _postings_ashby(payload: object) -> list[Posting] | None:
    if not isinstance(payload, dict):
        return None
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return None
    out: list[Posting] = []
    for job in jobs:
        if isinstance(job, dict):
            location = job.get("location")
            parts = [location] if isinstance(location, str) else []
            secondary = job.get("secondaryLocations")
            if isinstance(secondary, list):
                for entry in secondary:
                    name = entry.get("location") if isinstance(entry, dict) else None
                    if isinstance(name, str):
                        parts.append(name)
            out.append(Posting(" | ".join(parts), _parse_date(job.get("publishedAt"))))
    return out


def _postings_smartrecruiters(payload: object) -> list[Posting] | None:
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    out: list[Posting] = []
    for job in content:
        if isinstance(job, dict):
            location = job.get("location")
            text = ""
            if isinstance(location, dict):
                city, country = location.get("city"), location.get("country")
                parts = [city] if isinstance(city, str) else []
                if isinstance(country, str):
                    # SmartRecruiters gives ISO country codes; "il" alone is too short to keyword-match.
                    parts.append("Israel" if country.lower() == "il" else country)
                text = " ".join(parts)
            out.append(Posting(text, _parse_date(job.get("releasedDate"))))
    return out


def _board_name_greenhouse(payload: object) -> str | None:
    name = payload.get("name") if isinstance(payload, dict) else None
    return name if isinstance(name, str) else None


def _board_name_smartrecruiters(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return None
    company = content[0].get("company")
    name = company.get("name") if isinstance(company, dict) else None
    return name if isinstance(name, str) else None


def _next_offset_smartrecruiters(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    offset, limit, total = payload.get("offset"), payload.get("limit"), payload.get("totalFound")
    if not (isinstance(offset, int) and isinstance(limit, int) and isinstance(total, int)) or limit <= 0:
        return None
    next_offset = offset + limit
    return next_offset if next_offset < total else None


@dataclass(frozen=True, slots=True)
class BoardApi:
    ats: str
    # Formatted with slug and offset; templates without {offset} return everything in one response.
    url_template: str
    parse_postings: Callable[[object], list[Posting] | None]
    parse_board_name: Callable[[object], str | None] | None = None
    # When set, the board name is read from this endpoint instead of the postings payload.
    name_url_template: str | None = None
    # Returns the offset of the next page, or None on the last page.
    next_offset: Callable[[object], int | None] | None = None


BOARD_APIS: Final[tuple[BoardApi, ...]] = (
    BoardApi(
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        _postings_greenhouse,
        _board_name_greenhouse,
        "https://boards-api.greenhouse.io/v1/boards/{slug}",
    ),
    BoardApi("lever", "https://api.lever.co/v0/postings/{slug}?mode=json", _postings_lever),
    BoardApi("ashby", "https://api.ashbyhq.com/posting-api/job-board/{slug}", _postings_ashby),
    BoardApi(
        "smartrecruiters",
        "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}",
        _postings_smartrecruiters,
        _board_name_smartrecruiters,
        next_offset=_next_offset_smartrecruiters,
    ),
)


def summarize_board(api: BoardApi, slug: str, postings: Sequence[Posting], board_name: str | None) -> BoardHit:
    dates = [p.published for p in postings if p.published is not None]
    return BoardHit(
        ats=api.ats,
        slug=slug,
        total_jobs=len(postings),
        israel_jobs=sum(1 for p in postings if is_israel_location(p.location)),
        latest_posting=max(dates, default=None),
        board_name=board_name,
    )


def probe_board(api: BoardApi, slug: str) -> BoardHit | None:
    payload = _fetch_json(api.url_template.format(slug=slug, offset=0))
    if payload is None:
        return None
    postings = api.parse_postings(payload)
    if not postings:
        return None
    notes: list[str] = []
    board_name: str | None = None
    if api.parse_board_name is not None:
        try:
            name_payload = payload if api.name_url_template is None else _fetch_json(api.name_url_template.format(slug=slug))
            board_name = api.parse_board_name(name_payload)
        except FETCH_ERRORS as exc:
            notes.append(f"board name: {exc}")
    pages = 1
    while api.next_offset is not None and pages < MAX_PAGES:
        offset = api.next_offset(payload)
        if offset is None:
            break
        try:
            payload = _fetch_json(api.url_template.format(slug=slug, offset=offset))
        except FETCH_ERRORS as exc:
            notes.append(f"page {pages + 1}: {exc}")
            break
        page = api.parse_postings(payload) if payload is not None else None
        if not page:
            break
        postings.extend(page)
        pages += 1
    return replace(summarize_board(api, slug, postings, board_name), notes=tuple(notes))


def probe_company(company: Company, today: date) -> ProbeResult:
    result = ProbeResult(company=company)
    slugs = company.slugs or candidate_slugs(company.name)
    for api in BOARD_APIS:
        for slug in slugs:
            try:
                hit = probe_board(api, slug)
            except FETCH_ERRORS as exc:
                result.errors.append(f"{api.ats}:{slug}: {exc}")
                continue
            if hit is None:
                continue
            result.errors.extend(f"{api.ats}:{slug}: {note}" for note in hit.notes)
            reason = rejection_reason(hit, company.name, today)
            if reason is None:
                result.hits.append(hit)
                break
            result.rejected.append((hit, reason))
    if company.careers_url:
        try:
            status, html = _fetch(company.careers_url)
            if status == 200:
                result.fingerprints.extend(detect_fingerprints(html))
            else:
                result.errors.append(f"careers_url: HTTP {status}")
        except FETCH_ERRORS as exc:
            result.errors.append(f"careers_url: {exc}")
    return result


def read_companies(path: Path) -> list[Company]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        companies: list[Company] = []
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name or name.startswith("#"):
                continue
            raw_slugs = (row.get("slugs") or "").strip()
            slugs = tuple(s.strip().lower() for s in raw_slugs.split(";") if s.strip())
            careers_url = (row.get("careers_url") or "").strip() or None
            companies.append(Company(name=name, slugs=slugs, careers_url=careers_url))
    return companies


def write_results(path: Path, results: Iterable[ProbeResult]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "name", "verdict", "ats", "slug", "total_jobs", "israel_jobs", "latest_posting",
            "fingerprints", "rejected", "errors",
        ])
        for result in results:
            best = result.best_hit
            writer.writerow([
                result.company.name,
                result.verdict,
                best.ats if best else "",
                best.slug if best else "",
                best.total_jobs if best else "",
                best.israel_jobs if best else "",
                best.latest_posting.isoformat() if best and best.latest_posting else "",
                ";".join(sorted(set(result.fingerprints))),
                " | ".join(f"{hit.ats}:{hit.slug} ({reason})" for hit, reason in result.rejected),
                format_errors(result.errors),
            ])


def format_errors(errors: Sequence[str]) -> str:
    shown = list(errors[:MAX_ERRORS_IN_CSV])
    hidden = len(errors) - len(shown)
    if hidden:
        shown.append(f"(+{hidden} more)")
    return " | ".join(shown)


def render_result_line(result: ProbeResult) -> str:
    line = f"{result.company.name:<28} {result.verdict}"
    if result.rejected:
        line += f"  (rejected: {', '.join(f'{h.ats}:{h.slug}' for h, _ in result.rejected)})"
    if result.errors:
        line += f"  (errors: {len(result.errors)})"
    return line


def render_summary(results: Sequence[ProbeResult]) -> str:
    by_verdict: dict[str, int] = {}
    for result in results:
        key = result.verdict.split(":", 1)[0]
        by_verdict[key] = by_verdict.get(key, 0) + 1
    israel_total = sum(max((h.israel_jobs for h in r.hits), default=0) for r in results)
    rejected_total = sum(len(r.rejected) for r in results)
    with_errors = sum(1 for r in results if r.errors)
    lines = [
        f"Companies probed: {len(results)}",
        f"Israel jobs via public APIs: {israel_total}",
        f"Boards rejected (name mismatch / stale): {rejected_total}",
        f"Companies with fetch errors (see errors column): {with_errors}",
    ]
    lines.extend(f"  {verdict}: {count}" for verdict, count in sorted(by_verdict.items()))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("companies", type=Path, help="CSV with columns: name, slugs, careers_url")
    parser.add_argument("--out", type=Path, default=Path("data/ats_map.csv"))
    args = parser.parse_args(argv)

    companies = read_companies(args.companies)
    if not companies:
        print("No companies found in input.", file=sys.stderr)
        return 1

    probe = functools.partial(probe_company, today=datetime.now(tz=UTC).date())
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(probe, companies))

    for result in results:
        print(render_result_line(result))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_results(args.out, results)
    print()
    print(render_summary(results))
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

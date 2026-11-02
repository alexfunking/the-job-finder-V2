"""The daily digest: what is new among the scored jobs, as text ready to send.

A digest holds jobs first seen in the last few days that were never sent, best first: matches, then the
strongest maybes in the user's field. Age is the only way an alert job leaves, because an alert job is never closed. Postings
with the same company and title are one item, across sources too, and once any of them was sent the rest
stay quiet. A job the user labeled not fit is never sent. Items over the caps wait for the next digest.
"""

from __future__ import annotations

import html
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from jobfinder.matches import MatchRow
from jobfinder.scoring import Verdict
from jobfinder.telegram import MESSAGE_LIMIT

DEFAULT_DAYS: Final = 7
MAX_ITEMS: Final = 15
MAX_MAYBES: Final = 5
MAYBE_MIN_POINTS: Final = 50


@dataclass(frozen=True, slots=True)
class DigestItem:
    """One job to show: the best of its postings, and every new posting it stands for."""

    best: MatchRow
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Digest:
    items: tuple[DigestItem, ...]
    waiting: int  # items left for the next digest by the caps


@dataclass(frozen=True, slots=True)
class DigestMessage:
    text: str
    keys: tuple[str, ...]  # the postings this message reports, to mark as sent once it is delivered


def _same_job(row: MatchRow) -> tuple[str, str]:
    job = row.stored.job
    return job.company.casefold(), job.title.casefold()


def _worth_sending(row: MatchRow) -> bool:
    if row.score.verdict == Verdict.MATCH:
        return True
    # Without field points a maybe is only a nearby entry-level job, mostly in some other line of work.
    score = row.score
    return score.verdict == Verdict.MAYBE and score.field_points > 0 and score.points >= MAYBE_MIN_POINTS


def select_digest(rows: Sequence[MatchRow], sent: Collection[str], now: datetime, days: int = DEFAULT_DAYS) -> Digest:
    """The items to send from `rows`, which must be in `match_rows` order, best first."""
    silent = {
        _same_job(row)
        for row in rows
        if row.stored.job.key in sent or (row.label is not None and row.label.verdict == "no_fit")
    }
    oldest = now - timedelta(days=days)
    groups: dict[tuple[str, str], list[MatchRow]] = {}
    for row in rows:
        if _same_job(row) not in silent and row.stored.first_seen >= oldest and _worth_sending(row):
            groups.setdefault(_same_job(row), []).append(row)

    matches = [group for group in groups.values() if group[0].score.verdict == Verdict.MATCH]
    maybes = [group for group in groups.values() if group[0].score.verdict != Verdict.MATCH]
    chosen = matches[:MAX_ITEMS]
    chosen += maybes[:min(MAX_MAYBES, MAX_ITEMS - len(chosen))]
    items = tuple(DigestItem(group[0], tuple(row.stored.job.key for row in group)) for group in chosen)
    return Digest(items, len(groups) - len(items))


def render_item(item: DigestItem) -> str:
    """One job in Telegram's HTML subset. Job text comes from outside, so all of it is escaped."""
    job, score = item.best.stored.job, item.best.score
    place = f"{job.company} · {job.location}" if job.location else job.company
    notes = [str(score.points)]
    if job.description_text is None:
        notes.append("title only")
    if len(item.keys) > 1:
        notes.append(f"{len(item.keys)} postings")
    link = f'<a href="{html.escape(job.url)}">Open</a>'
    return f"<b>{html.escape(job.title)}</b>\n{html.escape(place)}\n{' · '.join(notes)} · {link}"


def render_messages(digest: Digest) -> list[DigestMessage]:
    """The digest as messages within Telegram's limit; an empty digest gives none."""
    if not digest.items:
        return []
    matched = sum(1 for item in digest.items if item.best.score.verdict == Verdict.MATCH)
    title = f"<b>New jobs: {matched} match{'' if matched == 1 else 'es'}, {len(digest.items) - matched} maybe</b>"
    parts: list[DigestMessage] = [DigestMessage(title, ())]
    for position, item in enumerate(digest.items):
        if position == matched:
            parts.append(DigestMessage("<b>Maybe</b>", ()))
        parts.append(DigestMessage(render_item(item), item.keys))
    if digest.waiting:
        parts.append(DigestMessage(f"{digest.waiting} more in the next digest", ()))

    messages = [parts[0]]
    for part in parts[1:]:
        last = messages[-1]
        if len(last.text) + 2 + len(part.text) > MESSAGE_LIMIT:
            messages.append(part)
        else:
            messages[-1] = DigestMessage(f"{last.text}\n\n{part.text}", last.keys + part.keys)
    return messages

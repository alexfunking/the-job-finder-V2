"""The daily digest: what is new among the scored jobs, as messages with buttons, and the answers to them.

A digest holds jobs first seen in the last few days that were never sent, best first: matches, then the
strongest maybes in the user's field. Age is the only way an alert job leaves, because an alert job is
never closed. Postings with the same company and title are one item, across sources too, and once any of
them was sent the rest stay quiet. A job the user labeled not fit is never sent. Items over the caps wait
for the next digest.

Each job is its own message with three buttons. Applied and Not relevant also become labels, so the
daily use keeps growing the eval set. Later brings the job back in the next digest, whatever its age.

Once a week a separate message lists the careers pages of companies no source covers (ADR-007).
"""

from __future__ import annotations

import hashlib
import html
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal

from jobfinder.config import WatchedCompany
from jobfinder.http import FetchError
from jobfinder.labels import Label, append_label
from jobfinder.matches import MatchRow
from jobfinder.scoring import Verdict
from jobfinder.storage import LATER, JobStore
from jobfinder.telegram import Button, ButtonPress, TelegramBot

DEFAULT_DAYS: Final = 7
MAX_ITEMS: Final = 15
MAX_MAYBES: Final = 5
MAYBE_MIN_POINTS: Final = 50
# A little under a week, so a run that starts earlier in the day than last week's still sends it.
WATCHLIST_EVERY: Final = timedelta(days=6, hours=12)


@dataclass(frozen=True, slots=True)
class Answer:
    code: str  # what a button carries, short because Telegram allows 64 bytes
    name: str  # what is stored
    button: str
    verdict: Literal["fit", "no_fit"] | None  # the label this answer adds, if any
    reason: str = ""


ANSWERS: Final = (
    Answer("a", "applied", "✅ Applied", "fit", "Applied (Telegram button)"),
    Answer("n", "not_relevant", "❌ Not relevant", "no_fit", "Not relevant (Telegram button)"),
    Answer("l", LATER, "⏰ Later", None),
)
ANSWERED_CODE: Final = "done"  # the one button left on an answered job; pressing it does nothing


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
    buttons: tuple[Button, ...] = ()


def _same_job(row: MatchRow) -> tuple[str, str]:
    job = row.stored.job
    return job.company.casefold(), job.title.casefold()


def _worth_sending(row: MatchRow) -> bool:
    if row.score.verdict == Verdict.MATCH:
        return True
    # Without field points a maybe is only a nearby entry-level job, mostly in some other line of work.
    score = row.score
    return score.verdict == Verdict.MAYBE and score.field_points > 0 and score.points >= MAYBE_MIN_POINTS


def select_digest(
    rows: Sequence[MatchRow],
    sent: Collection[str],
    now: datetime,
    days: int = DEFAULT_DAYS,
    later: Collection[str] = (),
) -> Digest:
    """The items to send from `rows`, which must be in `match_rows` order, best first."""
    refused = {_same_job(r) for r in rows if r.label is not None and r.label.verdict == "no_fit"}
    back = {_same_job(row) for row in rows if row.stored.job.key in later}
    done = {_same_job(row) for row in rows if row.stored.job.key in sent} - back
    oldest = now - timedelta(days=days)
    groups: dict[tuple[str, str], list[MatchRow]] = {}
    for row in rows:
        job = _same_job(row)
        recent = row.stored.first_seen >= oldest or job in back
        if job not in refused and job not in done and recent and _worth_sending(row):
            groups.setdefault(job, []).append(row)

    matches = [group for group in groups.values() if group[0].score.verdict == Verdict.MATCH]
    maybes = [group for group in groups.values() if group[0].score.verdict != Verdict.MATCH]
    chosen = matches[:MAX_ITEMS]
    chosen += maybes[:min(MAX_MAYBES, MAX_ITEMS - len(chosen))]
    items = tuple(DigestItem(group[0], tuple(row.stored.job.key for row in group)) for group in chosen)
    return Digest(items, len(groups) - len(items))


def short_key(key: str) -> str:
    """A job key may not fit in a button, so a button carries this instead."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _buttons(key: str) -> tuple[Button, ...]:
    return tuple(Button(answer.button, f"{answer.code}:{short_key(key)}") for answer in ANSWERS)


def render_item(item: DigestItem) -> str:
    """One job in Telegram's HTML subset. Job text comes from outside, so all of it is escaped."""
    job, score = item.best.stored.job, item.best.score
    place = f"{job.company} · {job.location}" if job.location else job.company
    notes = [str(score.points)]
    if score.verdict != Verdict.MATCH:
        notes.append("maybe")
    if job.description_text is None:
        notes.append("title only")
    if len(item.keys) > 1:
        notes.append(f"{len(item.keys)} postings")
    link = f'<a href="{html.escape(job.url)}">Open</a>'
    return f"<b>{html.escape(job.title)}</b>\n{html.escape(place)}\n{' · '.join(notes)} · {link}"


def render_messages(digest: Digest) -> list[DigestMessage]:
    """A title, then one message per job with its buttons; an empty digest gives none."""
    if not digest.items:
        return []
    matched = sum(1 for item in digest.items if item.best.score.verdict == Verdict.MATCH)
    title = f"<b>New jobs: {matched} match{'' if matched == 1 else 'es'}, {len(digest.items) - matched} maybe</b>"
    if digest.waiting:
        title += f"\n{digest.waiting} more in the next digest"
    jobs = [DigestMessage(render_item(item), item.keys, _buttons(item.keys[0])) for item in digest.items]
    return [DigestMessage(title, ()), *jobs]


def watchlist_due(last_sent: datetime | None, now: datetime) -> bool:
    return last_sent is None or now - last_sent >= WATCHLIST_EVERY


def render_watchlist(companies: Sequence[WatchedCompany]) -> str:
    """The weekly reminder of the careers pages no source covers; no companies gives an empty text."""
    if not companies:
        return ""
    lines = [f'<a href="{html.escape(company.url)}">{html.escape(company.name)}</a>' for company in companies]
    return "\n".join(["<b>Weekly check: careers pages with no job feed</b>", *lines])


def _apply(press: ButtonPress, store: JobStore, labels_path: Path, now: datetime) -> Answer | None:
    code, _, short = press.data.partition(":")
    answer = next((answer for answer in ANSWERS if answer.code == code), None)
    key = next((key for key in store.digest_sent_keys() if short_key(key) == short), None)
    stored = store.get(key) if key else None
    if answer is None or key is None or stored is None:
        return None
    store.save_digest_answer(key, answer.name, now)
    if answer.verdict is not None:
        append_label(labels_path, Label(verdict=answer.verdict, reason=answer.reason, labeled_at=now, job=stored.job))
    return answer


def collect_answers(bot: TelegramBot, store: JobStore, labels_path: Path, chat_id: str, now: datetime) -> int:
    """Save the presses since the last run and return how many counted.

    Only presses by the user in their own chat count; anyone else who finds the bot is ignored. A press
    is saved before Telegram is told it was handled, so a failure repeats it rather than losing it.
    """
    saved = 0
    while True:
        presses, offset = bot.read_presses()
        if offset is None:
            return saved
        for press in presses:
            if press.user_id != chat_id or press.chat_id != chat_id:
                continue
            answer = _apply(press, store, labels_path, now)
            if answer is None:
                continue
            saved += 1
            try:
                bot.replace_buttons(chat_id, press.message_id, (Button(answer.button, ANSWERED_CODE),))
            except FetchError:
                pass  # the answer is saved; a repeated press is refused here, and must not block the digest
        bot.confirm_presses(offset)

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import FakePoster
from test_scoring import PRIORITIES_TOML

from jobfinder.cli import run_digest, run_find_chat
from jobfinder.digest import MAX_ITEMS, MAX_MAYBES, render_messages, select_digest
from jobfinder.http import FetchError
from jobfinder.labels import Label
from jobfinder.matches import MatchRow, match_rows
from jobfinder.models import Job
from jobfinder.scoring import Priorities, Verdict, load_priorities
from jobfinder.storage import JobStore
from jobfinder.telegram import MESSAGE_LIMIT, TelegramBot

NOW = datetime(2026, 11, 2, 8, tzinfo=UTC)
OK = {"ok": True, "result": {}}


def job(job_id: str, title: str, company: str = "Acme", location: str = "Tel Aviv") -> Job:
    return Job(
        source="greenhouse", slug="acme", job_id=job_id, company=company, title=title, location=location,
        url=f"https://example.test/{job_id}", description_text=f"About {title}.",
    )


@pytest.fixture
def priorities(tmp_path: Path) -> Priorities:
    path = tmp_path / "priorities.txt"
    path.write_text(PRIORITIES_TOML, encoding="utf-8")
    return load_priorities(path)


def rows_for(jobs: list[Job], priorities: Priorities, labels: dict[str, Label] | None = None) -> list[MatchRow]:
    store = JobStore.open(":memory:")
    store.record_board("greenhouse", "acme", jobs, NOW - timedelta(days=1))
    rows = match_rows(store, priorities, labels or {})
    store.close()
    return rows


def test_selects_new_matches_once_and_leaves_weak_maybes_out(priorities: Priorities) -> None:
    rows = rows_for([job("1", "Junior Software Engineer"), job("2", "Office Coordinator")], priorities)
    assert [item.keys for item in select_digest(rows, set(), NOW).items] == [(job("1", "x").key,)]
    assert select_digest(rows, {job("1", "x").key}, NOW).items == ()


def test_a_high_maybe_outside_the_field_is_left_out(priorities: Priorities) -> None:
    row = rows_for([job("1", "Office Coordinator")], priorities)[0]
    assert row.score.verdict == Verdict.MAYBE
    outside = replace(row, score=replace(row.score, points=70, field_points=0))
    inside = replace(row, score=replace(row.score, points=70, field_points=30))
    assert select_digest([outside], set(), NOW).items == ()
    assert len(select_digest([inside], set(), NOW).items) == 1


def test_old_jobs_are_left_out_because_alert_jobs_never_close(priorities: Priorities) -> None:
    rows = rows_for([job("1", "Junior Software Engineer")], priorities)
    assert select_digest(rows, set(), NOW + timedelta(days=8), days=7).items == ()


def test_same_company_and_title_is_one_item_and_a_sent_one_silences_the_rest(priorities: Priorities) -> None:
    rows = rows_for([job("1", "Junior Software Engineer"), job("2", "JUNIOR software engineer")], priorities)
    item = select_digest(rows, set(), NOW).items[0]
    assert set(item.keys) == {job("1", "x").key, job("2", "x").key}
    assert "2 postings" in render_messages(select_digest(rows, set(), NOW))[0].text
    assert select_digest(rows, {job("2", "x").key}, NOW).items == ()


def test_a_job_labeled_not_fit_is_never_sent(priorities: Priorities) -> None:
    a_job = job("1", "Junior Software Engineer")
    label = Label(verdict="no_fit", reason="too senior", labeled_at=NOW, job=a_job)
    assert select_digest(rows_for([a_job], priorities, {a_job.key: label}), set(), NOW).items == ()


def test_caps_leave_the_rest_for_the_next_digest(priorities: Priorities) -> None:
    jobs = [job(str(n), f"Junior Software Engineer {n}") for n in range(MAX_ITEMS + 3)]
    digest = select_digest(rows_for(jobs, priorities), set(), NOW)
    assert (len(digest.items), digest.waiting) == (MAX_ITEMS, 3)
    assert MAX_MAYBES < MAX_ITEMS


def test_job_text_is_escaped(priorities: Priorities) -> None:
    rows = rows_for([job("1", "Junior <b>Software</b> & Engineer", company="A&B")], priorities)
    text = render_messages(select_digest(rows, set(), NOW))[0].text
    assert "Junior &lt;b&gt;Software&lt;/b&gt; &amp; Engineer" in text
    assert "A&amp;B" in text


def test_long_digests_split_within_the_limit_and_keep_every_key(priorities: Priorities) -> None:
    long = "Junior Software Engineer " + "x" * 900
    rows = rows_for([job(str(n), f"{long}{n}") for n in range(8)], priorities)
    digest = select_digest(rows, set(), NOW)
    messages = render_messages(digest)
    assert len(messages) > 1
    assert all(len(message.text) <= MESSAGE_LIMIT for message in messages)
    assert sorted(key for message in messages for key in message.keys) == sorted(
        key for item in digest.items for key in item.keys
    )


def test_an_empty_digest_is_no_message() -> None:
    assert render_messages(select_digest([], set(), NOW)) == []


def test_bot_turns_a_refusal_into_an_error_and_keeps_previews_off() -> None:
    poster = FakePoster(OK, {"ok": False})
    bot = TelegramBot("secret", poster)
    bot.send_message("42", "hi")
    url, payload, _ = poster.requests[0]
    assert url.endswith("/botsecret/sendMessage")
    assert payload == {
        "chat_id": "42", "text": "hi", "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
    }
    with pytest.raises(FetchError) as error:
        bot.send_message("42", "hi")
    assert "secret" not in str(error.value)


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    db, labels, priorities, env = (
        tmp_path / "jobs.db", tmp_path / "labels.jsonl", tmp_path / "priorities.txt", tmp_path / ".env",
    )
    priorities.write_text(PRIORITIES_TOML, encoding="utf-8")
    env.write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=42\n", encoding="utf-8")
    store = JobStore.open(db)
    store.record_board("greenhouse", "acme", [job("1", "Junior Software Engineer")], datetime.now(tz=UTC))
    store.close()
    return db, labels, priorities, env


def sent_keys(db: Path) -> set[str]:
    store = JobStore.open(db)
    keys = store.digest_sent_keys()
    store.close()
    return keys


def test_sending_marks_jobs_and_a_second_run_sends_nothing(
    files: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    db, labels, priorities, env = files
    poster = FakePoster(OK)
    assert run_digest(db, labels, priorities, env, 7, poster=poster) == 0
    assert run_digest(db, labels, priorities, env, 7, poster=poster) == 0
    assert len(poster.requests) == 1
    assert sent_keys(db) == {job("1", "x").key}


def test_a_failed_send_is_not_marked_and_exits_non_zero(
    files: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    db, labels, priorities, env = files
    assert run_digest(db, labels, priorities, env, 7, poster=FakePoster(FetchError("HTTP 500"))) == 1
    assert sent_keys(db) == set()


def test_dry_run_needs_no_secrets_and_marks_nothing(
    files: tuple[Path, Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, labels, priorities, _ = files
    assert run_digest(db, labels, priorities, tmp_path / "missing.env", 7, dry_run=True) == 0
    assert "Junior Software Engineer" in capsys.readouterr().out
    assert sent_keys(db) == set()


def test_missing_secrets_are_a_config_error(
    files: tuple[Path, Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    db, labels, priorities, _ = files
    assert run_digest(db, labels, priorities, tmp_path / "missing.env", 7) == 2


def test_find_chat_prints_each_chat_once(
    files: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    message = {"message": {"chat": {"id": 42}}}
    assert run_find_chat(files[3], FakePoster({"ok": True, "result": [message, message]})) == 0
    assert capsys.readouterr().out.strip() == "42"

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import FakePoster
from test_scoring import PRIORITIES_TOML

from jobfinder.cli import run_digest, run_find_chat
from jobfinder.digest import (
    MAX_ITEMS,
    MAX_MAYBES,
    collect_answers,
    render_messages,
    select_digest,
    short_key,
)
from jobfinder.http import FetchError
from jobfinder.labels import Label, load_labels
from jobfinder.matches import MatchRow, match_rows
from jobfinder.models import Job
from jobfinder.scoring import Priorities, Verdict, load_priorities
from jobfinder.storage import LATER, JobStore
from jobfinder.telegram import TelegramBot

NOW = datetime(2026, 11, 2, 8, tzinfo=UTC)
OK = {"ok": True, "result": {}}
NO_UPDATES = {"ok": True, "result": []}


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
    assert "2 postings" in render_messages(select_digest(rows, set(), NOW))[1].text
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
    text = render_messages(select_digest(rows, set(), NOW))[1].text
    assert "Junior &lt;b&gt;Software&lt;/b&gt; &amp; Engineer" in text
    assert "A&amp;B" in text


def test_each_job_is_its_own_message_with_three_buttons(priorities: Priorities) -> None:
    jobs = [job("1", "Junior Software Engineer"), job("2", "Junior Data Analyst")]
    messages = render_messages(select_digest(rows_for(jobs, priorities), set(), NOW))
    assert messages[0].text.startswith("<b>New jobs: 2 matches, 0 maybe</b>")
    assert messages[0].buttons == ()
    assert [len(message.buttons) for message in messages[1:]] == [3, 3]
    assert all(len(button.data.encode()) <= 64 for message in messages for button in message.buttons)


def test_an_empty_digest_is_no_message() -> None:
    assert render_messages(select_digest([], set(), NOW)) == []


def test_bot_turns_a_refusal_into_an_error_and_keeps_previews_off() -> None:
    poster = FakePoster(OK, {"ok": False})
    bot = TelegramBot("secret", poster)
    bot.send_message("42", "hi", quiet=True)
    url, payload, _ = poster.requests[0]
    assert url.endswith("/botsecret/sendMessage")
    assert payload == {
        "chat_id": "42", "text": "hi", "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
        "disable_notification": True,
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
    poster = FakePoster(NO_UPDATES, OK, OK, NO_UPDATES)
    assert run_digest(db, labels, priorities, env, 7, poster=poster) == 0
    assert run_digest(db, labels, priorities, env, 7, poster=poster) == 0
    assert [url.rsplit("/", 1)[1] for url, _, _ in poster.requests] == [
        "getUpdates", "sendMessage", "sendMessage", "getUpdates",
    ]
    assert sent_keys(db) == {job("1", "x").key}


def test_a_failed_send_is_not_marked_and_exits_non_zero(
    files: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    db, labels, priorities, env = files
    assert run_digest(db, labels, priorities, env, 7, poster=FakePoster(NO_UPDATES, OK, FetchError("HTTP 500"))) == 1
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


def press(update_id: int, data: str, user: int = 42, chat: int = 42) -> dict[str, object]:
    return {
        "update_id": update_id,
        "callback_query": {"from": {"id": user}, "data": data, "message": {"message_id": 7, "chat": {"id": chat}}},
    }


def updates(*items: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "result": list(items)}


@pytest.fixture
def sent_store(tmp_path: Path) -> JobStore:
    store = JobStore.open(tmp_path / "jobs.db")
    a_job = job("1", "Junior Software Engineer")
    store.record_board("greenhouse", "acme", [a_job], NOW)
    store.mark_digest_sent([a_job.key], NOW)
    return store


@pytest.mark.parametrize(
    ("code", "answer", "verdict"), [("a", "applied", "fit"), ("n", "not_relevant", "no_fit"), ("l", LATER, None)]
)
def test_a_press_is_saved_and_applied_and_not_relevant_become_labels(
    sent_store: JobStore, tmp_path: Path, code: str, answer: str, verdict: str | None
) -> None:
    key = job("1", "x").key
    labels = tmp_path / "labels.jsonl"
    poster = FakePoster(updates(press(5, f"{code}:{short_key(key)}")), OK, OK, NO_UPDATES)
    assert collect_answers(TelegramBot("t", poster), sent_store, labels, "42", NOW) == 1
    assert sent_store.digest_answers() == {key: answer}
    assert {k: label.verdict for k, label in load_labels(labels).items()} == ({key: verdict} if verdict else {})
    edit, confirm = poster.requests[1], poster.requests[2]
    assert edit[0].endswith("/editMessageReplyMarkup")
    assert confirm[1] == {"offset": 6, "limit": 1, "allowed_updates": ["callback_query"]}


def test_a_refused_button_update_does_not_block_the_run(sent_store: JobStore, tmp_path: Path) -> None:
    data = f"a:{short_key(job('1', 'x').key)}"
    poster = FakePoster(updates(press(5, data)), FetchError("HTTP 400: message is not modified"), OK, NO_UPDATES)
    assert collect_answers(TelegramBot("t", poster), sent_store, tmp_path / "labels.jsonl", "42", NOW) == 1
    assert poster.requests[2][1] == {"offset": 6, "limit": 1, "allowed_updates": ["callback_query"]}


def test_presses_from_anyone_else_are_ignored_but_confirmed(sent_store: JobStore, tmp_path: Path) -> None:
    data = f"n:{short_key(job('1', 'x').key)}"
    poster = FakePoster(updates(press(5, data, user=99, chat=99), press(6, data, user=99)), OK, NO_UPDATES)
    assert collect_answers(TelegramBot("t", poster), sent_store, tmp_path / "labels.jsonl", "42", NOW) == 0
    assert sent_store.digest_answers() == {}
    assert not (tmp_path / "labels.jsonl").exists()


@pytest.mark.parametrize("data", ["done", "n:unknown", "x:abc", "garbage"])
def test_unknown_presses_change_nothing(sent_store: JobStore, tmp_path: Path, data: str) -> None:
    poster = FakePoster(updates(press(5, data)), OK, NO_UPDATES)
    assert collect_answers(TelegramBot("t", poster), sent_store, tmp_path / "labels.jsonl", "42", NOW) == 0
    assert sent_store.digest_answers() == {}


def test_later_brings_an_old_sent_job_back_once(priorities: Priorities) -> None:
    a_job = job("1", "Junior Software Engineer")
    rows = rows_for([a_job], priorities)
    long_after = NOW + timedelta(days=30)
    assert select_digest(rows, {a_job.key}, long_after, later=[a_job.key]).items[0].keys == (a_job.key,)

    store = JobStore.open(":memory:")
    store.record_board("greenhouse", "acme", [a_job], NOW)
    store.mark_digest_sent([a_job.key], NOW)
    store.save_digest_answer(a_job.key, LATER, NOW)
    store.mark_digest_sent([a_job.key], NOW)
    assert store.digest_answers() == {}
    store.close()


def test_not_relevant_wins_over_later(priorities: Priorities) -> None:
    a_job = job("1", "Junior Software Engineer")
    label = Label(verdict="no_fit", reason="no", labeled_at=NOW, job=a_job)
    rows = rows_for([a_job], priorities, {a_job.key: label})
    assert select_digest(rows, {a_job.key}, NOW, later=[a_job.key]).items == ()


def test_find_chat_prints_each_chat_once(
    files: tuple[Path, Path, Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    message = {"message": {"chat": {"id": 42}}}
    assert run_find_chat(files[3], FakePoster({"ok": True, "result": [message, message]})) == 0
    assert capsys.readouterr().out.strip() == "42"

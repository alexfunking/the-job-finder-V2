from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from email.message import EmailMessage, Message
from pathlib import Path

import pytest

from jobfinder.alerts import import_alerts, received_at
from jobfinder.cli import EXIT_CONFIG_ERROR, EXIT_OK, run_alerts
from jobfinder.envfile import read_env_file, secret
from jobfinder.mailbox import imap_date
from jobfinder.sources.linkedin_alerts import SENDER
from jobfinder.storage import JobStore

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def alert(fixture: str, sent: str = "Tue, 27 Oct 2026 05:38:24 +0000") -> EmailMessage:
    message = EmailMessage()
    message["From"] = f"LinkedIn Job Alerts <{SENDER}>"
    message["Date"] = sent
    message.set_content((FIXTURES / f"linkedin_alert_{fixture}.txt").read_text(encoding="utf-8"))
    message.add_alternative("<p>html version</p>", subtype="html")
    return message


class FakeMailbox:
    def __init__(self, messages: list[Message]) -> None:
        self._messages = messages
        self.searches: list[tuple[str, date]] = []

    def messages(self, sender: str, since: date) -> Iterator[Message]:
        self.searches.append((sender, since))
        yield from self._messages


@pytest.fixture
def store() -> Iterator[JobStore]:
    s = JobStore.open(":memory:")
    yield s
    s.close()


def test_import_stores_every_job_once_and_counts_new_ones(store: JobStore) -> None:
    mailbox = FakeMailbox([alert("en"), alert("he"), alert("en", "Wed, 28 Oct 2026 05:00:00 +0000")])
    report = import_alerts(mailbox, store, date(2026, 10, 14), NOW)
    assert (report.messages, report.jobs, report.new, report.skipped) == (3, 12, 9, [])
    assert mailbox.searches == [(SENDER, date(2026, 10, 14))]
    assert store.count() == 9


def test_jobs_are_first_seen_when_the_alert_was_sent(store: JobStore) -> None:
    import_alerts(FakeMailbox([alert("en")]), store, date(2026, 10, 14), NOW)
    stored = store.get("linkedin:alerts:4282849155")
    assert stored is not None
    assert stored.first_seen == datetime(2026, 10, 27, 5, 38, 24, tzinfo=UTC)


def test_alert_without_a_text_part_is_reported(store: JobStore) -> None:
    html_only = EmailMessage()
    html_only["Date"] = "Tue, 27 Oct 2026 05:38:24 +0000"
    html_only.set_content("<p>jobs</p>", subtype="html")
    report = import_alerts(FakeMailbox([html_only]), store, date(2026, 10, 14), NOW)
    assert report.skipped == ["alert Tue, 27 Oct 2026 05:38:24 +0000: no text part"]


@pytest.mark.parametrize("header", ["", "not a date", "Tue, 27 Oct 2026 05:38:24"])
def test_received_at_falls_back_when_the_date_is_missing_or_naive(header: str) -> None:
    message = EmailMessage()
    if header:
        message["Date"] = header
    assert received_at(message, NOW) == NOW


def test_imap_date_uses_english_month_names() -> None:
    assert imap_date(date(2026, 3, 5)) == "05-Mar-2026"


def test_env_file_parsing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# secrets\nA=1\n\nB = 'two words'\nNOT A PAIR\nC=\"x=y\"\n", encoding="utf-8")
    assert read_env_file(env) == {"A": "1", "B": "two words", "C": "x=y"}
    assert read_env_file(tmp_path / "missing") == {}


def test_environment_variable_wins_over_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("TOKEN=from-file\nEMPTY=\n", encoding="utf-8")
    assert secret("TOKEN", env, environ={}) == "from-file"
    assert secret("TOKEN", env, environ={"TOKEN": "from-env"}) == "from-env"
    assert secret("EMPTY", env, environ={}) is None
    assert secret("MISSING", env, environ={}) is None


def test_run_alerts_reports_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run_alerts(tmp_path / "jobs.db", tmp_path / ".env", 14, mailbox=FakeMailbox([alert("he_single")]))
    assert code == EXIT_OK
    assert "1 emails, 1 job listings, 1 new jobs; 1 open in store" in capsys.readouterr().out


def test_run_alerts_without_credentials_is_a_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALERTS_EMAIL_ACCOUNT", raising=False)
    monkeypatch.delenv("ALERTS_EMAIL_APP_PASSWORD", raising=False)
    assert run_alerts(tmp_path / "jobs.db", tmp_path / ".env", 14) == EXIT_CONFIG_ERROR
    assert "set ALERTS_EMAIL_ACCOUNT and ALERTS_EMAIL_APP_PASSWORD" in capsys.readouterr().err

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfinder.labeling import Console, render_job, run_session
from jobfinder.labels import Label, Suggestion, load_labels, load_suggestions
from jobfinder.models import Job

T1 = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def job(job_id: str, description: str = "Build things.") -> Job:
    return Job(
        source="greenhouse",
        slug="acme",
        job_id=job_id,
        company="Acme",
        title=f"Engineer {job_id}",
        location="Tel Aviv",
        url=f"https://example.test/acme/{job_id}",
        description_text=description,
        published_at=T1,
    )


class ScriptedConsole:
    """Answers prompts from a script; running out of answers behaves like Ctrl-Z / Ctrl-D."""

    def __init__(self, answers: Iterable[str]) -> None:
        self._answers = iter(answers)
        self.output: list[str] = []
        self.opened: list[str] = []

    def console(self) -> Console:
        return Console(read=self._read, write=self.output.append, open_url=self.opened.append, width=40)

    def _read(self, prompt: str) -> str:
        try:
            return next(self._answers)
        except StopIteration:
            raise EOFError from None

    @property
    def text(self) -> str:
        return "\n".join(self.output)


def run(
    answers: Iterable[str], jobs: list[Job], tmp_path: Path, target: int = 80, labels: dict[str, Label] | None = None
) -> tuple[dict[str, Label], ScriptedConsole]:
    labels = {} if labels is None else labels
    scripted = ScriptedConsole(answers)
    run_session(jobs, labels, tmp_path / "labels.jsonl", target, scripted.console(), lambda: T1)
    return labels, scripted


def test_answers_are_saved_with_reasons(tmp_path: Path) -> None:
    labels, _ = run(["y", "Python, Tel Aviv", "n", "needs 5 years", "s", "q"], [job("1"), job("2"), job("3"), job("4")], tmp_path)
    assert {k: (v.verdict, v.reason) for k, v in labels.items()} == {
        "greenhouse:acme:1": ("fit", "Python, Tel Aviv"),
        "greenhouse:acme:2": ("no_fit", "needs 5 years"),
        "greenhouse:acme:3": ("skip", ""),
    }
    assert load_labels(tmp_path / "labels.jsonl") == labels


def test_empty_reason_is_asked_again(tmp_path: Path) -> None:
    labels, scripted = run(["y", "", "  ", "remote friendly"], [job("1")], tmp_path)
    assert labels["greenhouse:acme:1"].reason == "remote friendly"
    assert scripted.text.count("A reason is required") == 2


def test_input_ending_while_asking_why_saves_nothing_for_that_job(tmp_path: Path) -> None:
    labels, _ = run(["y"], [job("1")], tmp_path)
    assert labels == {}
    assert not (tmp_path / "labels.jsonl").exists()


def test_open_shows_the_same_job_again(tmp_path: Path) -> None:
    labels, scripted = run(["o", "n", "Go only"], [job("1"), job("2")], tmp_path)
    assert scripted.opened == ["https://example.test/acme/1"]
    assert list(labels) == ["greenhouse:acme:1"]


def test_back_relabels_the_previous_job(tmp_path: Path) -> None:
    labels, scripted = run(["y", "looked fine", "b", "n", "actually onsite 5 days", "q"], [job("1"), job("2")], tmp_path)
    assert labels["greenhouse:acme:1"].verdict == "no_fit"
    assert "Currently: fit. looked fine" in scripted.text
    # The file keeps both answers; loading takes the latest.
    assert len((tmp_path / "labels.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert load_labels(tmp_path / "labels.jsonl") == labels


def test_back_on_the_first_job_stays_put(tmp_path: Path) -> None:
    _, scripted = run(["b", "q"], [job("1")], tmp_path)
    assert "Nothing to go back to" in scripted.text


def test_unknown_answer_is_reported_and_the_job_shown_again(tmp_path: Path) -> None:
    labels, scripted = run(["maybe", "Y", "ok"], [job("1")], tmp_path)
    assert "Unknown answer 'maybe'" in scripted.text
    assert labels["greenhouse:acme:1"].verdict == "fit"


def test_stops_at_the_target_counting_earlier_labels_but_not_skips(tmp_path: Path) -> None:
    earlier = {"greenhouse:acme:0": Label(verdict="fit", reason="r", labeled_at=T1, job=job("0"))}
    labels, scripted = run(["s", "n", "no", "y", "never asked"], [job("1"), job("2"), job("3")], tmp_path, target=2, labels=earlier)
    assert "Reached the target of 2 labels" in scripted.text
    assert "greenhouse:acme:3" not in labels
    assert "[2/2]  fit 1  not fit 1  skipped 1" in scripted.text


def test_running_out_of_jobs_is_reported(tmp_path: Path) -> None:
    _, scripted = run(["n", "no"], [job("1")], tmp_path)
    assert "No more jobs to label" in scripted.text


def test_few_fit_labels_at_the_target_are_warned_about(tmp_path: Path) -> None:
    _, scripted = run(["n", "no", "n", "no"], [job("1"), job("2")], tmp_path, target=2)
    assert "Only 0 jobs are labeled fit" in scripted.text


def test_render_job_wraps_the_description_and_indents_list_items() -> None:
    text = render_job(job("1", "About\n\n- " + "word " * 20 + "\nShort line"), width=40)
    lines = text.splitlines()
    assert "Acme | Engineer 1" in lines
    assert "Published: 2026-10-26" in lines
    assert all(len(line) <= 40 for line in lines if not line.startswith("URL"))
    first_item = lines.index(next(line for line in lines if line.startswith("- word")))
    assert lines[first_item + 1].startswith("  word")
    assert "" in lines  # blank lines between paragraphs are kept


def suggest(job_id: str, verdict: str, reason: str) -> Suggestion:
    return Suggestion.model_validate({"key": job(job_id).key, "verdict": verdict, "reason": reason})


def run_suggested(
    answers: Iterable[str], jobs: list[Job], tmp_path: Path, suggestions: list[Suggestion]
) -> tuple[dict[str, Label], ScriptedConsole]:
    labels: dict[str, Label] = {}
    scripted = ScriptedConsole(answers)
    by_key = {s.key: s for s in suggestions}
    run_session(jobs, labels, tmp_path / "labels.jsonl", 80, scripted.console(), lambda: T1, by_key)
    return labels, scripted


def test_enter_accepts_the_suggestion_with_its_reason(tmp_path: Path) -> None:
    labels, scripted = run_suggested([""], [job("1")], tmp_path, [suggest("1", "fit", "student role, Python")])
    label = labels[job("1").key]
    assert (label.verdict, label.reason, label.suggestion) == ("fit", "student role, Python", "fit")
    assert not label.overrode_suggestion
    assert "Suggested: fit. student role, Python" in scripted.text
    assert "Suggestions: 1 accepted, 0 changed." in scripted.text


def test_overriding_a_suggestion_needs_a_reason_of_your_own(tmp_path: Path) -> None:
    labels, scripted = run_suggested(
        ["n", "", "too far for me"], [job("1")], tmp_path, [suggest("1", "fit", "student role")]
    )
    label = labels[job("1").key]
    assert (label.verdict, label.reason, label.suggestion) == ("no_fit", "too far for me", "fit")
    assert label.overrode_suggestion
    assert "A reason is required" in scripted.text
    assert "Suggestions: 0 accepted, 1 changed." in scripted.text


def test_picking_the_suggested_verdict_by_key_can_keep_its_reason(tmp_path: Path) -> None:
    labels, _ = run_suggested(["n", ""], [job("1")], tmp_path, [suggest("1", "no_fit", "needs 5 years")])
    assert labels[job("1").key].reason == "needs 5 years"


def test_jobs_without_a_suggestion_work_as_before(tmp_path: Path) -> None:
    labels, scripted = run_suggested(["", "y", "ok"], [job("1")], tmp_path, [suggest("2", "fit", "x")])
    assert "Unknown answer ''" in scripted.text
    assert labels[job("1").key].suggestion is None
    assert load_labels(tmp_path / "labels.jsonl")[job("1").key].suggestion is None


def test_load_suggestions_reads_the_last_line_per_key_and_rejects_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "suggestions.jsonl"
    assert load_suggestions(path) == {}
    path.write_text(
        suggest("1", "fit", "a").model_dump_json() + "\n\n" + suggest("1", "no_fit", "b").model_dump_json() + "\n",
        encoding="utf-8",
    )
    assert load_suggestions(path)[job("1").key].verdict == "no_fit"
    path.write_text('{"key": "k", "verdict": "skip", "reason": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="suggestions.jsonl:1"):
        load_suggestions(path)

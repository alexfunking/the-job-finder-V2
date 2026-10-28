from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes import FakePoster

from jobfinder.cli import EXIT_BOARD_ERRORS, EXIT_CONFIG_ERROR, EXIT_OK, run_extract
from jobfinder.extraction import CachedExtractor, Extraction, ExtractionError, ExtractionResult, build_extractor
from jobfinder.extraction.base import MAX_DESCRIPTION_CHARS, SCHEMA, input_hash, job_prompt, parse_extraction
from jobfinder.extraction.gemini import GeminiExtractor, answer_text
from jobfinder.extraction.ollama import OllamaExtractor
from jobfinder.http import FetchError
from jobfinder.models import Job
from jobfinder.storage import JobStore

ANSWER = {"years_required": 2, "onsite_days": 3, "is_student": False, "location": "Tel Aviv", "stack": ["Python"]}


def job(job_id: str = "1", description: str | None = "Python, 2+ years, 3 days a week in the office.") -> Job:
    return Job(
        source="greenhouse",
        slug="acme",
        job_id=job_id,
        company="Acme",
        title="Backend Engineer",
        location="Tel Aviv",
        url=f"https://example.test/{job_id}",
        description_text=description,
    )


def gemini_response(text: str, *, thought: str | None = None) -> dict[str, object]:
    parts: list[dict[str, object]] = [{"text": thought, "thought": True}] if thought else []
    parts.append({"text": text})
    return {
        "candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 60, "thoughtsTokenCount": 600},
    }


# --- the shared contract ---------------------------------------------------------------------------


def test_schema_names_exactly_the_extraction_fields() -> None:
    properties, required = SCHEMA["properties"], SCHEMA["required"]
    assert isinstance(properties, dict)
    assert isinstance(required, list)
    assert set(properties) == set(Extraction.model_fields) == set(required)


def test_parse_extraction_accepts_plain_and_fenced_json() -> None:
    expected = Extraction.model_validate(ANSWER)
    assert parse_extraction(json.dumps(ANSWER)) == expected
    assert parse_extraction(f"```json\n{json.dumps(ANSWER)}\n```") == expected


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        json.dumps({**ANSWER, "onsite_days": 6}),
        json.dumps({**ANSWER, "years_required": -1}),
        json.dumps({key: value for key, value in ANSWER.items() if key != "is_student"}),
        json.dumps({**ANSWER, "salary": 1}),
    ],
)
def test_parse_extraction_rejects_invalid_answers(answer: str) -> None:
    with pytest.raises(ExtractionError, match="invalid answer"):
        parse_extraction(answer)


def test_extraction_cleans_stack_and_blank_location() -> None:
    extraction = Extraction.model_validate(
        {**ANSWER, "location": " ", "stack": ["Python", " python ", "", "Go", *[f"t{i}" for i in range(20)]]}
    )
    assert extraction.location is None
    assert extraction.stack[:3] == ("Python", "Go", "t0")
    assert len(extraction.stack) == 15


def test_prompt_truncates_long_descriptions() -> None:
    prompt = job_prompt(job(description="x" * (MAX_DESCRIPTION_CHARS + 500)))
    assert prompt.endswith("[truncated]")
    assert prompt.count("x") == MAX_DESCRIPTION_CHARS


def test_prompt_for_a_title_only_job_says_so() -> None:
    prompt = job_prompt(job(description=None))
    assert "Title: Backend Engineer" in prompt
    assert "only the title is known" in prompt


# --- Gemini ------------------------------------------------------------------------------------------


def test_gemini_sends_key_in_header_and_schema_in_config() -> None:
    poster = FakePoster(gemini_response(json.dumps(ANSWER)))
    GeminiExtractor("secret-key", poster, model="gemini-test").extract(job())
    url, payload, headers = poster.requests[0]
    assert url.endswith("/models/gemini-test:generateContent")
    assert "secret-key" not in url
    assert headers == {"x-goog-api-key": "secret-key"}
    assert isinstance(payload, dict)
    assert payload["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert "Backend Engineer" in payload["contents"][0]["parts"][0]["text"]


def test_gemini_skips_thoughts_and_bills_them_as_output() -> None:
    result = GeminiExtractor("k", FakePoster(gemini_response(json.dumps(ANSWER), thought="hmm"))).extract(job())
    assert result == ExtractionResult(Extraction.model_validate(ANSWER), input_tokens=900, output_tokens=660)


def test_gemini_without_candidates_is_an_error() -> None:
    with pytest.raises(ExtractionError, match="no answer"):
        answer_text({"promptFeedback": {"blockReason": "SAFETY"}})


def test_gemini_http_failure_is_an_extraction_error() -> None:
    extractor = GeminiExtractor("k", FakePoster(FetchError("HTTP 429: quota")))
    with pytest.raises(ExtractionError, match="gemini:.*HTTP 429"):
        extractor.extract(job())


# --- Ollama ------------------------------------------------------------------------------------------


def test_ollama_sends_schema_as_format_and_reads_the_message() -> None:
    poster = FakePoster({"message": {"content": json.dumps(ANSWER)}, "prompt_eval_count": 800, "eval_count": 50})
    result = OllamaExtractor(poster, model="qwen-test").extract(job())
    url, payload, _ = poster.requests[0]
    assert url == "http://localhost:11434/api/chat"
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen-test"
    assert payload["format"] == SCHEMA
    assert payload["stream"] is False
    assert result == ExtractionResult(Extraction.model_validate(ANSWER), input_tokens=800, output_tokens=50)


def test_ollama_unreachable_says_how_to_fix_it() -> None:
    with pytest.raises(ExtractionError, match="ollama serve"):
        OllamaExtractor(FakePoster(FetchError("URLError: refused"))).extract(job())


# --- choosing one ------------------------------------------------------------------------------------


def test_build_extractor_needs_the_gemini_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_extractor("gemini", None, tmp_path / ".env", FakePoster())
    (tmp_path / ".env").write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    assert build_extractor("gemini", "m1", tmp_path / ".env", FakePoster()).name == "gemini:m1"


def test_build_extractor_defaults_and_unknown_kind(tmp_path: Path) -> None:
    assert build_extractor("ollama", None, tmp_path / ".env", FakePoster()).name == "ollama:qwen2.5:14b"
    with pytest.raises(ValueError, match="unknown extractor"):
        build_extractor("gpt", None, tmp_path / ".env", FakePoster())


# --- jobfinder extract -------------------------------------------------------------------------------


class ScriptedExtractor:
    name = "fake:model"

    def __init__(self, fail_on: str | None = None) -> None:
        self._fail_on = fail_on
        self.asked: list[str] = []

    def extract(self, job: Job) -> ExtractionResult:
        self.asked.append(job.job_id)
        if job.job_id == self._fail_on:
            raise ExtractionError("boom")
        return ExtractionResult(Extraction.model_validate(ANSWER), 100, 10)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.db"
    store = JobStore.open(path)
    store.record_board("greenhouse", "acme", [job("1"), job("2"), job("3")], datetime(2026, 10, 28, tzinfo=UTC))
    store.close()
    return path


def test_extract_prints_each_answer_and_token_totals(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run_extract(db, db.parent / ".env", "gemini", None, limit=2, seed=1, extractor=ScriptedExtractor())
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert out.count('"years_required": 2') == 2
    assert "2 extracted (0 from saved answers), 0 failed" in out
    assert "tokens spent: 200 in, 20 out" in out


def test_extract_reports_failures_and_keeps_going(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run_extract(db, db.parent / ".env", "gemini", None, limit=5, seed=1, extractor=ScriptedExtractor("2"))
    out = capsys.readouterr().out
    assert code == EXIT_BOARD_ERRORS
    assert "ERROR boom" in out
    assert "2 extracted (0 from saved answers), 1 failed" in out


def test_extract_without_db_or_key_is_a_config_error(tmp_path: Path, db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert run_extract(tmp_path / "none.db", tmp_path / ".env", "gemini", None, 1, 1) == EXIT_CONFIG_ERROR
    assert run_extract(db, tmp_path / ".env", "gemini", None, 1, 1) == EXIT_CONFIG_ERROR


def test_extract_rerun_uses_saved_answers_unless_told_not_to(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_extract(db, db.parent / ".env", "gemini", None, limit=3, seed=1, extractor=ScriptedExtractor())
    capsys.readouterr()
    again = ScriptedExtractor()
    assert run_extract(db, db.parent / ".env", "gemini", None, limit=3, seed=1, extractor=again) == EXIT_OK
    out = capsys.readouterr().out
    assert again.asked == []
    assert "3 extracted (3 from saved answers)" in out
    assert "tokens spent: 0 in, 0 out" in out
    fresh = ScriptedExtractor()
    run_extract(db, db.parent / ".env", "gemini", None, limit=3, seed=1, extractor=fresh, use_cache=False)
    assert len(fresh.asked) == 3


# --- the cache ---------------------------------------------------------------------------------------


T0 = datetime(2026, 10, 28, 12, tzinfo=UTC)


def test_cache_asks_once_then_answers_from_the_store() -> None:
    store = JobStore.open(":memory:")
    inner = ScriptedExtractor()
    cached = CachedExtractor(inner, store, lambda: T0)
    first, second = cached.extract(job()), cached.extract(job())
    assert inner.asked == ["1"]
    assert (first.cached, second.cached) == (False, True)
    assert second.extraction == first.extraction
    assert (second.input_tokens, second.output_tokens) == (100, 10)
    assert cached.name == "fake:model"


def test_cache_misses_when_the_posting_changes() -> None:
    store = JobStore.open(":memory:")
    inner = ScriptedExtractor()
    cached = CachedExtractor(inner, store, lambda: T0)
    cached.extract(job())
    edited = job(description="Now 5+ years.")
    assert input_hash(edited) != input_hash(job())
    assert cached.extract(edited).cached is False
    assert inner.asked == ["1", "1"]


def test_cache_does_not_save_failures() -> None:
    store = JobStore.open(":memory:")
    inner = ScriptedExtractor(fail_on="1")
    cached = CachedExtractor(inner, store, lambda: T0)
    for _ in range(2):
        with pytest.raises(ExtractionError):
            cached.extract(job())
    assert inner.asked == ["1", "1"]


def test_extract_all_runs_on_every_kept_job_with_a_description(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    store = JobStore.open(path)
    seen = datetime(2026, 10, 28, tzinfo=UTC)
    senior = job("2").model_copy(update={"title": "Senior Backend Engineer"})
    store.record_board("greenhouse", "acme", [job("1"), senior], seen)
    alert = job("3", description=None).model_copy(update={"source": "linkedin", "slug": "alerts"})
    store.record_sightings([alert], seen)
    store.close()
    scripted = ScriptedExtractor()
    assert run_extract(path, tmp_path / ".env", "gemini", None, 0, 1, extractor=scripted, all_kept=True) == EXIT_OK
    assert scripted.asked == ["1"]

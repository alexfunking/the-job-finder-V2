"""The API extractor for now: Gemini through the Generative Language API (ADR-006, amendment of 28.10)."""

from __future__ import annotations

from typing import Final

from jobfinder.extraction.base import (
    INSTRUCTIONS,
    SCHEMA,
    ExtractionError,
    ExtractionResult,
    job_prompt,
    parse_extraction,
)
from jobfinder.http import FetchError, Poster
from jobfinder.models import Job

API_URL: Final = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL: Final = "gemini-3.5-flash"


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def answer_text(payload: object) -> tuple[str, int | None, int | None]:
    """The answer's text and its input / output token counts, from a generateContent response."""
    if not isinstance(payload, dict):
        raise ExtractionError(f"unexpected response: {str(payload)[:200]}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise ExtractionError(f"no answer: {str(payload.get('promptFeedback', payload))[:200]}")
    content = candidates[0].get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise ExtractionError(f"answer has no text (finishReason {candidates[0].get('finishReason')})")
    # Parts flagged "thought" are the model's reasoning summary, not the answer.
    text = "".join(
        str(part["text"]) for part in parts if isinstance(part, dict) and "text" in part and not part.get("thought")
    )
    usage = payload.get("usageMetadata")
    usage = usage if isinstance(usage, dict) else {}
    output = _int_or_none(usage.get("candidatesTokenCount"))
    thoughts = _int_or_none(usage.get("thoughtsTokenCount"))
    if output is not None and thoughts is not None:
        output += thoughts  # thinking tokens are billed as output
    return text, _int_or_none(usage.get("promptTokenCount")), output


class GeminiExtractor:
    def __init__(self, api_key: str, poster: Poster, model: str = DEFAULT_MODEL) -> None:
        self._api_key = api_key
        self._poster = poster
        self._model = model

    @property
    def name(self) -> str:
        return f"gemini:{self._model}"

    def extract(self, job: Job) -> ExtractionResult:
        payload = {
            "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": [{"text": job_prompt(job)}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": SCHEMA,
            },
        }
        try:
            # The key goes in a header, never in the URL, so it cannot end up in an error message or log.
            response = self._poster.post_json(
                API_URL.format(model=self._model), payload, {"x-goog-api-key": self._api_key}
            )
        except FetchError as exc:
            raise ExtractionError(f"{self.name}: {exc}") from exc
        text, input_tokens, output_tokens = answer_text(response)
        return ExtractionResult(parse_extraction(text), input_tokens, output_tokens)

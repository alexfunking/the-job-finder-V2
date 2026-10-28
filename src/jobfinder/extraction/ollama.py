"""The local extractor: a model served by Ollama on this machine, so nothing leaves it (ADR-006)."""

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

API_URL: Final = "http://localhost:11434/api/chat"
# Fits in 16GB of VRAM; larger local models are compared on the eval set with --model.
DEFAULT_MODEL: Final = "qwen2.5:14b"


class OllamaExtractor:
    def __init__(self, poster: Poster, model: str = DEFAULT_MODEL, url: str = API_URL) -> None:
        self._poster = poster
        self._model = model
        self._url = url

    @property
    def name(self) -> str:
        return f"ollama:{self._model}"

    def extract(self, job: Job) -> ExtractionResult:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": job_prompt(job)},
            ],
            "stream": False,
            "format": SCHEMA,
            "options": {"temperature": 0},
        }
        try:
            response = self._poster.post_json(self._url, payload, {})
        except FetchError as exc:
            raise ExtractionError(f"{self.name}: {exc} (is `ollama serve` running?)") from exc
        if not isinstance(response, dict):
            raise ExtractionError(f"unexpected response: {str(response)[:200]}")
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ExtractionError(f"answer has no text: {str(response)[:200]}")
        input_tokens = response.get("prompt_eval_count")
        output_tokens = response.get("eval_count")
        return ExtractionResult(
            parse_extraction(content),
            input_tokens if isinstance(input_tokens, int) else None,
            output_tokens if isinstance(output_tokens, int) else None,
        )

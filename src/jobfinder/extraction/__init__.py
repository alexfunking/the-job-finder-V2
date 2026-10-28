"""Extractors: a job posting in, structured fields out (ADR-006). `build_extractor` picks one by name."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jobfinder.envfile import secret
from jobfinder.extraction.base import Extraction, ExtractionError, ExtractionResult, Extractor
from jobfinder.extraction.gemini import GeminiExtractor
from jobfinder.extraction.ollama import OllamaExtractor
from jobfinder.http import Poster

__all__ = [
    "EXTRACTORS", "Extraction", "ExtractionError", "ExtractionResult", "Extractor", "build_extractor",
]

EXTRACTORS: Final = ("gemini", "ollama")
GEMINI_KEY_VAR: Final = "GEMINI_API_KEY"


def build_extractor(kind: str, model: str | None, env_file: Path, poster: Poster) -> Extractor:
    """The extractor called `kind`, with its default model unless `model` is given.

    Raises ValueError for an unknown kind or a missing API key.
    """
    if kind == "gemini":
        key = secret(GEMINI_KEY_VAR, env_file)
        if not key:
            raise ValueError(f"set {GEMINI_KEY_VAR} in {env_file}")
        return GeminiExtractor(key, poster, model) if model else GeminiExtractor(key, poster)
    if kind == "ollama":
        return OllamaExtractor(poster, model) if model else OllamaExtractor(poster)
    raise ValueError(f"unknown extractor {kind!r}; choose one of {', '.join(EXTRACTORS)}")

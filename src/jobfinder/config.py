"""Hand-maintained TOML lists: the boards to scan (data/sources.toml) and the watchlist (data/watchlist.toml)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from jobfinder.models import KeyPart, NonEmptyStr
from jobfinder.sources import SOURCES


class ConfigError(Exception):
    """The sources file is missing, malformed, or names something the scanner cannot use."""


class Board(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    company: NonEmptyStr
    source: KeyPart
    slug: KeyPart

    @property
    def label(self) -> str:
        return f"{self.source}:{self.slug}"


class WatchedCompany(BaseModel):
    """A company with no public job API: the user checks its careers page by hand (ADR-007)."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: NonEmptyStr
    url: NonEmptyStr

    @field_validator("url")
    @classmethod
    def _require_https_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("url must start with https://")
        return value


def load_watchlist(path: Path) -> list[WatchedCompany]:
    """The companies in the watchlist file; a missing file means none."""
    if not path.exists():
        return []
    try:
        entries = tomllib.loads(path.read_text(encoding="utf-8")).get("company", [])
        return [WatchedCompany.model_validate(entry) for entry in entries]
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc.errors()[0]['msg']}") from exc


def load_boards(path: Path) -> list[Board]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"sources file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    entries = data.get("board")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: expected at least one [[board]] entry")

    boards: list[Board] = []
    for index, entry in enumerate(entries, start=1):
        try:
            board = Board.model_validate(entry)
        except ValidationError as exc:
            raise ConfigError(f"{path}: board #{index}: {exc.errors()[0]['msg']} ({entry})") from exc
        if board.source not in SOURCES:
            raise ConfigError(
                f"{path}: board #{index} ({board.company}): unknown source {board.source!r}; "
                f"known: {', '.join(sorted(SOURCES))}"
            )
        boards.append(board)

    seen: set[str] = set()
    for board in boards:
        if board.label in seen:
            raise ConfigError(f"{path}: {board.label} is listed more than once")
        seen.add(board.label)
    return boards

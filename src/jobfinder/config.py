"""The list of boards to scan, read from a hand-maintained TOML file (data/sources.toml)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

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

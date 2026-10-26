from __future__ import annotations

from pathlib import Path

import pytest

from jobfinder.config import Board, ConfigError, load_boards

REPO_SOURCES = Path(__file__).resolve().parents[1] / "data" / "sources.toml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_boards(tmp_path: Path) -> None:
    path = write(tmp_path, """
[[board]]
company = "Wiz"
source = "greenhouse"
slug = "wizinc"

[[board]]
company = " monday.com "
source = "ashby"
slug = "monday.com"
""")
    assert load_boards(path) == [
        Board(company="Wiz", source="greenhouse", slug="wizinc"),
        Board(company="monday.com", source="ashby", slug="monday.com"),
    ]


def test_the_committed_sources_file_is_valid() -> None:
    boards = load_boards(REPO_SOURCES)
    assert len(boards) >= 20
    assert {b.source for b in boards} <= {"greenhouse", "ashby"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "at least one"),
        ("board = 3", "at least one"),
        ("[[board]\n", "invalid TOML"),
        ('[[board]]\ncompany = "X"\nsource = "greenhouse"\n', "board #1"),
        ('[[board]]\ncompany = "X"\nsource = "greenhouse"\nslug = "x"\nextra = 1\n', "board #1"),
        ('[[board]]\ncompany = "X"\nsource = "comeet"\nslug = "x"\n', "unknown source 'comeet'"),
        ('[[board]]\ncompany = "X"\nsource = "greenhouse"\nslug = "a:b"\n', "board #1"),
    ],
)
def test_rejects_invalid_files(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_boards(write(tmp_path, text))


def test_rejects_duplicate_boards(tmp_path: Path) -> None:
    entry = '[[board]]\ncompany = "X"\nsource = "greenhouse"\nslug = "x"\n'
    with pytest.raises(ConfigError, match="greenhouse:x is listed more than once"):
        load_boards(write(tmp_path, entry + entry))


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_boards(tmp_path / "nope.toml")

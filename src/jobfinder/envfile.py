"""Secrets from the environment or a local `.env` file (gitignored), without a python-dotenv dependency."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines; blank lines and # comments are ignored, surrounding quotes are dropped."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def secret(name: str, env_file: Path, environ: Mapping[str, str] = os.environ) -> str | None:
    """The value of `name`: a real environment variable wins over the `.env` file."""
    value = environ.get(name) or read_env_file(env_file).get(name)
    return value or None

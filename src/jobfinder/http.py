"""HTTP access for sources, behind a Protocol so tests inject canned responses instead of the network (ADR-004)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Final, Protocol

USER_AGENT: Final = "job-finder/0.1 (personal job search; contact via GitHub)"
TIMEOUT_S: Final = 30.0


class FetchError(Exception):
    """The request did not produce a usable answer: network failure, timeout, 429/5xx, or invalid JSON."""


class Fetcher(Protocol):
    def get_json(self, url: str) -> object | None:
        """Return the parsed JSON body, None on HTTP 404, or raise FetchError."""
        ...


def parse_response(status: int, body: bytes) -> object | None:
    """Map an HTTP status and body to the Fetcher contract."""
    if status == 404:
        return None
    if status != 200:
        raise FetchError(f"HTTP {status}")
    try:
        payload: object = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FetchError(f"invalid JSON: {exc}") from exc
    return payload


class UrllibFetcher:
    def __init__(self, user_agent: str = USER_AGENT, timeout_s: float = TIMEOUT_S) -> None:
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._timeout_s = timeout_s

    def get_json(self, url: str) -> object | None:
        request = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                status: int = response.status
                body: bytes = response.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, b""
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        return parse_response(status, body)

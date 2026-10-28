"""HTTP access for sources, behind a Protocol so tests inject canned responses instead of the network (ADR-004)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Final, Protocol

USER_AGENT: Final = "job-finder/0.1 (personal job search; contact via GitHub)"
TIMEOUT_S: Final = 30.0
RETRY_STATUSES: Final = frozenset({429, 503})


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


class Poster(Protocol):
    def post_json(self, url: str, payload: object, headers: Mapping[str, str]) -> object:
        """POST `payload` as JSON and return the parsed JSON answer, or raise FetchError (404 included)."""
        ...


class UrllibPoster:
    """JSON-over-HTTP calls to model APIs (the extractor); slower than board fetches, so a longer timeout.

    A busy model (503) or a per-minute quota (429) usually clears within a minute, so those are retried
    after each delay in `retry_delays_s`; every other failure is raised at once.
    """

    def __init__(
        self,
        timeout_s: float = 180.0,
        retry_delays_s: Sequence[float] = (5.0, 20.0, 60.0),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._timeout_s = timeout_s
        self._retry_delays_s = tuple(retry_delays_s)
        self._sleep = sleep

    def post_json(self, url: str, payload: object, headers: Mapping[str, str]) -> object:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
            method="POST",
        )
        for delay in (*self._retry_delays_s, None):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                    body: bytes = response.read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_STATUSES and delay is not None:
                    self._sleep(delay)
                    continue
                # The error body says why (bad model name, quota, ...); it never echoes the request headers.
                detail = " ".join(exc.read().decode("utf-8", errors="replace").split())[:300]
                raise FetchError(f"HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise FetchError(f"{type(exc).__name__}: {exc}") from exc
        try:
            parsed: object = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FetchError(f"invalid JSON: {exc}") from exc
        return parsed

"""Test doubles shared across test modules."""

from __future__ import annotations

from collections.abc import Mapping


class FakeFetcher:
    """A Fetcher that serves canned responses by exact URL and records every request.

    A value that is an Exception is raised instead of returned. Requesting a URL that has no canned
    response fails the test, so a source cannot silently hit an unexpected endpoint.
    """

    def __init__(self, responses: Mapping[str, object | None | Exception]) -> None:
        self._responses = dict(responses)
        self.requested: list[str] = []

    def get_json(self, url: str) -> object | None:
        self.requested.append(url)
        if url not in self._responses:
            raise AssertionError(f"unexpected request: {url}")
        response = self._responses[url]
        if isinstance(response, Exception):
            raise response
        return response

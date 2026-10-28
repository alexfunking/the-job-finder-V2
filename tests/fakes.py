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


class FakePoster:
    """A Poster that answers every POST with the next canned response and records each request.

    A response that is an Exception is raised instead of returned.
    """

    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, object, dict[str, str]]] = []

    def post_json(self, url: str, payload: object, headers: Mapping[str, str]) -> object:
        self.requests.append((url, payload, dict(headers)))
        if not self._responses:
            raise AssertionError(f"unexpected request: {url}")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

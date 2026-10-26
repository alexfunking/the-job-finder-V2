from __future__ import annotations

import email.message
import io
import urllib.error
import urllib.request

import pytest

from jobfinder.http import FetchError, UrllibFetcher, parse_response


def test_parse_response_200_returns_payload() -> None:
    assert parse_response(200, b'{"jobs": []}') == {"jobs": []}


def test_parse_response_404_means_no_board() -> None:
    assert parse_response(404, b"") is None


@pytest.mark.parametrize("status", [400, 403, 429, 500, 503])
def test_parse_response_other_statuses_raise(status: int) -> None:
    with pytest.raises(FetchError, match=f"HTTP {status}"):
        parse_response(status, b"")


@pytest.mark.parametrize("body", [b"<html>blocked</html>", b"", b"\xff\xfe"])
def test_parse_response_200_with_invalid_json_raises(body: bytes) -> None:
    with pytest.raises(FetchError, match="invalid JSON"):
        parse_response(200, body)


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_urllib_fetcher_sends_user_agent_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request)
        return _Response(200, b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert UrllibFetcher(user_agent="test-agent").get_json("https://example.test/x") == {"ok": True}
    assert seen[0].get_header("User-agent") == "test-agent"


def test_urllib_fetcher_maps_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        code = 404 if request.full_url.endswith("missing") else 503
        raise urllib.error.HTTPError(request.full_url, code, "error", email.message.Message(), io.BytesIO())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    fetcher = UrllibFetcher()
    assert fetcher.get_json("https://example.test/missing") is None
    with pytest.raises(FetchError, match="HTTP 503"):
        fetcher.get_json("https://example.test/down")


@pytest.mark.parametrize("error", [urllib.error.URLError("dns failure"), TimeoutError("read timed out")])
def test_urllib_fetcher_wraps_network_errors(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(FetchError, match=type(error).__name__):
        UrllibFetcher().get_json("https://example.test/x")

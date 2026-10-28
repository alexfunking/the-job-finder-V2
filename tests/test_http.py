from __future__ import annotations

import email.message
import io
import urllib.error
import urllib.request

import pytest

from jobfinder.http import FetchError, UrllibFetcher, UrllibPoster, parse_response


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


def _http_error(url: str, code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", email.message.Message(), io.BytesIO(body))


def test_urllib_poster_sends_json_and_retries_busy_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[_Response | urllib.error.HTTPError] = [
        _http_error("u", 503), _http_error("u", 429), _Response(200, b'{"ok": true}'),
    ]
    seen: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    slept: list[float] = []
    poster = UrllibPoster(retry_delays_s=(1.0, 2.0, 3.0), sleep=slept.append)
    assert poster.post_json("https://example.test/m", {"q": 1}, {"x-key": "k"}) == {"ok": True}
    assert slept == [1.0, 2.0]
    assert seen[0].data == b'{"q": 1}'
    assert seen[0].get_header("X-key") == "k"
    assert seen[0].get_method() == "POST"


def test_urllib_poster_gives_up_after_the_last_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        raise _http_error(request.full_url, 429, b'{\n  "error": "quota"\n}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    slept: list[float] = []
    with pytest.raises(FetchError, match='HTTP 429: { "error": "quota" }'):
        UrllibPoster(retry_delays_s=(1.0,), sleep=slept.append).post_json("https://example.test/m", {}, {})
    assert slept == [1.0]


def test_urllib_poster_does_not_retry_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        raise _http_error(request.full_url, 400, b"bad model")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    slept: list[float] = []
    with pytest.raises(FetchError, match="HTTP 400: bad model"):
        UrllibPoster(sleep=slept.append).post_json("https://example.test/m", {}, {})
    assert slept == []

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from jobfinder.label_desk import LabelDesk, LabelError
from jobfinder.labels import Suggestion, load_labels
from jobfinder.models import Job
from jobfinder.sampling import review_queue
from jobfinder.storage import JobStore
from jobfinder.ui import make_handler, render_label_page

T1 = datetime(2026, 10, 30, 8, tzinfo=UTC)
TOKEN = "test-token"


def job(job_id: str, title: str = "Backend Engineer") -> Job:
    return Job(
        source="greenhouse", slug="acme", job_id=job_id, company="Acme", title=title, location="Tel Aviv",
        url=f"https://example.test/{job_id}", description_text="Build things.",
    )


def suggestion(job_id: str, verdict: str, reason: str) -> Suggestion:
    return Suggestion.model_validate({"key": job(job_id).key, "verdict": verdict, "reason": reason})


@pytest.fixture
def desk(tmp_path: Path) -> LabelDesk:
    db = tmp_path / "jobs.db"
    store = JobStore.open(db)
    store.record_board("greenhouse", "acme", [job("1"), job("2"), job("3")], T1)
    store.close()
    path = tmp_path / "suggestions.jsonl"
    path.write_text(suggestion("3", "fit", "junior, Tel Aviv").model_dump_json() + "\n", encoding="utf-8")
    return LabelDesk(db, tmp_path / "labels.jsonl", path, seed=1, now=lambda: T1)


def labels_of(tmp_path: Path) -> dict[str, tuple[str, str, str | None]]:
    labels = load_labels(tmp_path / "labels.jsonl")
    return {key: (label.verdict, label.reason, label.suggestion) for key, label in labels.items()}


def test_review_queue_puts_suggested_jobs_first() -> None:
    jobs = [job("1"), job("2"), job("3")]
    queue = review_queue(jobs, {}, {job("2").key: suggestion("2", "fit", "r")}, seed=1)
    assert queue[0].job_id == "2"
    assert {j.job_id for j in queue} == {"1", "2", "3"}


def test_state_lists_suggested_jobs_first_with_their_suggestion(desk: LabelDesk) -> None:
    state = desk.state()
    items = state["items"]
    assert isinstance(items, list)
    assert len(items) == 3
    assert items[0]["key"] == job("3").key
    assert items[0]["suggestion"] == {"verdict": "fit", "reason": "junior, Tel Aviv"}
    assert items[1]["suggestion"] is None


def test_accepting_a_suggestion_keeps_its_reason_and_records_it(desk: LabelDesk, tmp_path: Path) -> None:
    progress = desk.record(job("3").key, "fit", "")
    assert labels_of(tmp_path)[job("3").key] == ("fit", "junior, Tel Aviv", "fit")
    assert (progress["done"], progress["fit"], progress["accepted"], progress["changed"]) == (1, 1, 1, 0)


def test_overriding_needs_a_reason_and_counts_as_changed(desk: LabelDesk, tmp_path: Path) -> None:
    with pytest.raises(LabelError, match="needs a reason"):
        desk.record(job("3").key, "no_fit", "  ")
    assert not (tmp_path / "labels.jsonl").exists()
    progress = desk.record(job("3").key, "no_fit", "too far for me")
    assert labels_of(tmp_path)[job("3").key] == ("no_fit", "too far for me", "fit")
    assert (progress["accepted"], progress["changed"]) == (0, 1)


def test_a_job_without_a_suggestion_needs_a_reason(desk: LabelDesk) -> None:
    with pytest.raises(LabelError, match="needs a reason"):
        desk.record(job("1").key, "fit", "")


def test_skip_needs_no_reason_and_leaves_the_queue(desk: LabelDesk) -> None:
    progress = desk.record(job("1").key, "skip", "")
    assert (progress["done"], progress["skip"]) == (0, 1)
    items = desk.state()["items"]
    assert isinstance(items, list)
    assert job("1").key not in {item["key"] for item in items}


def test_answering_again_replaces_the_answer(desk: LabelDesk, tmp_path: Path) -> None:
    desk.record(job("1").key, "fit", "first")
    desk.record(job("1").key, "no_fit", "second")
    assert labels_of(tmp_path)[job("1").key] == ("no_fit", "second", None)


def test_unknown_job_and_verdict_are_refused(desk: LabelDesk) -> None:
    with pytest.raises(LabelError, match="no job with key"):
        desk.record("greenhouse:acme:9", "fit", "r")
    with pytest.raises(LabelError):
        desk.record(job("1").key, "great", "r")


def test_label_page_carries_the_token() -> None:
    page = render_label_page(TOKEN)
    assert f'const TOKEN = "{TOKEN}";' in page
    assert "__TOKEN__" not in page


@pytest.fixture
def base_url(desk: LabelDesk) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(lambda: {}, desk, TOKEN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def post(url: str, body: object, token: str | None = TOKEN, host: str | None = None) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Label-Token"] = token
    if host is not None:
        headers["Host"] = host
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_saving_over_http_with_the_token(base_url: str, tmp_path: Path) -> None:
    status, body = post(f"{base_url}/api/label", {"key": job("3").key, "verdict": "fit"})
    assert status == 200
    assert json.loads(body)["fit"] == 1
    assert job("3").key in labels_of(tmp_path)


@pytest.mark.parametrize("token", [None, "wrong"])
def test_saving_without_the_right_token_is_refused(base_url: str, tmp_path: Path, token: str | None) -> None:
    status, _ = post(f"{base_url}/api/label", {"key": job("3").key, "verdict": "fit"}, token=token)
    assert status == 403
    assert not (tmp_path / "labels.jsonl").exists()


def test_a_foreign_host_header_is_refused(base_url: str, tmp_path: Path) -> None:
    status, _ = post(f"{base_url}/api/label", {"key": job("3").key, "verdict": "fit"}, host="evil.example:8765")
    assert status == 403
    assert not (tmp_path / "labels.jsonl").exists()
    request = urllib.request.Request(f"{base_url}/api/label/state", headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError, match="403"):
        urllib.request.urlopen(request, timeout=5)


def test_a_bad_answer_is_a_400(base_url: str) -> None:
    assert post(f"{base_url}/api/label", {"verdict": "fit"})[0] == 400
    assert post(f"{base_url}/api/label", ["not", "an", "object"])[0] == 400
    status, body = post(f"{base_url}/api/label", {"key": job("1").key, "verdict": "fit", "reason": ""})
    assert status == 400
    assert b"needs a reason" in body


def test_label_page_and_state_are_served(base_url: str) -> None:
    with urllib.request.urlopen(f"{base_url}/label", timeout=5) as response:
        assert "Label Jobs" in response.read().decode("utf-8")
    with urllib.request.urlopen(f"{base_url}/api/label/state", timeout=5) as response:
        assert len(json.loads(response.read())["items"]) == 3

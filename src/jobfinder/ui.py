"""`jobfinder ui`: a local web page with the scored jobs and a screen to label them.

The server listens on 127.0.0.1 only and reads the store fresh on every request, so a scan or an extraction
run shows up on reload. The only thing it writes is labels. Because any website the user visits could try
to reach a local server, every request must name a local Host (which stops DNS rebinding), and saving a
label needs a token that is created at startup and only given to this server's own page. Job text is put
into the page with textContent, never as HTML.
"""

from __future__ import annotations

import hmac
import json
import secrets
import webbrowser
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

from jobfinder.label_desk import LabelDesk, LabelError
from jobfinder.labels import load_labels
from jobfinder.matches import generated_at, match_rows, summary_counts
from jobfinder.scoring import load_priorities
from jobfinder.storage import JobStore

PAGE_TEMPLATE: Final = Path(__file__).with_name("ui_page.html")
LABEL_TEMPLATE: Final = Path(__file__).with_name("ui_label.html")
HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
LOCAL_HOSTNAMES: Final = frozenset({"127.0.0.1", "localhost"})
TOKEN_HEADER: Final = "X-Label-Token"
MAX_BODY_BYTES: Final = 64 * 1024


def build_payload(
    db_path: Path, labels_path: Path, priorities_path: Path, prefer: Sequence[str], now: datetime
) -> dict[str, object]:
    """What the page shows. Raises PrioritiesError or ValueError (a bad labels file) for the caller to report."""
    priorities = load_priorities(priorities_path)
    labels = load_labels(labels_path)
    store = JobStore.open(db_path)
    try:
        rows = match_rows(store, priorities, labels, prefer)
    finally:
        store.close()
    return {
        "generated_at": generated_at(now),
        "counts": summary_counts(rows),
        "extracted": sum(1 for row in rows if row.extraction is not None),
        "rows": [row.to_json() for row in rows],
    }


def render_page(payload: dict[str, object]) -> str:
    # "</" inside the JSON would end the <script> element early; "<\/" is the same string to JSON.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return PAGE_TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data)


def render_label_page(token: str) -> str:
    return LABEL_TEMPLATE.read_text(encoding="utf-8").replace("__TOKEN__", token)


def make_handler(
    payload: Callable[[], dict[str, object]], desk: LabelDesk | None = None, token: str = ""
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (the name http.server calls)
            if not self._local_host():
                return
            path = self.path.split("?", 1)[0]
            if path in ("/label", "/api/label/state") and desk is not None:
                self._label_get(path, desk)
                return
            if path not in ("/", "/api/matches"):
                self._send(404, "text/plain; charset=utf-8", b"not found")
                return
            try:
                current = payload()
            except Exception as exc:  # shown in the browser rather than as a dropped connection
                self._send(500, "text/plain; charset=utf-8", f"cannot build the page: {exc}".encode())
                return
            if path == "/":
                self._send(200, "text/html; charset=utf-8", render_page(current).encode("utf-8"))
            else:
                self._send(200, "application/json", json.dumps(current, ensure_ascii=False).encode("utf-8"))

        def do_POST(self) -> None:  # noqa: N802 (the name http.server calls)
            if not self._local_host():
                return
            if self.path != "/api/label" or desk is None:
                self._send(404, "text/plain; charset=utf-8", b"not found")
                return
            if not token or not hmac.compare_digest(self.headers.get(TOKEN_HEADER, ""), token):
                self._send(403, "text/plain; charset=utf-8", b"bad token: reload the page")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY_BYTES:
                self._send(400, "text/plain; charset=utf-8", b"bad request size")
                return
            try:
                answer = json.loads(self.rfile.read(length))
                result = desk.record(str(answer["key"]), str(answer["verdict"]), str(answer.get("reason", "")))
            except LabelError as exc:
                self._send(400, "text/plain; charset=utf-8", str(exc).encode("utf-8"))
            except (ValueError, KeyError, TypeError, AttributeError):
                self._send(400, "text/plain; charset=utf-8", b"bad answer")
            else:
                self._send(200, "application/json", json.dumps(result).encode("utf-8"))

        def _label_get(self, path: str, desk: LabelDesk) -> None:
            if path == "/label":
                self._send(200, "text/html; charset=utf-8", render_label_page(token).encode("utf-8"))
                return
            try:
                state = desk.state()
            except Exception as exc:  # shown in the browser rather than as a dropped connection
                self._send(500, "text/plain; charset=utf-8", f"cannot load the jobs: {exc}".encode())
                return
            self._send(200, "application/json", json.dumps(state, ensure_ascii=False).encode("utf-8"))

        def _local_host(self) -> bool:
            hostname = self.headers.get("Host", "").rsplit(":", 1)[0]
            if hostname in LOCAL_HOSTNAMES:
                return True
            self._send(403, "text/plain; charset=utf-8", b"not a local host")
            return False

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (the base class's name)
            return

    return Handler


def serve(
    db_path: Path,
    labels_path: Path,
    priorities_path: Path,
    suggestions_path: Path,
    prefer: Sequence[str],
    seed: int,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    def payload() -> dict[str, object]:
        return build_payload(db_path, labels_path, priorities_path, prefer, datetime.now(tz=UTC))

    desk = LabelDesk(db_path, labels_path, suggestions_path, seed, lambda: datetime.now(tz=UTC))
    server = ThreadingHTTPServer((HOST, port), make_handler(payload, desk, secrets.token_urlsafe(32)))
    url = f"http://{HOST}:{server.server_port}/"
    print(f"Job matches at {url}  -  label jobs at {url}label  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

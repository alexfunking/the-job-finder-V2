"""`jobfinder ui`: a local web page listing the scored jobs, to see the matches before the Telegram digest.

The server listens on 127.0.0.1 only, reads the store fresh on every request (so a scan or an extraction
run shows up on reload), and never writes anything. Job text is put into the page with textContent, never
as HTML.
"""

from __future__ import annotations

import json
import webbrowser
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final

from jobfinder.labels import load_labels
from jobfinder.matches import generated_at, match_rows, summary_counts
from jobfinder.scoring import load_priorities
from jobfinder.storage import JobStore

PAGE_TEMPLATE: Final = Path(__file__).with_name("ui_page.html")
HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765


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


def make_handler(payload: Callable[[], dict[str, object]]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (the name http.server calls)
            path = self.path.split("?", 1)[0]
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
    prefer: Sequence[str],
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    def payload() -> dict[str, object]:
        return build_payload(db_path, labels_path, priorities_path, prefer, datetime.now(tz=UTC))

    server = ThreadingHTTPServer((HOST, port), make_handler(payload))
    url = f"http://{HOST}:{server.server_port}/"
    print(f"Job matches at {url} (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

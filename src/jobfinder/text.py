"""Plain-text rendering of job-description HTML, for storage and for the Phase 2 extractor."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Final

BLOCK_TAGS: Final = frozenset({
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6",
})
SKIP_TAGS: Final = frozenset({"script", "style", "noscript", "template"})


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in BLOCK_TAGS and tag != "li":
            # A list item's line break comes from the next item's start tag, so items stay on adjacent lines.
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Render HTML as plain text: block elements become line breaks, list items get "- ", whitespace is tidied."""
    collector = _TextCollector()
    collector.feed(html)
    collector.close()
    raw = "".join(collector.parts).replace("\xa0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

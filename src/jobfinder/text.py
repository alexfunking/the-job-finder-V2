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
        # True between a list item's "- " marker and its first visible text. Blocks opened in that window
        # (Ashby wraps every item in <p>) must not start a new line, or the text lands below the dash.
        self._item_empty = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "li":
            self.parts.extend(("\n", "- "))
            self._item_empty = True
        elif tag in BLOCK_TAGS and not self._item_empty:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in ("li", "ul", "ol") and self._item_empty:
            # Closing the item, or its list when </li> was omitted, ends an item that never got text.
            self.parts.pop()  # its "- "
            self._item_empty = False
        if tag in BLOCK_TAGS and tag != "li" and not self._item_empty:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or (self._item_empty and not data.strip()):
            return
        self._item_empty = False
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """Render HTML as plain text: block elements become line breaks, list items get "- ", whitespace is tidied."""
    collector = _TextCollector()
    collector.feed(html)
    collector.close()
    raw = "".join(collector.parts).replace("\xa0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n"))
    text = "\n".join(lines)
    # An unclosed empty item (<li></ul>) never reaches handle_endtag("li"), so its bare dash is removed here.
    text = re.sub(r"(?m)^-\n?$", "", text)
    # A block closing inside an item (</p></li>) leaves a blank line before the next item; keep items adjacent.
    text = re.sub(r"(?m)^(- .*)\n\n+(?=- )", r"\1\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

"""Converting a job posting's HTML description into plain text.

Deliberately simple and dependency-free (stdlib ``html.parser`` only — this project
keeps dependencies minimal, and a full HTML parser like BeautifulSoup is more than
converting marked-up prose into text needs). Block-level tags become line breaks,
everything else is flattened; entities are unescaped; whitespace is collapsed.

This does not attempt to recover structure (which bullet was a requirement versus a
responsibility) — see ``app/connectors/greenhouse.py`` for why that is deliberately
left alone rather than guessed at.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

#: Tags that should force a line break so paragraphs and list items don't run
#: together into one unreadable line.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "blockquote",
    }
)

_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str | None) -> str | None:
    """Plain text from a job description's HTML, or ``None`` for ``None`` in."""
    if not html or not html.strip():
        return None

    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Pathological markup must not take down a whole discovery run over one
        # posting's description. Fall back to a conservative tag strip.
        stripped = re.sub(r"<[^>]+>", " ", html)
        return _normalise_whitespace(stripped)

    return _normalise_whitespace(parser.text())


def _normalise_whitespace(text: str) -> str:
    lines = [_WHITESPACE_RUN.sub(" ", line).strip() for line in text.split("\n")]
    joined = "\n".join(line for line in lines)
    collapsed = _BLANK_LINES.sub("\n\n", joined)
    return collapsed.strip()

"""An HTML mail body as plain text: ONE function for every process that reads one.

WHY A MODULE OF ITS OWN (2026-11-20). The seat reads a Graph email's HTML body
as text with :func:`html_to_text` (``shared/msgraph_client``), and the plain-yes
grammar judges that text against the sender's signature block. The block is
learned by the broker's root seed (``workspace_broker/sender_block``) from the
same mailbox, and a second converter there gave different lines for the same
HTML (an unclosed ``<p>``, a ``<style>`` block), so his own signature could fail
to match his own block. The broker runs in its own virtualenv and imports
nothing from the overlay, so it carries a byte-identical copy written by
``operator/bin/gen-skill-shared.py`` (module ``mail_text`` in
``operator/contracts/skill-shared-modules.yaml``). Edit this file and regenerate.

Standard library only.
"""

from __future__ import annotations

import html
from html.parser import HTMLParser
from typing import Any

_DROP_CONTENT_TAGS = {"script", "style", "head", "title"}
_BREAK_TAGS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}


class _TextExtractor(HTMLParser):
    """Collect visible text from an HTML fragment, dropping script/style content
    and inserting newlines at block boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _DROP_CONTENT_TAGS:
            self._skip_depth += 1
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BREAK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def html_to_text(raw_html: str) -> str:
    """Strip an HTML mail body to plain text (stdlib only): block boundaries become
    line breaks, empty lines dropped, entities decoded. Content, not layout."""
    parser = _TextExtractor()
    parser.feed(raw_html)
    parser.close()
    text = html.unescape(parser.get_text())
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


__all__ = ["html_to_text"]

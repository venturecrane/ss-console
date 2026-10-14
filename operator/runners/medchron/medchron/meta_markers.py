"""Notes the pipeline writes about its own reading, which must never reach the
firm's document.

The map pass sometimes marks a page whose text runs on to the next with a
parenthetical of its own, "(text continues beyond the page)", and on
2026-10-13 one reached an added entry of a real update. It is a statement
about our extraction, not about the record. `strip` removes every such note
before audit and render; `found` is what a proof fails on.
"""

from __future__ import annotations

import re

MARKER = re.compile(
    r"\s*[\(\[](?:the\s+)?(?:text|note|record|entry|sentence|page|content|report|transcription)\b[^()\[\]\n]{0,60}?"
    r"\b(?:continues|continued|cut off|truncated|incomplete|ends mid|runs on|beyond (?:the|this) page)"
    r"[^()\[\]\n]{0,60}[\)\]]",
    re.I,
)


def found(text: str | None) -> list[str]:
    return [m.group(0).strip() for m in MARKER.finditer(text or "")]


def strip(text: str) -> str:
    return MARKER.sub("", text)

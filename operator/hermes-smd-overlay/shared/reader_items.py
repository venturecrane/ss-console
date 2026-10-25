"""Validation of the sealed reader's model output (ADR 0099).

An item survives only when its ``source_url`` is EXACTLY one code fetched and
its title and summary carry no URL, no email address and no instruction or
recipient shape (English and Spanish). Markup and control characters are
stripped and lengths capped. Split out of ``shared.sealed_reader`` to keep
each module under the file-size ceiling; the reader re-exports these names.
"""

from __future__ import annotations

import re
from typing import Any

from shared.safe_fetch import _CONTROL_RE

MAX_ITEMS = 8
MAX_SUMMARY_CHARS = 280
MAX_TITLE_CHARS = 200
MAX_DATE_CHARS = 40

#: Instruction / recipient shapes a summary or title may not carry (EN + ES).
_INSTRUCTION_RE = re.compile(
    r"\b(?:ignore|ignoring|disregard|forget\s+(?:all|previous|your|the)|override|"
    r"system\s+prompt|instructions?|send|forward|e-?mail\s+(?:to|us|me)|reply\s+to|"
    r"respond\s+to|contact\s+(?:us|me)|click|password|api\s+key|"
    r"ignora|ignorar|ignore\s+las|olvida|olvidar|env[ií]a|enviar|reenv[ií]a|reenviar|"
    r"responde\s+a|responder\s+a|correo\s+a|escribe\s+a|instrucci[oó]n(?:es)?|"
    r"mensaje\s+del\s+sistema|contrase[nñ]a)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|\bwww\.|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|co|us|"
    r"gov|edu|info|biz|ly|me|tv|ai|app|dev|xyz|link|site|online|es|mx|uk|ca)\b)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_MARKUP_RE = re.compile(r"<[^>]{0,500}>|[`*_#\[\]{}|<>]")

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = _CONTROL_RE.sub(" ", value)
    text = _MARKUP_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def text_is_safe(text: str) -> bool:
    """No URL, no email address, no instruction or recipient shape."""
    return not (_URL_RE.search(text) or _EMAIL_RE.search(text) or _INSTRUCTION_RE.search(text))


def validate_items(raw_items: list[Any], allowed_urls: set[str]) -> tuple[list[dict], int]:
    """Keep only items whose source_url is EXACTLY one code fetched and whose
    text passes :func:`text_is_safe`. Returns ``(items, dropped)``."""
    kept: list[dict] = []
    dropped = 0
    for item in raw_items:
        if len(kept) >= MAX_ITEMS:
            dropped += 1
            continue
        if not isinstance(item, dict):
            dropped += 1
            continue
        url = item.get("source_url")
        if not isinstance(url, str) or url not in allowed_urls:
            dropped += 1
            continue
        summary = clean_text(item.get("summary"), MAX_SUMMARY_CHARS)
        title = clean_text(item.get("title"), MAX_TITLE_CHARS)
        date = clean_text(item.get("date"), MAX_DATE_CHARS)
        if not summary or not text_is_safe(summary) or not text_is_safe(title):
            dropped += 1
            continue
        if date and not text_is_safe(date):
            date = ""
        kept.append({"title": title, "date": date, "summary": summary, "source_url": url})
    return kept, dropped

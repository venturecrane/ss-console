"""Reading the scanned pages of a mixed PDF, and only those (wave 5, K1).

A mixed PDF is typed pages with scans among them (``extract.METHOD_MIXED``).
Its typed pages are read from the text layer for nothing. Its scanned pages
need a vision read, and this module is the one place that pays for one:

* **Only the unread pages are sent.** They are cut out of the file
  (``letter_pages.extract_pages``) and sent through the same per-page road a
  whole scan takes (``vision.transcribe_pdf``: one page per call, the same model
  and instruction, every gate checked before any spend), the way
  ``letter_tools`` reads a post bundle.
* **Code holds the bound.** At most ``vision.page_cap()`` pages of one file are
  ever sent, counted on pages SENT, with pages already stored counted against
  it. A page over the bound stays ``[p.N: scanned page, not read]`` and the
  result says so (``REASON_OVER_PAGE_CAP``).
* **Each page is stored once**, under its own cache name
  (``extract_cache.page_key``: the file's sha, the page, and
  :func:`method_version`). A second read of the same file pays nothing, and the
  drafting record check, which never transcribes, reads the same stored pages.
* **Vision is used only where the caller already allows it**
  (``allow_vision=True``: ``read_document``, ``cited_facts``). Every other caller
  is served what is stored and nothing more.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from . import letter_pages, vision
from .extract import REASON_INCOMPLETE, REASON_OVER_PAGE_CAP
from .extract_cache import cache_key, page_get, page_put

#: How a page is rendered for its call: pypdf copies the page into a PDF of its
#: own (``letter_pages.extract_pages`` then ``vision._split_pages``). Part of
#: the method version, so a change to the render is a new cache name.
PAGE_RENDER = "pypdf-page-copy/1"


def method_version() -> str:
    """The model, the prompt (the whole request but the page) and the render a
    page is read with, as 16 hex characters."""
    raw = json.dumps({"request": vision.build_request(""), "render": PAGE_RENDER}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class PagesRead:
    """What became of a mixed file's unread pages.

    ``read`` maps a 1-based page to its transcription (the model's
    ``[no legible content]`` for a page it could not read). ``paid`` is True
    when this call transcribed any page. ``reason`` says why a page is still
    unread (over the bound, or the read failed or was refused), else None."""

    read: dict[int, str] = field(default_factory=dict)
    paid: bool = False
    reason: str | None = None


def read_unread(blob: bytes, unread: tuple[int, ...], *, allow_vision: bool) -> PagesRead:
    """The stored reading of each unread page, then (only with
    ``allow_vision``) a vision read of the rest, up to the bound. Never raises:
    a read that fails leaves its pages unread, with the closed-set reason."""
    sha, version = cache_key(blob), method_version()
    read: dict[int, str] = {}
    for page in unread:
        stored = page_get(sha, page, version)
        if stored is not None:
            read[page] = stored
    missing = [page for page in unread if page not in read]
    if not missing or not allow_vision:
        return PagesRead(read)
    send = missing[: max(0, vision.page_cap() - len(read))]
    capped = REASON_OVER_PAGE_CAP if len(send) < len(missing) else None
    if not send:
        return PagesRead(read, reason=capped)
    try:
        sub = letter_pages.extract_pages(blob, [page - 1 for page in send])
    except letter_pages.PageReadError:
        return PagesRead(read, reason=REASON_INCOMPLETE)
    outcome = vision.transcribe_pdf(sub, pages=len(send))
    if outcome.reason is not None:
        return PagesRead(read, reason=outcome.reason)
    try:
        texts = letter_pages.parse_marked(outcome.text, len(send))
    except letter_pages.PageReadError:
        return PagesRead(read, reason=REASON_INCOMPLETE)
    for page, text in zip(send, texts, strict=True):
        body = text or vision.NO_CONTENT_SENTINEL
        page_put(sha, page, version, body)
        read[page] = body
    return PagesRead(read, paid=True, reason=capped)


__all__ = ["PAGE_RENDER", "PagesRead", "method_version", "read_unread"]

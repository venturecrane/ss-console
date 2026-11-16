"""Reading the scanned pages of a mixed PDF, and only those (wave 5, K1).

A mixed PDF is typed pages with scans among them (``extract.METHOD_MIXED``).
Its typed pages are read from the text layer for nothing. Its scanned pages
need a vision read, and this module is the one place that pays for one:

* **Only the unread pages are sent.** They are cut out of the file
  (``letter_pages.extract_pages``), split one page per PDF, and each goes
  through the same per-page call a whole scan's pages go through
  (``vision._transcribe_page``: the same model, instruction and retries), after
  the same gates (kill switch, credential, page cap, per-page byte cap).
* **Every page keeps its own result.** A page that reads, legibly or as the
  model's ``[no legible content]``, is stored, whatever happens to the pages
  around it: there is no all-or-nothing refusal on this road, because each
  page is cited by its own number. A page the model refused or ran out of room
  on is retried on a later read, at most ``PAGE_ATTEMPTS`` times per method
  version, then left unread with its reason and never paid for again.
* **Code holds the bound.** At most ``vision.page_cap()`` distinct pages of one
  file are ever sent, counted on pages SENT (stored readings and recorded
  failures count against it). A page over the bound stays
  ``[p.N: scanned page, not read]`` and the result says so
  (``REASON_OVER_PAGE_CAP``).
* **Each page is stored once**, under its own cache name
  (``extract_cache.page_key``: the file's sha, the page, and
  :func:`method_version`). A second read of the same file pays nothing, and the
  drafting record check, which never transcribes, reads the same stored pages.
* **One read at a time.** The pages to send are chosen again AFTER the shared
  vision lock is taken, so two reads of one file racing each other pay for
  each page once.
* **Vision is used only where the caller already allows it**
  (``allow_vision=True``: ``read_document``, ``cited_facts``). Every other caller
  is served what is stored and nothing more.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import letter_pages, vision
from .extract import (
    REASON_BUSY,
    REASON_INCOMPLETE,
    REASON_OVER_BYTE_CAP,
    REASON_OVER_PAGE_CAP,
    REASON_REFUSED,
    REASON_TRUNCATED,
)
from .extract_cache import cache_key, page_failure_get, page_failure_put, page_get, page_put

#: How a page is rendered for its call: pypdf copies the page into a PDF of its
#: own (``letter_pages.extract_pages`` then ``vision._split_pages``). Part of
#: the method version, so a change to the render is a new cache name.
PAGE_RENDER = "pypdf-page-copy/1"
#: Reads of one page that may fail the same way again (the model refused it, or
#: ran out of room) before it is left unread for good under a method version.
PAGE_ATTEMPTS = 2
#: The failures that would recur on the same page, and so count as attempts.
#: A transport fault, a busy lock or a missing key is the seat's condition, not
#: the page's: nothing is recorded and the next read tries again.
_PAGE_FAILURES = frozenset({REASON_REFUSED, REASON_TRUNCATED})


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
    unread (over the bound, a failed or refused read), else None."""

    read: dict[int, str] = field(default_factory=dict)
    paid: bool = False
    reason: str | None = None


@dataclass
class _Ledger:
    """One file's stored state: its readings, its failed pages, the bound."""

    sha: str
    version: str
    read: dict[int, str] = field(default_factory=dict)
    failed: dict[int, tuple[int, str]] = field(default_factory=dict)

    def refresh(self, pages: tuple[int, ...]) -> None:
        for page in pages:
            stored = page_get(self.sha, page, self.version)
            if stored is not None:
                self.read[page] = stored
                continue
            failure = page_failure_get(self.sha, page, self.version)
            if failure is not None:
                self.failed[page] = failure

    def plan(self, unread: tuple[int, ...]) -> tuple[list[int], str | None]:
        """The pages to send now, and why any other page stays unread."""
        missing = [p for p in unread if p not in self.read]
        retry = [p for p in missing if p in self.failed and self.failed[p][0] < PAGE_ATTEMPTS]
        spent = [p for p in missing if p in self.failed and self.failed[p][0] >= PAGE_ATTEMPTS]
        fresh = [p for p in missing if p not in self.failed]
        budget = max(0, min(vision.page_cap() - len(self.read) - len(self.failed), vision.page_cap() - len(retry)))
        send = retry + fresh[:budget]
        if len(fresh) > budget:
            return send, REASON_OVER_PAGE_CAP
        return send, (self.failed[spent[0]][1] if spent else None)


def read_unread(blob: bytes, unread: tuple[int, ...], *, allow_vision: bool) -> PagesRead:
    """The stored reading of each unread page, then (only with
    ``allow_vision``) a vision read of the rest, up to the bound. Never raises:
    a page that fails is left unread, with the closed-set reason."""
    ledger = _Ledger(cache_key(blob), method_version())
    ledger.refresh(unread)
    send, left_why = ledger.plan(unread)
    if not send or not allow_vision:
        return PagesRead(dict(ledger.read), reason=left_why if allow_vision else None)
    refusal = vision.gate(b"", pages=len(send))  # the byte cap is checked per page below
    if refusal is not None:
        return PagesRead(dict(ledger.read), reason=refusal)
    if not vision._LOCK.acquire(timeout=vision.lock_timeout()):
        return PagesRead(dict(ledger.read), reason=REASON_BUSY)
    try:
        ledger.refresh(tuple(send))  # a read that held the lock before us may have stored these
        send, left_why = ledger.plan(unread)
        failure = _read_pages(blob, send, ledger) if send else None
    finally:
        vision._LOCK.release()
    left = [p for p in unread if p not in ledger.read]
    return PagesRead(dict(ledger.read), paid=bool(send), reason=(failure or left_why) if left else None)


def _read_pages(blob: bytes, send: list[int], ledger: _Ledger) -> str | None:
    """Send each page once, store each result as it is. The first failure
    reason, or None."""
    try:
        page_pdfs = vision._split_pages(letter_pages.extract_pages(blob, [p - 1 for p in send]))
    except Exception:  # noqa: BLE001 - pages that cannot be cut out cannot be read; nothing was sent
        return REASON_INCOMPLETE
    if len(page_pdfs) != len(send):
        return REASON_INCOMPLETE
    sized = [(p, pdf) for p, pdf in zip(send, page_pdfs, strict=True) if len(pdf) <= vision.max_bytes()]
    first = REASON_OVER_BYTE_CAP if len(sized) < len(send) else None
    with ThreadPoolExecutor(max_workers=max(1, min(vision.workers(), len(sized) or 1))) as pool:
        outcomes = list(pool.map(lambda item: vision._transcribe_page(item[1]), sized))
    for (page, _pdf), (text, _stop, failed) in zip(sized, outcomes, strict=True):
        if failed is None:
            body = text.strip() or vision.NO_CONTENT_SENTINEL
            page_put(ledger.sha, page, ledger.version, body)
            ledger.read[page] = body
            continue
        first = first or failed
        if failed in _PAGE_FAILURES:
            attempts = ledger.failed.get(page, (0, failed))[0] + 1
            page_failure_put(ledger.sha, page, ledger.version, attempts, failed)
            ledger.failed[page] = (attempts, failed)
    return first


__all__ = ["PAGE_ATTEMPTS", "PAGE_RENDER", "PagesRead", "method_version", "read_unread"]

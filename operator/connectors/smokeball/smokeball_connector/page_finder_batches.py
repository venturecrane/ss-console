"""The page finder's batch reader (page_finder.py, ADR 0102 amendment 2).

Split out of ``page_finder.py`` (module size). Reads batches of single-page
PDFs into a file's index through a bounded pool; a batch that fails is read a
page at a time; a page the model declines is retried once on a later call,
then settled as unreadable. A paid-call hook's ``CallRefused`` stops every
batch not yet started (ADR 0102 amendment 4), and the refusal is raised as it
was once the calls in flight are recorded.

Requests go through ``page_finder.index_batch`` looked up at call time, so
the hook seam and a rehearsal's guard on page reads both see every one.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import vision


def _pf() -> Any:
    from . import page_finder

    return page_finder


#: Times a page may be declined before it is treated as unreadable for good:
#: once on the call that met it, once more on a later call.
REFUSAL_TRIES = 2


def _settled(index: dict[str, Any], key: str) -> bool:
    """An unreadable page that is not retried: declined :data:`REFUSAL_TRIES`
    times, or unreadable for a reason a retry cannot change (too large)."""
    return int(index["refusals"].get(key, REFUSAL_TRIES)) >= REFUSAL_TRIES


class _BatchReader:
    """Reads batches of pages into the index; one per build, shared by its workers.

    A batch that fails is split and its pages read ONE AT A TIME, so one page
    the model declines never sinks the seven beside it. A page that still fails
    is recorded with its reason: ``unreadable`` (the model answered and declined
    or ran out: cached, not retried) or ``failed`` (the wire, or out of time:
    retried on the next call). Nothing is dropped silently."""

    def __init__(self, index: dict[str, Any], facts: dict[str, Any], deadline: float) -> None:
        self.index, self.facts, self.deadline = index, facts, deadline
        self.lock = threading.Lock()
        #: The first ``page_finder.CallRefused`` a hook raised: no batch starts after it.
        self.stopped: Any = None

    def _record(self, rows: dict[str, dict[str, Any]], tokens: dict[str, int]) -> None:
        with self.lock:
            self.index["pages"].update(rows)
            self.facts["read_now"].extend(int(k) for k in rows)
            for key, value in tokens.items():
                self.facts["tokens"][key] += value

    def _charge(self, exc: Any) -> None:
        with self.lock:
            for key, value in exc.tokens.items():
                self.facts["tokens"][key] += value

    def _note(self, number: int, exc: Any) -> None:
        with self.lock:
            if exc is None:
                self.facts["failed"][str(number)] = "out of time this call"
            elif exc.stop_reason:
                why = f"the model declined to read it (stop_reason {exc.stop_reason})"
                self.facts["unreadable_now"][str(number)] = why
                self.facts["refused_now"].add(str(number))
            else:
                self.facts["failed"][str(number)] = str(exc)

    def _attempt(self, batch: list[tuple[int, bytes]]) -> Any:
        try:
            self._record(*_pf().index_batch(batch))
        except _pf()._CallFailed as exc:
            self._charge(exc)
            return exc
        return None

    def _late(self) -> bool:
        return time.monotonic() > self.deadline

    def read(self, batch: list[tuple[int, bytes]]) -> None:
        try:
            self._read(batch)
        except _pf().CallRefused as exc:
            with self.lock:
                if self.stopped is None:
                    self.stopped = exc
            self._unsent(batch)

    def _unsent(self, batch: list[tuple[int, bytes]]) -> None:
        """Pages the stop kept from being sent: tried again on the next call,
        never recorded unreadable."""
        with self.lock:
            for number, _ in batch:
                if str(number) not in self.index["pages"]:
                    self.facts["failed"].setdefault(str(number), "not sent: the job's cost limit stopped the read")

    def _read(self, batch: list[tuple[int, bytes]]) -> None:
        if self.stopped is not None:
            self._unsent(batch)
            return
        if self._late():
            for number, _ in batch:
                self._note(number, None)
            return
        first = self._attempt(batch)
        if first is None:
            return
        if len(batch) == 1:
            self._note(batch[0][0], first)
            return
        for page in batch:
            self._single(page)

    def _single(self, page: tuple[int, bytes]) -> None:
        if self.stopped is not None:
            self._unsent([page])
            return
        if self._late():
            self._note(page[0], None)
            return
        failure = self._attempt([page])
        if failure is not None:
            self._note(page[0], failure)


def _settle_unreadable(index: dict[str, Any], facts: dict[str, Any]) -> None:
    """Record this call's unreadable pages; a page read on a retry stops being one."""
    for key in map(str, facts["read_now"]):
        index["unreadable"].pop(key, None)
        index["refusals"].pop(key, None)
    for key, why in facts["unreadable_now"].items():
        if key in facts["refused_now"]:
            tries = int(index["refusals"].get(key, 0)) + 1
            index["refusals"][key] = tries
            later = "; it is tried once more on the next call" if tries < REFUSAL_TRIES else f"; declined {tries} times"
            index["unreadable"][key] = why + later
        else:
            index["refusals"][key] = REFUSAL_TRIES
            index["unreadable"][key] = why


def _run_batches(
    batches: list[list[tuple[int, bytes]]], index: dict[str, Any], facts: dict[str, Any], deadline: float
) -> None:
    """Every batch through a bounded pool (:class:`_BatchReader`). A hook's
    ``page_finder.CallRefused`` stops every batch not yet started; the calls already
    in flight finish and are recorded, then the refusal is raised as it was."""
    if not batches:
        return
    reader = _BatchReader(index, facts, deadline)
    with ThreadPoolExecutor(max_workers=max(1, min(vision.workers(), len(batches)))) as pool:
        list(pool.map(reader.read, batches))
    if reader.stopped is not None:
        raise reader.stopped


def _covered(index: dict[str, Any], retry_refused: bool = True) -> bool:
    """Every page of the file is in the index: read, or unreadable for good
    (:func:`_settled`). A page declined only once is still owed its one retry,
    so it keeps the build going exactly as :func:`_wanted` would, unless
    ``retry_refused`` is False (any unreadable page then counts as settled)."""
    total = index.get("page_count")
    if not isinstance(total, int) or isinstance(total, bool) or total < 1:
        return False
    settled = sum(
        1 for k in index["unreadable"] if (not retry_refused or _settled(index, k)) and k not in index["pages"]
    )
    return len(index["pages"]) + settled >= total


#: A scanned page's request cost, the estimate ``before`` is given per page.
_TOKENS_PER_PAGE = 1600


def _estimate(body: dict[str, Any]) -> int:
    """Tokens a request may cost: its text at four characters a token, each
    page document at :data:`_TOKENS_PER_PAGE`, and the output it may write."""
    est = int(body.get("max_tokens") or 0)
    for message in body.get("messages") or []:
        for block in message.get("content") or []:
            if block.get("type") == "document":
                est += _TOKENS_PER_PAGE
            else:
                est += len(str(block.get("text") or "")) // 4
    return est

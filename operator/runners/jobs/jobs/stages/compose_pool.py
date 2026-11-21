"""The writer's live-mode loop (``stages/compose``): several chunks at once.

One task per chunk, each running that chunk's pieces one at a time exactly as
the serial loop did: a refused or emptied piece's follow-ups go to the front
of the chunk's own list, so part numbers, ``chunk-XX.parts`` records and
``map-XX-k.md`` names are the serial ones by construction. Parallelism is
ACROSS chunks only, and with one worker the run is the old serial loop.

WHY (2026-11-19): one call at a time, the writer spent 362 minutes of a
3,821-page matter's 13.5 hours.

What two chunks share, so every write to it is under one lock: the queue file
(every chunk's open pieces, in the order the stage listed them), usage.jsonl,
unread_pages.json (sorted by chunk on write, ``chunk_of``), the composer's
truncated, refused and emptied lists and its infra flag, and the stage log.
The model call runs outside the lock, with the request the serial loop sent.

The first chunk runs alone first: it warms the prompt cache and builds the
SDK client once. A limit or a stop raised in one worker sets the halt: the
others finish the call in flight, record its paid reply, and take no next
piece, so the queue file still names every piece without an answer and a
rerun asks exactly those.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from .. import stop_request
from ..medchron.audit.run import run_pool

if TYPE_CHECKING:
    from .compose import Chunk, _Composer

#: Chunks composed at once.
COMPOSE_WORKERS = 8


def chunk_of(row: dict[str, Any]) -> int:
    """The chunk number of an unread row's label ("3" or "3.2")."""
    head = str(row.get("chunk")).split(".", 1)[0]
    return int(head) if head.isdigit() else 0


class _Pool:
    def __init__(self, comp: "_Composer", todo: list["Chunk"]) -> None:
        self.comp = comp
        self.lock = threading.Lock()
        self.halt = threading.Event()
        #: Each chunk's open pieces, in the stage's order (dicts keep it).
        self.open: dict[int, list[Chunk]] = {}
        for c in todo:
            self.open.setdefault(c.i, []).append(c)

    def _save(self) -> None:
        """Persist every chunk's open pieces; the caller holds the lock."""
        self.comp.save_queue([c for pieces in self.open.values() for c in pieces])

    def chunk(self, i: int) -> None:
        """Chunk `i`'s pieces, one at a time, until none is open or a halt.
        Anything raised (a limit, a stop, a defect) halts every worker."""
        try:
            self._pieces(self.open[i])
        except BaseException:
            self.halt.set()
            raise

    def _pieces(self, q: list["Chunk"]) -> None:
        while q and not self.halt.is_set():
            # The Machine's stop: no new piece starts once it is requested (the
            # doorway checks only SMD's). A call already in flight is the
            # residual exposure: paid, and unrecorded if the drain kills it.
            stop_request.check("compose")
            with self.lock:
                self._save()
            c = q[0]
            r, err = self.comp.ask(c)  # outside the lock: the paid call
            with self.lock:
                state = self.comp.handle(c, r, err)
                nxt = self.comp.after(c, state)
                q[0:1] = nxt
                self._save()
            if state in ("refused", "error") and nxt and nxt[0] is c and not self.halt.is_set():
                time.sleep(10 * max(c.attempts, 1))

    def task(self, i: int) -> Callable[[], None]:
        def run() -> None:
            if not self.halt.is_set():
                self.chunk(i)

        return run


def run(comp: "_Composer", todo: list["Chunk"], workers: int | None = None) -> None:
    """Compose every open piece in `todo`, `workers` (COMPOSE_WORKERS) chunks at once."""
    workers = COMPOSE_WORKERS if workers is None else workers
    pool = _Pool(comp, todo)
    chunks = list(pool.open)
    if chunks:
        pool.chunk(chunks[0])  # alone: warms the prompt cache, builds the client once
    rest = chunks[1:]
    if rest:
        run_pool([pool.task(i) for i in rest], max(1, min(workers, len(rest))))
    comp.save_queue([])

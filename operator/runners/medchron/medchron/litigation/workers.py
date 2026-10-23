"""Several matters read at once (``read_workers``), and one stop for all of them.

WHY. A read is a model tool loop that spends nearly all its time waiting on
the model; three passes over a list past 200 matters, one matter at a time, is
hours of waiting. ``read_workers`` matters run at once instead (default 4,
``firm.MAX_READ_WORKERS`` at most); width 1 never enters this module.

THE STOP. A limit (the job's cap, the month's budget) is not one matter's
failure: it stops the whole stage, exactly as it does one matter at a time.

* The first worker to hit it TRIPS the :class:`Halt` from inside the per-call
  hook (``RunBase._before_call``), before any money moves for that call.
* From then on every sibling's next paid call raises :class:`Halted` from the
  same hook, so no paid call STARTS after the hold. Calls already in flight
  when the line was crossed finish and are recorded (their money is spent);
  that is the overshoot, at most ``read_workers`` calls rather than one.
* No new matter starts; the stage waits for its workers and raises the FIRST
  stop once, in the stage's own thread, so the run records it as before.

A stopped sibling's matter is not marked unread (it did not fail; a resume
reads it), exactly like the one matter a serial stop interrupts.

THE SEAT. A read may fetch a document the fetch stage did not (``tools``); the
seat holds the firm's delegated token, so seat calls take turns
(:class:`SerialSeat`) rather than risk two token refreshes at once.
"""

from __future__ import annotations

import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from typing import Any, Callable, Iterator


class Halted(BaseException):
    """A sibling worker stopped the stage; this worker's read ends here.

    BaseException, like ``stop_request.StopRequested``, so no ``except
    Exception`` inside a read turns it into that matter's failure."""


class Halt:
    """The stage's one stop: the first exception that tripped it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.first: BaseException | None = None

    @property
    def tripped(self) -> bool:
        return self.first is not None

    def trip(self, exc: BaseException) -> None:
        with self._lock:
            if self.first is None and not isinstance(exc, Halted):
                self.first = exc

    def check(self) -> None:
        if self.first is not None:
            raise Halted(f"stopped by a sibling: {type(self.first).__name__}")


class SerialSeat:
    """The seat, one call at a time across workers."""

    def __init__(self, seat: Any) -> None:
        self._seat = seat
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._seat, name)
        if not callable(attr):
            return attr

        def serial(*a: Any, **k: Any) -> Any:
            with self._lock:
                return attr(*a, **k)

        return serial


@contextmanager
def _shared(r: Any, halt: Halt) -> Iterator[None]:
    """The run's seat serialized and the halt bound to its per-call hook, for
    the stage's duration. The seat is opened here, on the stage's thread, so
    two workers never race to open it (every read opens it anyway)."""
    seat = r.seat
    r._seat, r._halt = SerialSeat(seat), halt
    try:
        yield
    finally:
        r._seat, r._halt = seat, None


def in_parallel(r: Any, items: list[tuple[Any, ...]], fn: Callable[..., None], width: int) -> None:
    """Run ``fn(*item)`` for every item, at most ``width`` at once. ``fn``
    handles a matter's own failure; whatever escapes it is a STOP: it trips
    the halt, nothing new starts, and the first one is raised here, once."""
    if not items:
        return
    halt = Halt()

    def guarded(item: tuple[Any, ...]) -> None:
        if halt.tripped:
            return
        try:
            fn(*item)
        except Halted:
            return
        except BaseException as exc:  # noqa: BLE001 - a stop of any kind stops the stage; raised below, once
            halt.trip(exc)

    with _shared(r, halt), ThreadPoolExecutor(max_workers=width, thread_name_prefix="read") as pool:
        live: set[Future[None]] = set()
        try:
            for item in items:
                if halt.tripped:
                    break
                live.add(pool.submit(guarded, item))
                if len(live) >= width:
                    _done, live = wait(live, return_when=FIRST_COMPLETED)
            wait(live)
        except BaseException as exc:  # a signal on the stage's thread: siblings stop at their next call
            halt.trip(exc)
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    if halt.first is not None:
        raise halt.first

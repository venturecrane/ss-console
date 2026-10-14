"""A hard wall-clock bound on every interactive model call.

Why this exists (2026-10-14): two offline runner processes each sat about an
hour inside one connection to the model API while the client was configured
with `timeout=600.0`. That number never bounded a call. The SDK hands it to
httpx, and httpx applies it PER SOCKET OPERATION: the read timer restarts on
every byte. A server that sends headers and then trickles anything at all (the
SSE `ping` events the API sends on a stream, or keepalive whitespace on a plain
response) resets the timer forever, so the call neither returns nor fails.
Reproduced against a local server in tests/test_llm_deadline.py: headers then
silence DOES time out; headers then a ping every second never does.

So the bound is enforced here, outside the SDK. The request runs on a worker
thread; this thread waits on a clock it owns. Two limits:

- the DEADLINE, scaled to the size of the call (`deadline_for`): the most a
  call that asked for `max_tokens` output tokens can legitimately take;
- the STALL window, for streams only: no event other than `ping` for that long
  means nothing is coming.

On either, the stream is closed (best effort, so the worker unblocks at its
next byte) and `CallDeadlineError` is raised. The doorway retries it like any
transport failure and, past its attempts, raises it out of the stage, which is
the ordinary failure path: the job ends failed and pages instead of hanging.
A worker stuck in a read that never returns is a daemon thread and is
abandoned; it cannot hold the run open.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

#: The floor covers the fixed cost of any call: connection, queueing, prompt
#: processing of a large input, adaptive thinking before the first token.
DEADLINE_FLOOR_S = 300.0
#: The slowest output rate a healthy call is credited with. Real rates are well
#: above this; the slack is what keeps a slow but working call from being cut.
DEADLINE_TOKENS_PER_S = 20.0
#: No call gets longer than this, whatever it asked for. A 64k-token call hits
#: it; three attempts then bound the stage at a little over two hours.
DEADLINE_CEILING_S = 2700.0
#: A stream that has sent nothing but pings for this long is dead. This is the
#: meaning the old `timeout=600.0` was meant to carry and never did.
STALL_S = 600.0
#: How often the waiting thread looks at the clock.
_TICK_S = 0.25


class CallDeadlineError(RuntimeError):
    """A model call ran past its wall-clock deadline or stalled. Retryable."""


def deadline_for(max_tokens: int) -> float:
    """Seconds a call asking for `max_tokens` output tokens may take in total."""
    scaled = DEADLINE_FLOOR_S + max(0, int(max_tokens)) / DEADLINE_TOKENS_PER_S
    return min(DEADLINE_CEILING_S, scaled)


class _Run:
    """One attempt's shared state between the worker and the waiter."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.abort = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None
        self.progress_at = time.monotonic()
        self.handle: Any = None  # the open stream, so the waiter can close it


def _consume(st: Any, run: _Run) -> Any:
    """Read a stream to its end, stamping progress on every non-ping event.
    A fake stream that is not iterable goes straight to its final message."""
    run.handle = st
    if hasattr(st, "__iter__"):
        for event in st:
            if run.abort.is_set():
                raise CallDeadlineError("aborted by the deadline")
            if getattr(event, "type", None) != "ping":
                run.progress_at = time.monotonic()
    return st.get_final_message()


def _work(client: Any, params: dict[str, Any], stream: bool, run: _Run) -> None:
    try:
        if stream:
            with client.messages.stream(**params) as st:
                run.result = _consume(st, run)
        else:
            run.result = client.messages.create(**params)
    except BaseException as exc:  # noqa: BLE001 - handed to the waiting thread, which re-raises it
        run.error = exc
    finally:
        run.done.set()


def _breach(run: _Run, started: float, deadline_s: float, stall_s: float | None) -> str | None:
    now = time.monotonic()
    if now - started >= deadline_s:
        return f"no reply within its {deadline_s:.0f}s deadline"
    if stall_s is not None and now - run.progress_at >= stall_s:
        return f"no content for {stall_s:.0f}s (stream stalled)"
    return None


def _abort(run: _Run) -> None:
    run.abort.set()
    close: Callable[[], Any] | None = getattr(run.handle, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001 - best effort; the worker is abandoned either way
            pass


def bounded_call(
    client: Any,
    params: dict[str, Any],
    *,
    stream: bool,
    deadline_s: float,
    stall_s: float | None = STALL_S,
) -> Any:
    """Run one request with a hard wall-clock deadline (and, for a stream, a
    no-progress window). Returns the final message or raises what the request
    raised, or CallDeadlineError."""
    run = _Run()
    worker = threading.Thread(target=_work, args=(client, params, stream, run), daemon=True)
    started = time.monotonic()
    worker.start()
    while not run.done.wait(_TICK_S):
        why = _breach(run, started, deadline_s, stall_s if stream else None)
        if why is not None:
            _abort(run)
            raise CallDeadlineError(f"model call aborted: {why}")
    if run.error is not None:
        raise run.error
    return run.result

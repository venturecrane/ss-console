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
next byte) and `CallDeadlineError` is raised. The doorway retries it up to its
attempt count and, past that, raises it out of the stage, which is the
ordinary failure path: the job ends failed and pages instead of hanging.

The deadline and the doorway's network-retry budget are two separate clocks
(2026-10-14). A hung call can take the whole deadline, so a blown deadline is
counted against the ATTEMPTS. A connection error, a 429 or a 5xx fails in
seconds and says nothing about the call, so it is retried against a wall-clock
budget instead (`Doorway.retry_budget_s`), and never spends an attempt meant
for a hung stream.
A worker stuck in a read that never returns is a daemon thread and is
abandoned; it cannot hold the run open.

The retry classification (`classify_error`) lives here too, beside the one
error it has to know is retryable; the doorway's attempt loop uses it.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
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
#: Wall-clock seconds one call keeps retrying through connection errors, 429s
#: and 5xx before it gives up. On 2026-10-14 one APIConnectionError, three
#: attempts and sixty seconds after the first, ended an update after $29.69 of
#: work with its audit already passed; the API answered again minutes later.
NETWORK_RETRY_S = 600.0
#: The longest single wait between two network retries.
NETWORK_BACKOFF_CAP_S = 120.0
#: A runaway guard on the count, for a caller that passes no backoff (a test):
#: with zero waits the budget is never spent and the loop would spin.
NETWORK_MAX_TRIES = 200


class CallDeadlineError(RuntimeError):
    """A model call ran past its wall-clock deadline or stalled. Retryable."""


def deadline_for(max_tokens: int) -> float:
    """Seconds a call asking for `max_tokens` output tokens may take in total."""
    scaled = DEADLINE_FLOOR_S + max(0, int(max_tokens)) / DEADLINE_TOKENS_PER_S
    return min(DEADLINE_CEILING_S, scaled)


def classify_error(exc: BaseException) -> str:
    """'retry' for transport/429/5xx and a blown deadline, 'raise' for 4xx
    invalid requests."""
    if isinstance(exc, CallDeadlineError):
        return "retry"
    try:
        import anthropic
    except Exception:  # noqa: BLE001 - the anthropic SDK may be absent in the audit venv; classify by message text then
        return "raise" if "invalid_request" in str(exc) else "retry"
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.RateLimitError)):
        return "retry"
    if isinstance(exc, anthropic.APIStatusError):
        code = getattr(exc, "status_code", 0) or 0
        return "retry" if (code == 429 or code >= 500 or code in (408, 409)) else "raise"
    return "retry"


#: The SDK's transient classes by name, for the audit venv where the SDK is
#: absent and the classes cannot be imported to test against.
_TRANSIENT_NAMES = frozenset(
    {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "OverloadedError"}
)


def is_transient(exc: BaseException) -> bool:
    """True for the failures that say nothing about the request and clear on
    their own: a dropped connection, a timeout, 408/409/429, any 5xx
    (overloaded is 529), and a blown deadline. Narrower than `classify_error`,
    which also retries what it does not recognise: an unknown error (a bug in
    a stage, a malformed fake) is never given a ten-minute retry budget, and a
    400, an auth failure or a refusal is never retried at all."""
    if isinstance(exc, CallDeadlineError):
        return True
    try:
        import anthropic
    except Exception:  # noqa: BLE001 - the anthropic SDK may be absent in the audit venv; classify by class name then
        return type(exc).__name__ in _TRANSIENT_NAMES
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.RateLimitError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        code = getattr(exc, "status_code", 0) or 0
        return code >= 500 or code in (408, 409, 429)
    return False


@dataclass
class RetryClock:
    """One call's retry state across its attempts. Two budgets that never
    spend each other: `attempts` for a blown deadline or an unrecognised
    error (each may have taken the whole deadline), and `budget_s` of wall
    clock for a transient network failure, which fails fast. A 400, an auth
    failure or a refusal never reaches here: the doorway raises those."""

    attempts: int
    backoff: float
    budget_s: float = NETWORK_RETRY_S
    cap_s: float = NETWORK_BACKOFF_CAP_S
    rand: Callable[[], float] = random.random
    spent: float = 0.0
    network: int = 0
    other: int = 0

    def wait_after(self, exc: BaseException, took: float) -> float | None:
        """Seconds to wait before the next attempt, or None to give up. `took`
        is how long the failed attempt ran."""
        if is_transient(exc) and not isinstance(exc, CallDeadlineError):
            self.network += 1
            self.spent += max(0.0, took)
            left = self.budget_s - self.spent
            if left <= 0 or self.network >= NETWORK_MAX_TRIES:
                return None
            # Exponential with jitter: half the step fixed, half random, so a
            # fleet of calls that failed together does not retry together.
            step = min(self.cap_s, self.backoff * 2 ** (self.network - 1))
            wait = min(left, step * (0.5 + 0.5 * self.rand()))
            self.spent += wait
            return wait
        self.other += 1
        if self.other >= self.attempts:
            return None
        return self.backoff * self.other

    def summary(self) -> str:
        return f"{self.network} network error(s) over {self.spent:.0f}s, {self.other} other failure(s)"


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


def retrying_call(
    attempt: Callable[[], Any],
    clock: RetryClock,
    *,
    stage: str,
    log: Callable[[str], None],
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    gave_up: list[str] | None = None,
) -> Any:
    """Run `attempt` until it answers, under `clock`'s two budgets. A 400, an
    auth failure or a refusal is raised at once. Past the budgets the last
    error is raised; a network one is first noted on `gave_up`, so a stage
    that catches it and exits non-zero still leaves the driver able to tell a
    network blip (requeued once) from a defect (terminal)."""
    while True:
        t0 = now()
        try:
            msg = attempt()
        except Exception as exc:
            if classify_error(exc) == "raise":
                raise
            wait = clock.wait_after(exc, now() - t0)
            log(f"{stage}: attempt failed ({type(exc).__name__}: {str(exc)[:200]}); {clock.summary()}")
            if wait is None:
                if is_transient(exc) and gave_up is not None:
                    gave_up.append(f"{stage}: {type(exc).__name__} after {clock.summary()}")
                if isinstance(exc, CallDeadlineError):
                    raise CallDeadlineError(f"{stage}: {exc}, on all {clock.other} attempts") from exc
                raise
            sleep(wait)
            continue
        if clock.network:
            log(f"{stage}: answered after {clock.summary()}")
        return msg

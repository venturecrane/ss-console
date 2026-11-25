"""One ``render_docx_draft`` call answers inside the MCP tool timeout, always.

WHY (2026-11-24, A&P). An attorney asked for a Word document. Two render calls
each ran into the gateway's 300 s tool timeout, about five minutes apart, and
the Operator then filed a .txt and told the attorney to paste it into his own
template. A call cut off by the timeout returns nothing the Operator can act
on, so it improvised. Nothing in the render path was bounded as a whole: it
downloads and extracts EVERY document on the matter (``draft_sources``), runs
the record checker (180 s on its own), fetches the firm's Word template, files
the bytes, and polls the read-back. Each step was bounded alone or not at all,
and their sum was not bounded under the timeout.

THE RULE. Each stage has a deadline, in seconds from the start of the call,
all of them well inside the tool timeout. A stage still running at its deadline
is abandoned and the call returns a REFUSAL VALUE that says what happened, so
the Operator always gets an answer it can act on:

* before anything is sent (reading the matter, the record check, the template),
  a refusal with ``fileId: null``: nothing was filed, calling again is safe;
* once the filing has started, the read-back's ``link_not_visible`` shortfall:
  the file may have landed, SMD is paged, and the agent is told not to file it
  again, because a second upload would be a duplicate on the matter.

Inside a stage the same deadline is visible to the code that blocks
(``client.request`` will not sleep out a rate limit past it, each HTTP request
is given only the time left, and the read-back poll stops waiting), so an
abandoned stage also stops soon instead of running on in the background.

WHERE THE 300 s IS SET. Not in this repo: it is Hermes's default MCP tool-call
timeout. The overlay writes each connector's ``mcp_servers`` entry
(``bootstrap/translate.py`` ``_materialize_mcp_servers``) with no ``timeout``
key, so the default applies. ``page_finder.CALL_DEADLINE_SECONDS`` is budgeted
against the same number.
"""

from __future__ import annotations

import contextvars
import hashlib
import re
import threading
import time
from typing import Any, Callable, TypeVar

#: Hermes's MCP tool-call timeout, the ceiling every deadline below sits under.
TOOL_TIMEOUT_SECONDS = 300.0
#: Stage deadlines, seconds from the start of the call. The filing stage ends
#: 30 s before the tool timeout so its answer is always the one the agent reads,
#: and starts at least 90 s before its own end: the upload plus the read-back's
#: full set of pauses (``readback.WAITS``, 31 s) plus its reads, so a healthy
#: filing is never cut short into ``link_not_visible`` (review of #4223).
SOURCES_BY = 100.0
CHECK_BY = 150.0
RENDER_BY = 180.0
FILED_BY = 270.0
#: The smallest filing window the deadlines above leave; pinned by a test
#: against ``readback.WAITS``.
FILING_WINDOW_MIN = FILED_BY - RENDER_BY

#: The stages, as the refusal names them.
LISTING = "counting the listed folder"
READING = "reading the matter's documents"
CHECKING = "checking the draft against the record"
RENDERING = "rendering the Word document"
FILING = "filing the Word document"

T = TypeVar("T")

_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("smokeball_draft_deadline", default=None)


class OutOfTime(Exception):
    """A stage did not finish by its deadline. ``stage`` names it plainly."""

    def __init__(self, stage: str, seconds: float) -> None:
        super().__init__(f"{stage} did not finish within {seconds:.0f} s")
        self.stage = stage
        self.seconds = seconds


def remaining() -> float | None:
    """Seconds left before the current stage's deadline, or None outside one."""
    deadline = _DEADLINE.get()
    return None if deadline is None else deadline - time.monotonic()


def would_pass(seconds: float) -> bool:
    """True when waiting ``seconds`` more would cross the current deadline."""
    left = remaining()
    return left is not None and seconds >= left


def check(stage: str) -> None:
    """Raise :class:`OutOfTime` when the current stage's deadline has passed."""
    left = remaining()
    if left is not None and left <= 0:
        raise OutOfTime(stage, 0.0)


def request_timeout(default: float) -> float:
    """An HTTP timeout no longer than the time left (at least one second)."""
    left = remaining()
    return default if left is None else max(1.0, min(default, left))


class Clock:
    """The start of one render call, and the stage runner keyed to it."""

    def __init__(self) -> None:
        self.start = time.monotonic()

    def left(self, by: float) -> float:
        return self.start + by - time.monotonic()

    def run(self, stage: str, by: float, fn: Callable[..., T], *args: Any) -> T:
        """``fn(*args)`` on a daemon thread that sees the stage deadline; its
        value, or :class:`OutOfTime` when it is still running at ``by``.
        An exception ``fn`` raises is raised here."""
        seconds = self.left(by)
        if seconds <= 0:
            raise OutOfTime(stage, by)
        box: dict[str, Any] = {}
        ctx = contextvars.copy_context()
        ctx.run(_DEADLINE.set, time.monotonic() + seconds)

        def work() -> None:
            try:
                box["value"] = ctx.run(fn, *args)
            except BaseException as exc:  # noqa: BLE001 - handed back to the caller's thread below
                box["error"] = exc

        worker = threading.Thread(target=work, name=f"render-{stage}", daemon=True)
        worker.start()
        worker.join(seconds)
        if worker.is_alive():
            raise OutOfTime(stage, by)
        error = box.get("error")
        if isinstance(error, OutOfTime):
            raise OutOfTime(stage, by) from error
        if error is not None:
            raise error
        return box["value"]


#: The one sentence the Operator sends the person when a render still does not
#: finish on its second try (the overlay's ``reply_could_not.LOOK_INTO``).
LOOK_INTO = "Let me look into that. I'll reply in this thread."


def not_filed(exc: OutOfTime) -> str:
    """The refusal for a stage that stopped before anything was sent."""
    return (
        f"Not filed: {exc.stage} did not finish within {exc.seconds:.0f} s of this call, so it was stopped "
        "before the 300 s tool limit. Nothing was sent to the matter, and calling render_docx_draft once "
        f'more with the same draft is safe. If that call stops too, reply only "{LOOK_INTO}" Never file a '
        ".txt in place of the Word document and never ask the attorney to paste the text into a template."
    )


def checked(verdict: Any) -> Any:
    """The record check's verdict, or :class:`OutOfTime` when the budget cut
    the checker short (disposition ``timeout``): that is a stopped stage the
    retry-once rule covers, not a finding about the draft."""
    if getattr(verdict, "disposition", None) == "timeout":
        raise OutOfTime(CHECKING, CHECK_BY)
    return verdict


_URL = re.compile(r"https?://\S+")


def filing_unfinished(result: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """The filing started and did not finish: it ran out of time, or it raised
    (a presigned PUT that timed out after the metadata POST had created the
    file record). Either way the file may be on the matter, so this is the
    read-back's ``link_not_visible`` shortfall (SMD is paged, the agent is
    told not to file it again), never a refusal or an error that invites a
    second, duplicate upload. URLs are dropped from the detail: a presigned
    one carries its signature."""
    from .readback import NOT_VISIBLE, shortfall

    if isinstance(exc, OutOfTime):
        detail, extra = f"{exc.stage} did not finish within {exc.seconds:.0f} s of the call", {"outOfTime": exc.stage}
    else:
        detail = f"{FILING} stopped with {type(exc).__name__}: {_URL.sub('<url>', str(exc))[:200]}"
        extra = {"filingError": type(exc).__name__}
    return {**shortfall(result, NOT_VISIBLE, detail), "refusals": [], **extra}


def file_by(clock: Clock, file: Callable[[], Any], base: dict[str, Any], data: bytes) -> dict[str, Any]:
    """Run the filing (``add_file``) as the last stage and shape its answer.
    ANYTHING raised once the filing starts is treated as "may have landed"
    (:func:`filing_unfinished`), because the metadata POST creates the file
    record before the bytes are PUT."""
    sizes = {"sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data)}
    try:
        result = clock.run(FILING, FILED_BY, file)
    except Exception as exc:  # noqa: BLE001 - after the filing starts, every failure may have landed
        return filing_unfinished({**base, **sizes}, exc)
    out = dict(result) if isinstance(result, dict) else {"result": result}
    return {**out, **sizes, "refusals": [], **{k: v for k, v in base.items() if k not in out}}


__all__ = [
    "CHECKING",
    "FILING",
    "FILING_WINDOW_MIN",
    "LISTING",
    "LOOK_INTO",
    "READING",
    "RENDERING",
    "checked",
    "file_by",
    "filing_unfinished",
    "not_filed",
    "CHECK_BY",
    "FILED_BY",
    "RENDER_BY",
    "SOURCES_BY",
    "TOOL_TIMEOUT_SECONDS",
    "Clock",
    "OutOfTime",
    "check",
    "remaining",
    "request_timeout",
    "would_pass",
]

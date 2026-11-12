"""One home for "is this HTTP failure worth retrying, and retry it".

Factored out of ``seam_pull`` on 2026-11-12, when the daily send reconcile
held on a single Microsoft Graph 503/502 (run 43278803074): the seam reader
already retried transient failures, but the reconcile's own Graph and AgentMail
reads did not, so one bad answer from a vendor turned into a HOLD and a page.
Every read that should survive a transient failure uses this module, so the
retryable set and the delays live in one place.

Only idempotent requests belong here. A retry repeats the request exactly, and
the caller's error contract is kept: the LAST exception re-raises once
attempts are exhausted, and a non-transient one re-raises at once.
"""

from __future__ import annotations

import http.client
import json
import logging
import ssl
import time
import urllib.error
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")

RETRY_DELAYS_SECONDS: tuple[float, ...] = (2.0, 6.0)
RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})

log = logging.getLogger("aie.bin.transient_http")


def is_transient(exc: BaseException) -> bool:
    """True only for failures a retry of the same idempotent request can fix."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP_STATUS
    if isinstance(exc, urllib.error.URLError):
        # DNS, refused, reset, TLS EOF/handshake: all arrive wrapped here.
        return True
    return isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError, http.client.HTTPException))


def with_retry(
    attempt_once: Callable[[], T],
    *,
    label: str,
    delays: Sequence[float] = RETRY_DELAYS_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call ``attempt_once`` until it succeeds or fails for good.

    ``label`` names the read in the log line and must carry no secret.
    ``sleep`` defaults to ``time.sleep`` looked up at call time, so a test can
    monkeypatch this module's ``time.sleep`` without threading a parameter
    through every caller.
    """
    pause = sleep or time.sleep
    attempts = len(delays) + 1
    for attempt in range(attempts):
        try:
            return attempt_once()
        except Exception as exc:  # classified below; a non-transient error re-raises at once
            if attempt == attempts - 1 or not is_transient(exc):
                raise
            delay = delays[attempt]
            log.warning(
                "%s: transient %s on attempt %d/%d; retrying in %.0fs",
                label,
                type(exc).__name__,
                attempt + 1,
                attempts,
                delay,
            )
            pause(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def get_json(open_fn: Callable, request, *, timeout: float, label: str) -> object:
    """Open ``request`` with ``open_fn`` and decode the JSON body, retrying a
    transient failure. ``open_fn`` is ``urllib.request.urlopen`` or a test's
    fake; the caller keeps its own except clauses and error wording."""

    def once() -> object:
        with open_fn(request, timeout=timeout) as response:
            return json.loads(response.read())

    return with_retry(once, label=label)

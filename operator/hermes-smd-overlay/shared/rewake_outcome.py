"""What happened to an SMD re-wake after the adapter accepted it (ADR 0102).

``python3 -m shared.msgraph_replay --message-id <id>`` re-wakes the Operator on
an email it already answered. The Hermes webhook adapter answering HTTP 202 only
means the POST was queued: the gateway's ``pre_gateway_dispatch`` hooks run
after that, and the webhook router can still drop the delivery (its replay
guard did exactly that on A&P, 2026-11-03, with nothing telling the operator).
The gateway logs to a stdout the replay tool cannot read, so the router writes
the outcome here, on the seat's volume, and the replay tool waits for it.

Two pieces, both keyed on the re-wake id (the fresh 64-hex request id the replay
tool minted and signed into the payload as :data:`MARKER_KEY`):

* the ``marker`` helpers: the signed field that tells the router a delivery is
  an SMD re-wake, so its replay guard keys on ``<message id>:rewake:<id>``
  instead of the bare message id. A true duplicate of the same email (no
  marker) still collapses; a re-wake does not collapse into the original.
* the outcome journal: one small JSON file per re-wake under
  :func:`outcome_dir`, holding ``{"state": ..., "reason": ..., "session_id":
  ...}``. ``refused`` is written when the router drops the delivery,
  ``dispatched`` when it passed every router check and went to the gateway, and
  ``turn_started`` when the model turn it produced made its first LLM call.

The marker grants nothing. It rides inside the body the route secret signs, so
the adapter (HMAC V2) and the router's own header path reject it forged exactly
as they reject any forged body, and every floor still applies to the turn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

#: The payload field carrying the re-wake id. Top-level, inside the signed body.
MARKER_KEY = "smd_rewake"

#: The re-wake id is the replay tool's fresh request id: a sha256 hex digest.
_ID_RE = re.compile(r"^[0-9a-f]{64}$")

#: A fixed volume path, not HERMES_HOME: the gateway runs under its persona's
#: profile home while the replay tool runs from a plain seat shell, and both
#: must name the same directory.
DEFAULT_OUTCOME_DIR = "/opt/data/smd/rewake-outcomes"

STATE_REFUSED = "refused"
STATE_DISPATCHED = "dispatched"
STATE_TURN_STARTED = "turn_started"

#: A dispatched re-wake unclaimed by a turn this long is forgotten (the replay
#: tool waits far less than this, so it has already reported "no turn").
_UNCLAIMED_TTL_SECONDS = 900

#: Hermes' ``platform`` kwarg on a webhook-delivered turn.
WEBHOOK_PLATFORM = "webhook"

_lock = threading.Lock()
_unclaimed: deque[tuple[float, str]] = deque()


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def marker(payload: Any) -> tuple[bool, str | None]:
    """``(present, rewake_id)`` for a webhook payload.

    ``(False, None)``: not a re-wake. ``(True, id)``: a well-formed re-wake.
    ``(True, None)``: the marker is present but malformed, which the router
    refuses rather than guessing at."""
    if not isinstance(payload, dict) or MARKER_KEY not in payload:
        return False, None
    value = payload.get(MARKER_KEY)
    return True, (value if valid_id(value) else None)


def replay_key(event_id: str, rewake_id: str) -> str:
    """The router's replay-guard key for a re-wake of ``event_id``."""
    return f"{event_id}:rewake:{rewake_id}"


def outcome_dir() -> str:
    return os.environ.get("SMD_REWAKE_OUTCOME_DIR") or DEFAULT_OUTCOME_DIR


def _path(rewake_id: str, directory: str | None) -> str:
    return os.path.join(directory or outcome_dir(), f"{rewake_id}.json")


def record(
    rewake_id: str,
    state: str,
    *,
    reason: str = "",
    session_id: str = "",
    directory: str | None = None,
) -> bool:
    """Write the outcome for one re-wake, atomically. Never raises."""
    if not valid_id(rewake_id):
        return False
    try:
        target_dir = directory or outcome_dir()
        os.makedirs(target_dir, mode=0o700, exist_ok=True)
        body = json.dumps(
            {"state": state, "reason": reason, "session_id": session_id, "at": time.time()}
        )
        fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".rewake-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, _path(rewake_id, target_dir))
        return True
    except Exception:  # noqa: BLE001 - an outcome is never worth a dispatch failure
        logger.warning("rewake_outcome: could not record %s for %s", state, rewake_id[:12])
        return False


def read(rewake_id: str, *, directory: str | None = None) -> dict | None:
    """The recorded outcome, or None when nothing is recorded (yet)."""
    if not valid_id(rewake_id):
        return None
    try:
        with open(_path(rewake_id, directory), encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def dispatched(rewake_id: str, *, directory: str | None = None, now: float | None = None) -> None:
    """Router: the re-wake passed every check and went to the gateway. The next
    model turn's first LLM call claims it (:func:`claim_turn`). Never raises."""
    if not record(rewake_id, STATE_DISPATCHED, directory=directory):
        return
    with _lock:
        _unclaimed.append((time.monotonic() if now is None else now, rewake_id))


def claim_turn(
    session_id: str,
    *,
    platform: object = WEBHOOK_PLATFORM,
    is_first_turn: object = True,
    directory: str | None = None,
    now: float | None = None,
) -> None:
    """pre_llm_call: mark the oldest dispatched, unclaimed re-wake as having
    started a turn. Exactly one claim per re-wake; a stale entry expires. Only a
    webhook turn's first call claims: a cron or chat turn is never a re-wake.

    Attribution is by order, as the inbound quarantine's unbound bucket is: a
    webhook dispatch carries no session id on the live path. A turn that began
    after the dispatch is the turn it produced unless two webhook deliveries
    raced, and then both started turns. Never raises."""
    try:
        if platform != WEBHOOK_PLATFORM or is_first_turn is False:
            return
        ts = time.monotonic() if now is None else now
        with _lock:
            while _unclaimed and ts - _unclaimed[0][0] > _UNCLAIMED_TTL_SECONDS:
                _unclaimed.popleft()
            if not _unclaimed:
                return
            _, rewake_id = _unclaimed.popleft()
        record(rewake_id, STATE_TURN_STARTED, session_id=session_id or "", directory=directory)
    except Exception:  # noqa: BLE001 - hook callbacks are exception-safe
        logger.debug("rewake_outcome: claim_turn failed", exc_info=True)


def reset() -> None:
    """Test seam."""
    with _lock:
        _unclaimed.clear()

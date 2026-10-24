"""The seat-spec verifier's verdict, for the heartbeat (`spec`).

What it reads. The root ``config_applier`` loop's spec verifier
(``config_applier/spec_verifier.py``, ss-console Wave 2 W4, shadow mode) writes
``/run/smd-spec/state.json`` 0644 by rename, at least every five minutes, so
this gate-uid process can read it.

What it ships, ``spec``::

    {"v": 1, "verdict": str, "checked_at": ISO, "spec_sha256": hex64 | null,
     "key_id": hex16 | null, "compiled_from": hex40 | null,
     "unmet_count": int, "unmet": [id, ...], "customer_yaml_match": bool | null}

Silence is a verdict, decided here like the self-test's: a state file older
than ``STALE_SECONDS``, or none on a Machine up longer than that, ships
verdict ``stale`` (the verifier is not running); a file that cannot be read or
parsed ships ``unreadable``. No file inside the first ``STALE_SECONDS`` of
uptime is ``None``: nothing to say yet, the console holds.

Read with the same discipline as ``selftest_check``: never through a link,
never a FIFO, at most ``READ_CAP_BYTES``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from shared.gateway_loop_check import _read_regular_tail
from shared.selftest_check import _system_uptime

logger = logging.getLogger(__name__)

STATE_PATH = Path("/run/smd-spec/state.json")
#: Six re-verify intervals: a verifier that missed that many is not running.
STALE_SECONDS = 30 * 60
READ_CAP_BYTES = 64 * 1024
MAX_UNMET = 32
VERDICTS = frozenset(
    {
        "verified",
        "absent",
        "fetch_error",
        "malformed",
        "unknown_key",
        "bad_digest",
        "bad_signature",
        "wrong_seat",
        "spec_missing",
        "unmet_requirement",
    }
)
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"\A[0-9a-f]{16}\Z")
_COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")
_UNMET_ID = re.compile(r"\A[A-Za-z0-9:@/._-]{1,200}\Z")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _synthetic(verdict: str, now: float) -> dict:
    return {
        "v": 1,
        "verdict": verdict,
        "checked_at": _iso(now),
        "spec_sha256": None,
        "key_id": None,
        "compiled_from": None,
        "unmet_count": 0,
        "unmet": [],
        "customer_yaml_match": None,
    }


def _match(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.match(value) else None


def summarize(doc: dict, now: float) -> dict:
    """The heartbeat object for one parsed state document."""
    checked = _epoch(doc.get("checked_at"))
    if checked is None or now - checked > STALE_SECONDS:
        return _synthetic("stale", now)
    verdict = doc.get("verdict")
    if verdict not in VERDICTS:
        return _synthetic("unreadable", now)
    raw_unmet = doc.get("unmet") if isinstance(doc.get("unmet"), list) else []
    unmet = [
        u if isinstance(u, str) and _UNMET_ID.match(u) else "malformed-requirement"
        for u in raw_unmet
    ]
    count = doc.get("unmet_count")
    match = doc.get("customer_yaml_match")
    return {
        "v": 1,
        "verdict": verdict,
        "checked_at": _iso(checked),
        "spec_sha256": _match(doc.get("spec_sha256"), _SHA256),
        "key_id": _match(doc.get("key_id"), _KEY_ID),
        "compiled_from": _match(doc.get("compiled_from"), _COMMIT),
        "unmet_count": count if isinstance(count, int) and count >= len(unmet) else len(unmet),
        "unmet": unmet[:MAX_UNMET],
        "customer_yaml_match": match if isinstance(match, bool) else None,
    }


def read_spec(
    path: Path = STATE_PATH,
    now_fn: Callable[[], float] = time.time,
    uptime_fn: Callable[[], float | None] = _system_uptime,
) -> dict | None:
    """The ``spec`` heartbeat object, or None when there is nothing to say yet."""
    now = now_fn()
    try:
        text = _read_regular_tail(path, READ_CAP_BYTES)
    except FileNotFoundError:
        uptime = uptime_fn()
        return _synthetic("stale", now) if uptime is not None and uptime > STALE_SECONDS else None
    except OSError as exc:
        logger.warning("spec_check: cannot read %s (%s)", path, exc)
        return _synthetic("unreadable", now)
    try:
        doc = json.loads(text)
    except ValueError:
        return _synthetic("unreadable", now)
    if not isinstance(doc, dict):
        return _synthetic("unreadable", now)
    return summarize(doc, now)


def unreadable_result(now: float) -> dict:
    """The synthetic verdict for a state file this process could not read."""
    return _synthetic("unreadable", now)


__all__ = ["STALE_SECONDS", "STATE_PATH", "read_spec", "summarize", "unreadable_result"]

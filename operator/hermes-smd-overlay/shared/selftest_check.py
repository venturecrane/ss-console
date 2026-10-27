"""The in-image self-test's verdict, for the heartbeat (`selftest`).

What it reads. ``/app/selftest.sh`` (forked from the Machine entrypoint as root)
runs the seat check list at boot and hourly and writes
``/run/smd-selftest/results.json`` 0644 by rename, so this gate-uid process can
read it. The file carries every check; the heartbeat carries the verdict: which
checks failed and which warned, by id, plus the identity the run saw (code
version, image, Hermes sha), so ``fleet release`` can tell a run on the release
it is verifying from a late run on the image before it.

What it ships, ``selftest``::

    {"v": 1, "run_kind": "boot" | "hourly" | "stale", "finished_at": ISO,
     "version": str | null, "image_ref": str | null, "hermes_sha": str | null,
     "total": int, "failed_count": int, "warned_count": int,
     "failed_streak": int, "failed": [id, ...], "warned": [id, ...]}

Ids are ``[a-z0-9-]{1,80}`` and each list is capped at ``MAX_IDS`` (the counts
stay exact); the receiver NULLs anything else rather than trusting it.

Staleness is a FAILURE, decided here, not by a clock somewhere else. A loop that
died stops rewriting the file, and a stale file reporting green would be the
exact silence this exists to end. So a results file older than
``STALE_SECONDS``, or no file at all on a Machine up longer than that, ships as
one synthetic failed id, ``selftest-loop-dead``, with run_kind ``stale``. A file
that exists and cannot be read or parsed ships ``selftest-results-unreadable``
the same way: our blindness pages rather than reading as health. No file inside
the first ``STALE_SECONDS`` of uptime (the boot run waits for the gateway
supervisor to arm) is ``None``: nothing to say yet, the console holds.

A run in which no seat check ran is a FAILURE too, also with run_kind
``stale``: a file whose checks are only the runner's own ``selftest-*`` records
(the loop crashed, or the check list would not load) or that has no checks at
all. Its ids ship as failed, ``selftest-no-checks`` when there are none, even
when an older image wrote the crash record as a warning. Before 2026-10-27 a
crash record (one WARN, failed=0, rewritten fresh on every restart) shipped as
a clean run, so the loop-dead bound above never fired and a release passed.

Read with the same discipline as ``gateway_loop_check``: never through a link,
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

logger = logging.getLogger(__name__)

RESULTS_PATH = Path("/run/smd-selftest/results.json")
STALE_SECONDS = 3 * 3600
READ_CAP_BYTES = 256 * 1024
MAX_IDS = 32
ID_RE = re.compile(r"^[a-z0-9-]{1,80}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LOOP_DEAD = "selftest-loop-dead"
UNREADABLE = "selftest-results-unreadable"
NO_CHECKS = "selftest-no-checks"
# The runner's own records carry this prefix; no seat check may (seat-checks.sh).
SYNTHETIC_PREFIX = "selftest-"
RUN_KINDS = frozenset({"boot", "hourly"})


def _system_uptime() -> float | None:
    try:
        with open("/proc/uptime", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _synthetic(check_id: str, now: float) -> dict:
    return {
        "v": 1,
        "run_kind": "stale",
        "finished_at": _iso(now),
        "version": None,
        "image_ref": None,
        "hermes_sha": None,
        "total": 0,
        "failed_count": 1,
        "warned_count": 0,
        "failed_streak": 1,
        "failed": [check_id],
        "warned": [],
    }


def _is_failed(c: dict) -> bool:
    return not c.get("ok") and not c.get("warn")


def _is_warned(c: dict) -> bool:
    return bool(c.get("warn"))


def unreadable_result(now: float) -> dict:
    """The synthetic failure for a results file this process could not read."""
    return _synthetic(UNREADABLE, now)


def _ids(checks: list, pick: Callable[[dict], bool]) -> list[str]:
    """Ids of the checks ``pick`` selects; an id of another shape is never shipped."""
    out = []
    for c in checks:
        if isinstance(c, dict) and pick(c):
            cid = c.get("id")
            out.append(
                cid if isinstance(cid, str) and ID_RE.match(cid) else "selftest-malformed-id"
            )
    return out


def _string(value: object, limit: int = 300) -> str | None:
    return (
        value
        if isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()
        else None
    )


def _is_seat_check(c: object) -> bool:
    """A check from the seat list, not one of the runner's own ``selftest-*`` records."""
    return not (
        isinstance(c, dict)
        and isinstance(c.get("id"), str)
        and c["id"].startswith(SYNTHETIC_PREFIX)
    )


def _hollow(checks: list, doc: dict, finished: float) -> dict:
    """A run in which no seat check ran: a failure that names why, never a pass.

    The runner's own records (``selftest-crashlooping`` when the loop died,
    ``selftest-checks-unloadable`` when the list would not load) are shipped as
    failed whether the file marked them failed or, as images before 2026-10-27
    did for a crash, only warned. A run with no check at all ships
    ``selftest-no-checks``. Identity is kept, so a release judge reading it on
    the release image fails the seat rather than waiting forever.
    """
    ids_ = _ids(checks, lambda c: True) or [NO_CHECKS]
    hermes = doc.get("hermes_sha")
    return {
        "v": 1,
        "run_kind": "stale",
        "finished_at": _iso(finished),
        "version": _string(doc.get("version")),
        "image_ref": _string(doc.get("image_ref")),
        "hermes_sha": hermes if isinstance(hermes, str) and SHA_RE.match(hermes) else None,
        "total": len(checks),
        "failed_count": len(ids_),
        "warned_count": 0,
        "failed_streak": max(1, int(doc.get("failed_streak") or 0)),
        "failed": ids_[:MAX_IDS],
        "warned": [],
    }


def summarize(doc: dict, now: float) -> dict:
    """The heartbeat object for one parsed results document."""
    finished = _epoch(doc.get("finished_at"))
    if finished is None or now - finished > STALE_SECONDS:
        return _synthetic(LOOP_DEAD, now)
    checks = doc.get("checks") if isinstance(doc.get("checks"), list) else []
    if not any(_is_seat_check(c) for c in checks):
        return _hollow(checks, doc, finished)
    failed, warned = _ids(checks, _is_failed), _ids(checks, _is_warned)
    hermes = doc.get("hermes_sha")
    return {
        "v": 1,
        "run_kind": doc.get("run_kind") if doc.get("run_kind") in RUN_KINDS else "hourly",
        "finished_at": _iso(finished),
        "version": _string(doc.get("version")),
        "image_ref": _string(doc.get("image_ref")),
        "hermes_sha": hermes if isinstance(hermes, str) and SHA_RE.match(hermes) else None,
        "total": len(checks),
        "failed_count": len(failed),
        "warned_count": len(warned),
        "failed_streak": max(0, int(doc.get("failed_streak") or 0)) if failed else 0,
        "failed": failed[:MAX_IDS],
        "warned": warned[:MAX_IDS],
    }


def read_selftest(
    path: Path = RESULTS_PATH,
    now_fn: Callable[[], float] = time.time,
    uptime_fn: Callable[[], float | None] = _system_uptime,
) -> dict | None:
    """The ``selftest`` heartbeat object, or None when there is nothing to say yet."""
    now = now_fn()
    try:
        text = _read_regular_tail(path, READ_CAP_BYTES)
    except FileNotFoundError:
        uptime = uptime_fn()
        return _synthetic(LOOP_DEAD, now) if uptime is not None and uptime > STALE_SECONDS else None
    except OSError as exc:
        logger.warning("selftest_check: cannot read %s (%s)", path, exc)
        return _synthetic(UNREADABLE, now)
    try:
        doc = json.loads(text)
    except ValueError:
        return _synthetic(UNREADABLE, now)
    if not isinstance(doc, dict):
        return _synthetic(UNREADABLE, now)
    return summarize(doc, now)


__all__ = [
    "LOOP_DEAD",
    "MAX_IDS",
    "NO_CHECKS",
    "RESULTS_PATH",
    "STALE_SECONDS",
    "UNREADABLE",
    "read_selftest",
    "summarize",
    "unreadable_result",
]

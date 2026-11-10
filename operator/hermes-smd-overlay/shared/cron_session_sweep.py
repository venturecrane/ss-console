"""Remove the empty sessions that skipped scheduled checks leave behind (captain-mark, 2026-11-10).

WHY. A seat's routines are Hermes cron jobs, and several fire every fifteen
minutes only to ask whether anything is due (the couples gate,
``shared/couples_gate.py``). When nothing is due their pre_run prints
``wakeAgent=false`` and the scheduler skips the agent, so no model is called.
But the gateway admits a session for every fire BEFORE the gate runs
(``gateway/session_cron.py`` ``_create`` at the pinned Hermes), and a skipped
fire never ends it. Each one stays OPEN with no messages and leaves its local
creation policy in ``state_meta`` (``gateway.local_policy.v1:<id>``, about
14.5 KB: it carries the whole cron job), a ``gateway_routing`` row and a
terminal admission row.

Measured on captain-mark 2026-11-10 (read-only): 9,477 cron sessions, 9,412 of
them open, empty and over an hour old; 9,458 policy records holding 137 MB of a
195 MB ``state.db``; 9,461 routing rows. Hermes' own housekeeping cannot reach
them: ``sessions.auto_prune`` deletes only ENDED rows, and its stale-open reap
closes an automation row only after the 90-day retention, so the store grows
for about 180 days of fires. Every boot walks it (``state_db_auto_sweep``,
session recovery, the routing index load); on 2026-11-09 that pushed the
gateway past Hermes' startup watchdog on a CPU-throttled machine.

WHAT ONE PASS DOES, per profile store the gateway owns, and only through
Hermes' own APIs (no SQL write here; plugins never modify Hermes core):

1. SELECT (read-only) at most ``limit`` sessions with ``source='cron'``, no
   title, ``message_count = 0`` and no message rows, no child sessions, not
   pinned, started more than ``min_age_s`` ago, with no queued/started/unknown
   admission, no live worker execution, and no unexpired turn lease or
   compression lock. Oldest first.
2. END each one with ``hermes_state_local.end_idle_local_session``, the owner's
   own "end only while the FIFO is idle" write (its idle check and the stamp are
   one transaction, under the owner's epoch), with the reason
   :data:`END_REASON`.
3. DELETE with ``SessionDB.prune_sessions`` filtered to exactly those rows
   (``source='cron'``, ``end_reason=END_REASON``, ``max_messages=0``, started
   before the cutoff) and ``exclude_active_write_guards=True``. That is the
   housekeeping path Hermes built for cron-heavy stores: ``retire_prunable``
   skips any row with live work, ``retire_sessions`` deletes the policy record
   (``retire_local_receipts``) and the routing row (``retire_routes``), and moves
   each terminal admission out of ``session_admissions`` into a compacted
   tombstone that ``prune_retired_tombstones`` (run by the same call) reclaims
   after Hermes' 7-day exact-retry horizon. Its retired-session markers are the
   expiring kind, so they age out too.
4. On the owner loop, ``gateway.session_mutations.retire_live_sessions`` drops
   the deleted ids from the gateway's memory (routing entries, live sessions,
   launch secrets), exactly as Hermes' own ``session.prune`` does after its
   delete (``gateway/session_prune.py``).

The pass runs as a tracked mutation on the owning gateway's loop (so a drain
waits for it) with the database work in a worker thread, so the loop is never
blocked. A session with messages, a non-cron session, a titled one, a young one
and one under a lease or live admission are never selected, and the prune's
own filters and guards re-check every one of those inside its transaction.

WHERE IT RUNS. One daemon thread in the gateway process only
(``SMD_GATEWAY_PID``, :mod:`shared.gateway_identity`), started from the
hermes-smd-jobs plugin's register. A short-lived python that registers plugins
(bootstrap's invariant run) starts nothing, and a process that does not own the
store finds no owner (``gateway.session_cron.owner_for_home``) and skips.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The end reason this sweep stamps. ``prune_sessions`` is filtered on it, so a
#: pass can only ever delete rows this sweep itself ended.
END_REASON = "smd_skipped_cron_sweep"

#: A session younger than this is never touched: a fire that is still being
#: admitted, gated or run is minutes old, never an hour.
MIN_AGE_S = 3600.0

#: Rows ended and deleted per profile per pass. captain-mark's first pass meets
#: about 9,400; at 2,000 a pass it drains in five passes, and every later pass
#: meets the couple of hundred fires since the last one.
MAX_PER_PASS = 2000

#: Every two hours. Steady state on captain-mark is about 744 fires a day, so a
#: pass meets about 62. The first pass waits :data:`FIRST_PASS_DELAY_S` so it
#: never lands on the boot it exists to shorten.
INTERVAL_S = 2 * 3600.0
FIRST_PASS_DELAY_S = 600.0

#: How long the sweeper thread waits for one owner pass before giving up on it
#: (the pass itself keeps running on the loop; it is tracked, not abandoned).
PASS_TIMEOUT_S = 900.0

_SELECT_SQL = """
SELECT s.id FROM sessions s
WHERE s.source = 'cron'
  AND s.title IS NULL
  AND s.message_count = 0
  AND COALESCE(s.pinned, 0) = 0
  AND s.started_at < :cutoff
  AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id)
  AND NOT EXISTS (SELECT 1 FROM sessions c WHERE c.parent_session_id = s.id)
  AND NOT EXISTS (SELECT 1 FROM session_admissions a
                  WHERE a.target_session_id = s.id AND a.status IN ('queued', 'started', 'unknown'))
  AND NOT EXISTS (SELECT 1 FROM worker_executions w
                  WHERE w.session_id = s.id AND w.status IN ('registered', 'running', 'unknown'))
  AND NOT EXISTS (SELECT 1 FROM session_turn_leases l
                  WHERE l.conversation_id = s.id AND l.expires_at >= :now)
  AND NOT EXISTS (SELECT 1 FROM compression_locks k
                  WHERE k.session_id = s.id AND k.expires_at >= :now)
ORDER BY s.started_at ASC
LIMIT :limit
"""


@dataclass
class SweepResult:
    """What one pass did on one store."""

    selected: int = 0
    ended: int = 0
    deleted: int = 0
    deleted_ids: list[str] = field(default_factory=list)


def select_candidates(db: Any, *, now: float, min_age_s: float, limit: int) -> list[str]:
    """The ids one pass may end and delete: read-only, bounded, oldest first."""
    params = {"cutoff": now - min_age_s, "now": now, "limit": int(limit)}
    with db._read_ctx() as conn:
        return [str(row[0]) for row in conn.execute(_SELECT_SQL, params).fetchall()]


def end_candidates(
    db: Any, ids: Iterable[str], *, epoch: int, end_idle: Callable[..., int] | None = None
) -> int:
    """End each id through Hermes' idle-only end; returns how many it stamped.

    A row whose FIFO took work between the select and here is left open by
    Hermes (``end_idle_local_session`` returns 0), and a refusal of one row
    (a stale epoch, a vanished row) never stops the rest.
    """
    if end_idle is None:
        from hermes_state_local import end_idle_local_session as end_idle
    ended = 0
    for sid in ids:
        try:
            ended += int(
                bool(end_idle(db, epoch=epoch, session_id=sid, target_id=sid, reason=END_REASON))
            )
        except Exception as exc:  # noqa: BLE001 - one row never stops the pass
            logger.debug("cron session sweep: %s not ended (%s)", sid, exc)
    return ended


def sweep_store(
    db: Any,
    *,
    epoch: int,
    now: float | None = None,
    min_age_s: float = MIN_AGE_S,
    limit: int = MAX_PER_PASS,
    sessions_dir: Path | None = None,
) -> SweepResult:
    """One bounded pass over one Hermes store. Synchronous: run it off the loop."""
    now = time.time() if now is None else now
    result = SweepResult()
    ids = select_candidates(db, now=now, min_age_s=min_age_s, limit=limit)
    result.selected = len(ids)
    if ids:
        result.ended = end_candidates(db, ids, epoch=epoch)
    # Prune even when nothing new was ended this pass: a row an earlier pass
    # ended but could not delete (busy then) is retried here. The filters keep
    # the delete to rows this sweep ended; every one is empty, cron and old.
    deleted_ids: list[str] = []
    result.deleted = db.prune_sessions(
        older_than_days=None,
        source="cron",
        end_reason=END_REASON,
        max_messages=0,
        started_before=now - min_age_s,
        sessions_dir=sessions_dir,
        exclude_active_write_guards=True,
        deleted_ids=deleted_ids,
    )
    result.deleted_ids = deleted_ids
    return result


async def owner_pass(
    authority: Any, *, limit: int = MAX_PER_PASS, min_age_s: float = MIN_AGE_S
) -> SweepResult:
    """Run :func:`sweep_store` as the owning gateway, the way ``session.prune`` does.

    On the owner loop: refuse while draining, run the store work in a worker
    thread as a tracked mutation, then retire the deleted ids from the
    gateway's memory on the loop.
    """
    from gateway.session_mutations import retire_live_sessions
    from gateway.session_runtime_workers import track_mutation

    authority._require_admission_open()
    db = authority.db
    sessions_dir = Path(db.db_path).resolve().parent / "sessions"

    async def _apply() -> SweepResult:
        result = await asyncio.to_thread(
            sweep_store,
            db,
            epoch=authority.epoch,
            min_age_s=min_age_s,
            limit=limit,
            sessions_dir=sessions_dir,
        )
        if result.deleted_ids:
            retire_live_sessions(authority, result.deleted_ids)
        return result

    return await asyncio.shield(track_mutation(authority, _apply()))


def profile_homes(root: str | None = None) -> list[Path]:
    """Every Hermes home on this seat that holds a ``state.db``: the root and each profile."""
    base = Path(root or os.environ.get("HERMES_HOME") or "/opt/data")
    homes = [base, *sorted(p for p in (base / "profiles").glob("*") if p.is_dir())]
    return [h for h in homes if (h / "state.db").is_file()]


def run_pass(
    *,
    homes: Iterable[Path] | None = None,
    owner_for_home: Callable[[Any], Any] | None = None,
    limit: int = MAX_PER_PASS,
    timeout_s: float = PASS_TIMEOUT_S,
) -> dict[str, SweepResult]:
    """One pass over every store this process owns; logs one count line per store."""
    if owner_for_home is None:
        from gateway.session_cron import owner_for_home
    results: dict[str, SweepResult] = {}
    for home in profile_homes() if homes is None else homes:
        owner = owner_for_home(home)
        if owner is None:
            continue  # not this process's store, or its loop is gone
        authority, loop = owner
        future = asyncio.run_coroutine_threadsafe(owner_pass(authority, limit=limit), loop)
        try:
            result = future.result(timeout=timeout_s)
        except Exception as exc:  # noqa: BLE001 - one store never stops the others
            logger.warning(
                "cron session sweep: %s pass failed (%s: %s)", home, type(exc).__name__, exc
            )
            continue
        results[str(home)] = result
        logger.info(
            "cron session sweep: %s selected=%d ended=%d deleted=%d (limit %d)",
            home,
            result.selected,
            result.ended,
            result.deleted,
            limit,
        )
    return results


_start_lock = threading.Lock()
_started = False


def start_sweeper_thread(
    *,
    interval_s: float = INTERVAL_S,
    first_delay_s: float = FIRST_PASS_DELAY_S,
    stop_event: threading.Event | None = None,
) -> bool:
    """Start the daemon loop in the gateway process only. Idempotent; never raises."""
    global _started
    from shared.gateway_identity import _env_says_gateway

    with _start_lock:
        if _started or not _env_says_gateway():
            return False
        _started = True
    stop = stop_event or threading.Event()

    def _loop() -> None:
        wait = first_delay_s
        while not stop.wait(wait):
            wait = interval_s
            try:
                run_pass()
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                logger.warning("cron session sweep failed (%s: %s)", type(exc).__name__, exc)

    threading.Thread(target=_loop, name="smd-cron-session-sweep", daemon=True).start()
    logger.info("cron session sweep: thread started (every %.0fs)", interval_s)
    return True


__all__ = [
    "END_REASON",
    "MAX_PER_PASS",
    "MIN_AGE_S",
    "SweepResult",
    "end_candidates",
    "owner_pass",
    "profile_homes",
    "run_pass",
    "select_candidates",
    "start_sweeper_thread",
    "sweep_store",
]

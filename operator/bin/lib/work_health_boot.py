"""A seat restarting during the hourly check is slow, not unreadable.

WHY (triage, 2026-11-03 to 2026-11-07). Six times a seat's ledger read failed
because its Machine was mid-restart, and each one listed the seat as
unreadable, which started a triage run for nothing. The slowest took 5 minutes
7 seconds from the Machine stopping to its health check passing (fly machine
events), and its check began 13 seconds after the stop. So a read that fails
with a timeout or a connection error waits for the Machine's health check to
pass, up to ``BOOT_WAIT`` (7 minutes: inside the hour, and inside the triage
cron's grace), then reads once more. Only a read that fails again is listed,
and the line says how long the Machine had been down, so a seat that is really
stuck reads differently from one that was slow to boot.

Pure: the clock, the sleeper and the health probe are passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from bin.lib.work_health import hhmm, span

#: The longest the read waits for a restarting Machine.
BOOT_WAIT = timedelta(minutes=7)
#: How often the health check is looked at while waiting.
POLL_SECONDS = 15.0

#: Fly machine event statuses that mean the Machine went down.
_DOWN = frozenset({"stopped", "exited", "destroyed", "failed"})


@dataclass(frozen=True)
class MachineState:
    """What the Machine list says right now. ``passing`` is None when it could
    not be read; ``down_since`` is when the Machine last went down, while it is
    not passing."""

    passing: Optional[bool]
    down_since: Optional[datetime] = None


def _event_time(event: dict[str, Any]) -> Optional[datetime]:
    raw = event.get("timestamp")
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
    return None


def machine_state(machines: Optional[list[dict[str, Any]]]) -> MachineState:
    """Passing when a started Machine exists and every health check on the
    started Machines passes (a Machine with no checks counts by its state).
    While not passing, ``down_since`` is the newest stop or exit event."""
    if machines is None:
        return MachineState(None)
    started = [m for m in machines if m.get("state") == "started"]
    passing = bool(started) and all(
        all(c.get("status") == "passing" for c in (m.get("checks") or []) if isinstance(c, dict)) for m in started
    )
    if passing:
        return MachineState(True)
    downs = [
        t
        for m in machines
        for e in (m.get("events") or [])
        if isinstance(e, dict) and str(e.get("status") or "") in _DOWN
        for t in [_event_time(e)]
        if t is not None
    ]
    return MachineState(False, max(downs) if downs else None)


def wait_for_health(
    passing: Callable[[], Optional[bool]],
    *,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    wait: timedelta = BOOT_WAIT,
    poll_seconds: float = POLL_SECONDS,
) -> float:
    """Wait until ``passing()`` is True or ``wait`` has elapsed; return the
    seconds waited. ``passing`` is asked before each pause, so a Machine that
    is already healthy costs no wait at all."""
    start = clock()
    deadline = start + wait.total_seconds()
    while clock() < deadline:
        if passing() is True:
            break
        sleep(min(poll_seconds, max(0.0, deadline - clock())))
    return clock() - start


def unreadable_detail(exc: BaseException, waited_s: float, state: MachineState, now: datetime) -> str:
    """The unreadable line's account of the wait: how long the Machine had been
    down, or that its health check passed and the read still failed."""
    error = f"{type(exc).__name__}: {str(exc)[:160]}"
    waited = span(timedelta(seconds=waited_s))
    if state.passing is True:
        return f"{error}; the Machine's health check passed within {waited} but the read still failed"
    if state.down_since is not None:
        return (
            f"{error}; still after waiting {waited} for the Machine's health check: the Machine has been down "
            f"{span(now - state.down_since)}, since {hhmm(state.down_since)}"
        )
    return f"{error}; still after waiting {waited} for the Machine's health check to pass"


__all__ = [
    "BOOT_WAIT",
    "POLL_SECONDS",
    "MachineState",
    "machine_state",
    "unreadable_detail",
    "wait_for_health",
]

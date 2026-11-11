"""The busy gate: never restart a seat in the middle of a job.

Read LIVE, by `fly machine exec` (HTTPS, no tunnel), at the moment of decision, by the shipped
remote_busy_probe.py (works on images that predate any fleet tooling). D1's
heartbeat columns are a dashboard, not a gate: a heartbeat is minutes old by
construction, and on images before the lane beat threads a running chronology's
heartbeat goes stale for the whole run.

BUSY means any of:
  * a lane's job child pid is alive, or a lane heartbeat names a running job;
  * a lane has wakes pending (a finished job's notice not yet sent);
  * a DELIVER turn started in the last TURN_BUSY_S and its reply is not yet
    seen (the wake is consumed, so the lane reads idle while the turn sends);
  * a live gateway reports turns in flight: an answer to an inbound email is
    no lane and no cron. Asked live first (the gateway's ``active-work`` control
    verb, what Hermes' own restart guard asks); the ``active_agents`` snapshot in
    gateway_state.json is read only from a gateway that does not answer it;
  * a lane daemon is alive but its heartbeat is STALE: it cannot be shown idle,
    so it is treated as busy (a stale heartbeat never reads as idle), unless
    the live daemon started after that beat was written: then it is a lane the
    daemon does not run, left on the volume, and says nothing about now;
  * a managed cron (``op-managed:``) fires within the window, or fired within
    the last few minutes (its turn may still be running in the gateway);
  * a store the probe needed could not be read (cannot evaluate is not idle).

IDLE without a probe: the app has no Machine yet (a successful listing with zero
Machines). A FAILED listing is a tooling error, never idle.

QUEUED work is not busy: the queues live on the volume (/run/smd-jobs is a
bind mount of it) and survive the restart.

Busy -> write the quiesce marker (no new claims; expires on its own) and poll
until idle, up to a cap. The cap ends in WAITING_IDLE: the seat is skipped with
the reason, never deployed mid-job. ``--force-mid-job`` skips the gate, loudly.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import ids
from .fly import Fly
from .runner import ToolingError, looks_like_transport_failure

HERE = Path(__file__).resolve().parent
PROBE_MARK = "SMD_BUSY_PROBE "
STALE_AFTER_S = 300.0
# The cron windows must leave a gap inside the tightest managed schedule. Couples
# seats run slot crons every 15 minutes; 600 + 300 left no gap at all, so the
# gate could never pass there (captain-mark, 2026-10-19). Ahead covers one
# restart (deploy + boot, ~5 min observed on smd-staging); behind covers a
# slot's own turn. 360 + 120 leaves 7 minutes in every quarter hour, and the
# 30 s poll lands in it.
CRON_AHEAD_S = 360.0
CRON_BEHIND_S = 120.0
# A consumed wake is a gateway turn still sending (2026-11-02, A&P: a restart
# 10 s after seven offer-email turns started lost four). Busy until its reply is
# seen in Sent Items, at most this long: a reply that never shows is the reply
# watch's page, not a reason to hold every release.
TURN_BUSY_S = 600.0
# A gateway turn count is believed unless the gateway has written no
# conversation message for this long. A live turn writes as it goes (its
# prompt, each tool call, the reply); a count with half an hour of silence
# behind it is one the gateway never released (captain-mark 2026-11-02: an
# interrupted reply left 2 "turns" on an idle seat and the gate held a fix for
# 30 minutes). Unknown silence (no reading) still counts as busy.
GATEWAY_QUIET_S = 1800.0
QUIESCE_TTL_S = 1800.0
POLL_S = 30.0
# A wait can run for an hour of ssh calls; one of them losing its connection to
# Fly's API says nothing about the seat (captain-mark, 2026-10-26: one
# `api.machines.dev ... EOF` mid-wait halted ring 2). A transport failure is
# retried this many times in a row, a poll apart, before it halts the release.
# Anything else (the probe ran and answered nonsense, the marker was refused)
# halts at once, and a failure that persists still halts.
TRANSPORT_ATTEMPTS = 3


# A seat whose app exists but has no Machine yet (a first provision, before its
# first deploy) cannot be running anything: idle, not a tooling error (smd-guide,
# 2026-11-10, needed FLEET_FORCE_MID_JOB=1 for its first provision). Only a
# listing that SUCCEEDED with zero Machines reads this way: a listing that failed
# (API, auth, timeout) raises from Fly.machines and stays a tooling error.
NO_MACHINE_REASON = "no Machine yet; nothing can be running"


class TransportFailure(ToolingError):
    """The call never reached the seat: Fly's API, agent or ssh layer dropped it."""


@dataclass
class Verdict:
    busy: bool
    reasons: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)


def _lane_reasons(lane: dict[str, Any], stale_after: float) -> list[str]:
    name = lane.get("file", "?")
    out = []
    if lane.get("running"):
        out.append(f"{name}: running job {lane['running']}")
    if int(lane.get("wakes_pending") or 0) > 0:
        out.append(f"{name}: {lane['wakes_pending']} wake(s) pending")
    age = lane.get("age_s")
    # A file the live daemon never wrote is a lane it does not run (ADR 0100: a
    # seat that stopped authoring a lane keeps the old beat on the volume). A
    # running job still shows as its child pid above.
    leftover = lane.get("written_before_daemon") is True
    if lane.get("daemon_alive") and not leftover and (age is None or float(age) > stale_after):
        out.append(f"{name}: heartbeat stale ({age}s) with its daemon alive; cannot show it idle")
    return out


def _cron_reasons(cron: dict[str, Any], ahead: float, behind: float) -> list[str]:
    nxt, last = cron.get("next_in_s"), cron.get("last_ago_s")
    if nxt is not None and -behind <= float(nxt) <= ahead:
        return [f"cron {cron['name']} fires in {float(nxt):.0f}s"]
    if last is not None and 0 <= float(last) <= behind:
        return [f"cron {cron['name']} fired {float(last):.0f}s ago; its turn may still be running"]
    return []


def decide(
    facts: dict[str, Any],
    stale_after: float = STALE_AFTER_S,
    ahead: float = CRON_AHEAD_S,
    behind: float = CRON_BEHIND_S,
) -> Verdict:
    reasons: list[str] = []
    for child in facts.get("children") or []:
        if child.get("alive"):
            reasons.append(f"{child.get('file')}: job child pid {child.get('pid')} alive")
    for lane in facts.get("lanes") or []:
        reasons.extend(_lane_reasons(lane, stale_after))
    for cron in facts.get("crons") or []:
        reasons.extend(_cron_reasons(cron, ahead, behind))
    for gw in facts.get("gateways") or []:
        live = gw.get("live")
        if gw.get("alive") and isinstance(live, dict):
            # The gateway answered its own active-work verb: that is the count, in
            # both directions (a stale file count is not work; a live unit is work
            # even when the file says 0). Older gateways fall through to the file.
            kinds = [str(k) for k in live.get("active_work") or []]
            try:
                count = max(int(live.get("active_agents") or 0), len(kinds))
            except (TypeError, ValueError):
                count = max(1, len(kinds))  # an unreadable count is never idle
            if count > 0:
                what = ", ".join(kinds) if kinds else "unnamed"
                reasons.append(f"{gw.get('file')}: the gateway is running {count} turn(s) (live: {what})")
            continue
        try:
            active = int(gw.get("active_agents") or 0)
        except (TypeError, ValueError):
            active = 0
        quiet = gw.get("quiet_s")
        leaked = isinstance(quiet, (int, float)) and float(quiet) > GATEWAY_QUIET_S
        if gw.get("alive") and active > 0 and not leaked:
            reasons.append(f"{gw.get('file')}: the gateway is running {active} turn(s)")
    for turn in facts.get("turns") or []:
        ago = turn.get("sent_ago_s")
        if turn.get("reply_open") and ago is not None and 0 <= float(ago) <= TURN_BUSY_S:
            reasons.append(f"{turn.get('file')}: DELIVER turn started {float(ago):.0f}s ago, its reply not yet seen")
    for err in facts.get("errors") or []:
        reasons.append(f"cannot evaluate: {err}")
    return Verdict(bool(reasons), reasons, facts)


def parse_probe(stdout: str) -> dict[str, Any]:
    for line in stdout.splitlines():
        if line.startswith(PROBE_MARK):
            return json.loads(line[len(PROBE_MARK) :])
    raise ToolingError("busy probe returned no verdict line (ssh failed or the probe did not run)")


MARKER_PREFIX = "fleet:"


def quiesce_source(action: str, by: str, until: float = 0.0, reason: str = "", prefix: str | None = None) -> str:
    args = {"action": action, "by": by, "until": until, "reason": reason, "prefix": prefix}
    body = (HERE / "remote_quiesce.py").read_text(encoding="utf-8")
    # A JSON string literal is also a valid Python string literal.
    return "import json\nARGS = json.loads(" + json.dumps(json.dumps(args)) + ")\n" + body


class BusyGate:
    """Probe, quiesce, wait. One instance per rollout."""

    def __init__(
        self, fly: Fly, holder: str, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time
    ) -> None:
        self.fly = fly
        self.holder = MARKER_PREFIX + holder
        self.sleep = sleep
        self.clock = clock
        self.probe_source = (HERE / "remote_busy_probe.py").read_text(encoding="utf-8")

    def probe(self, seat: str) -> Verdict:
        if not self.fly.machines(ids.app_name(seat)):
            return Verdict(False, [NO_MACHINE_REASON], {"machines": 0})
        res = self.fly.run_python(ids.app_name(seat), self.probe_source)
        if not res.ok and PROBE_MARK not in res.stdout:
            if looks_like_transport_failure(res):
                raise TransportFailure(f"busy probe on {seat} failed (transport): {res.text.strip()[-300:]}")
            raise ToolingError(f"busy probe on {seat} failed (rc={res.rc}): {res.text.strip()[-300:]}")
        return decide(parse_probe(res.stdout))

    def quiesce(self, seat: str, reason: str) -> None:
        src = quiesce_source("set", self.holder, self.clock() + QUIESCE_TTL_S, reason)
        res = self.fly.run_python(ids.app_name(seat), src)
        if "SMD_QUIESCE set" not in res.stdout and "SMD_QUIESCE no-run-dir" not in res.stdout:
            if not res.ok and looks_like_transport_failure(res):
                raise TransportFailure(f"could not reach {seat} to write the quiesce marker: {res.text.strip()[-300:]}")
            raise ToolingError(f"could not write the quiesce marker on {seat}: {res.text.strip()[-300:]}")

    def clear(self, seat: str) -> None:
        """Best effort: a new boot ignores an expired marker anyway."""
        try:
            self.fly.run_python(ids.app_name(seat), quiesce_source("clear", self.holder))
        except ToolingError:
            pass

    def clear_any_fleet(self, seat: str) -> str:
        """Remove a marker ANY fleet run left (reprovision clears after its deploy)."""
        if not self.fly.machines(ids.app_name(seat)):
            return "SMD_QUIESCE no Machine; no marker to clear"
        res = self.fly.run_python(ids.app_name(seat), quiesce_source("clear", self.holder, prefix=MARKER_PREFIX))
        return next((ln for ln in res.stdout.splitlines() if ln.startswith("SMD_QUIESCE")), "SMD_QUIESCE no answer")

    def _riding_transport(self, seat: str, what: str, call: Callable[[], Any], log: Callable[[str], None]) -> Any:
        """``call()``, retried through up to TRANSPORT_ATTEMPTS transport failures in a row."""
        for attempt in range(1, TRANSPORT_ATTEMPTS + 1):
            try:
                return call()
            except TransportFailure as exc:
                if attempt == TRANSPORT_ATTEMPTS:
                    raise TransportFailure(f"{exc} ({TRANSPORT_ATTEMPTS} attempts in a row)") from exc
                log(f"{seat}: {what} lost its connection; retrying in {POLL_S:.0f}s ({attempt}/{TRANSPORT_ATTEMPTS})")
                self.sleep(POLL_S)
        raise AssertionError("unreachable")

    def wait_idle(self, seat: str, max_wait: float, log: Callable[[str], None]) -> Verdict:
        """Idle verdict, or the last busy verdict once ``max_wait`` has passed."""

        def probe() -> Verdict:
            return self._riding_transport(seat, "busy probe", lambda: self.probe(seat), log)

        def quiesce() -> None:
            reason = f"fleet release by {self.holder}"
            self._riding_transport(seat, "quiesce marker", lambda: self.quiesce(seat, reason), log)

        verdict = probe()
        if not verdict.busy:
            return verdict
        log(f"{seat}: busy ({'; '.join(verdict.reasons)}); quiescing and waiting up to {max_wait:.0f}s")
        quiesce()
        deadline = self.clock() + max_wait
        while self.clock() < deadline:
            self.sleep(POLL_S)
            verdict = probe()
            if not verdict.busy:
                log(f"{seat}: idle")
                return verdict
            quiesce()  # keep the marker ahead of its expiry
        return verdict

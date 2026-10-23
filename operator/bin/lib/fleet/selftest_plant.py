"""`fleet selftest --plant <check-id> <slug>`: re-prove the self-test's alert path.

The self-test is only worth what its alert path delivers, and that path has
five hops (check, results file, heartbeat, fleet_status, fleet-alerts worker),
each of which can break silently. After any change to the self-test this verb
proves all five on smd-staging, end to end, with a REAL violation:

  1. plant the violation (remote_selftest_plant.py, as root over ssh) and ask
     the seat's own self-test loop for a run (its root-only trigger),
  2. wait until the heartbeat shows that check failed,
  3. ask for a second run and wait for failed_streak >= 2 (an hourly failure
     pages on the second consecutive run),
  4. wait until fleet_alert_state shows selftest_failing OPEN,
  5. clear the violation (always, even if a step above timed out), ask for a
     run, wait until the check passes and, when nothing else failed, until the
     alert RESOLVES.

Staging only, refused for any other slug: it plants a fault on a seat.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import ids
from .fly import Fly
from .runner import Refusal, ToolingError

HERE = Path(__file__).resolve().parent
PLANT_SEAT = "smd-staging"
PLANTABLE = frozenset({"retired-skills-absent"})
POLL_S = 20.0
DEFAULT_WINDOW_S = 15 * 60


def plant_source(action: str) -> str:
    body = (HERE / "remote_selftest_plant.py").read_text(encoding="utf-8")
    # A JSON string literal is also a valid Python string literal.
    return "import json\nARGS = json.loads(" + json.dumps(json.dumps({"action": action})) + ")\n" + body


def selftest_doc(row: dict[str, Any] | None) -> dict[str, Any] | None:
    raw = (row or {}).get("selftest_json")
    try:
        doc = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


@dataclass
class PlantReport:
    steps: list[str] = field(default_factory=list)
    ok: bool = True

    def step(self, ok: bool, text: str) -> bool:
        self.steps.append(("ok    " if ok else "FAILED ") + text)
        self.ok = self.ok and ok
        return ok


class SelftestPlant:
    def __init__(
        self,
        fly: Fly,
        seat_status: Callable[[str], dict[str, Any] | None],
        alert_status: Callable[[str], str | None],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.fly, self.seat_status, self.alert_status = fly, seat_status, alert_status
        self.sleep, self.clock = sleep, clock

    def _remote(self, seat: str, action: str) -> str:
        res = self.fly.ssh_python(ids.app_name(seat), plant_source(action))
        line = next((ln for ln in res.text.splitlines() if ln.startswith("SMD_SELFTEST_PLANT ")), "")
        if not line.startswith("SMD_SELFTEST_PLANT ok"):
            raise ToolingError(f"{action} on {seat}: {line or res.text.strip()[-300:] or 'no answer'}")
        return line.removeprefix("SMD_SELFTEST_PLANT ")

    def _until(self, window: float, probe: Callable[[], Any]) -> Any:
        deadline = self.clock() + window
        while True:
            got = probe()
            if got or self.clock() >= deadline:
                return got
            self.sleep(POLL_S)

    def _run_after(self, seat: str, after: str, pred: Callable[[dict[str, Any]], bool], window: float):
        floor = ids.parse_iso(after)

        def probe() -> dict[str, Any] | None:
            doc = selftest_doc(self.seat_status(seat))
            done = ids.parse_iso((doc or {}).get("finished_at"))
            return doc if doc and done and floor and done > floor and pred(doc) else None

        return self._until(window, probe)

    def run(self, seat: str, check_id: str, window: float = DEFAULT_WINDOW_S) -> PlantReport:
        if ids.slug(seat) != PLANT_SEAT:
            raise Refusal(f"fleet selftest --plant runs on {PLANT_SEAT} only (it plants a fault on a seat)")
        if check_id not in PLANTABLE:
            raise Refusal(f"no planter for {check_id}; plantable: {', '.join(sorted(PLANTABLE))}")
        report = PlantReport()
        start = ids.iso(ids.utc_now())
        try:
            self._plant_phase(seat, check_id, start, window, report)
        finally:
            cleared_at = ids.iso(ids.utc_now())
            report.step(True, self._remote(seat, "clear"))
        self._clear_phase(seat, check_id, cleared_at, window, report)
        return report

    def _plant_phase(self, seat: str, check_id: str, start: str, window: float, report: PlantReport) -> None:
        report.step(True, self._remote(seat, "plant"))
        first = self._run_after(seat, start, lambda d: check_id in (d.get("failed") or []), window)
        if not report.step(bool(first), f"heartbeat shows {check_id} failed"):
            return
        self._remote(seat, "trigger")
        second = self._run_after(seat, first["finished_at"], lambda d: int(d.get("failed_streak") or 0) >= 2, window)
        if not report.step(bool(second), "a second consecutive failed run (failed_streak >= 2)"):
            return
        opened = self._until(window, lambda: self.alert_status(seat) == "open")
        report.step(bool(opened), "fleet_alert_state shows selftest_failing open")

    def _clear_phase(self, seat: str, check_id: str, cleared_at: str, window: float, report: PlantReport) -> None:
        clean = self._run_after(seat, cleared_at, lambda d: check_id not in (d.get("failed") or []), window)
        if not report.step(bool(clean), f"heartbeat shows {check_id} passing again"):
            return
        others = int(clean.get("failed_count") or 0)
        if others:
            report.step(
                False, f"selftest_failing cannot resolve: {others} other check(s) fail on {seat}: {clean.get('failed')}"
            )
            return
        closed = self._until(window, lambda: self.alert_status(seat) == "resolved")
        report.step(bool(closed), "fleet_alert_state shows selftest_failing resolved")

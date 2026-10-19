"""Did the seat come up on the release? Boot smoke plus a heartbeat read-back.

Each check ends in one of four classes, and only one of them may roll a seat back:

  pass           every check green and the heartbeat reports the target.
  fail           the seat is genuinely unhealthy: a smoke check ran and said no,
                 the Machine never reached started, or the heartbeat reports a
                 DIFFERENT image or overlay than the release. -> roll back + halt.
  tooling_error  no verdict came back: ssh failed on every attempt, wrangler
                 could not reach D1, the smoke died at an unguarded line. ->
                 halt, touch nothing.
  timeout        the smoke overran its budget, or no fresh heartbeat arrived in
                 the read-back window. -> halt, touch nothing.

The smoke script's own vocabulary is what is read (boot-smoke-test.sh): a check
that RAN and failed logs "FAILED: <step> — command failed", one that never got a
verdict logs "no verdict from the Machine on two attempts (ssh failed)", and a
precondition abort logs "FATAL: machine-state-started".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from . import ids
from .d1 import FleetStore
from .runner import Runner, ToolingError

SMOKE_TIMEOUT_S = 45 * 60
READBACK_WINDOW_S = 6 * 60
SKILLS_READBACK_WINDOW_S = 3 * 60
READBACK_POLL_S = 20.0


@dataclass
class Check:
    cls: str  # pass | fail | tooling_error | timeout
    detail: str


def classify_smoke(rc: int, output: str) -> Check:
    if rc == 0:
        return Check("pass", "boot smoke green")
    failed = [ln.split("FAILED:", 1)[1].strip() for ln in output.splitlines() if "  FAILED:" in ln or "] FAILED:" in ln]
    if "FATAL: machine-state-started" in output:
        return Check("fail", "the Machine never reached state=started")
    ran_and_failed = [f for f in failed if "command failed" in f]
    if ran_and_failed:
        return Check("fail", "; ".join(ran_and_failed[:5]))
    if failed and all("no verdict from the Machine" in f for f in failed):
        return Check("tooling_error", f"{len(failed)} check(s) got no verdict over ssh")
    if failed:
        return Check("fail", "; ".join(failed[:5]))
    return Check(
        "tooling_error", f"boot smoke ended rc={rc} with no failed check recorded (unguarded error or precondition)"
    )


class SmokeRunner:
    def __init__(self, runner: Runner, repo_root: str) -> None:
        self.runner = runner
        self.repo_root = repo_root

    def run(self, seat: str, expected_hermes_sha: str | None) -> Check:
        env = {"BOOT_SMOKE_EXPECTED_HERMES_SHA": expected_hermes_sha} if expected_hermes_sha else {}
        try:
            res = self.runner.run(
                ["bash", "operator/bin/boot-smoke-test.sh", ids.slug(seat)],
                timeout=SMOKE_TIMEOUT_S,
                cwd=self.repo_root,
                env=env,
            )
        except ToolingError as exc:
            return Check("timeout", str(exc))
        return classify_smoke(res.rc, res.text)


def _fresh(row: dict[str, Any], since: datetime) -> bool:
    beat = ids.parse_iso(row.get("last_heartbeat_ts"))
    return bool(beat and beat >= since)


def judge_image_row(row: dict[str, Any] | None, since: datetime, release: dict[str, Any]) -> Check | None:
    """A verdict from one fleet_status row, or None to keep waiting."""
    if not row or not _fresh(row, since):
        return None
    reported = row.get("image_ref")
    if reported is None:
        return None  # unknown: an emitter that has not reported yet is not a mismatch
    if not ids.image_matches(reported, release["image_ref"], release.get("image_digest")):
        return Check("fail", f"heartbeat reports image {reported}, release is {release['image_ref']}")
    overlay = release.get("overlay_ref")
    if overlay and row.get("version") != overlay:
        return Check("fail", f"heartbeat reports overlay {row.get('version')}, release pins {overlay}")
    want_skills = release.get("skills_version")
    if want_skills and row.get("skills_version") != want_skills:
        return (
            None
            if row.get("skills_version") is None
            else Check("fail", f"heartbeat reports skills {row.get('skills_version')}, release carries {want_skills}")
        )
    return Check("pass", f"heartbeat at {row.get('last_heartbeat_ts')} reports the release image and overlay")


def judge_skills_row(row: dict[str, Any] | None, since: datetime, version: str) -> Check | None:
    if not row or not _fresh(row, since):
        return None
    got = row.get("skills_version")
    if got == version:
        return Check("pass", f"heartbeat at {row.get('last_heartbeat_ts')} reports skills {version}")
    return None


def read_back(
    store: FleetStore,
    seat: str,
    judge: Callable[[dict[str, Any] | None], Check | None],
    window: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> Check:
    deadline = clock() + window
    last: dict[str, Any] | None = None
    while True:
        try:
            last = store.seat_status(seat)
        except ToolingError as exc:
            return Check("tooling_error", f"fleet_status read failed: {exc}")
        verdict = judge(last)
        if verdict is not None:
            return verdict
        if clock() >= deadline:
            seen = (
                "no row"
                if last is None
                else f"last heartbeat {last.get('last_heartbeat_ts')}, image {last.get('image_ref')}, skills {last.get('skills_version')}"
            )
            return Check("timeout", f"no fresh heartbeat confirming the target in {window:.0f}s ({seen})")
        sleep(READBACK_POLL_S)

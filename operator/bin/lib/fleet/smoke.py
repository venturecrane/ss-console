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

The seat's in-image self-test is the second verifier (`--verify selftest`):
the same check list, run on the Machine and read back from the heartbeat
(judge_selftest_row), after the two checks only the fleet can make
(FleetSideChecks: the Machine started, on the authored guest size).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

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


# ---------------------------------------------------------------------------
# The in-image self-test as the verifier (fleet release --verify selftest).
#
# The seat runs the boot smoke's own check list in place (operator/templates/
# selftest.sh) and ships the verdict on its heartbeat as `selftest`, stored in
# fleet_status.selftest_json. A row verifies the release only when it is the
# RELEASE's run: fresh, finished after the deploy started, on the release image,
# and on the release's code version, so a late row from the previous image can
# never pass. Until such a row exists the judge waits (None).
# ---------------------------------------------------------------------------

SELFTEST_WINDOW_S = 20 * 60
STARTED_WAIT_S = 60


def _selftest_doc(row: dict[str, Any]) -> dict[str, Any] | None:
    raw = row.get("selftest_json")
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _selftest_is_the_release_run(
    doc: dict[str, Any], row: dict[str, Any], since: datetime, release: dict[str, Any]
) -> bool:
    ran = ids.parse_iso(doc.get("finished_at"))
    if ran is None or ran < since:
        return False
    if not ids.image_matches(doc.get("image_ref"), release["image_ref"], release.get("image_digest")):
        return False
    overlay = release.get("overlay_ref")
    return not overlay or (row.get("version") == overlay and doc.get("version") == overlay)


def judge_selftest_row(
    row: dict[str, Any] | None, since: datetime, release: dict[str, Any], expected_hermes_sha: str | None
) -> Check | None:
    """A verdict from the seat's own self-test, or None to keep waiting."""
    if not row or not _fresh(row, since):
        return None
    doc = _selftest_doc(row)
    if doc is None or not _selftest_is_the_release_run(doc, row, since, release):
        return None
    if expected_hermes_sha and doc.get("hermes_sha") != expected_hermes_sha:
        return Check("fail", f"self-test reports Hermes {doc.get('hermes_sha')}, release pins {expected_hermes_sha}")
    failed = doc.get("failed") or []
    if int(doc.get("failed_count") or 0) > 0:
        return Check(
            "fail",
            f"self-test ({doc.get('run_kind')} run at {doc.get('finished_at')}) failed: " + "; ".join(failed[:10]),
        )
    return Check(
        "pass",
        f"self-test ({doc.get('run_kind')} run at {doc.get('finished_at')}) passed {doc.get('total')} checks"
        f" with {doc.get('warned_count')} warning(s) on the release image",
    )


def selftest_note(row: dict[str, Any] | None, since: datetime, release: dict[str, Any], hermes: str | None) -> str:
    """One line for fleet_rollouts.detail when the bash smoke decided."""
    verdict = judge_selftest_row(row, since, release, hermes)
    return "selftest: not yet" if verdict is None else f"selftest: {verdict.cls}: {verdict.detail}"


def authored_guest(customer_yaml: Path) -> tuple[str, int, int]:
    """(cpu_kind, cpus, memory_mb) the seat authors, by lib/machine-size.sh's rule."""
    doc = yaml.safe_load(customer_yaml.read_text(encoding="utf-8")) or {}
    machine = doc.get("machine") or {}
    size, mem = str(machine.get("size") or ""), int(machine.get("memory_mb") or 0)
    found = re.fullmatch(r"(shared-cpu|performance)-(\d+)x", size)
    if not found or int(found.group(2)) not in (1, 2, 4, 6, 8, 16) or mem <= 0:
        raise ValueError(f"{customer_yaml}: machine.size {size!r} / memory_mb {mem} is not a Fly guest")
    return ("shared" if found.group(1) == "shared-cpu" else "performance", int(found.group(2)), mem)


class FleetSideChecks:
    """What the seat cannot report about itself: the Machine is started, and its
    guest is the size customer.yaml authored (boot smoke steps 1 and 1b)."""

    def __init__(
        self,
        fly: Any,
        repo_root: str,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.fly, self.repo_root, self.sleep, self.clock = fly, repo_root, sleep, clock

    def _started(self, app: str) -> str:
        deadline = self.clock() + STARTED_WAIT_S
        state = "none"
        while True:
            machines = self.fly.machines(app)
            state = str((machines[0] if machines else {}).get("state") or "none")
            if state == "started" or self.clock() >= deadline:
                return state
            self.sleep(3)

    def run(self, seat: str) -> Check:
        app = ids.app_name(seat)
        try:
            state = self._started(app)
            if state != "started":
                return Check("fail", f"the Machine never reached state=started (state={state})")
            guest = self.fly.guest(app)
        except ToolingError as exc:
            return Check("tooling_error", f"fleet-side checks could not read Fly: {exc}")
        path = Path(self.repo_root) / "operator" / "customers" / ids.slug(seat) / "customer.yaml"
        if not path.is_file():
            return Check("pass", "Machine started (no customer.yaml in this checkout to size against)")
        try:
            want = authored_guest(path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return Check("tooling_error", f"cannot read the authored guest size: {exc}")
        got = (guest.get("cpu_kind"), guest.get("cpus"), guest.get("memory_mb"))
        if got != want:
            return Check("fail", f"guest is {got}, customer.yaml authors {want}")
        return Check("pass", f"Machine started on the authored guest {want}")


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

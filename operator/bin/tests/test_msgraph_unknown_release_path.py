"""A Microsoft outage is not a breach, end to end: probe, self-test, heartbeat, release.

THE INCIDENT (2026-11-12). Microsoft Graph returned 502/503 or timed out on the
mailbox endpoint for the client tenant and the lab tenant from about 18:10Z to
at least 23:28Z, while token minting kept working. The twin seat's boot
self-test read each 503 as "the read app can read the approver's inbox", the
check failed, and `fleet release` rolled back every release on the twin, which
holds every release to the law-firm client behind it.

WHAT THIS DRIVES, all real code, nothing read as source text:

  the probe          operator/templates/msgraph-read-app-cannot-read-staff-probe.py,
                     its run() and its real urllib ask, pointed at a local
                     server playing Microsoft (only the token mint and the
                     backoff sleep are replaced)
  the self-test      operator/templates/selftest.sh --once, with a one-check
                     list that calls check_vendor_strict on that probe, the verb
                     the real list uses for it (pinned by the seat-checks golden
                     fixture)
  the heartbeat      shared.selftest_check.summarize on the results.json written
  the release        ImageRollout with --verify selftest reading that heartbeat

THE OUTCOMES THIS PINS:

  UNKNOWN at boot    a WARNING naming the HTTP status; the release is NOT rolled
                     back, and the rollout's detail line names the check.
  UNKNOWN that stays the first hourly run at INCONCLUSIVE_FAIL_AFTER in a row,
                     or four hours after the first, counted across restarts
                     (the streak lives on the volume), records the check
                     FAILED, and the next hourly run gives
                     failed_streak 2: the shape fleet-alerts pages as
                     selftest_failing (workers/fleet-alerts/src/selftest.test.ts
                     pins the paging half on this exact shape).
  ALLOWED (planted)  a FAILED check at boot, and the release IS rolled back.
  ALLOWED hourly     a FAILED check on the first hourly run (strict), and the
                     next hourly run gives failed_streak 2, which pages.
"""

from __future__ import annotations

import http.server
import json
import os
import pwd
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from bin.lib.fleet.smoke import FleetSideChecks
from bin.tests.fleet_fakes import DIGEST, OVERLAY, TARGET_REF, fresh_status
from bin.tests.test_fleet_release import World
from shared import selftest_check

REPO = Path(__file__).resolve().parents[3]
TEMPLATES = REPO / "operator" / "templates"
SELFTEST = TEMPLATES / "selftest.sh"
PROBE = TEMPLATES / "msgraph-read-app-cannot-read-staff-probe.py"
CHECK_ID = "msgraph-read-app-cannot-read-staff-mailbox"
HERMES = "3" * 40
STAFF = "paralegal@firm.example"

# Runs the REAL probe's run() with its REAL urllib ask, pointed at the local
# server. The mint is replaced (Microsoft's token endpoint answered throughout
# the incident) and the backoff sleep is a no-op so the test is fast.
DRIVER = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("probe", sys.argv[1])
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
base, yaml_path, proc, user = sys.argv[2:6]
sys.exit(probe.run(
    yaml_path, user, proc,
    mint=lambda *_: "token",
    ask=lambda token, mailbox: probe._urllib_ask(token, mailbox, base=base, timeout=0.4),
    sleep=lambda _s: None,
))
"""


class _Graph(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        script = self.server.script  # type: ignore[attr-defined]
        step = script.pop(0) if len(script) > 1 else script[0]
        if step == "hang":
            time.sleep(1.5)
            step = 403
        self.send_response(step)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def graph() -> Iterator[http.server.ThreadingHTTPServer]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Graph)
    server.daemon_threads = True
    server.script = [403]  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def seat(tmp_path: Path, graph: http.server.ThreadingHTTPServer) -> dict:
    root = tmp_path / "root"
    (root / "opt/hermes").mkdir(parents=True)
    (root / "opt/hermes/HERMES_SHA").write_text(HERMES + "\n")
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(f"scope:\n  staff_send_as:\n    - address: {STAFF}\n      name: Pat\n")
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    (proc / "environ").write_bytes(b"MSGRAPH_TENANT_ID=tid\x00MSGRAPH_CLIENT_ID=cid\x00MSGRAPH_CLIENT_SECRET=s\x00")
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    checks = tmp_path / "checks"
    checks.mkdir()
    base = f"http://127.0.0.1:{graph.server_address[1]}"
    user = pwd.getpwuid(os.getuid()).pw_name
    command = f"{sys.executable} {driver} {PROBE} {base} {yaml_path} {tmp_path / 'proc'} {user}"
    (checks / "seat-checks.sh").write_text(f'seat_checks() {{\ncheck_vendor_strict "{CHECK_ID}" "{command}"\n}}\n')
    return {"root": root, "checks": checks, "graph": graph}


def _selftest(seat: dict, kind: str) -> dict:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(seat["root"]),
        "SMD_SELFTEST_ROOT": str(seat["root"]),
        "SMD_SELFTEST_CHECKS_DIR": str(seat["checks"]),
        "SMD_SELFTEST_PYTHON": sys.executable,
        "SMD_SELFTEST_CHECK_TIMEOUT_S": "60",
        "SMD_HERMES_UPSTREAM_SHA": HERMES,
        "SMD_OVERLAY_REF": OVERLAY,
        "FLY_IMAGE_REF": TARGET_REF + "@" + DIGEST,
        # The state directory must be owned by "root"; here that is this user.
        "SMD_SELFTEST_ROOT_UID": str(os.getuid()),
    }
    done = subprocess.run(  # noqa: S603 - the repo's own self-test, against a tmp tree
        ["bash", str(SELFTEST), "--once", kind], env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads((seat["root"] / "run/smd-selftest/results.json").read_text())


def _check(doc: dict) -> dict:
    (found,) = [c for c in doc["checks"] if c["id"] == CHECK_ID]
    return found


def _heartbeat(doc: dict) -> dict:
    return selftest_check.summarize(doc, time.time())


def _release(tmp_path: Path, heartbeat: dict) -> tuple[str, str]:
    """(rollout state, rollout detail) for a --verify selftest release reading this heartbeat."""
    w = World()
    w.roll.fleet_side = FleetSideChecks(w.fly, str(tmp_path), w.clock.sleep, w.clock)  # type: ignore[arg-type]
    # The run above finished in real time, before the fake release began; the
    # judge only takes a run that finished after the deploy started. Moving the
    # timestamp forward is the one liberty taken: every verdict field is as written.
    beat = {**heartbeat, "finished_at": "2099-01-01T00:00:00Z"}
    w.store.status["ap-staging"] = {**fresh_status(), "selftest_json": json.dumps(beat)}
    w.release_all(only="ap-staging", verify="selftest", selftest_capable=True)
    detail = next(r["detail"] for r in w.store.rollouts.values() if r["customer_slug"] == "ap-staging")
    return w.store.states()["ap-staging"], detail


def test_the_incident_pattern_warns_at_boot_and_does_not_roll_back(seat: dict, tmp_path: Path) -> None:
    # Token OK; the mailbox endpoint answers 502, then 503, then not at all.
    seat["graph"].script = [502, 503, "hang"]
    doc = _selftest(seat, "boot")
    check = _check(doc)
    assert (check["ok"], check["warn"], check["inconclusive_streak"]) == (False, True, 1)
    assert "INCONCLUSIVE" in check["detail"] and "not a pass" in check["detail"]
    assert doc["failed"] == 0 and doc["warned"] == 1
    heartbeat = _heartbeat(doc)
    assert (heartbeat["failed_count"], heartbeat["warned"]) == (0, [CHECK_ID])
    state, detail = _release(tmp_path, heartbeat)
    assert state == "done"
    assert f"1 warning(s) on the release image: {CHECK_ID}" in detail


def test_a_503_at_boot_names_the_status(seat: dict) -> None:
    seat["graph"].script = [503]
    check = _check(_selftest(seat, "boot"))
    assert check["warn"] is True and "HTTP 503" in check["detail"]


def test_planted_breach_fails_at_boot_and_rolls_the_release_back(seat: dict, tmp_path: Path) -> None:
    # FALSIFIER: Microsoft answers 200, the read app CAN read the approver's
    # inbox. This must stay a failure that rolls the release back.
    seat["graph"].script = [200]
    doc = _selftest(seat, "boot")
    check = _check(doc)
    assert (check["ok"], check["warn"]) == (False, False)
    assert "can read" in check["detail"] and "inconclusive_streak" not in check
    heartbeat = _heartbeat(doc)
    assert heartbeat["failed"] == [CHECK_ID]
    state, detail = _release(tmp_path, heartbeat)
    assert state == "rolled_back"
    assert CHECK_ID in detail


def test_a_refused_read_passes_and_releases(seat: dict, tmp_path: Path) -> None:
    seat["graph"].script = [403]
    doc = _selftest(seat, "boot")
    assert _check(doc)["ok"] is True
    assert _release(tmp_path, _heartbeat(doc))[0] == "done"


def test_an_outage_that_does_not_clear_becomes_a_failure_hourly_and_pages(seat: dict) -> None:
    seat["graph"].script = [503]
    streaks = []
    boot = _selftest(seat, "boot")
    streaks.append(_check(boot)["inconclusive_streak"])
    hourly = [_selftest(seat, "hourly") for _ in range(4)]
    streaks += [_check(d)["inconclusive_streak"] for d in hourly]
    assert streaks == [1, 2, 3, 4, 5]
    # Runs 1-3 warn; run 4 (the default INCONCLUSIVE_FAIL_AFTER) is the first FAILED.
    assert [_check(d)["warn"] for d in [boot, *hourly]] == [True, True, True, False, False]
    assert [d["failed_streak"] for d in [boot, *hourly]] == [0, 0, 0, 1, 2]
    assert "inconclusive on 4 consecutive runs" in _check(hourly[2])["detail"]
    # What the heartbeat ships on run 5: an hourly failure, two runs in a row.
    # fleet-alerts pages selftest_failing on exactly this (SELFTEST_HOURLY_STREAK).
    last = _heartbeat(hourly[3])
    assert (last["run_kind"], last["failed"], last["failed_streak"]) == ("hourly", [CHECK_ID], 2)


def test_a_boot_run_never_escalates_and_an_answer_clears_the_streak(seat: dict) -> None:
    seat["graph"].script = [503]
    for _ in range(4):
        _selftest(seat, "hourly")
    # A boot run with streak 5 still only warns: a release's verdict is never a
    # failure for Microsoft's weather.
    boot = _check(_selftest(seat, "boot"))
    assert (boot["warn"], boot["inconclusive_streak"]) == (True, 5)
    seat["graph"].script = [403]
    cleared = _selftest(seat, "hourly")
    assert _check(cleared)["ok"] is True and "inconclusive_streak" not in _check(cleared)
    seat["graph"].script = [503]
    assert _check(_selftest(seat, "hourly"))["inconclusive_streak"] == 1


STATE = "opt/data/selftest-state/inconclusive.json"


def _restart(seat: dict) -> None:
    """A Machine restart: /run is a tmpfs and comes back empty; the volume stays."""
    run = seat["root"] / "run"
    for path in sorted(run.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()


def test_the_streak_survives_restarts_and_escalates_on_the_first_hourly_run_after(seat: dict) -> None:
    # Review finding: on tmpfs a seat restarted more often than every ~3 h never
    # escalated. Boot runs count toward the streak; only an hourly run escalates.
    seat["graph"].script = [503]
    boots = []
    for _ in range(3):
        boots.append(_check(_selftest(seat, "boot")))
        _restart(seat)
    assert [(b["warn"], b["inconclusive_streak"]) for b in boots] == [(True, 1), (True, 2), (True, 3)]
    assert not (seat["root"] / "run/smd-selftest/results.json").exists()
    hourly = _check(_selftest(seat, "hourly"))
    assert (hourly["ok"], hourly["warn"], hourly["inconclusive_streak"]) == (False, False, 4)
    state = seat["root"] / STATE
    assert oct(state.parent.stat().st_mode & 0o777) == "0o700" and oct(state.stat().st_mode & 0o777) == "0o600"


def test_four_hours_since_the_first_inconclusive_escalates_before_four_runs(seat: dict) -> None:
    seat["graph"].script = [503]
    assert _check(_selftest(seat, "boot"))["inconclusive_streak"] == 1
    state = seat["root"] / STATE
    doc = json.loads(state.read_text())
    doc[CHECK_ID]["first_seen"] -= 4 * 3600 + 60  # first seen just over four hours ago
    state.write_text(json.dumps(doc))
    hourly = _check(_selftest(seat, "hourly"))
    assert (hourly["ok"], hourly["warn"], hourly["inconclusive_streak"]) == (False, False, 2)
    assert "over 241 min" in hourly["detail"]
    # The same age on a boot run is still only a warning.
    doc = json.loads(state.read_text())
    doc[CHECK_ID]["first_seen"] -= 3600
    state.write_text(json.dumps(doc))
    assert _check(_selftest(seat, "boot"))["warn"] is True


def test_a_state_root_did_not_write_is_discarded_never_read(seat: dict) -> None:
    # A planted streak cannot forge anything: a directory with a loose mode is
    # removed and the streak starts over.
    seat["graph"].script = [503]
    state = seat["root"] / STATE
    state.parent.mkdir(parents=True)
    state.parent.chmod(0o755)
    state.write_text(json.dumps({CHECK_ID: {"streak": 99, "first_seen": 0}}))
    assert _check(_selftest(seat, "hourly"))["inconclusive_streak"] == 1
    assert oct(state.parent.stat().st_mode & 0o777) == "0o700"


def test_an_address_graph_does_not_know_warns_at_boot_and_does_not_roll_back(seat: dict, tmp_path: Path) -> None:
    seat["graph"].script = [404]
    doc = _selftest(seat, "boot")
    check = _check(doc)
    assert (check["ok"], check["warn"]) == (False, True)
    assert f"cannot prove the policy refuses {STAFF}" in check["detail"]
    assert _release(tmp_path, _heartbeat(doc))[0] == "done"


def test_a_breach_found_hourly_fails_at_once_and_pages_on_the_existing_streak(seat: dict) -> None:
    # Strict hourly: Microsoft's YES between releases is a breach, not weather.
    # FALSIFIER: bind the check soft (check_vendor) and run 1 is a warning,
    # failed_streak stays 0 and nothing ever pages.
    seat["graph"].script = [403]
    assert _check(_selftest(seat, "boot"))["ok"] is True
    seat["graph"].script = [200]
    first = _selftest(seat, "hourly")
    check = _check(first)
    assert (check["ok"], check["warn"]) == (False, False)
    assert "can read" in check["detail"] and "inconclusive_streak" not in check
    assert first["failed_streak"] == 1
    beat = _heartbeat(first)
    assert (beat["run_kind"], beat["failed"], beat["failed_streak"]) == ("hourly", [CHECK_ID], 1)
    # The next hourly run: failed_streak 2, which fleet-alerts pages as
    # selftest_failing (SELFTEST_HOURLY_STREAK; selftest.test.ts pins that half).
    second = _heartbeat(_selftest(seat, "hourly"))
    assert (second["run_kind"], second["failed"], second["failed_streak"]) == ("hourly", [CHECK_ID], 2)

"""The in-image self-test (selftest.sh) runs the seat check list and writes results.

``selftest.sh`` is EXECUTED here, against a fake seat root (SMD_SELFTEST_ROOT)
with planted violations, running the REAL shared list in
``operator/templates/seat-checks.sh``. Two shims stand in for what a macOS
laptop or an unprivileged CI runner lacks: ``setpriv`` (drops its flags and
runs the command as the current user) and ``stat`` (GNU ``-c`` formats,
answered from Python). Most of the 70-odd checks fail in a fake root (nothing
is owned by root here); each test asserts on the checks it plants, plus a
control that the same check passes when the violation is absent.

The runner mechanics that do not depend on the list (failed_streak, the
trigger, the crash counter) run against a one-check list in a tmp dir, so a
clean run is possible.

Falsifiers carried, one per behaviour the plan requires:
  empty retired list fails              retired-skills-absent
  unauthored lane passes with a note    demand-firm-inputs-absent
  cleanly empty vault is not authored   demand-firm-inputs-absent (state absent)
  unreachable vault still fails         demand-firm-inputs-present (state unreachable)
  busy lane warns hourly, fails at boot medchron-daemon-idle
  vendor probe failure warns hourly     agentmail-webhook-secret-matches-vendor
  schema, atomic 0644 write             results.json
  crash counter                         selftest-crashlooping
  a trigger root did not place is ignored
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "operator" / "templates"
SELFTEST = TEMPLATES / "selftest.sh"
HERMES = "3" * 40

STAT_SHIM = f"""#!{sys.executable}
import grp, os, pwd, stat, sys
args = sys.argv[1:]
if len(args) != 3 or args[0] != "-c":
    os.execv("/usr/bin/stat", ["stat", *args])
fmt, path = args[1], args[2]
try:
    st = os.stat(path)
except OSError as exc:
    sys.stderr.write(f"stat: {{exc}}\\n")
    sys.exit(1)
def user(u):
    try:
        return pwd.getpwuid(u).pw_name
    except KeyError:
        return str(u)
def group(g):
    try:
        return grp.getgrgid(g).gr_name
    except KeyError:
        return str(g)
out = (fmt.replace("%U", user(st.st_uid)).replace("%G", group(st.st_gid))
       .replace("%a", format(stat.S_IMODE(st.st_mode), "o")).replace("%Y", str(int(st.st_mtime))))
print(out)
"""

SETPRIV_SHIM = """#!/bin/sh
while [ $# -gt 0 ]; do case "$1" in --*) shift ;; *) break ;; esac; done
exec "$@"
"""


# What Hermes v0.22.5 records in the launching profile's gateway_state.json when
# gateway.standalone: true keeps it single-profile (STANDALONE_PROFILE_REASON,
# hermes_cli/gateway_multiplex_mode.py).
STANDALONE_REASON = "this profile is standalone (gateway.standalone: true); it serves only itself"
STANDALONE_STATE = {"gateway_state": "running", "multiplex_standalone_reason": STANDALONE_REASON}


def _write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def _customer_yaml(skills: list[str]) -> str:
    rows = "".join(f"      - name: {s}\n" for s in skills)
    return f"machine:\n  memory_mb: 2048\npersonas:\n  - slug: operator\n    skills:\n{rows}"


@pytest.fixture
def seat(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "root"
    shims = tmp_path / "shims"
    _write(shims / "stat", STAT_SHIM, 0o755)
    _write(shims / "setpriv", SETPRIV_SHIM, 0o755)
    venv_bin = root / "opt/hermes/.venv/bin"
    venv_bin.mkdir(parents=True)
    # A wrapper, not a symlink: a venv python reached through a symlink in
    # another directory loses its venv (pyvenv.cfg is found beside argv[0]).
    _write(venv_bin / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', 0o755)
    _write(root / "opt/hermes/HERMES_SHA", HERMES + "\n")
    _write(root / "var/lib/smd-config/customer.yaml", _customer_yaml(["inbox-triage"]))
    _write(root / "app/retired-skills.txt", "# retired\nold-tracker\n")
    (root / "opt/data/skills").mkdir(parents=True)
    _write(root / "opt/data/profiles/operator/state/gateway.heartbeat", "beat\n")
    _write(root / "opt/data/profiles/operator/gateway_state.json", json.dumps(STANDALONE_STATE))
    _write(root / "run/smd-gateway-liveness/state", "armed")
    _write(root / "app/agentmail-webhook-secret-probe.py", "raise SystemExit(0)\n")
    return {"root": root, "shims": shims}


def _env(seat: dict[str, Path], **over: str) -> dict[str, str]:
    env = {
        "PATH": f"{seat['shims']}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(seat["root"]),
        "SMD_SELFTEST_ROOT": str(seat["root"]),
        "SMD_SELFTEST_CHECKS_DIR": str(TEMPLATES),
        "SMD_SELFTEST_PYTHON": sys.executable,
        "SMD_SELFTEST_CHECK_TIMEOUT_S": "60",
        "SMD_SELFTEST_POLL_S": "1",
        "SMD_HERMES_UPSTREAM_SHA": HERMES,
        "SMD_OVERLAY_REF": "1" * 40,
        "FLY_IMAGE_REF": "registry.fly.io/hermes-fleet:rel-11111111-202610230000",
        "FLY_APP_NAME": "hermes-fixture",
    }
    env.update(over)
    return env


def _once(seat: dict[str, Path], kind: str, **over: str) -> dict:
    done = subprocess.run(  # noqa: S603 - the repo's own self-test, run against a tmp tree
        ["bash", str(SELFTEST), "--once", kind], env=_env(seat, **over), capture_output=True, text=True, timeout=600
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads((seat["root"] / "run/smd-selftest/results.json").read_text())


def _check(doc: dict, cid: str) -> dict:
    found = [c for c in doc["checks"] if c["id"] == cid]
    assert len(found) == 1, f"{cid} ran {len(found)} times"
    return found[0]


def test_schema_and_atomic_0644_write(seat: dict[str, Path]) -> None:
    doc = _once(seat, "boot")
    assert doc["schema_version"] == 1 and doc["run_kind"] == "boot"
    assert doc["version"] == "1" * 40 and doc["image_ref"].startswith("registry.fly.io/hermes-fleet:")
    assert doc["hermes_sha"] == HERMES and doc["expected_hermes_sha"] == HERMES
    assert doc["total"] == len(doc["checks"]) >= 60
    assert doc["failed"] == sum(1 for c in doc["checks"] if not c["ok"] and not c["warn"])
    assert doc["warned"] == sum(1 for c in doc["checks"] if c["warn"])
    assert all(set(c) == {"id", "ok", "warn", "detail"} and len(c["detail"]) <= 300 for c in doc["checks"])
    assert _check(doc, "hermes-sha-matches-pin")["ok"] is True
    results = seat["root"] / "run/smd-selftest/results.json"
    assert stat.S_IMODE(results.stat().st_mode) == 0o644
    # Written beside and renamed over: no temp file is left behind.
    assert sorted(p.name for p in results.parent.iterdir() if p.name.startswith(".")) == []


def test_empty_retired_list_fails_and_a_retired_skill_on_the_volume_fails(seat: dict[str, Path]) -> None:
    assert _check(_once(seat, "hourly"), "retired-skills-absent")["ok"] is True
    (seat["root"] / "opt/data/skills/old-tracker").mkdir()
    planted = _check(_once(seat, "hourly"), "retired-skills-absent")
    assert planted["ok"] is False and "old-tracker" in planted["detail"]
    (seat["root"] / "opt/data/skills/old-tracker").rmdir()
    _write(seat["root"] / "app/retired-skills.txt", "# nothing but comments\n\n")
    empty = _check(_once(seat, "hourly"), "retired-skills-absent")
    assert empty["ok"] is False and "no retired skill name" in empty["detail"]


def test_unauthored_lane_passes_with_a_note_and_an_authored_one_is_checked(seat: dict[str, Path]) -> None:
    note = _check(_once(seat, "hourly"), "demand-firm-inputs-absent")
    assert note["ok"] is True and "not authored" in note["detail"]
    _write(seat["root"] / "var/lib/smd-config/customer.yaml", _customer_yaml(["demand-letter-drafter"]))
    doc = _once(seat, "hourly")
    assert not [c for c in doc["checks"] if c["id"] == "demand-firm-inputs-absent"]
    assert _check(doc, "demand-firm-inputs-present")["ok"] is False


def _lane_state(seat: dict[str, Path], lane: str, word: str) -> None:
    """What entrypoint-fetch-inputs.sh records for a lane at boot."""
    _write(seat["root"] / f"run/smd-lanes/{lane}.inputs", f"{word}\n")


# pilot-smokeball (2026-10-23): demand-letter-drafter enabled, the vault's
# demand/ prefix empty by design since 2026-07-28. The boot's fetch records a
# clean `absent`; the lane is not authored for the seat and passes with a note.
def test_enabled_lane_whose_vault_is_cleanly_empty_passes_with_a_note(seat: dict[str, Path]) -> None:
    _write(seat["root"] / "var/lib/smd-config/customer.yaml", _customer_yaml(["demand-letter-drafter"]))
    _lane_state(seat, "demand", "absent")
    doc = _once(seat, "boot", SMD_SELFTEST_ROOT_UID=str(os.getuid()))
    note = _check(doc, "demand-firm-inputs-absent")
    assert note["ok"] is True and note["warn"] is False and "not authored" in note["detail"]
    assert not [c for c in doc["checks"] if c["id"] == "demand-firm-inputs-present"]


# The 2026-10-14 A&P v141 falsifier: the inputs are in the vault and the
# boot's fetch failed. `unreachable` keeps the lane authored, so the check fails.
def test_enabled_lane_whose_fetch_was_unreachable_still_fails(seat: dict[str, Path]) -> None:
    _write(seat["root"] / "var/lib/smd-config/customer.yaml", _customer_yaml(["demand-letter-drafter"]))
    _lane_state(seat, "demand", "unreachable")
    doc = _once(seat, "boot", SMD_SELFTEST_ROOT_UID=str(os.getuid()))
    assert not [c for c in doc["checks"] if c["id"] == "demand-firm-inputs-absent"]
    present = _check(doc, "demand-firm-inputs-present")
    assert present["ok"] is False and present["warn"] is False


def test_enabled_lane_with_no_state_file_still_fails(seat: dict[str, Path]) -> None:
    _write(seat["root"] / "var/lib/smd-config/customer.yaml", _customer_yaml(["demand-letter-drafter"]))
    doc = _once(seat, "boot", SMD_SELFTEST_ROOT_UID=str(os.getuid()))
    assert not [c for c in doc["checks"] if c["id"] == "demand-firm-inputs-absent"]
    assert _check(doc, "demand-firm-inputs-present")["ok"] is False


def test_an_absent_verdict_root_did_not_write_is_not_trusted(seat: dict[str, Path]) -> None:
    # ROOT_UID 0 while the test user wrote the file: a non-root writer cannot
    # turn a lane's presence check off.
    _write(seat["root"] / "var/lib/smd-config/customer.yaml", _customer_yaml(["demand-letter-drafter"]))
    _lane_state(seat, "demand", "absent")
    doc = _once(seat, "boot", SMD_SELFTEST_ROOT_UID="0")
    assert not [c for c in doc["checks"] if c["id"] == "demand-firm-inputs-absent"]
    assert _check(doc, "demand-firm-inputs-present")["ok"] is False


def test_enabled_lane_with_inputs_fetched_runs_the_presence_check(seat: dict[str, Path]) -> None:
    # The stat shim answers the real owner; the fixture's files belong to the
    # test user, so a shim that names that owner root:medchron stands in for a
    # seat where the entrypoint chowned the inputs.
    shim = seat["shims"] / "stat"
    shim.write_text(
        shim.read_text().replace(
            "print(out)", "print(out.replace(user(os.getuid()), 'root', 1).replace(group(os.getgid()), 'medchron', 1))"
        )
    )
    _write(seat["root"] / "var/lib/smd-config/customer.yaml", _customer_yaml(["demand-letter-drafter"]))
    _lane_state(seat, "demand", "fetched")
    _write(seat["root"] / "var/lib/smd-config/demand/demand-firm.yaml", "firm: x\n", 0o640)
    doc = _once(seat, "boot", SMD_SELFTEST_ROOT_UID=str(os.getuid()))
    assert _check(doc, "demand-firm-inputs-present")["ok"] is True
    assert not [c for c in doc["checks"] if c["id"] == "demand-firm-inputs-absent"]


def test_unreadable_customer_yaml_reads_as_authored_never_as_absent(seat: dict[str, Path]) -> None:
    (seat["root"] / "var/lib/smd-config/customer.yaml").write_text(": not yaml : [")
    doc = _once(seat, "hourly")
    assert _check(doc, "medchron-firm-config-present")["ok"] is False
    assert not [c for c in doc["checks"] if c["id"].endswith("-absent") and "not authored" in c["detail"]]


def test_busy_lane_fails_at_boot_and_warns_hourly(seat: dict[str, Path]) -> None:
    assert _check(_once(seat, "boot"), "medchron-daemon-idle")["ok"] is True
    _write(seat["root"] / "run/smd-medchron/child.pid", "4242\n")
    boot = _check(_once(seat, "boot"), "medchron-daemon-idle")
    assert boot["ok"] is False and boot["warn"] is False
    hourly = _check(_once(seat, "hourly"), "medchron-daemon-idle")
    assert hourly["ok"] is False and hourly["warn"] is True


def test_vendor_probe_failure_fails_at_boot_and_warns_hourly(seat: dict[str, Path]) -> None:
    cid = "agentmail-webhook-secret-matches-vendor"
    assert _check(_once(seat, "hourly"), cid)["ok"] is True
    _write(seat["root"] / "app/agentmail-webhook-secret-probe.py", "raise SystemExit('vendor said 503')\n")
    hourly = _check(_once(seat, "hourly"), cid)
    assert hourly["warn"] is True and "503" in hourly["detail"]
    boot = _check(_once(seat, "boot"), cid)
    assert boot["ok"] is False and boot["warn"] is False


# ---- runner mechanics, against a one-check list ----


def _mini_checks(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "checks"
    _write(d / "seat-checks.sh", f"seat_checks() {{\n{body}\n}}\n")
    return d


def test_failed_streak_counts_consecutive_failed_runs(seat: dict[str, Path], tmp_path: Path) -> None:
    flag = tmp_path / "broken"
    checks = _mini_checks(tmp_path, f'check "probe" "! test -e {flag}"')
    over = {"SMD_SELFTEST_CHECKS_DIR": str(checks)}
    assert _once(seat, "hourly", **over)["failed_streak"] == 0
    flag.touch()
    assert _once(seat, "boot", **over)["failed_streak"] == 1
    assert _once(seat, "hourly", **over)["failed_streak"] == 2
    flag.unlink()
    assert _once(seat, "hourly", **over)["failed_streak"] == 0


def test_crash_record_is_a_failure_and_keeps_the_failed_streak(seat: dict[str, Path], tmp_path: Path) -> None:
    # FALSIFIER: record the crash as a warning (the code before 2026-10-27) and
    # a failing verdict is overwritten with failed=0 on every restart, which
    # resolves selftest_failing and passes a release (review SB-2).
    _write(seat["root"] / "run/smd-selftest/results.json", json.dumps({"failed": 3, "failed_streak": 2, "checks": []}))
    checks = tmp_path / "crash"
    _write(checks / "seat-checks.sh", "exit 3\n")
    env = _env(
        seat,
        SMD_SELFTEST_CHECKS_DIR=str(checks),
        SMD_SELFTEST_MAX_RESTARTS="2",
        SMD_SELFTEST_BACKOFF_BASE_S="0",
        SMD_SELFTEST_ARM_WAIT_S="0",
    )
    done = subprocess.run(  # noqa: S603 - the repo's own self-test, run against a tmp tree
        ["bash", str(SELFTEST), "--supervise"], env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 1
    doc = json.loads((seat["root"] / "run/smd-selftest/results.json").read_text())
    crash = _check(doc, "selftest-crashlooping")
    assert crash["ok"] is False and crash["warn"] is False
    assert "exited 3" in crash["detail"] and "restart 2" in crash["detail"]
    # Two restarts, each a failed run on top of the two failed runs before.
    assert doc["failed"] == 1 and doc["warned"] == 0 and doc["failed_streak"] == 4


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ('seat_checks() {\n  check "a" "true"\n  if then\n}\n', "could not source"),
        (None, "could not source"),
        ('check_list() { check "a" "true"; }\n', "defines no seat_checks"),
        ("seat_checks() {\n  :\n}\n", "recorded no check"),
    ],
    ids=["syntax-error", "missing", "no-seat-checks-function", "empty"],
)
def test_a_check_list_that_does_not_load_is_a_failed_run(
    seat: dict[str, Path], tmp_path: Path, body: str | None, detail: str
) -> None:
    # FALSIFIER: call seat_checks unconditionally after `source` (the code before
    # 2026-10-27) and each of these writes total=0, failed=0, a pass (SB-3).
    checks = tmp_path / "broken"
    checks.mkdir()
    if body is not None:
        _write(checks / "seat-checks.sh", body)
    doc = _once(seat, "boot", SMD_SELFTEST_CHECKS_DIR=str(checks))
    assert doc["failed"] == 1 and doc["total"] == 1, doc
    (only,) = doc["checks"]
    assert only["id"].startswith("selftest-") and not only["ok"] and not only["warn"]
    assert detail in only["detail"]


def test_no_seat_check_uses_the_runners_reserved_prefix() -> None:
    # The consumers read a run whose only checks are `selftest-*` as one in which
    # no seat check ran; a seat check named that way would hide a real verdict.
    text = (TEMPLATES / "seat-checks.sh").read_text()
    assert '"selftest-' not in text and "'selftest-" not in text


def _run_kind(seat: dict[str, Path]) -> str | None:
    try:
        return json.loads((seat["root"] / "run/smd-selftest/results.json").read_text())["run_kind"]
    except (OSError, ValueError):
        return None


def _await(pred, budget: float = 30.0) -> bool:
    end = time.monotonic() + budget
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


@pytest.mark.parametrize("honoured", [True, False])
def test_trigger_runs_only_when_root_placed_it(seat: dict[str, Path], tmp_path: Path, honoured: bool) -> None:
    checks = _mini_checks(tmp_path, 'check "probe" "true"')
    # The trigger must be owned by ROOT_UID. As a non-root test user, naming our
    # own uid stands in for root; naming 0 is the case a non-root writer is in.
    root_uid = str(os.getuid()) if honoured else "0"
    env = _env(seat, SMD_SELFTEST_CHECKS_DIR=str(checks), SMD_SELFTEST_PERIOD_S="3600", SMD_SELFTEST_ROOT_UID=root_uid)
    proc = subprocess.Popen(["bash", str(SELFTEST), "--loop"], env=env)  # noqa: S603 - the repo's own self-test
    try:
        assert _await(lambda: _run_kind(seat) == "boot")
        trigger_dir = seat["root"] / "run/smd-selftest/trigger"
        assert stat.S_IMODE(trigger_dir.stat().st_mode) == 0o700
        (trigger_dir / "run-now").touch()
        if honoured:
            assert _await(lambda: _run_kind(seat) == "hourly")
        else:
            assert _await(lambda: not (trigger_dir / "run-now").exists())
            time.sleep(2)
            assert _run_kind(seat) == "boot"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_the_gateway_must_record_the_standalone_guard(seat: dict[str, Path]) -> None:
    # Hermes v0.22.5 promotion (#3564). FALSIFIER: a gateway that multiplexes
    # (no guard reason) or stayed standalone for another cause fails the check;
    # a missing record fails it too. Only the gateway.standalone reason passes.
    state = seat["root"] / "opt/data/profiles/operator/gateway_state.json"
    assert _check(_once(seat, "hourly"), "gateway-multiplex-standalone")["ok"] is True
    state.write_text(json.dumps({"gateway_state": "running", "multiplex_standalone_reason": None}))
    assert _check(_once(seat, "hourly"), "gateway-multiplex-standalone")["ok"] is False
    state.write_text(json.dumps({"gateway_state": "running", "multiplex_standalone_reason": "only one profile exists"}))
    assert _check(_once(seat, "hourly"), "gateway-multiplex-standalone")["ok"] is False
    state.unlink()
    assert _check(_once(seat, "hourly"), "gateway-multiplex-standalone")["ok"] is False


def test_a_stale_heartbeat_profile_is_not_judged_and_no_live_gateway_fails(seat: dict[str, Path]) -> None:
    beat = seat["root"] / "opt/data/profiles/operator/state/gateway.heartbeat"
    old = time.time() - 3600
    os.utime(beat, (old, old))
    found = _check(_once(seat, "hourly"), "gateway-multiplex-standalone")
    assert found["ok"] is False
    assert "no profile has a fresh gateway heartbeat" in found["detail"]

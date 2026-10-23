"""Regression pins for bin/connect-smokeball.sh (ss#2149 / ss#2171 PR C).

The script's two refusal branches were live-proven at merge (#2184:
vfy_01KZ20HF7AMCCZTT0N49WJVNAR currency-refusal, #2192 rescoped) but carried
ZERO test coverage — a regression in the seat-currency gate would have gone
uncaught until the next live connect attempt. These tests pin the gate
hermetically by stubbing `fly` on PATH and the fleet CLI through SMD_FLEET_BIN.
Since the fold (ADR 0095) there is no overlay pin to compare against: "current"
is the seat's `fleet status` row (no image drift against the newest completed
release) and the gateway's baked SMD_OVERLAY_REF equal to the heartbeat's
reported version.

What is pinned:
  * arg validation (usage, slug shape, missing customer.yaml)
  * currency REFUSAL: fleet status reports image drift -> exit 3, "REFUSED"
  * fail-closed: unreadable seat ref -> exit 3 (a seat whose gateway is down
    must not receive a token); no fleet row -> exit 3
  * gateway/heartbeat disagreement (a release mid-flight) -> exit 3
  * currency PASS falls through to the next gate (missing env -> exit 2),
    proving the gate passes on a current seat rather than refusing everything
    (Law 12: the check must be able to pass, or the refusal tests measure
    nothing).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "bin" / "connect-smokeball.sh"

# Syntactically-valid 40-hex ss-console commits that will never be real.
_CURRENT_REF = "a1b2c3d4" * 5
_STALE_REF = "deadbeef" * 5


def _write_fleet_stub(bin_dir: Path, overlay: str, drift: list[str]) -> Path:
    """A fake `operator/bin/fleet` whose `status --json` reports one seat row the
    way the real CLI does (operator/bin/lib/fleet/status.py seat_rows)."""
    rows = [
        {
            "seat": "pilot-smokeball",
            "ring": 1,
            "image_ref": "registry.fly.io/hermes-fleet:rel-x",
            "overlay": overlay,
            "drift": drift,
        }
    ]
    stub = bin_dir / "fleet"
    stub.write_text("#!/usr/bin/env bash\n" + "cat <<'EOF'\n" + json.dumps(rows) + "\nEOF\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def _write_fly_stub(bin_dir: Path, ref_output: str | None) -> None:
    """A fake `fly` that prints ``ref_output`` for the ssh env probe (or
    nothing at all when ``ref_output`` is None — the unreadable-seat case)."""
    stub = bin_dir / "fly"
    body = "#!/usr/bin/env bash\n"
    if ref_output is not None:
        body += f"echo '{ref_output}'\n"
    body += "exit 0\n"
    stub.write_text(body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _run(args: list[str], bin_dir: Path | None = None, env_extra: dict | None = None):
    env = dict(os.environ)
    # The env gate must see ABSENCE, not inherited operator creds.
    env.pop("OPERATOR_OAUTH_STATE_MASTER", None)
    env.pop("SMOKEBALL_STAGING_CLIENT_ID", None)
    env.pop("SMOKEBALL_PROD_CLIENT_ID", None)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(  # noqa: S603 - test runs the script under bash with test-authored args, no shell
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_no_args_is_usage_error() -> None:
    result = _run([])
    assert result.returncode == 1
    assert "Usage" in result.stderr or "usage" in result.stderr.lower()


def test_invalid_slug_refused() -> None:
    result = _run(["Bad_Slug!"])
    assert result.returncode == 1
    assert "invalid slug" in result.stderr


def test_missing_customer_yaml_refused() -> None:
    result = _run(["no-such-customer"])
    assert result.returncode == 1
    assert "no customer.yaml" in result.stderr


def test_stale_seat_is_refused_exit_3(tmp_path: Path) -> None:
    _write_fly_stub(tmp_path, _STALE_REF)
    fleet = _write_fleet_stub(tmp_path, _STALE_REF, ["image"])
    result = _run(["pilot-smokeball"], bin_dir=tmp_path, env_extra={"SMD_FLEET_BIN": str(fleet)})
    assert result.returncode == 3, result.stderr
    assert "REFUSED" in result.stderr
    assert "fleet release" in result.stderr and "--only pilot-smokeball" in result.stderr  # remediation printed


def test_unreadable_seat_ref_fails_closed_exit_3(tmp_path: Path) -> None:
    _write_fly_stub(tmp_path, None)  # gateway down / env unreadable
    fleet = _write_fleet_stub(tmp_path, _CURRENT_REF, [])
    result = _run(["pilot-smokeball"], bin_dir=tmp_path, env_extra={"SMD_FLEET_BIN": str(fleet)})
    assert result.returncode == 3, result.stderr
    assert "cannot verify" in result.stderr


def test_missing_fleet_row_fails_closed_exit_3(tmp_path: Path) -> None:
    """D1 unreachable or the seat in no ring reads as unknown, never as current."""
    _write_fly_stub(tmp_path, _CURRENT_REF)
    stub = tmp_path / "fleet"
    stub.write_text("#!/usr/bin/env bash\necho '[]'\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    result = _run(["pilot-smokeball"], bin_dir=tmp_path, env_extra={"SMD_FLEET_BIN": str(stub)})
    assert result.returncode == 3, result.stderr
    assert "cannot verify" in result.stderr


def test_gateway_and_heartbeat_disagree_exit_3(tmp_path: Path) -> None:
    """A release mid-flight: the gateway runs one commit, the heartbeat still reports another."""
    _write_fly_stub(tmp_path, _CURRENT_REF)
    fleet = _write_fleet_stub(tmp_path, _STALE_REF, [])
    result = _run(["pilot-smokeball"], bin_dir=tmp_path, env_extra={"SMD_FLEET_BIN": str(fleet)})
    assert result.returncode == 3, result.stderr
    assert "REFUSED" in result.stderr and "heartbeat" in result.stderr


def test_current_seat_passes_gate_and_stops_at_env_check(tmp_path: Path) -> None:
    """The pass direction (Law 12): a seat with no image drift whose gateway and
    heartbeat agree clears the gate — the run proceeds to the yaml/env stage and
    fails there on MISSING env (exit 2), never on the currency gate (exit 3)."""
    _write_fly_stub(tmp_path, _CURRENT_REF)
    fleet = _write_fleet_stub(tmp_path, _CURRENT_REF, [])
    result = _run(["pilot-smokeball"], bin_dir=tmp_path, env_extra={"SMD_FLEET_BIN": str(fleet)})
    assert "proceeding" in result.stderr  # the gate's success line
    assert result.returncode == 2, result.stderr
    assert "missing required env" in result.stderr
